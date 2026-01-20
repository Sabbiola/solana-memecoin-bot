from __future__ import annotations

import asyncio
from solana_bot.config import Settings
from solana_bot.core.models import TradeFill
from solana_bot.core.trading_mode_manager import TradingModeManager
from solana_bot.paper_trading.broker import PaperBroker


class Trader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mode_manager = TradingModeManager(settings)
        self.paper_broker = PaperBroker(settings)
        self._live_broker = None

    def _get_live_broker(self):
        """Lazy initialize live broker only when needed."""
        if self._live_broker is None:
            from solana_bot.core.live_broker import LiveBroker
            self._live_broker = LiveBroker(self.settings)
        return self._live_broker

    def buy(self, mint: str, size_sol: float, price: float, reason: str) -> TradeFill:
        """Synchronous buy - for paper trading only in sync context."""
        if self.mode_manager.is_paper():
            return self.paper_broker.execute_trade("BUY", mint, size_sol, price, reason)
        # For live trading, caller should use buy_async
        raise RuntimeError("Live trading requires async context - use buy_async()")

    def sell(self, mint: str, size_sol: float, price: float, reason: str) -> TradeFill:
        """Synchronous sell - for paper trading only in sync context."""
        if self.mode_manager.is_paper():
            return self.paper_broker.execute_trade("SELL", mint, size_sol, price, reason)
        # For live trading, caller should use sell_async
        raise RuntimeError("Live trading requires async context - use sell_async()")
    
    async def buy_async(self, mint: str, size_sol: float, price: float, reason: str) -> TradeFill:
        """Async version of buy - works in both paper and live mode."""
        if self.mode_manager.is_paper():
            return self.paper_broker.execute_trade("BUY", mint, size_sol, price, reason)
        broker = self._get_live_broker()
        return await broker.execute_trade("BUY", mint, size_sol, price, reason)
    
    async def sell_async(self, mint: str, size_sol: float, price: float, reason: str, token_amount_raw: int = 0) -> TradeFill:
        """Async version of sell - works in both paper and live mode."""
        if self.mode_manager.is_paper():
            return self.paper_broker.execute_trade("SELL", mint, size_sol, price, reason)
        broker = self._get_live_broker()
        return await broker.execute_trade("SELL", mint, size_sol, price, reason, token_amount_raw=token_amount_raw)

    async def get_balance(self) -> float | None:
        """Get current wallet balance (live) or simulated balance (paper)."""
        if self.mode_manager.is_paper():
            return None  # Paper balance is managed by BotStats
            
        broker = self._get_live_broker()
        return await broker.get_balance()

    async def sell_all_async(
        self,
        mint: str,
        price: float,
        reason: str,
        size_sol: float | None = None,
        token_amount_raw: int = 0,
    ) -> TradeFill:
        """Async sell ALL tokens for a mint (Live) or a known size (Paper)."""
        if self.mode_manager.is_paper():
            if size_sol is None:
                return TradeFill(
                    success=False,
                    side="SELL",
                    mint=mint,
                    size_sol=0.0,
                    price=price,
                    reason=f"{reason}_PAPER_SIZE_REQUIRED",
                )
            return self.paper_broker.execute_trade("SELL", mint, size_sol, price, reason)

        broker = self._get_live_broker()
        # Pass -1.0 as size_sol to indicate SELL ALL logic
        return await broker.execute_trade("SELL", mint, -1.0, price, reason, token_amount_raw=token_amount_raw)

    async def get_token_balance(self, mint: str) -> int:
        """Get current token balance for a mint (live only)."""
        if self.mode_manager.is_paper():
            return 0
        broker = self._get_live_broker()
        return await broker.get_token_balance(mint)
