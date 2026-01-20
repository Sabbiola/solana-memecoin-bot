from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from solana_bot.core.bot import Bot
from solana_bot.core.models import Phase, TokenInfo


@dataclass(frozen=True)
class BacktestTick:
    ts: float
    tokens: list[TokenInfo]
    prices: dict[str, float]


class BacktestScanner:
    def __init__(self) -> None:
        self._tick: BacktestTick | None = None
        self.pumpportal = None
        self.dex_client = None

    def set_tick(self, tick: BacktestTick) -> None:
        self._tick = tick

    async def start(self) -> None:
        return

    async def close(self) -> None:
        return

    async def scan(self) -> Iterable[TokenInfo]:
        if not self._tick:
            return []
        return list(self._tick.tokens)


class BacktestPriceFeed:
    def __init__(self) -> None:
        self._prices: dict[str, float] = {}

    def set_tick(self, tick: BacktestTick) -> None:
        self._prices = tick.prices

    async def close(self) -> None:
        return

    async def update(self, position, now: float | None = None) -> float:
        return self._prices.get(position.token.mint, position.last_price)


class BacktestRunner:
    def __init__(
        self,
        bot: Bot,
        scanner: BacktestScanner,
        price_feed: BacktestPriceFeed,
        ticks: list[BacktestTick],
    ) -> None:
        self.bot = bot
        self.scanner = scanner
        self.price_feed = price_feed
        self.ticks = ticks

    async def run(self) -> None:
        await self.bot.initialize()
        for tick in self.ticks:
            self.scanner.set_tick(tick)
            self.price_feed.set_tick(tick)
            await self.bot.step(tick.ts)
        await self.bot.shutdown()


def load_ticks(path: Path) -> list[BacktestTick]:
    ticks: list[BacktestTick] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            tokens = [_parse_token(item) for item in payload.get("tokens", [])]
            prices = {k: float(v) for k, v in payload.get("prices", {}).items()}
            ticks.append(BacktestTick(ts=float(payload["ts"]), tokens=tokens, prices=prices))
    return sorted(ticks, key=lambda tick: tick.ts)


def _parse_token(data: dict) -> TokenInfo:
    phase = data.get("phase")
    if phase:
        try:
            data["phase"] = Phase(phase)
        except ValueError:
            data["phase"] = Phase.UNKNOWN
    return TokenInfo(**data)
