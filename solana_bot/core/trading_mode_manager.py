from __future__ import annotations

from solana_bot.config import Settings


class TradingModeManager:
    def __init__(self, settings: Settings) -> None:
        self.paper_mode = settings.PAPER_TRADING_MODE

    def is_paper(self) -> bool:
        return self.paper_mode
