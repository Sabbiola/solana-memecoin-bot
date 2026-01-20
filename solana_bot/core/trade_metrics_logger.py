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
        # 1. Log to local file
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data) + "\n")

        # 2. Sync to Supabase
        try:
            import supabase_sync
            if supabase_sync.is_enabled():
                # Check if this qualifies as a trade event to sync
                # Usually events have 'type' like 'BUY', 'SELL' or 'complete_trade'
                evt_type = data.get("type", "").upper()
                
                # Check if it has essential trade fields
                if "mint" in data and ("price" in data or "limit_price" in data) and evt_type in ["BUY", "SELL"]:
                    price = data.get("price") or data.get("limit_price", 0)
                    amount = data.get("amount", 0) # Token amount
                    # Sometimes amount is in data['size'] ? let's fallback
                    if amount == 0 and "size" in data:
                        amount = data["size"]
                    
                    # Calculate total SOL if not present
                    total_sol = data.get("cost_sol") or data.get("proceeds_sol") or (amount * price)
                    
                    trade_record = {
                        "user_id": supabase_sync.get_user_id(),  # REQUIRED for RLS
                        "token_mint": data["mint"],
                        "token_symbol": data.get("symbol", "???"),
                        "type": evt_type.lower(),
                        "amount": amount,
                        "price_sol": price,
                        "price_usd": 0, # Metric not always available in event
                        "total_sol": total_sol,
                        "signature": data.get("signature", ""),
                        "platform": data.get("source", "bot"),
                        "block_time": int(data.get("timestamp", 0) * 1000) if "timestamp" in data else 0 
                    }
                    supabase_sync.safe_insert("trades", trade_record)
                    
        except Exception as e:
            # Silently fail to avoid breaking the bot loop, but logs would normally catch this
            # Since we are IN the logger, we use print to avoid recursion loop if logger used supabase
            print(f"Failed to sync trade to Supabase: {e}")

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
