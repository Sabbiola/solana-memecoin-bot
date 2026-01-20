from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from solana_bot.config import Settings


class CoinGeckoClient:
    """Client for CoinGecko Onchain DEX API (GeckoTerminal).
    
    Primary data source for Solana token information including:
    - Token prices and market data
    - Token info (name, symbol, socials)
    - Top holders
    - New and trending pools
    - OHLCV data
    - Recent trades
    """
    
    NETWORK = "solana"  # Solana network ID for CoinGecko
    
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.coingecko")
        
        # Get API key
        api_key = getattr(settings, "COINGECKO_API_KEY", "")
        
        if api_key:
            # Demo keys start with "CG-", Pro keys don't
            is_demo_key = api_key.startswith("CG-")
            
            if is_demo_key:
                self.base_url = "https://api.coingecko.com/api/v3"
                self.headers = {"x-cg-demo-api-key": api_key}
                self.logger.info("CoinGecko initialized with Demo API key")
            else:
                self.base_url = getattr(settings, "COINGECKO_API_BASE", "https://pro-api.coingecko.com/api/v3")
                self.headers = {"x-cg-pro-api-key": api_key}
                self.logger.info("CoinGecko initialized with Pro API key")
        else:
            self.base_url = "https://api.coingecko.com/api/v3"
            self.headers = {}
            self.logger.info("CoinGecko initialized without API key (public endpoints only)")
        
        timeout = getattr(settings, "API_TIMEOUT_SEC", 15.0)
        self.client = client or httpx.AsyncClient(timeout=timeout, headers=self.headers)
        
        # Cache for rate limit optimization
        self._price_cache: dict[str, tuple[float, float]] = {}  # mint -> (price, ts)
        self._cache_ttl = getattr(settings, "COINGECKO_CACHE_TTL_SEC", 30.0)
        self._max_retries = getattr(settings, "COINGECKO_MAX_RETRIES", 3)
        self._retry_backoff = getattr(settings, "COINGECKO_RETRY_BACKOFF_SEC", 1.0)

    async def close(self) -> None:
        await self.client.aclose()

    # =====================================================================
    # PRICE ENDPOINTS
    # =====================================================================

    async def get_token_price(self, token_address: str) -> float | None:
        """Get token price in USD."""
        # Check cache first
        now = time.time()
        if token_address in self._price_cache:
            cached_price, cached_ts = self._price_cache[token_address]
            if now - cached_ts < self._cache_ttl:
                return cached_price
        
        url = f"{self.base_url}/onchain/simple/networks/{self.NETWORK}/token_price/{token_address}"
        params = {"vs_currencies": "usd"}
        
        data = await self._request(url, params=params)
        if not data:
            return None
        
        # Response: {"token_prices": {"<address>": {"usd": 0.123}}}
        token_prices = data.get("token_prices", {})
        price_data = token_prices.get(token_address, {})
        price = price_data.get("usd")
        
        if price is not None:
            self._price_cache[token_address] = (price, now)
        
        return price

    async def get_multi_token_prices(self, token_addresses: list[str]) -> dict[str, float]:
        """Get prices for multiple tokens at once (max 100)."""
        if not token_addresses:
            return {}
        
        # Limit to 100 tokens per request
        addresses = token_addresses[:100]
        addresses_str = ",".join(addresses)
        
        url = f"{self.base_url}/onchain/simple/networks/{self.NETWORK}/token_price/{addresses_str}"
        params = {"vs_currencies": "usd"}
        
        data = await self._request(url, params=params)
        if not data:
            return {}
        
        result: dict[str, float] = {}
        token_prices = data.get("token_prices", {})
        now = time.time()
        
        for addr in addresses:
            price_data = token_prices.get(addr, {})
            price = price_data.get("usd")
            if price is not None:
                result[addr] = price
                self._price_cache[addr] = (price, now)
        
        return result

    # =====================================================================
    # TOKEN DATA ENDPOINTS
    # =====================================================================

    async def get_token_data(self, token_address: str) -> dict[str, Any] | None:
        """Get comprehensive token data including price, volume, market cap, liquidity."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/tokens/{token_address}"
        
        data = await self._request(url)
        if not data:
            return None
        
        return data.get("data", {}).get("attributes", {})

    async def get_token_info(self, token_address: str) -> dict[str, Any] | None:
        """Get token info (name, symbol, image, description, socials)."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/tokens/{token_address}/info"
        
        data = await self._request(url)
        if not data:
            return None
        
        return data.get("data", {}).get("attributes", {})

    async def get_multi_token_data(self, token_addresses: list[str]) -> list[dict[str, Any]]:
        """Get data for multiple tokens at once."""
        if not token_addresses:
            return []
        
        # Limit to 30 tokens per request
        addresses = token_addresses[:30]
        addresses_str = ",".join(addresses)
        
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/tokens/multi/{addresses_str}"
        
        data = await self._request(url)
        if not data:
            return []
        
        items = data.get("data", [])
        return [item.get("attributes", {}) for item in items if item.get("attributes")]

    # =====================================================================
    # HOLDER ENDPOINTS
    # =====================================================================

    async def get_top_holders(self, token_address: str) -> list[dict[str, Any]]:
        """Get top token holders."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/tokens/{token_address}/top_holders"
        
        data = await self._request(url)
        if not data:
            return []
        
        return data.get("data", [])

    # =====================================================================
    # POOL DISCOVERY ENDPOINTS
    # =====================================================================

    async def get_new_pools(self, page: int = 1) -> list[dict[str, Any]]:
        """Get newly created pools on Solana."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/new_pools"
        params = {"page": page}
        
        data = await self._request(url, params=params)
        if not data:
            return []
        
        items = data.get("data", [])
        return [self._parse_pool(item) for item in items if item.get("attributes")]

    async def get_trending_pools(self, page: int = 1) -> list[dict[str, Any]]:
        """Get trending pools on Solana."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/trending_pools"
        params = {"page": page}
        
        data = await self._request(url, params=params)
        if not data:
            return []
        
        items = data.get("data", [])
        return [self._parse_pool(item) for item in items if item.get("attributes")]

    async def get_top_pools(self, page: int = 1) -> list[dict[str, Any]]:
        """Get top pools by volume on Solana."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/pools"
        params = {"page": page}
        
        data = await self._request(url, params=params)
        if not data:
            return []
        
        items = data.get("data", [])
        return [self._parse_pool(item) for item in items if item.get("attributes")]

    async def search_pools(self, query: str) -> list[dict[str, Any]]:
        """Search for pools by token name/symbol."""
        url = f"{self.base_url}/onchain/search/pools"
        params = {"query": query, "network": self.NETWORK}
        
        data = await self._request(url, params=params)
        if not data:
            return []
        
        items = data.get("data", [])
        return [self._parse_pool(item) for item in items if item.get("attributes")]

    # =====================================================================
    # TRADING DATA ENDPOINTS
    # =====================================================================

    async def get_token_trades(self, token_address: str, trade_volume_in_usd_greater_than: float = 0) -> list[dict[str, Any]]:
        """Get recent trades for a token."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/tokens/{token_address}/trades"
        params = {}
        if trade_volume_in_usd_greater_than > 0:
            params["trade_volume_in_usd_greater_than"] = trade_volume_in_usd_greater_than
        
        data = await self._request(url, params=params)
        if not data:
            return []
        
        return data.get("data", [])

    async def get_pool_ohlcv(
        self, 
        pool_address: str, 
        timeframe: str = "minute",  # minute, hour, day
        aggregate: int = 1,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get OHLCV (candle) data for a pool."""
        url = f"{self.base_url}/onchain/networks/{self.NETWORK}/pools/{pool_address}/ohlcv/{timeframe}"
        params = {"aggregate": aggregate, "limit": limit}
        
        data = await self._request(url, params=params)
        if not data:
            return []
        
        return data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])

    # =====================================================================
    # SOL PRICE (for USD conversion)
    # =====================================================================

    async def get_sol_price(self) -> tuple[float, float]:
        """Get SOL price in USD and EUR."""
        url = f"{self.base_url}/simple/price"
        params = {"ids": "solana", "vs_currencies": "usd,eur"}
        
        data = await self._request(url, params=params)
        if not data:
            return 0.0, 0.0
        
        sol_data = data.get("solana", {})
        return sol_data.get("usd", 0.0), sol_data.get("eur", 0.0)

    # =====================================================================
    # HELPER METHODS
    # =====================================================================

    def _parse_pool(self, item: dict[str, Any]) -> dict[str, Any]:
        """Parse pool data into a standardized format."""
        attrs = item.get("attributes", {})
        relationships = item.get("relationships", {})
        
        # Extract base token info
        base_token = relationships.get("base_token", {}).get("data", {})
        quote_token = relationships.get("quote_token", {}).get("data", {})
        
        return {
            "id": item.get("id", ""),
            "pool_address": attrs.get("address", ""),
            "name": attrs.get("name", ""),
            "base_token_address": base_token.get("id", "").split("_")[-1] if base_token.get("id") else "",
            "quote_token_address": quote_token.get("id", "").split("_")[-1] if quote_token.get("id") else "",
            "price_usd": float(attrs.get("base_token_price_usd", 0) or 0),
            "price_native": float(attrs.get("base_token_price_native_currency", 0) or 0),
            "volume_usd_h24": float(attrs.get("volume_usd", {}).get("h24", 0) or 0),
            "volume_usd_h1": float(attrs.get("volume_usd", {}).get("h1", 0) or 0),
            "volume_usd_m5": float(attrs.get("volume_usd", {}).get("m5", 0) or 0),
            "reserve_in_usd": float(attrs.get("reserve_in_usd", 0) or 0),
            "fdv_usd": float(attrs.get("fdv_usd", 0) or 0),
            "market_cap_usd": float(attrs.get("market_cap_usd", 0) or 0),
            "price_change_h24": float(attrs.get("price_change_percentage", {}).get("h24", 0) or 0),
            "price_change_h1": float(attrs.get("price_change_percentage", {}).get("h1", 0) or 0),
            "price_change_m5": float(attrs.get("price_change_percentage", {}).get("m5", 0) or 0),
            "pool_created_at": attrs.get("pool_created_at", ""),
            "transactions_h24": attrs.get("transactions", {}).get("h24", {}),
            "transactions_h1": attrs.get("transactions", {}).get("h1", {}),
            "transactions_m5": attrs.get("transactions", {}).get("m5", {}),
        }

    async def _request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        log_level: str = "warning",
    ) -> dict[str, Any] | None:
        """Make HTTP request with retry logic."""
        for attempt in range(self._max_retries):
            try:
                response = await self.client.get(url, params=params)
                
                if response.status_code == 429:
                    # Rate limited
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else self._retry_backoff * (attempt + 1)
                    self.logger.warning("CoinGecko rate limited, retrying in %.1fs", delay)
                    await asyncio.sleep(delay)
                    continue
                
                if response.status_code == 404:
                    self.logger.debug("CoinGecko 404 for %s", url)
                    return None
                
                response.raise_for_status()
                return response.json()
                
            except httpx.HTTPError as exc:
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(self._retry_backoff)
                    continue
                getattr(self.logger, log_level)(
                    "CoinGecko request failed for %s: %s", url, exc
                )
                return None
        
        return None
