"""Dedicated Position Price Monitor - Aggressive polling for open positions only.

This module polls prices specifically for open positions at high frequency (1-2s)
to ensure real-time price updates regardless of WebSocket reliability.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from solana_bot.config import Settings
    from solana_bot.core.realtime_price_feed import RealTimePriceFeed
    from solana_bot.core.models import Position

# Jupiter Price API v3
JUPITER_PRICE_API = "https://api.jup.ag/price/v3"
SOL_MINT = "So11111111111111111111111111111111111111112"

class PositionPriceMonitor:
    """
    High-frequency price monitor specifically for open positions.
    Supports Rich Logging with PnL and Currency conversion.
    """
    
    def __init__(self, settings: "Settings", realtime_feed: "RealTimePriceFeed" = None) -> None:
        self.settings = settings
        self.realtime_feed = realtime_feed
        self.logger = logging.getLogger("solana_bot.position_price_monitor")
        
        # Price cache: mint -> (price_usd, timestamp)
        self._prices: dict[str, tuple[float, float]] = {}
        
        # Positions Storage: mint -> Position Object (for PnL calc)
        self._positions: dict[str, "Position"] = {}
        
        # Polling control
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._poll_interval = 2.0
        self._client = None
        
        # Exchange Rates (approx or fetched)
        self._sol_price_usd: float = 0.0
        self._usd_eur_rate: float = 0.96  # Default fallback
    
    async def start(self) -> None:
        import httpx
        self._client = httpx.AsyncClient(timeout=5.0)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        self.logger.info("🎯 Position Price Monitor started (polling every %.1fs)", self._poll_interval)
    
    async def stop(self) -> None:
        self._running = False
        if self._poll_task: self._poll_task.cancel()
        if self._client: await self._client.aclose()
        self.logger.info("Position Price Monitor stopped")
    
    def add_position(self, pos: Union[str, "Position"]) -> None:
        """Add a position to monitor. Acccepts mint string (legacy) or Position object."""
        if hasattr(pos, 'token'):
            # It's a Position object
            if pos.token.mint not in self._positions:
                self.logger.debug("Added position (with PnL tracking): %s", pos.token.symbol)
            self._positions[pos.token.mint] = pos
        else:
            # It's a mint string (Legacy fallback)
            # Create a dummy entry if needed, but we prefer objects
            # We can't track PnL without entry price, but we monitors price
            pass 
            # Note: We track active mints via keys of _positions + explicit set if needed
            # But simpler to just require objects for full features.
            # For compatibility, if string is passed, we just add it to a temp set?
            # Let's handle string by creating a placeholder if not exists?
            # No, keep it clean.
            self.logger.warning("add_position called with string %s, PnL logging disabled for this token", pos[:8])
            # We add to _positions with None value to indicate just price tracking
            if pos not in self._positions:
               self._positions[pos] = None

    def remove_position(self, mint: str) -> None:
        self._positions.pop(mint, None)
        self._prices.pop(mint, None)
    
    def get_price(self, mint: str) -> float | None:
        cached = self._prices.get(mint)
        if cached:
            price, ts = cached
            if time.time() - ts < 10.0: return price
        return None

    def get_all_prices(self) -> dict[str, float]:
        now = time.time()
        return {m: p for m, (p, ts) in self._prices.items() if now - ts < 10.0}
    
    async def _poll_loop(self) -> None:
        while self._running:
            try:
                if self._positions:
                    await self._fetch_prices()
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError: break
            except Exception as e:
                self.logger.error("Poll error: %s", e)
                await asyncio.sleep(2.0)

    async def _fetch_prices(self) -> None:
        if not self._positions or not self._client: return
        
        # Always include SOL for conversion if we have positions
        mints = list(self._positions.keys())
        if SOL_MINT not in mints:
            mints.append(SOL_MINT)
            
        try:
            ids = ",".join(mints)
            url = f"{JUPITER_PRICE_API}?ids={ids}"
            headers = {}
            if self.settings.JUPITER_API_KEY:
                headers["x-api-key"] = self.settings.JUPITER_API_KEY
            
            response = await self._client.get(url, headers=headers)
            updated_logs = []
            
            if response.status_code == 200:
                data = response.json()
                prices_data = data.get("data", data)
                
                missing = []
                
                # First pass: Update SOL price
                sol_info = prices_data.get(SOL_MINT)
                if sol_info:
                    self._sol_price_usd = float(sol_info.get("usdPrice") or sol_info.get("price") or 0)

                for mint in self._positions.keys(): # Only iterate actual positions, ignore SOL if added just for ref
                    price_info = prices_data.get(mint)
                    price_found = False
                    
                    if price_info:
                        price = float(price_info.get("usdPrice") or price_info.get("price") or 0)
                        if price > 0:
                            self._prices[mint] = (price, time.time())
                            price_found = True
                            if self.realtime_feed: self.realtime_feed.update_price(mint, price)
                            
                            # Log Construction
                            pos = self._positions[mint]
                            if pos:
                                log_entry = self._format_log_entry(pos, price)
                                updated_logs.append(log_entry)
                    
                    if not price_found: missing.append(mint)
                
                if missing: await self._fallback(missing, updated_logs)
                
                # Include SOL price in header log
                header = f"🎯 PRICES [SOL: ${self._sol_price_usd:.2f}]"
                if updated_logs:
                    self.logger.info("%s | %s", header, " | ".join(updated_logs))
            
            elif response.status_code == 401:
                self.logger.warning("⚠️ Jupiter 401, using DexScreener")
                await self._fallback(list(self._positions.keys()), updated_logs)
                if updated_logs: self.logger.info("🎯 FALLBACK: %s", " | ".join(updated_logs))
            else:
                 await self._fallback(list(self._positions.keys()), [])

        except Exception as e:
            self.logger.error("Fetch error: %s", e)
            await self._fallback(list(self._positions.keys()), [])

    async def _fallback(self, mints: list[str], logs: list[str]) -> None:
        if not self._client: return
        chunk_size = 30
        for i in range(0, len(mints), chunk_size):
            chunk = mints[i:i + chunk_size]
            try:
                url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(chunk)}"
                resp = await self._client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    now = time.time()
                    processed = set()
                    for pair in data.get("pairs", []):
                        mint = pair.get("baseToken", {}).get("address")
                        if mint not in self._positions or mint in processed: continue
                        price = float(pair.get("priceUsd", 0) or 0)
                        if price > 0:
                            self._prices[mint] = (price, now)
                            processed.add(mint)
                            if self.realtime_feed: self.realtime_feed.update_price(mint, price)
                            
                            pos = self._positions[mint]
                            if pos:
                                logs.append(self._format_log_entry(pos, price) + "[DEX]")
            except Exception: pass

    def _format_log_entry(self, pos: "Position", current_price: float) -> str:
        symbol = pos.token.symbol
        pnl_pct = 0.0
        if pos.entry_price > 0:
            pnl_pct = ((current_price - pos.entry_price) / pos.entry_price) * 100
        
        icon = "🟢" if pnl_pct >= 0 else "🔴"
        price_eur = current_price * self._usd_eur_rate
        
        # Format: SYM: $0.00 (€0.00) PnL:+5.2%🟢
        # Conditional formatting for very small prices
        p_fmt = f"${current_price:.6f}" if current_price < 0.01 else f"${current_price:.4f}"
        e_fmt = f"€{price_eur:.6f}" if price_eur < 0.01 else f"€{price_eur:.4f}"
        
        return f"{symbol}: {p_fmt} ({e_fmt}) PnL:{pnl_pct:+.1f}%{icon}"
