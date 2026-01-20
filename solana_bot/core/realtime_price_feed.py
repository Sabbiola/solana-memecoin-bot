from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solana_bot.core.models import TokenInfo
    from solana_bot.core.pumpportal_client import PumpPortalClient
    from solana_bot.core.dexscreener_client import DexScreenerClient
    from solana_bot.core.birdeye_price_client import BirdeyePriceClient
    from solana_bot.config import Settings



class RealTimePriceFeed:
    """
    Orchestrates real-time price updates from multiple sources:
    - PumpPortal WebSocket for bonding curve tokens
    - Birdeye fast polling for migrated Raydium tokens
    - DexScreener fallback for reliability
    """
    
    def __init__(
        self,
        settings: Settings,
        pumpportal: PumpPortalClient | None,
        birdeye: BirdeyePriceClient,
        dex_client: DexScreenerClient,
    ) -> None:
        self.settings = settings
        self.pumpportal = pumpportal
        self.birdeye = birdeye
        self.dex_client = dex_client
        self.logger = logging.getLogger("solana_bot.realtime_feed")
        
        # Track subscribed tokens and their metadata
        self._subscriptions: dict[str, TokenInfo] = {}
        
        # Latest prices from all sources
        self._prices: dict[str, float] = {}
        self._price_timestamps: dict[str, float] = {}
        
        # Health monitoring
        self._running = False
        self._health_task: asyncio.Task | None = None

        if self.pumpportal:
            self.pumpportal.set_price_callback(self.update_price)
        
        # Also register callback with Birdeye for migrated tokens
        self.birdeye.set_price_callback(self.update_price)

    async def start(self) -> None:
        """Start the real-time feed system."""
        if not self.settings.REALTIME_PRICE_ENABLED:
            self.logger.info("Real-time price feed disabled in config")
            return
        
        self._running = True
        self._health_task = asyncio.create_task(self._health_check_loop())
        self.logger.info("Real-time price feed started")

    async def stop(self) -> None:
        """Stop the real-time feed system."""
        self._running = False
        
        # Unsubscribe all
        for mint in list(self._subscriptions.keys()):
            await self.unsubscribe(mint)
        
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        
        await self.birdeye.close()
        self.logger.info("Real-time price feed stopped")

    async def subscribe(self, token: TokenInfo) -> None:
        """Subscribe to real-time price updates for a token."""
        if not self.settings.REALTIME_PRICE_ENABLED:
            return
        
        mint = token.mint
        self._subscriptions[mint] = token
        
        # Determine the best source based on token phase
        phase = getattr(token, 'phase', None)
        is_bonding = phase and 'BONDING' in str(phase).upper()
        
        if is_bonding and self.pumpportal:
            # Use PumpPortal WebSocket for bonding curve tokens
            self.logger.info("Subscribed to PumpPortal WebSocket for %s (bonding)", token.symbol)
            await self.pumpportal.subscribe_trades(mint)
        else:
            # Use Birdeye fast polling for migrated tokens
            await self.birdeye.start_polling({mint})
            self.logger.info("Subscribed to Birdeye polling for %s (migrated)", token.symbol)

    async def unsubscribe(self, mint: str) -> None:
        """Unsubscribe from price updates for a token."""
        if mint in self._subscriptions:
            token = self._subscriptions.pop(mint)
            
            # Stop Birdeye polling if applicable
            await self.birdeye.stop_polling({mint})
            
            # Clean up cached data
            self._prices.pop(mint, None)
            self._price_timestamps.pop(mint, None)
            
            self.logger.debug("Unsubscribed from %s", token.symbol)

    def get_latest_price(self, mint: str) -> float | None:
        """
        Get the latest price for a token.
        Returns cached price even if stale as last resort (with warning).
        """
        # Check if we have a recent price
        price = self._prices.get(mint)
        timestamp = self._price_timestamps.get(mint)
        
        if price and timestamp:
            age = time.time() - timestamp
            if age < self.settings.REALTIME_STALE_THRESHOLD_SEC:
                return price
            else:
                # SAFETY NET: Use stale price as last resort instead of None
                # This prevents the bot from freezing when all APIs fail
                self.logger.debug(
                    "Using stale price for %s (%.1fs old): $%.6f",
                    mint[:8], age, price
                )
                return price
        
        return None

    def update_price(self, mint: str, price: float) -> None:
        """Update the cached price for a token (called by PumpPortal or other sources)."""
        self._prices[mint] = price
        self._price_timestamps[mint] = time.time()
        self.logger.debug("Updated price for %s: $%.6f", mint[:8], price)

    def set_initial_price(self, mint: str, price: float) -> None:
        """
        Manually set the initial price for a token (e.g. from trade execution).
        Useful to avoid '0 PnL' or stale waiting period immediately after buy.
        """
        self._prices[mint] = price
        self._price_timestamps[mint] = time.time()
        self.logger.info("Set initial price for %s: $%.6f (from execution)", mint[:8], price)

    async def _health_check_loop(self) -> None:
        """Monitor price staleness and trigger fallback refreshes if needed."""
        _fallback_attempted: dict[str, float] = {}  # Track last fallback attempt time
        
        while self._running:
            await asyncio.sleep(self.settings.REALTIME_STALE_THRESHOLD_SEC)
            
            now = time.time()
            for mint, token in list(self._subscriptions.items()):
                last_update = self._price_timestamps.get(mint, 0)
                age = now - last_update
                
                # If price is stale, trigger a DexScreener refresh (but not too frequently)
                if age > self.settings.REALTIME_STALE_THRESHOLD_SEC:
                    last_fallback = _fallback_attempted.get(mint, 0)
                    # Only attempt fallback every 30s to avoid spam
                    if now - last_fallback >= 5.0:
                        self.logger.warning(
                            "Price for %s stale (%.1fs), triggering DexScreener fallback",
                            token.symbol, age
                        )
                        await self._fallback_refresh(token)
                        _fallback_attempted[mint] = now

    async def _fallback_refresh(self, token: TokenInfo) -> None:
        """Fallback to DexScreener when WebSocket/Jupiter fails."""
        try:
            pairs = await self.dex_client.get_token_pairs(token.mint)
            if pairs:
                best_pair = pairs[0]
                price = float(best_pair.get("priceUsd", 0) or 0)
                if price > 0:
                    self.update_price(token.mint, price)
                    self.logger.info("Fallback refresh successful for %s: $%.6f", token.symbol, price)

                    # Check if we need to switch from PumpPortal to Birdeye (Migration detection)
                    phase = getattr(token, 'phase', '')
                    is_bonding = phase and 'BONDING' in str(phase).upper()
                    
                    if is_bonding and self.pumpportal:
                        # If we are here, it means PumpPortal is silent (stale) but DexScreener has data.
                        # This strongly suggests the token has migrated.
                        self.logger.info("Token %s seems to have migrated (DexScreener active), switching to Birdeye", token.symbol)
                        
                        # Attempt to unsubscribe from PumpPortal (best effort)
                        try:
                            # Note: PumpPortal client doesn't have explicit unsubscribe per token in this version easily accessible
                            # without checking the implementation, but we can just start Birdeye polling.
                            # The PumpPortal client receives all trades for subscribed tokens. 
                            pass 
                        except Exception:
                            pass
                        
                        # Switch to Birdeye
                        await self.birdeye.start_polling({token.mint})
        except Exception as e:
            self.logger.error("Fallback refresh failed for %s: %s", token.symbol, e)
