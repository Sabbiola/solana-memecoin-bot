"""
Trading Mode Manager

Handles switching between live trading and paper trading modes.
Provides unified interface that works with both Trader and PaperTrader.
"""

import logging
from typing import Tuple, Optional
from ..config import PAPER_TRADING_MODE, PAPER_INITIAL_BALANCE

logger = logging.getLogger(__name__)


class TradingModeManager:
    """
    Manages trading mode (live vs paper).
    
    Provides unified interface for trade execution that automatically
    routes to PaperTrader or real Trader based on configuration.
    """
    
    def __init__(self, real_trader, price_feed, telegram_notifier=None):
        """
        Initialize trading mode manager.
        
        Args:
            real_trader: Real Trader instance for live mode
            price_feed: PriceFeed instance for getting current prices
            telegram_notifier: TelegramNotifier for notifications (optional)
        """
        self.real_trader = real_trader
        self.price_feed = price_feed
        self.telegram_notifier = telegram_notifier
        self.paper_mode = PAPER_TRADING_MODE
        
        # Initialize paper trader if needed
        self.paper_trader = None
        if self.paper_mode:
            from ..paper_trading.paper_trader import PaperTrader
            self.paper_trader = PaperTrader(
                initial_balance=PAPER_INITIAL_BALANCE,
                slippage_pct=0.5,
                trade_log_path="paper_trades.json",
                telegram_notifier=telegram_notifier  # Pass telegram
            )
            logger.warning(
                f"🧪 PAPER TRADING MODE ENABLED - No real transactions! "
                f"Initial balance: {PAPER_INITIAL_BALANCE} SOL"
            )
        else:
            logger.info("💰 LIVE TRADING MODE - Real transactions enabled!")
    
    async def execute_buy(
        self,
        mint: str,
        symbol: str,
        amount_sol: float,
        phase: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Execute buy - routes to paper or live trader.
        
        Args:
            mint: Token mint address
            symbol: Token symbol  
            amount_sol: Amount in SOL
            phase: Token phase (BONDING_CURVE, PUMPSWAP, etc.)
            
        Returns:
            (success, signature)
        """
        if self.paper_mode and self.paper_trader:
            # Paper trading mode
            price = await self.price_feed.get_price(mint)
            if not price:
                logger.error(f"Cannot get price for {symbol}")
                return False, "NO_PRICE"
            
            return await self.paper_trader.execute_buy(
                mint=mint,
                symbol=symbol,
                amount_sol=amount_sol,
                current_price=price.price_sol,
                decimals=6,
                phase=phase or "UNKNOWN"
            )
        else:
            # Live trading mode
            # Note: execute_swap returns bool, we need to wrap it in tuple for consistency
            success = await self.real_trader.execute_swap(
                mint_str=mint,
                action="buy",
                amount_sol=amount_sol,
                phase=phase
            )
            # Return tuple format (success, signature)
            return (success, "LIVE_TX" if success else "FAILED")
    
    async def execute_sell(
        self,
        mint: str,
        symbol: str,
        amount_token: int = 0,
        sell_pct: float = 100.0,
        phase: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Execute sell - routes to paper or live trader.
        
        Args:
            mint: Token mint address
            symbol: Token symbol
            amount_token: Token amount (for live mode)
            sell_pct: Percentage to sell (0-100)
            phase: Token phase
            
        Returns:
            (success, signature)
        """
        if self.paper_mode and self.paper_trader:
            # Paper trading mode
            price = await self.price_feed.get_price(mint)
            if not price:
                logger.error(f"Cannot get price for {symbol}")
                return False, "NO_PRICE"
            
            return await self.paper_trader.execute_sell(
                mint=mint,
                current_price=price.price_sol,
                sell_pct=sell_pct
            )
        else:
            # Live trading mode
            # Note: execute_swap returns bool, we need to wrap it in tuple for consistency
            success = await self.real_trader.execute_swap(
                mint_str=mint,
                action="sell",
                amount_token=amount_token,
                phase=phase
            )
            # Return tuple format (success, signature)
            # In live mode, signature is not directly available from execute_swap
            return (success, "LIVE_TX" if success else "FAILED")
    
    def update_position_price(self, mint: str, current_price: float):
        """Update position with current price (paper mode only)."""
        if self.paper_mode and self.paper_trader:
            self.paper_trader.update_position(mint, current_price)
    
    def get_position_value(self, mint: str) -> dict:
        """
        Get current position value from paper trader.
        
        Returns:
            Dict with entry_sol, current_value, pnl_pct, token_amount, entry_price
            or empty dict if not found
        """
        if self.paper_mode and self.paper_trader:
            pos = self.paper_trader.positions.get(mint)
            if pos:
                return {
                    "entry_sol": pos.entry_sol,
                    "current_value": pos.current_value,
                    "pnl_pct": pos.pnl_pct,
                    "token_amount": pos.token_amount,
                    "entry_price": pos.entry_price,
                    "peak_value": pos.peak_value
                }
        return {}
    
    def get_paper_balance(self) -> float:
        """Get paper trading balance."""
        if self.paper_mode and self.paper_trader:
            return self.paper_trader.balance
        return 0.0
    
    def get_paper_metrics(self) -> dict:
        """Get paper trading performance metrics."""
        if self.paper_mode and self.paper_trader:
            return self.paper_trader.get_performance_metrics()
        return {}
    
    def print_paper_summary(self):
        """Print paper trading summary."""
        if self.paper_mode and self.paper_trader:
            self.paper_trader.print_summary()
