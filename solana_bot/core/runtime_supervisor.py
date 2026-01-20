from __future__ import annotations

from dataclasses import dataclass

from solana_bot.config import Settings
from solana_bot.core.models import BotStats


@dataclass
class SupervisorAction:
    pause_new_entries: bool
    stop_all: bool
    reason: str


class RuntimeSupervisor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, stats: BotStats) -> SupervisorAction | None:
        if stats.daily_loss_sol >= self.settings.MAX_DAILY_LOSS_SOL:
            return SupervisorAction(True, True, "MAX_DAILY_LOSS")
        if stats.daily_trades >= self.settings.MAX_DAILY_TRADES:
            return SupervisorAction(True, False, "MAX_DAILY_TRADES")
        if stats.cash_sol <= self.settings.MIN_RESERVE_SOL:
            return SupervisorAction(True, False, "MIN_RESERVE_BREACH")
        return None
