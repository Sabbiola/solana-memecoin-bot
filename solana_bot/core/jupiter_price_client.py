from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from solana_bot.config import Settings


class JupiterPriceClient:
    """Fast polling client for Jupiter Price API (for migrated Raydium tokens)."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.jupiter_price")
        self._polling_task: asyncio.Task | None = None
        self._active_mints: set[str] = set()
        self._prices: dict[str, float] = {}
        self._running = False
        self._consecutive_failures = 0
        self._max_failures_before_warn = 3
        # Persistent client with longer timeout and retry
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_keepalive_connections=5),
        )

    async def start_polling(self, mints: set[str]) -> None:
        """Start polling for the given mints."""
        self._active_mints.update(mints)
        if not self._running and self._active_mints:
            self._running = True
            self._polling_task = asyncio.create_task(self._poll_loop())
            self.logger.info("Jupiter price polling started for %d tokens", len(self._active_mints))

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
            self.logger.info("Jupiter price polling stopped")

    def get_price(self, mint: str) -> float | None:
        """Get the latest cached price for a mint."""
        return self._prices.get(mint)

    async def _poll_loop(self) -> None:
        """Main polling loop with DNS retry logic."""
        while self._running:
            if not self._active_mints:
                await asyncio.sleep(1)
                continue
            
            try:
                # Jupiter Price API v4: https://price.jup.ag/v4/price?ids=mint1,mint2
                mints_str = ",".join(self._active_mints)
                url = f"https://price.jup.ag/v4/price?ids={mints_str}"
                
                response = await self._client.get(url)
                response.raise_for_status()
                data = response.json()
                
                # Response format: {"data": {"mint": {"id": "mint", "price": "0.123"}}}
                if "data" in data:
                    for mint, price_data in data["data"].items():
                        if isinstance(price_data, dict) and "price" in price_data:
                            try:
                                price = float(price_data["price"])
                                self._prices[mint] = price
                            except (ValueError, TypeError):
                                continue
                    
                    self.logger.debug("Updated prices for %d mints from Jupiter", len(self._prices))
                
                # Reset failure counter on success
                if self._consecutive_failures > 0:
                    self.logger.info("Jupiter price poll recovered after %d failures", self._consecutive_failures)
                    self._consecutive_failures = 0
            
            except (httpx.ConnectError, OSError) as e:
                # DNS/network errors - use exponential backoff
                self._consecutive_failures += 1
                if self._consecutive_failures <= self._max_failures_before_warn:
                    self.logger.warning("Jupiter price poll failed (DNS/network): %s", e)
                elif self._consecutive_failures == self._max_failures_before_warn + 1:
                    self.logger.error("Jupiter price poll failing repeatedly, suppressing warnings. Using DexScreener fallback.")
                
                # Exponential backoff: 2s, 4s, 8s, max 30s
                backoff = min(30, 2 ** self._consecutive_failures)
                await asyncio.sleep(backoff)
                continue
            
            except Exception as e:
                self._consecutive_failures += 1
                if self._consecutive_failures <= self._max_failures_before_warn:
                    self.logger.warning("Jupiter price poll failed: %s", e)
            
            await asyncio.sleep(self.settings.REALTIME_JUPITER_POLL_SEC)

    async def close(self) -> None:
        """Clean shutdown."""
        await self.stop_polling()
        await self._client.aclose()
