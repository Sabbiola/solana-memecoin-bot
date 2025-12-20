"""
Paper Trader

Virtual trading system that simulates real trades without deploying capital.
Tracks virtual portfolio, simulates slippage, and logs all activity.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VirtualPosition:
    """Virtual position in paper trading portfolio"""
    mint: str
    symbol: str
    entry_price: float
    entry_sol: float
    token_amount: float
    entry_time: float
    decimals: int = 6
    
    # Tracking
    current_price: float = 0.0
    current_value: float = 0.0
    pnl_sol: float = 0.0
    pnl_pct: float = 0.0
    peak_value: float = 0.0


@dataclass
class VirtualTrade:
    """Record of a virtual trade"""
    timestamp: float
    action: str  # "BUY" or "SELL"
    mint: str
    symbol: str
    price: float
    amount_sol: float
    token_amount: float
    slippage_pct: float
    signature: str  # Virtual signature
    
    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat(),
            "action": self.action,
            "mint": self.mint,
            "symbol": self.symbol,
            "price": self.price,
            "amount_sol": self.amount_sol,
            "token_amount": self.token_amount,
            "slippage_pct": self.slippage_pct,
            "signature": self.signature
        }


class PaperTrader:
    """
    Paper trading system for risk-free testing.
    
    Features:
    - Virtual SOL balance
    - Simulated trade execution with slippage
    - Position tracking
    - Trade history logging
    - Performance metrics
    """
    
    def __init__(
        self,
        initial_balance: float = 10.0,
        slippage_pct: float = 0.5,
        trade_log_path: str = "paper_trades.json",
        telegram_notifier=None  # Optional Telegram notifier
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.slippage_pct = slippage_pct
        self.trade_log_path = Path(trade_log_path)
        self.telegram_notifier = telegram_notifier  # For notifications
        
        # Portfolio state
        self.positions: Dict[str, VirtualPosition] = {}
        self.trade_history: List[VirtualTrade] = []
        
        # Performance metrics
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        
        logger.info(
            f"📄 Paper Trading initialized: {initial_balance} SOL, "
            f"Slippage={slippage_pct}%"
        )
    
    def get_virtual_signature(self) -> str:
        """Generate a fake transaction signature."""
        import hashlib
        import secrets
        
        # Generate unique signature based on timestamp + random
        data = f"{time.time()}{secrets.token_hex(16)}".encode()
        return hashlib.sha256(data).hexdigest()[:64]
    
    async def execute_buy(
        self,
        mint: str,
        symbol: str,
        amount_sol: float,
        current_price: float,
        decimals: int = 6,
        phase: str = "UNKNOWN"
    ) -> Tuple[bool, str]:
        """
        Simulate a buy trade.
        
        Args:
            mint: Token mint address
            symbol: Token symbol
            amount_sol: Amount in SOL to spend
            current_price: Current token price in SOL
            decimals: Token decimals
            
        Returns:
            (success, signature)
        """
        # Check balance
        if amount_sol > self.balance:
            logger.warning(f"❌ Insufficient balance: {self.balance:.4f} SOL < {amount_sol:.4f} SOL")
            return False, "INSUFFICIENT_BALANCE"
        
        # Simulate slippage (buy at higher price)
        slippage_multiplier = 1 + (self.slippage_pct / 100)
        execution_price = current_price * slippage_multiplier
        
        # Calculate tokens received
        token_amount = amount_sol / execution_price
        
        # Deduct from balance
        self.balance -= amount_sol
        
        # Create position
        position = VirtualPosition(
            mint=mint,
            symbol=symbol,
            entry_price=execution_price,
            entry_sol=amount_sol,
            token_amount=token_amount,
            entry_time=time.time(),
            decimals=decimals,
            current_price=current_price,
            current_value=amount_sol,
            peak_value=amount_sol
        )
        
        self.positions[mint] = position
        
        # Generate signature
        signature = self.get_virtual_signature()
        
        # Log trade
        trade = VirtualTrade(
            timestamp=time.time(),
            action="BUY",
            mint=mint,
            symbol=symbol,
            price=execution_price,
            amount_sol=amount_sol,
            token_amount=token_amount,
            slippage_pct=self.slippage_pct,
            signature=signature
        )
        
        self.trade_history.append(trade)
        self.total_trades += 1
        self._save_trade(trade)
        
        logger.info(
            f"📄 PAPER BUY: {symbol} | "
            f"Amount: {amount_sol:.4f} SOL | "
            f"Price: {execution_price:.8f} SOL | "
            f"Tokens: {token_amount:,.0f} | "
            f"Balance: {self.balance:.4f} SOL"
        )
        
        # 🚨 Redundant notification removed - bot.py handles this with premium format
        # if self.telegram_notifier:
        #     try:
        #         from .telegram_notifier import PaperTradingNotifier
        #         notifier = PaperTradingNotifier(self.telegram_notifier)
        #         await notifier.send_paper_buy_alert(
        #             symbol=symbol,
        #             mint=mint,
        #             amount_sol=amount_sol,
        #             price=execution_price,
        #             token_amount=token_amount,
        #             balance_after=self.balance,
        #             slippage_pct=self.slippage_pct,
        #             phase=phase
        #         )
        #     except Exception as e:
        #         logger.error(f"Failed to send Telegram notification: {e}")
        
        return True, signature
    
    async def execute_sell(
        self,
        mint: str,
        current_price: float,
        sell_pct: float = 100.0
    ) -> Tuple[bool, str]:
        """
        Simulate a sell trade.
        
        Args:
            mint: Token mint address
            current_price: Current token price in SOL
            sell_pct: Percentage of position to sell (0-100)
            
        Returns:
            (success, signature)
        """
        # Check if position exists
        if mint not in self.positions:
            logger.warning(f"❌ No position found for {mint[:8]}")
            return False, "NO_POSITION"
        
        position = self.positions[mint]
        
        # Calculate amount to sell
        tokens_to_sell = position.token_amount * (sell_pct / 100)
        
        # Simulate slippage (sell at lower price)
        slippage_multiplier = 1 - (self.slippage_pct / 100)
        execution_price = current_price * slippage_multiplier
        
        # Calculate SOL received
        sol_received = tokens_to_sell * execution_price
        
        # Calculate PnL
        entry_value = position.entry_sol * (sell_pct / 100)
        pnl_sol = sol_received - entry_value
        pnl_pct = (pnl_sol / entry_value * 100) if entry_value > 0 else 0
        
        # Update balance
        self.balance += sol_received
        
        # Update metrics
        if pnl_sol > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        self.total_pnl += pnl_sol
        
        # Generate signature
        signature = self.get_virtual_signature()
        
        # Log trade
        trade = VirtualTrade(
            timestamp=time.time(),
            action="SELL",
            mint=mint,
            symbol=position.symbol,
            price=execution_price,
            amount_sol=sol_received,
            token_amount=tokens_to_sell,
            slippage_pct=self.slippage_pct,
            signature=signature
        )
        
        self.trade_history.append(trade)
        self.total_trades += 1
        self._save_trade(trade)
        
        # 🚨 Redundant notification removed - bot.py handles this
        # if self.telegram_notifier:
        #     try:
        #         from .telegram_notifier import PaperTradingNotifier
        #         notifier = PaperTradingNotifier(self.telegram_notifier)
        #         
        #         hold_time = None
        #         if sell_pct >= 100:
        #             hold_seconds = time.time() - position.entry_time
        #             hold_time = f"{int(hold_seconds // 60)}m" if hold_seconds < 3600 else f"{int(hold_seconds // 3600)}h"
        #         
        #         await notifier.send_paper_sell_alert(
        #             symbol=position.symbol,
        #             mint=mint,
        #             entry_sol=entry_value,
        #             exit_sol=sol_received,
        #             pnl_sol=pnl_sol,
        #             pnl_pct=pnl_pct,
        #             balance_after=self.balance,
        #             reason="Manual" if sell_pct < 100 else "Full Exit",
        #             hold_time=hold_time
        #         )
        #     except Exception as e:
        #         logger.error(f"Failed to send Telegram notification: {e}")
        
        # Remove or update position
        if sell_pct >= 100:
            del self.positions[mint]
        else:
            position.token_amount -= tokens_to_sell
            position.entry_sol -= entry_value
        
        return True, signature
    
    def update_position(self, mint: str, current_price: float):
        """Update position with current price."""
        if mint not in self.positions:
            return
        
        position = self.positions[mint]
        position.current_price = current_price
        position.current_value = position.token_amount * current_price
        position.pnl_sol = position.current_value - position.entry_sol
        position.pnl_pct = (position.pnl_sol / position.entry_sol * 100) if position.entry_sol > 0 else 0
        
        # Update peak
        if position.current_value > position.peak_value:
            position.peak_value = position.current_value
    
    def get_portfolio_value(self) -> float:
        """Calculate total portfolio value (balance + positions)."""
        positions_value = sum(p.current_value for p in self.positions.values())
        return self.balance + positions_value
    
    def get_performance_metrics(self) -> Dict:
        """Get performance statistics."""
        total_value = self.get_portfolio_value()
        total_return = total_value - self.initial_balance
        total_return_pct = (total_return / self.initial_balance * 100) if self.initial_balance > 0 else 0
        
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        return {
            "initial_balance": self.initial_balance,
            "current_balance": self.balance,
            "positions_value": sum(p.current_value for p in self.positions.values()),
            "total_value": total_value,
            "total_return": total_return,
            "total_return_pct": total_return_pct,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": win_rate,
            "total_pnl": self.total_pnl,
            "open_positions": len(self.positions)
        }
    
    def _save_trade(self, trade: VirtualTrade):
        """Save trade to JSON log file."""
        try:
            # Load existing trades
            trades = []
            if self.trade_log_path.exists():
                with open(self.trade_log_path, 'r') as f:
                    trades = json.load(f)
            
            # Append new trade
            trades.append(trade.to_dict())
            
            # Save
            with open(self.trade_log_path, 'w') as f:
                json.dump(trades, f, indent=2)
        
        except Exception as e:
            logger.error(f"Failed to save trade log: {e}")
    
    def print_summary(self):
        """Print performance summary."""
        metrics = self.get_performance_metrics()
        
        print("\n" + "=" * 60)
        print("📄 PAPER TRADING SUMMARY")
        print("=" * 60)
        print(f"Initial Balance:    {metrics['initial_balance']:.4f} SOL")
        print(f"Current Balance:    {metrics['current_balance']:.4f} SOL")
        print(f"Positions Value:    {metrics['positions_value']:.4f} SOL")
        print(f"Total Value:        {metrics['total_value']:.4f} SOL")
        print(f"Total Return:       {metrics['total_return']:+.4f} SOL ({metrics['total_return_pct']:+.2f}%)")
        print("-" * 60)
        print(f"Total Trades:       {metrics['total_trades']}")
        print(f"Winning Trades:     {metrics['winning_trades']}")
        print(f"Losing Trades:      {metrics['losing_trades']}")
        print(f"Win Rate:           {metrics['win_rate']:.1f}%")
        print(f"Total P&L:          {metrics['total_pnl']:+.4f} SOL")
        print(f"Open Positions:     {metrics['open_positions']}")
        print("=" * 60 + "\n")
