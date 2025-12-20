"""
Backtest Engine

Simulates trading strategy on historical data.
Tracks virtual portfolio, calculates metrics, generates reports.
"""

import asyncio
import logging
import time
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import statistics

from .historical_data_loader import HistoricalCandle, HistoricalDataLoader
from ..core.scalping_strategy import ScalpingStrategy, TokenData
from ..core.market_data import MarketDataCollector

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Single trade in backtest"""
    entry_time: int
    exit_time: Optional[int] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    amount_sol: float = 0.0
    pnl_sol: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = "OPEN"
    token: str = ""


@dataclass
class BacktestResult:
    """Results from a backtest run"""
    # Configuration
    strategy_name: str
    start_date: str
    end_date: str
    initial_balance: float
    
    # Performance metrics
    final_balance: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    
    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # Risk metrics
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    
    # Trade details
    trades: List[BacktestTrade] = field(default_factory=list)
    
    # Equity curve
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    
    def calculate_metrics(self):
        """Calculate performance metrics from trades."""
        if not self.trades:
            return
        
        # Basic stats
        self.total_trades = len(self.trades)
        self.winning_trades = len([t for t in self.trades if t.pnl_sol > 0])
        self.losing_trades = len([t for t in self.trades if t.pnl_sol < 0])
        self.win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        
        # P&L
        self.total_pnl = sum(t.pnl_sol for t in self.trades)
        self.final_balance = self.initial_balance + self.total_pnl
        self.total_pnl_pct = (self.total_pnl / self.initial_balance * 100) if self.initial_balance > 0 else 0
        
        # Drawdown
        peak = self.initial_balance
        max_dd = 0
        
        for point in self.equity_curve:
            equity = point['equity']
            if equity > peak:
                peak = equity
            
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        
        self.max_drawdown = max_dd
        self.max_drawdown_pct = (max_dd / peak * 100) if peak > 0 else 0
        
        # Sharpe Ratio (simplified)
        if len(self.trades) > 1:
            returns = [t.pnl_pct for t in self.trades]
            avg_return = statistics.mean(returns)
            std_return = statistics.stdev(returns)
            self.sharpe_ratio = (avg_return / std_return) if std_return > 0 else 0
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for reporting."""
        return {
            'strategy_name': self.strategy_name,
            'period': f"{self.start_date} to {self.end_date}",
            'initial_balance': self.initial_balance,
            'final_balance': self.final_balance,
            'total_pnl': self.total_pnl,
            'total_pnl_pct': self.total_pnl_pct,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': self.win_rate,
            'max_drawdown': self.max_drawdown,
            'max_drawdown_pct': self.max_drawdown_pct,
            'sharpe_ratio': self.sharpe_ratio
        }


class BacktestEngine:
    """
    Backtest engine for strategy validation.
    
    Simulates trading on historical data to:
    - Validate strategy profitability
    - Test parameter sensitivity
    - Calculate risk metrics
    - Generate performance reports
    """
    
    def __init__(
        self,
        strategy: ScalpingStrategy,
        initial_balance: float = 10.0,  # SOL
        trade_size_pct: float = 10.0,   # % of balance per trade
        max_positions: int = 1
    ):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.trade_size_pct = trade_size_pct
        self.max_positions = max_positions
        
        logger.info(
            f"💼 BacktestEngine initialized: "
            f"Balance={initial_balance} SOL, TradeSize={trade_size_pct}%, "
            f"MaxPos={max_positions}"
        )
    
    async def run_backtest(
        self,
        historical_data: List[HistoricalCandle],
        token_mint: str,
        start_date: str,
        end_date: str
    ) -> BacktestResult:
        """
        Run backtest on historical data.
        
        Args:
            historical_data: List of OHLCV candles
            token_mint: Token being tested
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            
        Returns:
            BacktestResult with metrics and trades
        """
        logger.info(f"🔬 Running backtest: {start_date} to {end_date} ({len(historical_data)} candles)")
        
        # Initialize result
        result = BacktestResult(
            strategy_name="Memecoin Scalping",
            start_date=start_date,
            end_date=end_date,
            initial_balance=self.initial_balance
        )
        
        # Portfolio state
        balance = self.initial_balance
        open_positions: List[BacktestTrade] = []
        
        # Market data collector for indicators
        market_data = MarketDataCollector(None)  # No session needed for backtest
        
        # Simulate candle by candle
        for i, candle in enumerate(historical_data):
            current_price = candle.close
            timestamp = candle.timestamp
            
            # Update indicators
            await market_data.get_token_indicators(
                token_mint,
                current_price,
                candle.volume
            )
            
            # Check exit conditions for open positions
            for position in open_positions[:]:
                # Simulate position tracking
                current_value_sol = (position.amount_sol / position.entry_price) * current_price
                pnl_pct = ((current_value_sol / position.amount_sol) - 1) * 100
                
                # Check exit (simplified - using SL/trailing)
                should_exit = False
                exit_reason = ""
                
                # Hard stop loss (-80%)
                if pnl_pct <= self.strategy.initial_stop_loss_pct:
                    should_exit = True
                    exit_reason = "STOP_LOSS"
                
                # Trailing stop (simplified - from peak) 
                # In real backtest, would track peak per position
                elif pnl_pct < -self.strategy.trailing_stop_pct:
                    should_exit = True
                    exit_reason = "TRAILING_STOP"
                
                if should_exit:
                    # Close position
                    position.exit_time = timestamp
                    position.exit_price = current_price
                    position.pnl_sol = current_value_sol - position.amount_sol
                    position.pnl_pct = pnl_pct
                    position.exit_reason = exit_reason
                    
                    # Return funds to balance
                    balance += current_value_sol
                    
                    # Move to closed trades
                    result.trades.append(position)
                    open_positions.remove(position)
                    
                    logger.debug(
                        f"📤 Exit: {exit_reason} @ ${current_price:.6f} "
                        f"P&L: {pnl_pct:+.1f}%"
                    )
            
            # Check entry conditions
            if len(open_positions) < self.max_positions and balance > 0:
                # Get indicators
                indicators = await market_data.get_token_indicators(
                    token_mint,
                    current_price,
                    candle.volume
                )
                
                # Create token data
                token_data = TokenData(
                    mint=token_mint,
                    price=current_price,
                    volume_24h=candle.volume,
                    liquidity_usd=0,  # Not available in backtest
                    price_change_24h=0,  # Calculate if needed
                    rsi=indicators.get('rsi'),
                    ema_20=indicators.get('ema_20'),
                    avg_volume=indicators.get('avg_volume')
                )
                
                # Check entry signal (skip validator checks in backtest)
                if token_data.rsi and token_data.rsi < self.strategy.rsi_oversold:
                    if token_data.avg_volume and candle.volume > token_data.avg_volume * self.strategy.volume_multiplier:
                        # Enter position
                        trade_size = balance * (self.trade_size_pct / 100)
                        
                        trade = BacktestTrade(
                            entry_time=timestamp,
                            entry_price=current_price,
                            amount_sol=trade_size,
                            token=token_mint
                        )
                        
                        open_positions.append(trade)
                        balance -= trade_size
                        
                        logger.debug(
                            f"📥 Entry: RSI={token_data.rsi:.1f} @ ${current_price:.6f} "
                            f"Size: {trade_size:.4f} SOL"
                        )
            
            # Record equity point
            equity = balance + sum((t.amount_sol / t.entry_price) * current_price for t in open_positions)
            result.equity_curve.append({
                'timestamp': timestamp,
                'equity': equity,
                'balance': balance,
                'positions': len(open_positions)
            })
        
        # Close any remaining positions at final price
        if historical_data:
            final_price = historical_data[-1].close
            final_timestamp = historical_data[-1].timestamp
            
            for position in open_positions:
                current_value_sol = (position.amount_sol / position.entry_price) * final_price
                position.exit_time = final_timestamp
                position.exit_price = final_price
                position.pnl_sol = current_value_sol - position.amount_sol
                position.pnl_pct = ((current_value_sol / position.amount_sol) - 1) * 100
                position.exit_reason = "END_OF_BACKTEST"
                
                balance += current_value_sol
                result.trades.append(position)
        
        # Calculate metrics
        result.calculate_metrics()
        
        logger.info(
            f"✅ Backtest complete: {result.total_trades} trades, "
            f"Win rate: {result.win_rate:.1f}%, P&L: {result.total_pnl_pct:+.1f}%"
        )
        
        return result
    
    def generate_report(self, result: BacktestResult) -> str:
        """
        Generate human-readable backtest report.
        
        Args:
            result: BacktestResult object
            
        Returns:
            Formatted markdown report
        """
        report = f"""# Backtest Report: {result.strategy_name}

## Period
{result.start_date} to {result.end_date}

## Portfolio Performance
| Metric | Value |
|--------|-------|
| Initial Balance | {result.initial_balance:.4f} SOL |
| Final Balance | {result.final_balance:.4f} SOL |
| **Total P&L** | **{result.total_pnl:+.4f} SOL ({result.total_pnl_pct:+.2f}%)** |

## Trade Statistics
| Metric | Value |
|--------|-------|
| Total Trades | {result.total_trades} |
| Winning Trades | {result.winning_trades} ({result.win_rate:.1f}%) |
| Losing Trades | {result.losing_trades} |
| **Win Rate** | **{result.win_rate:.1f}%** |

## Risk Metrics
| Metric | Value |
|--------|-------|
| Max Drawdown | {result.max_drawdown:.4f} SOL ({result.max_drawdown_pct:.2f}%) |
| Sharpe Ratio | {result.sharpe_ratio:.2f} |

## Trade List
"""
        
        # Add trade details
        for i, trade in enumerate(result.trades, 1):
            entry_dt = datetime.fromtimestamp(trade.entry_time).strftime("%Y-%m-%d %H:%M")
            exit_dt = datetime.fromtimestamp(trade.exit_time).strftime("%Y-%m-%d %H:%M") if trade.exit_time else "OPEN"
            
            pnl_emoji = "🟢" if trade.pnl_sol > 0 else "🔴"
            
            report += f"\n{i}. {pnl_emoji} {entry_dt} → {exit_dt} | "
            report += f"P&L: {trade.pnl_pct:+.1f}% | Reason: {trade.exit_reason}"
        
        report += f"\n\n---\n*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"
        
        return report
