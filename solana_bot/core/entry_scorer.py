from __future__ import annotations

from solana_bot.config import Settings
from solana_bot.core.models import SelectionSignals, TokenInfo


class EntryScorer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def score(self, token: TokenInfo) -> SelectionSignals:
        meta = token.metadata
        m5_buys = int(meta.get("txns_m5_buys", 0))
        m5_sells = int(meta.get("txns_m5_sells", 0))
        h1_buys = int(meta.get("txns_h1_buys", 0))
        h1_sells = int(meta.get("txns_h1_sells", 0))
        volume_m5 = float(meta.get("volume_m5", 0.0))

        m5_total = m5_buys + m5_sells
        h1_total = h1_buys + h1_sells
        baseline_m5 = max(1.0, h1_total / 12.0)
        baseline_buys = max(1.0, h1_buys / 12.0)

        tx_rate_accel = m5_total / baseline_m5
        wallet_influx_accel = m5_buys / baseline_buys

        price_change_m5 = float(meta.get("price_change_m5", 0.0))
        price_change_h1 = float(meta.get("price_change_h1", 0.0))
        h1_norm = max(0.01, abs(price_change_h1) / 12.0)
        curve_slope_accel = abs(price_change_m5) / h1_norm

        hh_confirmed = price_change_m5 > 0 and price_change_h1 > 0
        buy_sell_ratio = (m5_buys + 1) / (m5_sells + 1)
        sell_absorption = buy_sell_ratio >= 1.2 and price_change_m5 >= -0.5

        score = 0
        score += 1 if tx_rate_accel >= 1.8 else 0
        score += 1 if wallet_influx_accel >= 1.6 else 0
        score += 1 if hh_confirmed else 0
        score += 1 if curve_slope_accel >= 1.5 else 0
        score += 1 if sell_absorption else 0

        avg_trade_usd = volume_m5 / max(1, m5_total)
        anti_fake_ok = (m5_buys >= 5 and avg_trade_usd >= 30.0) or volume_m5 >= 1000.0

        return SelectionSignals(
            tx_rate_accel=tx_rate_accel,
            wallet_influx_accel=wallet_influx_accel,
            hh_confirmed=hh_confirmed,
            curve_slope_accel=curve_slope_accel,
            sell_absorption=sell_absorption,
            score=score,
            anti_fake_ok=anti_fake_ok,
        )
