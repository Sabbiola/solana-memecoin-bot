"""Helius Wallet Webhook - Real-time transaction monitoring for leader wallets.

Receives transaction events from Helius webhook and parses them to detect
buy/sell actions from tracked leader wallets.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from solana_bot.config import Settings
from solana_bot.core.wallet_tracker import WalletTracker


# Known DEX program IDs for transaction parsing
PROGRAM_IDS = {
    # Pump.fun
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pumpfun",
    "PSwapMdSai8tjrEXcxFeQth87xC4rRsa4VA5mhGhXkP": "pumpswap",
    # Jupiter
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "jupiter",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "jupiter_v4",
    # Raydium
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "raydium",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "raydium_clmm",
}

# SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"


class HeliusWalletWebhook:
    """Webhook server for receiving leader wallet transactions from Helius."""
    
    def __init__(self, settings: Settings, wallet_tracker: WalletTracker) -> None:
        self.settings = settings
        self.wallet_tracker = wallet_tracker
        self.logger = logging.getLogger("solana_bot.helius_wallet_webhook")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        
        # Self-wallet tracking for accurate entry price
        self._self_wallet: str = ""  # Our own wallet address
        self._on_self_trade_callback = None  # Callback when our trade is detected
    
    def set_self_wallet(self, wallet_address: str, callback) -> None:
        """Set our wallet address to track our own transactions for accurate entry."""
        self._self_wallet = wallet_address
        self._on_self_trade_callback = callback
        self.logger.info("Self-wallet tracking enabled for %s", wallet_address[:16])
    
    async def start(self) -> None:
        """Start the webhook server."""
        if self._server:
            return
        self._loop = asyncio.get_running_loop()
        # Use same host/port as main webhook, different path
        wallet_port = getattr(
            self.settings,
            "HELIUS_WALLET_WEBHOOK_PORT",
            self.settings.HELIUS_WEBHOOK_PORT,
        )
        if self.settings.USE_HELIUS_WEBHOOK and wallet_port == self.settings.HELIUS_WEBHOOK_PORT:
            wallet_port = self.settings.HELIUS_WEBHOOK_PORT + 1
            self.logger.warning(
                "Wallet webhook port conflicts with Helius webhook; using %s",
                wallet_port,
            )
        address = (self.settings.HELIUS_WEBHOOK_HOST, wallet_port)
        handler = self._make_handler()
        self._server = ThreadingHTTPServer(address, handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.logger.info(
            "Helius wallet webhook listening on %s:%s%s",
            address[0], address[1], self.settings.HELIUS_WALLET_WEBHOOK_PATH
        )
    
    async def stop(self) -> None:
        """Stop the webhook server."""
        if not self._server:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self.logger.info("Helius wallet webhook stopped")
    
    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        settings = self.settings
        logger = self.logger
        wallet_tracker = self.wallet_tracker
        
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                # Check path
                if self.path != settings.HELIUS_WALLET_WEBHOOK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                
                # Verify signature if secret is configured
                if settings.HELIUS_WEBHOOK_SECRET:
                    signature = self.headers.get("X-Helius-Signature", "")
                    expected = hmac.new(
                        settings.HELIUS_WEBHOOK_SECRET.encode("utf-8"),
                        body,
                        hashlib.sha256,
                    ).hexdigest()
                    if not hmac.compare_digest(signature, expected):
                        self.send_response(401)
                        self.end_headers()
                        return
                
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    self.send_response(400)
                    self.end_headers()
                    return
                
                # Process transactions
                signals = _process_wallet_transactions(payload, wallet_tracker, logger, settings)
                logger.debug("Processed %d signals from webhook", len(signals))
                
                self.send_response(200)
                self.end_headers()
            
            def log_message(self, fmt: str, *args: Any) -> None:
                return  # Suppress default logging
        
        return Handler


def _process_wallet_transactions(
    payload: Any,
    wallet_tracker: WalletTracker,
    logger: logging.Logger,
    settings: Settings,
) -> list[Any]:
    """Parse Helius webhook payload and extract trade signals."""
    signals = []
    
    # Handle both single transaction and array
    items: list[dict] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        # Could be wrapped in events/data/transactions
        items = (
            payload.get("events") or
            payload.get("data") or
            payload.get("transactions") or
            [payload]
        )
    
    for tx in items:
        if not isinstance(tx, dict):
            continue
        
        try:
            signal = _parse_transaction(tx, wallet_tracker, logger, settings)
            if signal:
                signals.append(signal)
        except Exception as e:
            logger.error("Error parsing transaction: %s", e)
    
    return signals


def _parse_transaction(
    tx: dict,
    wallet_tracker: WalletTracker,
    logger: logging.Logger,
    settings: Settings,
) -> Any:
    """Parse a single transaction to detect buy/sell."""
    # Get transaction signature
    signature = tx.get("signature", "")
    
    # Check if this is a swap/trade transaction
    tx_type = tx.get("type", "").upper()
    if tx_type not in ("SWAP", "TRANSFER", "UNKNOWN", ""):
        return None
    
    # Get fee payer (usually the wallet that initiated the tx)
    fee_payer = tx.get("feePayer", "")
    if not fee_payer:
        return None
    
    # Check if fee payer is a tracked leader
    if not wallet_tracker.is_leader(fee_payer):
        # Also check account keys for the leader
        account_keys = tx.get("accountData", []) or tx.get("accountKeys", [])
        leader_found = None
        for acc in account_keys:
            addr = acc.get("account") if isinstance(acc, dict) else acc
            if wallet_tracker.is_leader(addr):
                leader_found = addr
                break
        if not leader_found:
            return None
        fee_payer = leader_found
    
    # Parse token transfers to detect buy/sell
    token_transfers = tx.get("tokenTransfers", [])
    native_transfers = tx.get("nativeTransfers", [])
    
    action = None
    token_mint = None
    token_symbol = None
    amount_sol = 0.0
    
    # Analyze token transfers
    sol_out = 0.0  # SOL leaving the wallet
    sol_in = 0.0   # SOL entering the wallet
    token_out: dict[str, float] = {}  # tokens leaving
    token_in: dict[str, float] = {}   # tokens entering
    
    for transfer in native_transfers:
        from_acc = transfer.get("fromUserAccount", "")
        to_acc = transfer.get("toUserAccount", "")
        amount = float(transfer.get("amount", 0)) / 1e9  # Convert lamports to SOL
        
        if from_acc == fee_payer:
            sol_out += amount
        if to_acc == fee_payer:
            sol_in += amount
    
    for transfer in token_transfers:
        mint = transfer.get("mint", "")
        from_acc = transfer.get("fromUserAccount", "")
        to_acc = transfer.get("toUserAccount", "")
        amount = float(transfer.get("tokenAmount", 0))
        symbol = transfer.get("tokenSymbol", "") or transfer.get("symbol", "")
        
        if mint == SOL_MINT:
            # Wrapped SOL
            if from_acc == fee_payer:
                sol_out += amount
            if to_acc == fee_payer:
                sol_in += amount
        else:
            # Other token
            if from_acc == fee_payer:
                token_out[mint] = token_out.get(mint, 0) + amount
                if not token_symbol:
                    token_symbol = symbol
            if to_acc == fee_payer:
                token_in[mint] = token_in.get(mint, 0) + amount
                if not token_symbol:
                    token_symbol = symbol
    
    # Determine action based on flow
    # BUY: SOL/USDC out, token in
    # SELL: token out, SOL/USDC in
    
    # Stablecoin mints
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
    USD1_MINT = "USD1ttGY1N17NEEHvfE8PHr3XM2rv3e12qcNpc4pump"  # USD1 stablecoin
    STABLE_MINTS = (USDC_MINT, USDT_MINT, USD1_MINT)


    stable_out_usd = sum(token_out.get(m, 0.0) for m in STABLE_MINTS)
    stable_in_usd = sum(token_in.get(m, 0.0) for m in STABLE_MINTS)

    # Check for BUY (SOL or Stable out -> Token in)
    is_buy_sol = sol_out > 0.001 and token_in
    is_buy_stable = any(m in token_out for m in STABLE_MINTS) and token_in
    
    if is_buy_sol or is_buy_stable:
        # Filter out stablecoins from "token_in" (we are buying a target token, not swapping to stable)
        target_mints = [m for m in token_in.keys() if m not in STABLE_MINTS and m != SOL_MINT]
        
        if target_mints:
            action = "BUY"
            token_mint = target_mints[0]
            amount_sol = sol_out if is_buy_sol else 0.0
            
            logger.info(
                "Detected BUY: %s spent %.4f SOL/Stable for %s",
                fee_payer[:8], sol_out, token_mint[:16]
            )

    # Check for SELL (Token out -> SOL or Stable in)
    elif token_out:
        # Check if receiving SOL or Stable
        is_sell_sol = sol_in > 0.001
        is_sell_stable = any(m in token_in for m in STABLE_MINTS)
        
        # Identify the target token being sold (exclude stables from out)
        target_mints = [m for m in token_out.keys() if m not in STABLE_MINTS and m != SOL_MINT]
        
        if (is_sell_sol or is_sell_stable) and target_mints:
            action = "SELL"
            token_mint = target_mints[0]
            amount_sol = sol_in if is_sell_sol else 0.0
            
            logger.info(
                "Detected SELL: %s sold %s for %.4f SOL/Stable",
                fee_payer[:8], token_mint[:16], sol_in
            )
        elif target_mints:
             # Fallback: If token leaves and nothing obvious enters, it might be a complex route swap
             # Log it as potential sell/transfer
             if settings.COPY_SELL_ON_TRANSFER:
                 action = "SELL"
                 token_mint = target_mints[0]
                 amount_sol = sol_in if is_sell_sol else 0.0
                 logger.warning(
                     "TRANSFER treated as SELL for %s: %s out without SOL/USDC in",
                     fee_payer[:8], token_mint[:16]
                 )
             else:
                 logger.warning(
                     "Possible SELL/TRANSFER ignored for %s: %s out, but no SOL/USDC in (sol_in=%.4f)",
                     fee_payer[:8], target_mints[0], sol_in
                 )
    
    if not action or not token_mint:
        return None
    
    # Calculate price from transaction for immediate position opening
    # Price = SOL spent / tokens received (for BUY)
    # or Price = SOL received / tokens sold (for SELL)
    calculated_price = 0.0
    price_in_usd = False
    token_amount = token_in.get(token_mint, 0.0) if action == "BUY" else token_out.get(token_mint, 0.0)
    
    if token_amount > 0:
        if action == "BUY":
            # For BUY: prefer stable price when available, else use SOL
            if stable_out_usd > 0:
                calculated_price = stable_out_usd / token_amount
                price_in_usd = True
            else:
                calculated_price = sol_out / token_amount if sol_out > 0 else 0.0
        else:  # SELL
            # For SELL: prefer stable price when available, else use SOL
            if stable_in_usd > 0:
                calculated_price = stable_in_usd / token_amount
                price_in_usd = True
            else:
                calculated_price = sol_in / token_amount if sol_in > 0 else 0.0
    
    amount_usd = 0.0
    if action == "BUY":
        amount_usd = stable_out_usd
    elif action == "SELL":
        amount_usd = stable_in_usd

    # Create signal through wallet tracker
    return wallet_tracker.process_transaction(
        wallet_address=fee_payer,
        action=action,
        token_mint=token_mint,
        token_symbol=token_symbol or "UNKNOWN",
        amount_sol=amount_sol,
        amount_usd=amount_usd,
        signature=signature,
        price=calculated_price,  # Pass calculated price
        price_in_usd=price_in_usd,
    )
