from __future__ import annotations

import logging
from typing import Any

import httpx

from solana_bot.config import Settings


class JupiterClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.price_base = settings.JUPITER_PRICE_API_BASE.rstrip("/")
        self.quote_base = settings.JUPITER_QUOTE_API_BASE.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=settings.API_TIMEOUT_SEC)
        self.logger = logging.getLogger("solana_bot.jupiter")

    async def close(self) -> None:
        await self.client.aclose()

    async def get_quote(self, mint: str, decimals: int | None = None) -> float | None:
        price = await self._fetch_price(mint)
        if price is not None:
            return price
        return await self._fetch_quote_price(mint, decimals)

    async def _fetch_price(self, mint: str) -> float | None:
        url = f"{self.price_base}/price"
        try:
            response = await self.client.get(url, params={"ids": mint})
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            self.logger.debug("Jupiter price failed: %s", exc)
            return None

        data = payload.get("data") if isinstance(payload, dict) else None
        if not data:
            return None
        record = data.get(mint)
        if not isinstance(record, dict):
            return None
        price = record.get("price")
        return float(price) if price is not None else None

    async def _fetch_quote_price(self, mint: str, decimals: int | None) -> float | None:
        if decimals is None:
            return None
        amount = 10**decimals
        params = {
            "inputMint": mint,
            "outputMint": self.settings.JUPITER_QUOTE_OUTPUT_MINT,
            "amount": amount,
            "slippageBps": 50,
        }
        url = f"{self.quote_base}/quote"
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except httpx.HTTPError as exc:
            self.logger.debug("Jupiter quote failed: %s", exc)
            return None

        data = payload.get("data")
        if not data:
            return None
        quote = data[0] if isinstance(data, list) else None
        if not quote:
            return None
        out_amount = quote.get("outAmount")
        if out_amount is None:
            return None
        try:
            out_amount = float(out_amount)
        except (TypeError, ValueError):
            return None
        if out_amount <= 0:
            return None
        return out_amount / float(amount)
