from __future__ import annotations

import random

from solana_bot.config import Settings
from solana_bot.core.models import TradeFill
from solana_bot.utils.time import utc_ts


class PaperBroker:
    def __init__(self, settings: Settings, seed: int | None = None) -> None:
        self.settings = settings
        self.rng = random.Random(seed)

    def execute_trade(self, side: str, mint: str, size_sol: float, price: float, reason: str) -> TradeFill:
        slippage = self.rng.uniform(-self.settings.SIM_SLIPPAGE_PCT, self.settings.SIM_SLIPPAGE_PCT)
        fee_bps = self.settings.SIM_FEE_BPS
        if self.settings.SIM_FIXED_FEE_SOL > 0:
            fee_bps += (self.settings.SIM_FIXED_FEE_SOL / max(size_sol, 1e-9)) * 10000.0
        fee_pct = min(0.5, fee_bps / 10000.0)
        if side.upper() == "BUY":
            fill_price = price * (1 + slippage) * (1 + fee_pct)
        else:
            fill_price = price * (1 + slippage) * (1 - fee_pct)
        fill_price = max(0.0000001, fill_price)
        return TradeFill(
            mint=mint,
            side=side,
            size_sol=size_sol,
            price=fill_price,
            ts=utc_ts(),
            reason=reason,
            success=True,
        )
