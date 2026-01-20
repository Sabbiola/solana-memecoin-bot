from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from solana_bot.config import Settings


class DexScreenerClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.base_url = settings.DEXSCREENER_API_BASE.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=settings.API_TIMEOUT_SEC)
        self.logger = logging.getLogger("solana_bot.dexscreener")
        self._profiles_cache: list[dict[str, Any]] = []
        self._profiles_cache_ts: float = 0.0

    async def close(self) -> None:
        await self.client.aclose()

    async def get_token_profiles(self) -> list[dict[str, Any]]:
        now = time.time()
        if self._profiles_cache and (now - self._profiles_cache_ts) < self.settings.DEXSCREENER_PROFILES_TTL_SEC:
            return list(self._profiles_cache)

        url = f"{self.base_url}/token-profiles/latest/v1"
        payload = await self._request(url)
        if isinstance(payload, list):
            self._profiles_cache = payload
            self._profiles_cache_ts = now
            return payload
        if self._profiles_cache:
            return list(self._profiles_cache)
        return []

    async def get_token_pairs(self, token_address: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/latest/dex/tokens/{token_address}"
        payload = await self._request(url, log_level="debug")
        pairs = payload.get("pairs") if isinstance(payload, dict) else None
        return pairs or []

    async def search_pairs(self, query: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/latest/dex/search"
        payload = await self._request(url, params={"q": query}, log_level="debug")
        pairs = payload.get("pairs") if isinstance(payload, dict) else None
        return pairs or []

    async def _request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        log_level: str = "warning",
    ) -> dict[str, Any] | list | None:
        max_retries = max(1, self.settings.DEXSCREENER_MAX_RETRIES)
        backoff = max(0.5, self.settings.DEXSCREENER_RETRY_BACKOFF_SEC)
        for attempt in range(max_retries):
            try:
                response = await self.client.get(url, params=params)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else backoff * (attempt + 1)
                    self.logger.warning("DexScreener rate limited, retrying in %.1fs", delay)
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff)
                    continue
                getattr(self.logger, log_level)(
                    "DexScreener request failed for %s: %s", url, exc
                )
                return None
        return None
