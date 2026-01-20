from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

import httpx

if TYPE_CHECKING:
    from solana_bot.config import Settings


class BirdeyePriceClient:
    """Fast polling client for Birdeye Price API (real-time WebSocket alternative)."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.birdeye_price")
        self._polling_task: asyncio.Task | None = None
        self._active_mints: set[str] = set()
        self._prices: dict[str, float] = {}
        self._running = False
        self._consecutive_failures = 0
        self._max_failures_before_warn = 3
        self._on_price_update: Callable[[str, float], None] | None = None
        
        # Persistent client with timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=10),
            headers={
                "X-API-KEY": settings.BIRDEYE_API_KEY,
                "x-chain": "solana",
                "Accept": "application/json",
            }
        )

    async def start_polling(self, mints: set[str]) -> None:
        """Start polling for the given mints."""
        self._active_mints.update(mints)
        if not self._running and self._active_mints:
            self._running = True
            self._polling_task = asyncio.create_task(self._poll_loop())
            self.logger.info("Birdeye price polling started for %d tokens", len(self._active_mints))

    async def stop_polling(self, mints: set[str] | None = None) -> None:
        """Stop polling for specific mints or all."""
        if mints:
            self._active_mints -= mints
        else:
            self._active_mints.clear()
        
        if not self._active_mints and self._running:
            self._running = False
            if self._polling_task:
                self._polling_task.cancel()
                try:
                    await self._polling_task
                except asyncio.CancelledError:
                    pass
                self._polling_task = None
            self.logger.info("Birdeye price polling stopped")

    def get_price(self, mint: str) -> float | None:
        """Get the latest cached price for a mint."""
        return self._prices.get(mint)

    def set_price_callback(self, callback: Callable[[str, float], None]) -> None:
        """Set callback for price updates (called by RealTimePriceFeed)."""
        self._on_price_update = callback

    async def _poll_loop(self) -> None:
        """Main polling loop with error handling."""
        while self._running:
            if not self._active_mints:
                await asyncio.sleep(1)
                continue
            
            try:
                # Birdeye free tier: Use /defi/price endpoint (one token at a time)
                # Premium tier uses /defi/multi_price for batch
                for mint in list(self._active_mints):
                    clean_mint = mint.strip()
                    url = f"{self.settings.BIRDEYE_API_BASE}/price?address={clean_mint}"
                
                    response = await self._client.get(url)
                    response.raise_for_status()
                    data = response.json()
                    
                    # Response format: {"data": {"value": 0.123}, "success": true}
                    if data.get("success") and "data" in data:
                        price_data = data["data"]
                        if isinstance(price_data, dict) and "value" in price_data:
                            try:
                                price = float(price_data["value"])
                                if price > 0:
                                    self._prices[mint] = price
                                    # Notify RealTimePriceFeed
                                    if self._on_price_update:
                                        self._on_price_update(mint, price)
                            except (ValueError, TypeError):
                                continue
                
                self.logger.debug("Updated prices for %d mints from Birdeye", len(self._prices))
                
                # Reset failure counter on success
                if self._consecutive_failures > 0:
                    self.logger.info("Birdeye price poll recovered after %d failures", self._consecutive_failures)
                    self._consecutive_failures = 0
            
            except httpx.HTTPStatusError as e:
                self._consecutive_failures += 1
                if e.response.status_code == 429:
                    # Rate limit - back off significantly
                    if self._consecutive_failures <= self._max_failures_before_warn:
                        self.logger.warning("Birdeye rate limit hit, backing off 30s")
                    await asyncio.sleep(30)
                    continue
                elif e.response.status_code == 401:
                    self.logger.error("Birdeye API key invalid or expired!")
                    await asyncio.sleep(60)
                    continue
                elif e.response.status_code == 400:
                    # Invalid address or token not found (common for new pump tokens)
                    # Remove from active polling to reduce spam (will be re-added if needed)
                    self.logger.debug("Birdeye 400: Token too new, removing from active polling")
                    self._active_mints.discard(mint)  # Remove problematic token
                    self._consecutive_failures = 0 # Don't count as system failure
                    continue
                else:
                    if self._consecutive_failures <= self._max_failures_before_warn:
                        self.logger.warning("Birdeye API error %d: %s", e.response.status_code, e)
            
            except (httpx.ConnectError, OSError) as e:
                # Network errors
                self._consecutive_failures += 1
                if self._consecutive_failures <= self._max_failures_before_warn:
                    self.logger.warning("Birdeye connection error: %s", e)
                elif self._consecutive_failures == self._max_failures_before_warn + 1:
                    self.logger.error("Birdeye polling failing repeatedly, suppressing warnings")
                
                # Exponential backoff: 2s, 4s, 8s, max 30s
                backoff = min(30, 2 ** self._consecutive_failures)
                await asyncio.sleep(backoff)
                continue
            
            except Exception as e:
                self._consecutive_failures += 1
                if self._consecutive_failures <= self._max_failures_before_warn:
                    self.logger.warning("Birdeye price poll failed: %s", e)
            
            # Poll interval from settings
            await asyncio.sleep(self.settings.BIRDEYE_POLL_SEC)

    async def close(self) -> None:
        """Clean shutdown."""
        await self.stop_polling()
        await self._client.aclose()
