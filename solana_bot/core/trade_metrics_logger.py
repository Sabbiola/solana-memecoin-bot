from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from solana_bot.config import Settings, get_settings


class TradeMetricsLogger:
    def __init__(self, settings: Settings) -> None:
        self.path = Path(settings.LOG_DIR) / "trade_metrics.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(self, data: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data) + "\n")

    def print_report(self, days: int = 7) -> None:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            print("No metrics found")
            return

        print(f"Loaded {len(lines)} events (last {days} days window not enforced in stub)")


_METRICS_LOGGER: TradeMetricsLogger | None = None


def get_metrics_logger(settings: Settings | None = None) -> TradeMetricsLogger:
    global _METRICS_LOGGER
    if _METRICS_LOGGER is None:
        _METRICS_LOGGER = TradeMetricsLogger(settings or get_settings())
    return _METRICS_LOGGER
