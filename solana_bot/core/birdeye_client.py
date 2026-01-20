from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from solana_bot.config import Settings


@dataclass(frozen=True)
class OHLCVPoint:
    ts: int
    close: float
    volume: float
    trades: int


class BirdEyeClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.base_url = settings.BIRDEYE_API_BASE.rstrip("/")
        headers = {}
        if settings.BIRDEYE_API_KEY:
            headers["X-API-KEY"] = settings.BIRDEYE_API_KEY
        self.client = client or httpx.AsyncClient(timeout=settings.API_TIMEOUT_SEC, headers=headers)
        self.logger = logging.getLogger("solana_bot.birdeye")

    async def close(self) -> None:
        await self.client.aclose()

    async def get_ohlcv(
        self, address: str, interval: str, time_from: int | None, time_to: int | None
    ) -> list[OHLCVPoint]:
        params: dict[str, Any] = {"address": address, "type": interval, "interval": interval}
        if time_from is not None:
            params["time_from"] = int(time_from)
        if time_to is not None:
            params["time_to"] = int(time_to)
        payload = await self._get("/ohlcv", params)
        return _normalize_series(payload)

    async def get_price_history(
        self, address: str, interval: str, time_from: int | None, time_to: int | None
    ) -> list[OHLCVPoint]:
        params: dict[str, Any] = {"address": address, "type": interval, "interval": interval}
        if time_from is not None:
            params["time_from"] = int(time_from)
        if time_to is not None:
            params["time_to"] = int(time_to)
        payload = await self._get("/price_history", params)
        return _normalize_series(payload)

    async def get_token_overview(self, address: str) -> dict[str, Any]:
        payload = await self._get("/token_overview", {"address": address})
        return _extract_data(payload) if isinstance(payload, dict) else {}

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.settings.BIRDEYE_API_KEY:
            raise RuntimeError("BIRDEYE_API_KEY is required for BirdEye requests.")
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                response = await self.client.get(url, params=params)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2.0
                    self.logger.warning("BirdEye rate limited, retrying in %.1fs", delay)
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                payload = response.json()
                return payload
            except httpx.HTTPError as exc:
                if attempt < 2:
                    await asyncio.sleep(1.0)
                    continue
                self.logger.warning("BirdEye request failed: %s", exc)
                return {}
        return {}


def _extract_data(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if payload.get("success") is False:
        return {}
    data = payload.get("data")
    return data if data is not None else payload


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    data = _extract_data(payload)
    if isinstance(data, dict):
        for key in ("items", "list", "data", "prices", "candles"):
            items = data.get(key)
            if isinstance(items, list):
                return items
    if isinstance(data, list):
        return data
    if isinstance(payload, list):
        return payload
    return []


def _normalize_series(payload: Any) -> list[OHLCVPoint]:
    points: list[OHLCVPoint] = []
    for item in _extract_items(payload):
        ts = _get_first_int(item, ("t", "time", "unixTime", "timestamp", "startTime"))
        close = _get_first_float(item, ("c", "close", "value", "price"))
        if ts is None or close is None:
            continue
        volume = _get_first_float(item, ("v", "volume", "volumeUsd", "volumeUSD")) or 0.0
        trades = _get_first_int(item, ("trades", "count", "txns", "txnCount")) or 0
        points.append(OHLCVPoint(ts=int(ts), close=float(close), volume=float(volume), trades=int(trades)))
    return sorted(points, key=lambda pt: pt.ts)


def _get_first_float(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = item.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _get_first_int(item: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = item.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None
