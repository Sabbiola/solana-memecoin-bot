from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, Tuple

from solana_bot.config import Settings
from solana_bot.core.coingecko_client import CoinGeckoClient
from solana_bot.core.jupiter_client import JupiterClient
from solana_bot.core.models import Position
from solana_bot.utils.time import utc_ts

if TYPE_CHECKING:
    from solana_bot.core.pumpportal_client import PumpPortalClient
    from solana_bot.core.dexscreener_client import DexScreenerClient


class PriceFeed:
    def __init__(
        self,
        settings: Settings,
        jupiter: JupiterClient | None = None,
        coingecko: CoinGeckoClient | None = None,
        pumpportal: "PumpPortalClient | None" = None,
        dexscreener: "DexScreenerClient | None" = None,
    ) -> None:
        self.settings = settings
        self.jupiter = jupiter or JupiterClient(settings)
        self.coingecko = coingecko or CoinGeckoClient(settings)
        self.pumpportal = pumpportal  # Set by Bot after scanner initialization
        self.dexscreener = dexscreener
        self.logger = logging.getLogger("solana_bot.price_feed")
        self._cache: Dict[str, Tuple[float, float]] = {}

    def set_pumpportal(self, pumpportal: "PumpPortalClient") -> None:
        """Set PumpPortal client (called after bot initialization)."""
        self.pumpportal = pumpportal
        
    def set_dexscreener(self, dexscreener: "DexScreenerClient") -> None:
        """Set DexScreener client (called after bot initialization)."""
        self.dexscreener = dexscreener

    async def close(self) -> None:
        await self.jupiter.close()
        await self.coingecko.close()

    async def update(self, position: Position, now: float | None = None) -> float:
        if now is None:
            now = utc_ts()
        mint = position.token.mint
        cached = self._cache.get(mint)
        if cached and now - cached[0] < self.settings.QUOTE_CACHE_TTL_SEC:
            return cached[1]

        # Priority 1: PumpPortal real-time price (for fresh Pump.fun tokens)
        if self.pumpportal:
            pp_price = self.pumpportal.get_price(mint)
            if pp_price and pp_price > 0:
                self._cache[mint] = (now, pp_price)
                return pp_price

        # Priority 2: CoinGecko (primary source for accurate prices)
        if self.settings.USE_COINGECKO_PRIMARY:
            try:
                cg_price = await self.coingecko.get_token_price(mint)
                if cg_price and cg_price > 0:
                    self._cache[mint] = (now, cg_price)
                    return cg_price
            except Exception as e:
                self.logger.debug("CoinGecko price failed for %s: %s", mint[:8], e)

        # Priority 3: DexScreener API (Fallback for new tokens)
        if self.dexscreener:
            try:
                pairs = await self.dexscreener.get_token_pairs(mint)
                if pairs:
                    # Sort by liquidity/volume to find best pair
                    best_pair = max(
                        pairs, 
                        key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0)
                    )
                    price = float(best_pair.get("priceUsd", 0) or 0)
                    if price > 0:
                        self._cache[mint] = (now, price)
                        return price
            except Exception as e:
                self.logger.debug("DexScreener price failed for %s: %s", mint[:8], e)

        # Priority 4: DexScreener metadata (Static)
        dex_price = position.token.metadata.get("price_usd") or position.token.price
        if dex_price and float(dex_price) > 0:
            price = float(dex_price)
            self._cache[mint] = (now, price)
            return price

        # Priority 5: Jupiter
        if self.settings.USE_JUPITER_QUOTES:
            decimals = position.token.metadata.get("decimals")
            price = await self.jupiter.get_quote(mint, decimals=decimals)
            if price is not None:
                self._cache[mint] = (now, float(price))
                return float(price)

        # Fallback: keep last known price
        return position.last_price
    
    async def get_price_by_mint(self, mint: str) -> float | None:
        """Get current price for a token mint."""
        now = utc_ts()
        
        # Check cache first
        cached = self._cache.get(mint)
        if cached and now - cached[0] < self.settings.QUOTE_CACHE_TTL_SEC:
            return cached[1]
        
        # Try PumpPortal first
        if self.pumpportal:
            pp_price = self.pumpportal.get_price(mint)
            if pp_price and pp_price > 0:
                self._cache[mint] = (now, pp_price)
                return pp_price
        
        # Try CoinGecko (primary)
        if self.settings.USE_COINGECKO_PRIMARY:
            try:
                cg_price = await self.coingecko.get_token_price(mint)
                if cg_price and cg_price > 0:
                    self._cache[mint] = (now, cg_price)
                    return cg_price
            except Exception as e:
                self.logger.debug("CoinGecko price by mint failed for %s: %s", mint[:8], e)
        
        # Try DexScreener (Fallback)
        if self.dexscreener:
            try:
                pairs = await self.dexscreener.get_token_pairs(mint)
                if pairs:
                    # Sort by liquidity
                    best_pair = max(
                        pairs, 
                        key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0)
                    )
                    price = float(best_pair.get("priceUsd", 0) or 0)
                    if price > 0:
                        self._cache[mint] = (now, price)
                        return price
            except Exception as e:
                self.logger.debug("DexScreener price by mint failed for %s: %s", mint[:8], e)
        
        # Try Jupiter
        if self.settings.USE_JUPITER_QUOTES:
            try:
                price = await self.jupiter.get_quote(mint, decimals=None)
                if price is not None and price > 0:
                    self._cache[mint] = (now, float(price))
                    return float(price)
            except Exception as e:
                self.logger.debug("Failed to get Jupiter price for %s: %s", mint, e)
        
        if cached:
            return cached[1]
        
        return None
