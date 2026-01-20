"""Live Trading Broker - Executes real trades on Solana via Jupiter + Jito.

This module handles:
- Jupiter swap quote and transaction building
- Jito bundle submission for MEV protection
- Transaction signing and confirmation
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from solana_bot.config import Settings

from solana_bot.core.models import TradeFill

# Solana constants
SOL_MINT = "So11111111111111111111111111111111111111112"
WSOL_MINT = SOL_MINT
LAMPORTS_PER_SOL = 1_000_000_000


class LiveBroker:
    """Executes real trades on Solana using Jupiter API and Jito MEV protection."""

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.live_broker")
        self._client: httpx.AsyncClient | None = None
        self._wallet_keypair = None
        self._wallet_pubkey: str = ""
        self._decimals_cache: dict[str, int] = {}
        self._sol_price_usd: float = 0.0
        self._sol_price_last_update: float = 0.0
        self._sol_price_ttl_sec = 60.0
        
        # Use API URLs from settings
        # Base: https://api.jup.ag/swap/v1
        # Quote: https://api.jup.ag/swap/v1/quote
        # Swap: https://api.jup.ag/swap/v1/swap
        self.JUPITER_QUOTE_API = settings.JUPITER_QUOTE_API_BASE.rstrip("/")
        self.JUPITER_SWAP_API = settings.JUPITER_QUOTE_API_BASE.rstrip("/")
        self.JITO_BUNDLE_API = settings.JITO_BLOCK_ENGINE_URL + "/api/v1/bundles"
        
        # Initialize wallet from private key
        self._init_wallet()

    async def _get_token_decimals(self, mint: str) -> int:
        cached = self._decimals_cache.get(mint)
        if cached is not None:
            return cached
        if not self.settings.RPC_URL:
            return 6
        client = await self._ensure_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [mint],
        }
        try:
            response = await client.post(self.settings.RPC_URL, json=payload)
            response.raise_for_status()
            result = response.json().get("result") or {}
            decimals = int((result.get("value") or {}).get("decimals", 6))
        except Exception as e:
            self.logger.debug("Failed to fetch decimals for %s: %s", mint[:8], e)
            decimals = 6
        self._decimals_cache[mint] = decimals
        return decimals

    def _init_wallet(self) -> None:
        """Initialize wallet from private key in settings."""
        private_key = self.settings.SOLANA_PRIVATE_KEY
        if not private_key:
            self.logger.warning("No SOLANA_PRIVATE_KEY configured - live trading disabled")
            return

        try:
            # Try to import solders for wallet handling
            from solders.keypair import Keypair
            
            # Handle different key formats
            if private_key.startswith("["):
                # JSON array format
                import json
                key_bytes = bytes(json.loads(private_key))
                self._wallet_keypair = Keypair.from_bytes(key_bytes)
            else:
                # Base58 format
                import base58
                key_bytes = base58.b58decode(private_key)
                self._wallet_keypair = Keypair.from_bytes(key_bytes)
            
            self._wallet_pubkey = str(self._wallet_keypair.pubkey())
            self.logger.info("Wallet initialized: %s", self._wallet_pubkey[:12] + "...")
            
        except ImportError as e:
            self.logger.error("Missing dependency for live trading: %s", e)
            self.logger.error("Install with: pip install solders base58")
        except Exception as e:
            self.logger.error("Failed to initialize wallet: %s", e)

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client is initialized."""
        if self._client is None:
            headers = {}
            if self.settings.JUPITER_API_KEY:
                headers["x-api-key"] = self.settings.JUPITER_API_KEY
                self.logger.info("Using Jupiter API Key for authentication")
            
            self._client = httpx.AsyncClient(headers=headers, timeout=30.0)
        return self._client

    async def _get_sol_price_usd(self) -> float:
        """Fetch SOL price in USD (cached)."""
        now = time.time()
        if self._sol_price_usd > 0 and now - self._sol_price_last_update < self._sol_price_ttl_sec:
            return self._sol_price_usd

        client = await self._ensure_client()
        base = self.settings.JUPITER_PRICE_API_BASE.rstrip("/")
        params = {"ids": SOL_MINT}
        try:
            response = await client.get(f"{base}/price", params=params)
            response.raise_for_status()
            data = response.json().get("data") or {}
            price = float((data.get(SOL_MINT) or {}).get("price", 0) or 0)
            if price > 0:
                self._sol_price_usd = price
                self._sol_price_last_update = now
                return price
        except Exception as e:
            self.logger.debug("Failed to fetch SOL price: %s", e)

        if self._sol_price_usd > 0:
            return self._sol_price_usd
        return 130.0

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def execute_trade(
        self, 
        side: str, 
        mint: str, 
        size_sol: float, 
        price: float, 
        reason: str,
        token_amount_raw: int = 0
    ) -> TradeFill:
        """Execute a live trade on Solana.
        
        Args:
            side: "BUY" or "SELL"
            mint: Token mint address
            size_sol: Size in SOL (for BUY) or token value in SOL (for SELL)
            price: Expected price (for logging)
            reason: Reason for trade
            token_amount_raw: Optional raw token amount for instant sells
            
        Returns:
            TradeFill with execution details
        """
        if not self._wallet_keypair:
            self.logger.error("Cannot execute live trade: wallet not initialized")
            return TradeFill(
                success=False,
                side=side,
                mint=mint,
                size_sol=0.0,
                price=0.0,
                reason=f"{reason}_WALLET_ERROR",
            )

        try:
            if side == "BUY":
                return await self._execute_buy(mint, size_sol, price, reason)
            else:
                return await self._execute_sell(mint, size_sol, price, reason, token_amount_raw)
        except Exception as e:
            self.logger.error("Trade execution failed: %s", e)
            return TradeFill(
                success=False,
                side=side,
                mint=mint,
                size_sol=0.0,
                price=0.0,
                reason=f"{reason}_EXECUTION_ERROR",
            )

    async def _execute_buy(
        self, mint: str, size_sol: float, expected_price: float, reason: str
    ) -> TradeFill:
        """Execute a buy order (SOL -> Token)."""
        client = await self._ensure_client()
        
        # Get slippage and fees from config
        slippage_bps = self.settings.LIVE_BUY_SLIPPAGE_BPS
        priority_fee = self.settings.LIVE_BUY_PRIORITY_FEE_SOL
        jito_tip = self.settings.LIVE_BUY_JITO_TIP_SOL
        
        amount_lamports = int(size_sol * LAMPORTS_PER_SOL)
        
        self.logger.info(
            "🔵 LIVE BUY: %s @ $%.8f | Size: %.4f SOL | Slippage: %d bps | PriorityFee: %.6f | JitoTip: %.6f",
            mint[:12], expected_price, size_sol, slippage_bps, priority_fee, jito_tip
        )

        # 1. Get quote from Jupiter
        quote = await self._get_jupiter_quote(
            input_mint=SOL_MINT,
            output_mint=mint,
            amount=amount_lamports,
            slippage_bps=slippage_bps,
        )
        
        if not quote:
            return TradeFill(
                success=False, side="BUY", mint=mint, size_sol=0.0, 
                price=0.0, reason=f"{reason}_QUOTE_FAILED"
            )

        # 2. Build swap transaction
        swap_tx = await self._build_swap_transaction(quote, priority_fee)
        if not swap_tx:
            return TradeFill(
                success=False, side="BUY", mint=mint, size_sol=0.0,
                price=0.0, reason=f"{reason}_TX_BUILD_FAILED"
            )

        # 3. Sign transaction
        signed_tx = self._sign_transaction(swap_tx)
        if not signed_tx:
            return TradeFill(
                success=False, side="BUY", mint=mint, size_sol=0.0,
                price=0.0, reason=f"{reason}_SIGN_FAILED"
            )

        signed_sig = self._extract_signature(signed_tx)

        # 4. Submit via Jito or direct RPC
        if self.settings.JITO_ENABLED:
            submit_id = await self._submit_via_jito(signed_tx, jito_tip)
        else:
            submit_id = await self._submit_via_rpc(signed_tx)

        tx_sig = signed_sig or submit_id
        if not tx_sig:
            return TradeFill(
                success=False, side="BUY", mint=mint, size_sol=0.0,
                price=0.0, reason=f"{reason}_SUBMIT_FAILED"
            )

        if signed_sig and not await self._confirm_signature(signed_sig):
            self.logger.warning("LIVE BUY unconfirmed: %s", signed_sig[:16])


        # 5. Calculate actual fill price from quote
        out_amount = int(quote.get("outAmount", 0))
        actual_price_sol = 0.0  # Price in SOL per token
        
        if out_amount > 0:
            # Price in SOL = SOL spent / tokens received
            decimals = await self._get_token_decimals(mint)
            denom = out_amount / (10 ** decimals) if decimals >= 0 else 0
            if denom > 0:
                actual_price_sol = size_sol / denom
            
            # Convert to USD using cached SOL price
            sol_usd = await self._get_sol_price_usd()
            actual_price = actual_price_sol * sol_usd  # USD per token
            
            self.logger.debug(
                "Fill price: %.9f SOL/token * $%.0f = $%.9f USD/token",
                actual_price_sol, sol_usd, actual_price
            )
        else:
            actual_price = expected_price
        
        # Calculate total cost (for accurate tracking)
        total_cost_sol = size_sol + priority_fee
        if self.settings.JITO_ENABLED:
            total_cost_sol += jito_tip

        self.logger.info(
            "✅ LIVE BUY SUCCESS: %s | TX: %s | Price: $%.8f",
            mint[:12], tx_sig[:16] + "...", actual_price
        )
        self.logger.info(
            "💸 COST BREAKDOWN: Size=%.4f SOL + PriorityFee=%.6f SOL + JitoTip=%.6f SOL = TOTAL: %.4f SOL",
            size_sol, priority_fee, jito_tip if self.settings.JITO_ENABLED else 0.0, total_cost_sol
        )

        return TradeFill(
            success=True,
            side="BUY",
            mint=mint,
            size_sol=size_sol,  # Keep as requested amount (bot logic expects this)
            price=actual_price,
            reason=reason,
            signature=tx_sig,
            token_amount_raw=out_amount,  # Save for instant sell
        )

    async def _execute_sell(
        self, mint: str, size_sol: float, expected_price: float, reason: str, token_amount_raw: int = 0
    ) -> TradeFill:
        """Execute a sell order (Token -> SOL)."""
        client = await self._ensure_client()
        
        slippage_bps = self.settings.LIVE_SELL_SLIPPAGE_BPS
        priority_fee = self.settings.LIVE_SELL_PRIORITY_FEE_SOL
        jito_tip = self.settings.LIVE_SELL_JITO_TIP_SOL
        
        # For sell, we need to convert SOL value to token amount
        # If size_sol is negative (e.g. -1), it means "SELL ALL"
        amount_raw = 0
        is_sell_all = size_sol < 0

        if is_sell_all:
             # INSTANT SELL: Use cached token amount if available
             if token_amount_raw > 0:
                 amount_raw = token_amount_raw
                 self.logger.info("⚡ INSTANT SELL: Using cached token amount %d", amount_raw)
             else:
                 # Fallback: Quick balance check
                 for attempt in range(2):
                     amount_raw = await self.get_token_balance(mint)
                     if amount_raw > 0:
                         break
                     if attempt < 1:
                         await asyncio.sleep(0.5)
                 
                 if amount_raw <= 0:
                     self.logger.error("LIVE SELL FAILED: No balance for %s", mint[:12])
                     return TradeFill(
                         success=False, side="SELL", mint=mint, size_sol=0.0,
                         price=0.0, reason=f"{reason}_NO_BALANCE"
                     )
             self.logger.info(
                 "🔴 LIVE SELL ALL: %s | AmountRaw: %d | ExpectedPrice: $%.8f",
                 mint[:12], amount_raw, expected_price
             )
        else:
            # Estimate based on expected price (less accurate)
            if expected_price > 0:
                decimals = await self._get_token_decimals(mint)
                token_amount = size_sol / expected_price
                amount_raw = int(token_amount * (10 ** decimals))
            else:
                self.logger.error("Cannot sell: expected_price is 0")
                return TradeFill(
                    success=False, side="SELL", mint=mint, size_sol=0.0,
                    price=0.0, reason=f"{reason}_NO_PRICE"
                )

            self.logger.info(
                "🔴 LIVE SELL: %s @ $%.8f | Value: %.4f SOL | Slippage: %d bps",
                mint[:12], expected_price, size_sol, slippage_bps
            )

        # 1. Get quote
        quote = await self._get_jupiter_quote(
            input_mint=mint,
            output_mint=SOL_MINT,
            amount=amount_raw,
            slippage_bps=slippage_bps,
        )

        if not quote:
            return TradeFill(
                success=False, side="SELL", mint=mint, size_sol=0.0,
                price=0.0, reason=f"{reason}_QUOTE_FAILED"
            )

        # 2. Build swap transaction
        swap_tx = await self._build_swap_transaction(quote, priority_fee)
        if not swap_tx:
            return TradeFill(
                success=False, side="SELL", mint=mint, size_sol=0.0,
                price=0.0, reason=f"{reason}_TX_BUILD_FAILED"
            )

        # 3. Sign transaction
        signed_tx = self._sign_transaction(swap_tx)
        if not signed_tx:
            return TradeFill(
                success=False, side="SELL", mint=mint, size_sol=0.0,
                price=0.0, reason=f"{reason}_SIGN_FAILED"
            )

        signed_sig = self._extract_signature(signed_tx)

        # 4. Submit via Jito or direct RPC
        if self.settings.JITO_ENABLED:
            submit_id = await self._submit_via_jito(signed_tx, jito_tip)
        else:
            submit_id = await self._submit_via_rpc(signed_tx)

        tx_sig = signed_sig or submit_id
        if not tx_sig:
            return TradeFill(
                success=False, side="SELL", mint=mint, size_sol=0.0,
                price=0.0, reason=f"{reason}_SUBMIT_FAILED"
            )

        if signed_sig and not await self._confirm_signature(signed_sig):
            self.logger.warning("LIVE SELL unconfirmed: %s", signed_sig[:16])

        # Calculate actual SOL received
        out_amount = int(quote.get("outAmount", 0))
        actual_sol = out_amount / LAMPORTS_PER_SOL
        
        # Calculate net proceeds (after fees)
        net_proceeds = actual_sol - priority_fee
        if self.settings.JITO_ENABLED:
            net_proceeds -= jito_tip

        self.logger.info(
            "✅ LIVE SELL SUCCESS: %s | TX: %s | Received: %.4f SOL",
            mint[:12], tx_sig[:16] + "...", actual_sol
        )
        self.logger.info(
            "💰 PROCEEDS: Gross=%.4f SOL - PriorityFee=%.6f SOL - JitoTip=%.6f SOL = NET: %.4f SOL",
            actual_sol, priority_fee, jito_tip if self.settings.JITO_ENABLED else 0.0, net_proceeds
        )

        return TradeFill(
            success=True,
            side="SELL",
            mint=mint,
            size_sol=actual_sol,  # Gross amount (bot adjusts balance separately)
            price=expected_price,
            reason=reason,
            signature=tx_sig,
        )

    async def _get_jupiter_quote(
        self, 
        input_mint: str, 
        output_mint: str, 
        amount: int, 
        slippage_bps: int
    ) -> dict | None:
        """Get swap quote from Jupiter."""
        client = await self._ensure_client()
        
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage_bps,
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }

        try:
            response = await client.get(
                f"{self.JUPITER_QUOTE_API}/quote",
                params=params,
            )
            response.raise_for_status()
            quote = response.json()
            
            self.logger.debug(
                "Jupiter quote: in=%s out=%s route=%s",
                quote.get("inAmount"),
                quote.get("outAmount"),
                len(quote.get("routePlan", [])),
            )
            return quote
            
        except Exception as e:
            self.logger.error("Jupiter quote failed: %s", e)
            return None

    async def _build_swap_transaction(
        self, quote: dict, priority_fee_sol: float
    ) -> bytes | None:
        """Build swap transaction from Jupiter quote."""
        client = await self._ensure_client()
        
        priority_fee_lamports = int(priority_fee_sol * LAMPORTS_PER_SOL)

        payload = {
            "quoteResponse": quote,
            "userPublicKey": self._wallet_pubkey,
            "wrapAndUnwrapSol": True,
            "computeUnitPriceMicroLamports": priority_fee_lamports * 1000,  # Convert to microlamports
            "dynamicComputeUnitLimit": True,
        }

        try:
            response = await client.post(
                f"{self.JUPITER_SWAP_API}/swap",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            
            swap_tx_b64 = data.get("swapTransaction")
            if swap_tx_b64:
                return base64.b64decode(swap_tx_b64)
            return None
            
        except Exception as e:
            self.logger.error("Jupiter swap build failed: %s", e)
            return None

    def _sign_transaction(self, tx_bytes: bytes) -> bytes | None:
        """Sign a transaction with wallet keypair."""
        if not self._wallet_keypair:
            return None
        try:
            from solders.transaction import VersionedTransaction

            # Deserialize
            tx = VersionedTransaction.from_bytes(tx_bytes)

            # Recreate transaction with keypairs (it will sign automatically)
            # The constructor expects: VersionedTransaction(message, [signers])
            signed_tx = VersionedTransaction(tx.message, [self._wallet_keypair])

            return bytes(signed_tx)

        except Exception as e:
            self.logger.error("Transaction signing failed: %s", e)
            return None

    def _extract_signature(self, signed_tx: bytes) -> str | None:
        """Extract the transaction signature from signed bytes."""
        try:
            from solders.transaction import VersionedTransaction

            tx = VersionedTransaction.from_bytes(signed_tx)
            if tx.signatures:
                return str(tx.signatures[0])
        except Exception as e:
            self.logger.debug("Failed to extract signature: %s", e)
        return None

    async def _confirm_signature(self, signature: str, timeout_sec: float = 8.0) -> bool:
        """Best-effort confirmation for a transaction signature."""
        if not signature or not self.settings.RPC_URL:
            return False

        client = await self._ensure_client()
        deadline = time.time() + timeout_sec
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[signature], {"searchTransactionHistory": True}],
        }

        while time.time() < deadline:
            try:
                response = await client.post(self.settings.RPC_URL, json=payload)
                response.raise_for_status()
                result = response.json().get("result") or {}
                value = result.get("value") or []
                status = value[0] if value else None
                if status:
                    if status.get("err") is not None:
                        self.logger.warning("Transaction %s failed: %s", signature[:8], status.get("err"))
                        return False
                    confirmation_status = status.get("confirmationStatus") or ""
                    if status.get("confirmations") is None or confirmation_status in ("confirmed", "finalized"):
                        return True
            except Exception as e:
                self.logger.debug("Signature confirmation error: %s", e)
            await asyncio.sleep(0.4)

        return False

    async def _submit_via_jito(self, signed_tx: bytes, tip_sol: float) -> str | None:
        """Submit transaction via Jito for MEV protection."""
        client = await self._ensure_client()
        
        tx_b64 = base64.b64encode(signed_tx).decode()
        
        # Jito expects a bundle with tip
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [[tx_b64]],
        }

        try:
            response = await client.post(
                self.settings.JITO_BLOCK_ENGINE_URL + "/api/v1/bundles",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            
            bundle_id = result.get("result")
            if bundle_id:
                self.logger.info("Jito bundle submitted: %s", bundle_id)
                # Wait for confirmation (simplified - real impl would poll)
                await asyncio.sleep(2.0)
                return bundle_id
            return None
            
        except Exception as e:
            self.logger.error("Jito submission failed: %s", e)
            # Fallback to direct RPC
            self.logger.info("Falling back to direct RPC...")
            return await self._submit_via_rpc(signed_tx)

    async def _submit_via_rpc(self, signed_tx: bytes) -> str | None:
        """Submit transaction directly to RPC."""
        client = await self._ensure_client()
        
        tx_b64 = base64.b64encode(signed_tx).decode()
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "maxRetries": 3,
                }
            ],
        }

        try:
            response = await client.post(
                self.settings.RPC_URL,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            
            if "error" in result:
                self.logger.error("RPC error: %s", result["error"])
                return None
                
            tx_sig = result.get("result")
            if tx_sig:
                self.logger.info("Transaction submitted: %s", tx_sig)
                return tx_sig
            return None
            
        except Exception as e:
            self.logger.error("RPC submission failed: %s", e)
            return None

    async def get_balance(self) -> float | None:
        """Fetch current SOL balance from RPC."""
        if not self._wallet_keypair:
            return None

        client = await self._ensure_client()
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [self._wallet_pubkey]
        }

        try:
            response = await client.post(
                self.settings.RPC_URL,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            
            if "result" in result and "value" in result["result"]:
                lamports = result["result"]["value"]
                return lamports / 1_000_000_000
            
            self.logger.warning("Could not fetch balance: %s", result)
            return None
            
        except Exception as e:
            self.logger.error("Failed to fetch balance: %s", e)
            return None

    async def get_token_balance(self, mint: str) -> int:
        """Fetch current token balance (raw amount) from RPC."""
        if not self._wallet_keypair:
            return 0
        
        mint = mint.strip()
        client = await self._ensure_client()
        
        # We need to find the token account for this mint
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                self._wallet_pubkey,
                {"mint": mint},
                {"encoding": "jsonParsed"}
            ]
        }

        try:
            response = await client.post(
                self.settings.RPC_URL,
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            
            total_amount = 0
            decimals = 0
            
            if "result" in result and "value" in result["result"]:
                accounts = result["result"]["value"]
                for acc in accounts:
                    info = acc["account"]["data"]["parsed"]["info"]
                    amount = int(info["tokenAmount"]["amount"])
                    decimals = info["tokenAmount"]["decimals"]
                    ui_amount = info["tokenAmount"]["uiAmount"]
                    
                    if amount > 0:
                        total_amount += amount
                        self.logger.debug(
                            "Found account for %s: %d units (UI: %.4f, Dec: %d)",
                            mint[:8], amount, ui_amount or 0.0, decimals
                        )
            
            if total_amount > 0:
                 self.logger.info("Total balance for %s: %d (Decimals: %d)", mint, total_amount, decimals)

            return total_amount
            
        except Exception as e:
            self.logger.error("Failed to fetch token balance for %s: %s", mint, e)
            return 0
