"""
Helius Webhook Server

Receives real-time notifications from Helius webhooks for new token creations
on pump.fun bonding curve.

Setup:
1. Run this server on your VPS
2. Create webhook at dashboard.helius.dev pointing to http://YOUR_VPS_IP:8765/webhook
3. Configure webhook to monitor pump.fun program
"""

import asyncio
import logging
import json
import time
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from aiohttp import web

logger = logging.getLogger(__name__)

# Pump.fun program ID
PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# NEW: Raydium V4 program ID
RAYDIUM_V4_PROGRAM_ID = "675k1q2u71A56iwG8NjSyaWvXP9Xicp9QYS94UfSraN2"

# NEW: Import whale wallets for detection
try:
    from ..config import WHALE_WALLETS, WHALE_MIN_BUY_SOL
except ImportError:
    WHALE_WALLETS = []
    WHALE_MIN_BUY_SOL = 0.5


@dataclass
class NewToken:
    """Newly detected token from webhook"""
    mint: str
    symbol: str
    name: str
    creator: str
    timestamp: float
    bonding_curve: str = ""
    initial_buy_sol: float = 0.0
    whale_address: str = ""  # If this was a whale buy
    
    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp
    
    @property
    def age_minutes(self) -> float:
        return self.age_seconds / 60


@dataclass
class DevWalletEvent:
    """Event when dev wallet makes a transaction"""
    dev_wallet: str
    mint: str
    event_type: str  # SELL, TRANSFER, LP_REMOVE
    amount: float
    timestamp: float
    signature: str = ""
    
    @property
    def is_sell(self) -> bool:
        return self.event_type == "SELL"


@dataclass
class LPEvent:
    """Event when LP pool changes"""
    mint: str
    pool_address: str
    event_type: str  # LP_ADDED, LP_REMOVED, RESERVE_CHANGE
    change_pct: float  # Percentage change in liquidity
    timestamp: float
    signature: str = ""


class HeliusWebhookServer:
    """
    HTTP server that receives Helius webhook notifications.
    
    When a new token is created on pump.fun, Helius sends a POST request
    with the transaction details. We parse it and add to a queue.
    
    EXTENDED: Now also handles dev wallet and LP monitoring events.
    """
    
    def __init__(
        self,
        port: int = 8765,
        webhook_path: str = "/webhook",
        auth_token: Optional[str] = None,
        helius_api_key: Optional[str] = None,
        session = None  # aiohttp.ClientSession for API calls
    ):
        self.port = port
        self.webhook_path = webhook_path
        self.auth_token = auth_token
        self.helius_api_key = helius_api_key
        self.session = session
        
        # Token queue for the scanner to consume (increased to handle high volume)
        self.token_queue: asyncio.Queue[NewToken] = asyncio.Queue(maxsize=500)
        
        # Counter for dropped tokens (to reduce log spam)
        self.dropped_count = 0
        
        # Pause flag - when True, incoming tokens are ignored
        self.paused = False
        
        # Recent tokens cache (to avoid duplicates)
        self.recent_mints: Dict[str, float] = {}
        self.cache_ttl = 300  # 5 minutes
        
        # Callbacks for new tokens
        self.on_new_token: Optional[Callable[[NewToken], None]] = None
        
        # ===== NEW: Event-driven monitoring for dev/LP =====
        # Watched dev wallets (mint -> dev_wallet)
        self.watched_dev_wallets: Dict[str, str] = {}
        
        # Watched LP pools (mint -> pool_address)  
        self.watched_lp_pools: Dict[str, str] = {}
        
        # Event callbacks
        self.on_dev_event: Optional[Callable[[DevWalletEvent], None]] = None
        self.on_lp_event: Optional[Callable[[LPEvent], None]] = None
        
        # Event queues (alternative to callbacks)
        self.dev_event_queue: asyncio.Queue[DevWalletEvent] = asyncio.Queue(maxsize=100)
        self.lp_event_queue: asyncio.Queue[LPEvent] = asyncio.Queue(maxsize=100)
        # ===== END NEW =====
        
        # Server state
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.is_running = False
        
        logger.info(
            f"🔔 HeliusWebhookServer initialized on port {port}, "
            f"endpoint: {webhook_path}"
        )
    
    async def start(self):
        """Start the webhook server"""
        self.app = web.Application()
        self.app.router.add_post(self.webhook_path, self._handle_webhook)
        self.app.router.add_get("/health", self._handle_health)
        
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()
        
        self.is_running = True
        logger.info(f"✅ Webhook server started on http://0.0.0.0:{self.port}{self.webhook_path}")
        
        # Cleanup old cache entries periodically
        asyncio.create_task(self._cleanup_cache())
    
    async def stop(self):
        """Stop the webhook server"""
        if self.runner:
            await self.runner.cleanup()
        self.is_running = False
        logger.info("🛑 Webhook server stopped")
    
    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint"""
        return web.json_response({
            "status": "ok",
            "queue_size": self.token_queue.qsize(),
            "cached_tokens": len(self.recent_mints)
        })
    
    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook from Helius"""
        try:
            # If paused, just acknowledge without processing
            if self.paused:
                return web.json_response({"status": "paused", "received": 0})
            
            # Optional auth check
            if self.auth_token:
                auth_header = request.headers.get("Authorization", "")
                if auth_header != f"Bearer {self.auth_token}":
                    logger.warning("Unauthorized webhook request")
                    return web.Response(status=401, text="Unauthorized")
            
            # Parse body
            body = await request.json()
            
            # Helius sends array of transactions
            transactions = body if isinstance(body, list) else [body]
            
            tokens_found = 0
            dev_events = 0
            lp_events = 0
            
            for tx in transactions:
                # 1. Check for new token events
                token = self._parse_transaction(tx)
                if token:
                    # Check if we've seen this mint recently
                    if token.mint in self.recent_mints:
                        continue
                    
                    # Add to cache and queue
                    self.recent_mints[token.mint] = time.time()
                    
                    try:
                        self.token_queue.put_nowait(token)
                        tokens_found += 1
                        
                        source_text = "🐋 WHALE" if token.whale_address else "🆕 NEW"
                        logger.info(
                            f"{source_text}: [{token.symbol}] | "
                            f"Mint: {token.mint[:20]}..."
                        )
                        
                        # Call callback if set
                        if self.on_new_token:
                            self.on_new_token(token)
                            
                    except asyncio.QueueFull:
                        self.dropped_count += 1
                        # Only log every 50 dropped tokens to reduce spam
                        if self.dropped_count % 50 == 1:
                            logger.warning(f"Token queue full, dropped {self.dropped_count} tokens so far")
                
                # 2. Check for dev wallet events (event-driven monitoring)
                dev_event = self._check_dev_event(tx)
                if dev_event:
                    try:
                        self.dev_event_queue.put_nowait(dev_event)
                        dev_events += 1
                        if self.on_dev_event:
                            self.on_dev_event(dev_event)
                    except asyncio.QueueFull:
                        pass
                
                # 3. Check for LP events (event-driven monitoring)
                lp_event = self._check_lp_event(tx)
                if lp_event:
                    try:
                        self.lp_event_queue.put_nowait(lp_event)
                        lp_events += 1
                        if self.on_lp_event:
                            self.on_lp_event(lp_event)
                    except asyncio.QueueFull:
                        pass
            
            return web.json_response({
                "received": len(transactions), 
                "tokens": tokens_found,
                "dev_events": dev_events,
                "lp_events": lp_events
            })
            
        except json.JSONDecodeError:
            logger.error("Invalid JSON in webhook request")
            return web.json_response({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    def _parse_transaction(self, tx: Dict) -> Optional[NewToken]:
        """
        Generalized transaction parser for Helius enhanced transactions.
        Supports:
        - Pump.fun (New creation or Whale buy)
        - Raydium (Whale swap)
        """
        try:
            # 1. WHALE IDENTIFICATION
            fee_payer = tx.get("feePayer", "")
            active_whale = fee_payer if fee_payer in WHALE_WALLETS else ""
            
            # Detect program involvement
            instructions = tx.get("instructions", [])
            is_pump = any(ix.get("programId") == PUMP_PROGRAM_ID for ix in instructions)
            is_raydium = any(ix.get("programId") == RAYDIUM_V4_PROGRAM_ID for ix in instructions)
            source = tx.get("source", "")
            
            # 2. EXTRACT MINT
            mint = ""
            
            # Scenario A: Raydium Swap (Enhanced Helius Event)
            events = tx.get("events", {})
            swap_event = events.get("swap")
            if swap_event:
                # If a whale is swapping, we want to know what they are buying
                native_input = swap_event.get("nativeInput") # SOL
                native_output = swap_event.get("nativeOutput") # SOL
                
                # If they are giving SOL, they are buying a token
                if native_input:
                    # Token mint is usually in the tokenTransfers or instructions
                    # In Helius events, it's often more direct
                    inner_swaps = swap_event.get("innerSwaps", [])
                    if inner_swaps:
                        # Find the non-SOL mint
                        for s in inner_swaps:
                            if s.get("tokenInMint") and "So111" not in s.get("tokenInMint"):
                                mint = s.get("tokenInMint")
                            elif s.get("tokenOutMint") and "So111" not in s.get("tokenOutMint"):
                                mint = s.get("tokenOutMint")
            
            # Scenario B: Token Transfers (Universal Fallback)
            if not mint:
                token_transfers = tx.get("tokenTransfers", [])
                if token_transfers:
                    # Look for the non-SOL mint in the transfer related to the whale
                    for tt in token_transfers:
                        m = tt.get("mint", "")
                        if m and "So111" not in m:
                            # If it's a whale buying, they are the recipient
                            if active_whale and tt.get("toUserAccount") == active_whale:
                                mint = m
                                break
                            # If it's a new creation, it's just the first non-SOL mint
                            elif not mint:
                                mint = m
            
            if not mint:
                # Still no mint found? Check if it's even a target transaction
                if not is_pump and not is_raydium and source not in ["PUMP_FUN", "RAYDIUM"]:
                    return None
            
            # 3. BUILD RESULT
            # Only return IF:
            # - It's a new Pump.fun creation (any user)
            # - OR it's a whale transaction (on Pump or Raydium)
            
            if not active_whale and not is_pump:
                return None
            
            # If it's a pump creation, but not a whale, we need to be sure it's "CREATE"
            if not active_whale:
                # Existing logic: only track new creations from anyone
                if tx.get("type") != "CREATE" and source != "PUMP_FUN":
                    return None

            # Get symbol/name from events if possible (best source for new tokens)
            symbol = "???"
            name = ""
            
            # Scenario A: NFT/Token Events (New creations)
            events = tx.get("events", {})
            token_event = events.get("nft") or events.get("compressed") or events.get("token")
            if token_event:
                if not mint: mint = token_event.get("mint", "")
                symbol = token_event.get("symbol") or symbol
                name = token_event.get("name") or ""
            
            # Scenario B: Check tokenTransfers for any indexed metadata
            if symbol == "???" and tx.get("tokenTransfers"):
                for tt in tx["tokenTransfers"]:
                    if tt.get("symbol") and tt.get("symbol") != "SOL":
                        symbol = tt["symbol"]
                        if not mint: mint = tt.get("mint", "")
                        break
            
            # Scenario C: Robust Description Parsing
            if symbol == "???" and tx.get("description"):
                desc = tx.get("description", "")
                # Pattern 1: "User created ABC (mint)"
                if "created" in desc.lower():
                    parts = desc.split()
                    for i, p in enumerate(parts):
                        if p.lower() == "token" and i + 1 < len(parts):
                            symbol = parts[i+1].strip("()[],.")
                            break
                # Pattern 2: "User swapped X SOL for Y SYMBOL"
                elif "swapped" in desc.lower() or "bought" in desc.lower():
                    parts = desc.split()
                    if len(parts) >= 2:
                        potential = parts[-1].strip("()[],.")
                        if len(potential) < 10 and potential.isupper(): # Likely a symbol
                            symbol = potential
                
            # Scenario D: Instruction Data Search (Last Resort)
            if symbol == "???" and instructions:
                for ix in instructions:
                    # Some program logs contain the symbol string
                    inner = ix.get("innerInstructions", [])
                    # This is complex, but descriptive logs sometimes help
                    pass

            return NewToken(
                mint=mint,
                symbol=symbol,
                name=name,
                creator=fee_payer,
                timestamp=tx.get("timestamp", time.time()),
                initial_buy_sol=0.0, 
                whale_address=active_whale
            )
            
        except Exception as e:
            logger.debug(f"Error parsing transaction: {e}")
            return None
    
    async def _cleanup_cache(self):
        """Periodically clean up old cache entries"""
        while self.is_running:
            await asyncio.sleep(60)  # Check every minute
            
            now = time.time()
            expired = [
                mint for mint, ts in self.recent_mints.items()
                if now - ts > self.cache_ttl
            ]
            
            for mint in expired:
                del self.recent_mints[mint]
            
            if expired:
                logger.debug(f"Cleaned {len(expired)} expired mints from cache")
    
    async def get_new_token(self, timeout: float = 1.0) -> Optional[NewToken]:
        """Get a new token from the queue (non-blocking with timeout)"""
        try:
            return await asyncio.wait_for(self.token_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    def get_pending_tokens(self) -> List[NewToken]:
        """Get all pending tokens from the queue (non-blocking)"""
        tokens = []
        while not self.token_queue.empty():
            try:
                tokens.append(self.token_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return tokens
    
    async def fetch_token_metadata(self, mint: str) -> Dict:
        """
        Fetch token metadata from Helius DAS API.
        
        Returns dict with name, symbol, or empty dict if not found.
        """
        if not self.session or not self.helius_api_key:
            return {}
        
        try:
            url = f"https://mainnet.helius-rpc.com/?api-key={self.helius_api_key}"
            payload = {
                "jsonrpc": "2.0",
                "id": "get-asset",
                "method": "getAsset",
                "params": {"id": mint}
            }
            
            async with self.session.post(url, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    content = result.get("content", {})
                    metadata = content.get("metadata", {})
                    
                    return {
                        "name": metadata.get("name", ""),
                        "symbol": metadata.get("symbol", ""),
                        "description": metadata.get("description", "")
                    }
        except Exception as e:
            logger.debug(f"Error fetching token metadata: {e}")
        
        return {}

    # =========================================================================
    # EVENT-DRIVEN MONITORING: Dev Wallet and LP Pool Subscriptions
    # =========================================================================
    
    def watch_dev_wallet(self, mint: str, dev_wallet: str):
        """
        Start watching a dev wallet for transactions.
        
        When transactions from this wallet are detected via webhook,
        events will be pushed to dev_event_queue or on_dev_event callback.
        
        Note: The Helius webhook must be configured to monitor this address.
        Consider using the Helius API to dynamically add addresses.
        """
        self.watched_dev_wallets[mint] = dev_wallet
        logger.info(f"👁️ Watching dev wallet {dev_wallet[:12]}... for {mint[:12]}...")
    
    def unwatch_dev_wallet(self, mint: str):
        """Stop watching dev wallet for a token"""
        if mint in self.watched_dev_wallets:
            dev = self.watched_dev_wallets.pop(mint)
            logger.info(f"🚫 Stopped watching dev wallet for {mint[:12]}...")
    
    def watch_lp_pool(self, mint: str, pool_address: str):
        """
        Start watching an LP pool for changes.
        
        When transactions affecting this pool are detected,
        events will be pushed to lp_event_queue or on_lp_event callback.
        """
        self.watched_lp_pools[mint] = pool_address
        logger.info(f"💧 Watching LP pool {pool_address[:12]}... for {mint[:12]}...")
    
    def unwatch_lp_pool(self, mint: str):
        """Stop watching LP pool for a token"""
        if mint in self.watched_lp_pools:
            pool = self.watched_lp_pools.pop(mint)
            logger.info(f"🚫 Stopped watching LP pool for {mint[:12]}...")
    
    def unwatch_all(self, mint: str):
        """Stop all monitoring for a token"""
        self.unwatch_dev_wallet(mint)
        self.unwatch_lp_pool(mint)
    
    def _check_dev_event(self, tx: Dict) -> Optional[DevWalletEvent]:
        """
        Check if transaction involves a watched dev wallet.
        
        Called for every incoming webhook transaction.
        """
        fee_payer = tx.get("feePayer", "")
        
        # Check if this wallet is being watched
        for mint, dev_wallet in self.watched_dev_wallets.items():
            if fee_payer == dev_wallet:
                # Dev wallet made a transaction, check what type
                token_transfers = tx.get("tokenTransfers", [])
                
                for tt in token_transfers:
                    if tt.get("mint") == mint:
                        # Dev is moving this token
                        event_type = "TRANSFER"
                        amount = float(tt.get("tokenAmount", 0))
                        
                        # If sending to DEX/swap, it's likely a SELL
                        to_account = tt.get("toUserAccount", "")
                        if "swap" in str(tx.get("source", "")).lower():
                            event_type = "SELL"
                        
                        event = DevWalletEvent(
                            dev_wallet=dev_wallet,
                            mint=mint,
                            event_type=event_type,
                            amount=amount,
                            timestamp=tx.get("timestamp", time.time()),
                            signature=tx.get("signature", "")
                        )
                        
                        logger.warning(
                            f"🚨 Dev Activity: {event_type} | "
                            f"{mint[:12]}... | Amount: {amount:.2f}"
                        )
                        return event
        
        return None
    
    def _check_lp_event(self, tx: Dict) -> Optional[LPEvent]:
        """
        Check if transaction involves a watched LP pool.
        
        Called for every incoming webhook transaction.
        """
        # Check account keys for watched pools
        account_keys = tx.get("accountData", [])
        
        for mint, pool_address in self.watched_lp_pools.items():
            if pool_address in str(tx):  # Simple check
                # This transaction involves the LP pool
                event_type = "RESERVE_CHANGE"
                
                # Check for LP removal signals
                instructions = tx.get("instructions", [])
                for ix in instructions:
                    data = str(ix.get("data", ""))
                    # Simplified: look for common LP removal patterns
                    if "withdraw" in data.lower() or "remove" in data.lower():
                        event_type = "LP_REMOVED"
                        break
                
                event = LPEvent(
                    mint=mint,
                    pool_address=pool_address,
                    event_type=event_type,
                    change_pct=0.0,  # Would need reserve comparison
                    timestamp=tx.get("timestamp", time.time()),
                    signature=tx.get("signature", "")
                )
                
                if event_type == "LP_REMOVED":
                    logger.error(f"🚨 LP REMOVAL detected for {mint[:12]}...!")
                
                return event
        
        return None
    
    async def get_dev_event(self, timeout: float = 1.0) -> Optional[DevWalletEvent]:
        """Get a dev event from the queue (non-blocking with timeout)"""
        try:
            return await asyncio.wait_for(self.dev_event_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
    
    async def get_lp_event(self, timeout: float = 1.0) -> Optional[LPEvent]:
        """Get an LP event from the queue (non-blocking with timeout)"""
        try:
            return await asyncio.wait_for(self.lp_event_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
# Standalone server for testing
async def main():
    logging.basicConfig(level=logging.INFO)
    
    server = HeliusWebhookServer(port=8765, webhook_path="/webhook")
    await server.start()
    
    print("\n" + "=" * 60)
    print("🔔 Helius Webhook Server Running!")
    print("=" * 60)
    print(f"Endpoint: http://YOUR_VPS_IP:8765/webhook")
    print(f"Health:   http://YOUR_VPS_IP:8765/health")
    print("\nConfigure this URL in Helius Dashboard:")
    print("1. Go to dashboard.helius.dev")
    print("2. Create new webhook")
    print("3. Set URL to your VPS endpoint")
    print("4. Select 'Enhanced' transaction type")
    print("5. Add address: 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P (pump.fun)")
    print("=" * 60 + "\n")
    
    # Keep running and print new tokens
    while True:
        token = await server.get_new_token(timeout=5.0)
        if token:
            print(f"🆕 [{token.symbol}] {token.name} - {token.mint}")
        

if __name__ == "__main__":
    asyncio.run(main())
