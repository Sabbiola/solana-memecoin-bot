from __future__ import annotations

import time
from dataclasses import dataclass

from solana_bot.config import get_settings


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, object]] = {}
        self._stats = CacheStats()

    def get(self, key: str) -> object | None:
        entry = self._store.get(key)
        if not entry:
            self._stats.misses += 1
            return None
        expiry, value = entry
        if time.time() > expiry:
            self._store.pop(key, None)
            self._stats.misses += 1
            return None
        self._stats.hits += 1
        return value

    def set(self, key: str, value: object, ttl_sec: int) -> None:
        self._store[key] = (time.time() + ttl_sec, value)

    def print_stats(self) -> None:
        total = self._stats.hits + self._stats.misses
        hit_rate = (self._stats.hits / total) * 100 if total else 0
        print(f"RPC cache hit rate: {hit_rate:.1f}%")


class CreditLimiter:
    def __init__(self) -> None:
        settings = get_settings()
        self._daily_budget = 100000
        self._hourly_budget = 5000
        self._usage_daily = 0
        self._usage_hourly = 0
        self._last_hour = time.time()

    def record(self, cost: int = 1) -> None:
        now = time.time()
        if now - self._last_hour >= 3600:
            self._usage_hourly = 0
            self._last_hour = now
        self._usage_daily += cost
        self._usage_hourly += cost

    def should_throttle(self) -> bool:
        return self._usage_hourly >= self._hourly_budget or self._usage_daily >= self._daily_budget

    def print_status(self) -> None:
        print(f"RPC credits: daily={self._usage_daily} hourly={self._usage_hourly}")


_RPC_CACHE = TTLCache()
_CREDIT_LIMITER = CreditLimiter()


def get_rpc_cache() -> TTLCache:
    return _RPC_CACHE


def get_credit_limiter() -> CreditLimiter:
    return _CREDIT_LIMITER
