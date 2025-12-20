"""
Trade Performance Tracker

Logs detailed metrics for every trade to enable data-driven optimization.

Tracks:
- Entry conditions (EAS, liquidity, score)
- Max Favorable Excursion (MFE) - best profit seen
- Max Adverse Excursion (MAE) - worst loss seen
- Exit reason and timing
- Realized PnL

Enables analysis:
- EV by EAS bucket
- EV by exit reason
- Which exits cut winners early
"""

import json
import logging
from dataclasses import dataclass, asdict
from typing import Optional
import time
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TradeMetrics:
    """Complete trade metrics for EV analysis"""
    # Entry
    mint: str
    symbol: str
    entry_time: float
    entry_price: float
    entry_sol: float
    eas_score: float  # Execution-Aware Asymmetry
    total_score: float  # Overall entry score
    liquidity_entry: float
    
    # Excursions
    mfe_pct: float = 0  # Max Favorable Excursion
    mae_pct: float = 0  # Max Adverse Excursion
    mfe_time: float = 0  # When MFE occurred
    mae_time: float = 0  # When MAE occurred
    
    # Exit
    exit_time: float = 0
    exit_price: float = 0
    exit_reason: str = ""
    realized_pnl_sol: float = 0
    realized_pnl_pct: float = 0
    
    # Metadata
    strategy_profile: str = ""
    runner_mode_triggered: bool = False


class TradePerformanceTracker:
    """
    Track detailed trade metrics for EV analysis.
    
    Usage:
        tracker = TradePerformanceTracker()
        trade_id = tracker.start_trade(mint,symbol, entry_data)
        tracker.update_excursion(trade_id, current_pnl_pct)
        tracker.end_trade(trade_id, exit_data)
        
        # Later: analyze
        ev_by_eas = tracker.analyze_ev_by_eas()
    """
    
    def __init__(self, log_file: str = "logs/trade_metrics.json"):
        self.log_file = Path(log_file)
        self.active_trades: dict[str, TradeMetrics] = {}
        self.completed_trades: list[TradeMetrics] = []
        
        # Load existing if exists
        if self.log_file.exists():
            try:
                with open(self.log_file, 'r') as f:
                    data = json.load(f)
                    self.completed_trades = [TradeMetrics(**t) for t in data]
                logger.info(f"📊 Loaded {len(self.completed_trades)} historical trades")
            except Exception as e:
                logger.warning(f"Failed to load trade metrics: {e}")
                
    def start_trade(
        self,
        mint: str,
        symbol: str,
        entry_sol: float,
        entry_price: float,
        eas_score: float,
        total_score: float,
        liquidity: float,
        strategy_profile: str = "EARLY"
    ) -> str:
        """
        Start tracking a new trade.
        
        Returns:
            trade_id (mint address)
        """
        metrics = TradeMetrics(
            mint=mint,
            symbol=symbol,
            entry_time=time.time(),
            entry_price=entry_price,
            entry_sol=entry_sol,
            eas_score=eas_score,
            total_score=total_score,
            liquidity_entry=liquidity,
            strategy_profile=strategy_profile
        )
        
        self.active_trades[mint] = metrics
        logger.info(
            f"📊 Tracking started: {symbol} | "
            f"EAS={eas_score:.2f} Score={total_score:.0f}"
        )
        return mint
        
    def update_excursion(self, mint: str, current_pnl_pct: float):
        """
        Update MFE/MAE for active trade.
        
        Args:
            mint: Trade ID
            current_pnl_pct: Current PnL percentage
        """
        if mint not in self.active_trades:
            return
            
        trade = self.active_trades[mint]
        current_time = time.time()
        
        # Update MFE (best profit)
        if current_pnl_pct > trade.mfe_pct:
            trade.mfe_pct = current_pnl_pct
            trade.mfe_time = current_time
            
        # Update MAE (worst loss)
        if current_pnl_pct < trade.mae_pct:
            trade.mae_pct = current_pnl_pct
            trade.mae_time = current_time
            
    def end_trade(
        self,
        mint: str,
        exit_price: float,
        exit_reason: str,
        realized_pnl_sol: float,
        realized_pnl_pct: float,
        runner_mode: bool = False
    ):
        """
        Complete a trade and save metrics.
        
        Args:
            mint: Trade ID
            exit_price: Exit price
            exit_reason: Why we exited
            realized_pnl_sol: Realized profit/loss in SOL
            realized_pnl_pct: Realized PnL percentage
            runner_mode: Was runner protection active
        """
        if mint not in self.active_trades:
            logger.warning(f"Trade {mint[:8]} not found in active trades")
            return
            
        trade = self.active_trades[mint]
        trade.exit_time = time.time()
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.realized_pnl_sol = realized_pnl_sol
        trade.realized_pnl_pct = realized_pnl_pct
        trade.runner_mode_triggered = runner_mode
        
        # Move to completed
        self.completed_trades.append(trade)
        del self.active_trades[mint]
        
        # Log summary
        efficiency = (realized_pnl_pct / trade.mfe_pct * 100) if trade.mfe_pct > 0 else 0
        logger.info(
            f"📊 Trade closed: {trade.symbol} | "
            f"PnL={realized_pnl_pct:+.1f}% | "
            f"MFE={trade.mfe_pct:.1f}% Efficiency={efficiency:.0f}% | "
            f"Exit={exit_reason}"
        )
        
        # Save to disk
        self._save()
        
    def _save(self):
        """Save completed trades to JSON"""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, 'w') as f:
                data = [asdict(t) for t in self.completed_trades]
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save trade metrics: {e}")
            
    def analyze_ev_by_eas(self) -> dict:
        """
        Analyze Expected Value by EAS bucket.
        
        Returns:
            Dict with EV analysis per bucket
        """
        buckets = {
            "<1.2": [],
            "1.2-1.6": [],
            "1.6-2.0": [],
            ">2.0": []
        }
        
        for trade in self.completed_trades:
            eas = trade.eas_score
            if eas < 1.2:
                bucket = "<1.2"
            elif eas < 1.6:
                bucket = "1.2-1.6"
            elif eas < 2.0:
                bucket = "1.6-2.0"
            else:
                bucket = ">2.0"
                
            buckets[bucket].append(trade)
            
        analysis = {}
        for bucket_name, trades in buckets.items():
            if not trades:
                continue
                
            avg_pnl = sum(t.realized_pnl_pct for t in trades) / len(trades)
            avg_mfe = sum(t.mfe_pct for t in trades) / len(trades)
            win_count = sum(1 for t in trades if t.realized_pnl_pct > 0)
            
            analysis[bucket_name] = {
                "trades": len(trades),
                "avg_pnl": avg_pnl,
                "avg_mfe": avg_mfe,
                "win_rate": win_count / len(trades) if trades else 0,
                "ev": avg_pnl  # Simplified EV
            }
            
        return analysis
        
    def analyze_ev_by_exit_reason(self) -> dict:
        """Analyze which exits are cutting winners vs protecting capital"""
        reasons = {}
        
        for trade in self.completed_trades:
            reason = trade.exit_reason
            if reason not in reasons:
                reasons[reason] = []
            reasons[reason].append(trade)
            
        analysis = {}
        for reason, trades in reasons.items():
            avg_realized = sum(t.realized_pnl_pct for t in trades) / len(trades)
            avg_mfe = sum(t.mfe_pct for t in trades) / len(trades)
            
            # Capture efficiency = realized / mfe
            if avg_mfe > 0:
                efficiency = (avg_realized / avg_mfe) * 100
            else:
                efficiency = 0
                
            analysis[reason] = {
                "trades": len(trades),
                "avg_realized": avg_realized,
                "avg_mfe": avg_mfe,
                "efficiency": efficiency
            }
            
        return analysis
        
    def calculate_regret(self) -> dict:
        """
        Regret-based analysis for trailing optimization.
        
        Regret = (MFE - Realized) / MFE
        = How much upside was left on table
        
        Returns:
            Dict with regret metrics by bucket
        """
        if not self.completed_trades:
            return {}
            
        # Overall regret
        total_regret = []
        tail_regret = []  # Top 10% MFE trades
        
        # Sort by MFE
        sorted_trades = sorted(
            self.completed_trades,
            key=lambda t: t.mfe_pct,
            reverse=True
        )
        
        top_10_count = max(int(len(sorted_trades) * 0.1), 1)
        top_trades = sorted_trades[:top_10_count]
        
        for trade in self.completed_trades:
            if trade.mfe_pct > 0:
                regret = (trade.mfe_pct - trade.realized_pnl_pct) / trade.mfe_pct
                total_regret.append(regret)
                
        for trade in top_trades:
            if trade.mfe_pct > 0:
                regret = (trade.mfe_pct - trade.realized_pnl_pct) / trade.mfe_pct
                tail_regret.append(regret)
                
        return {
            "median_regret": sorted(total_regret)[len(total_regret)//2] if total_regret else 0,
            "tail_regret": sorted(tail_regret)[len(tail_regret)//2] if tail_regret else 0,
            "high_regret_count": sum(1 for r in total_regret if r > 0.5),
            "total_trades": len(total_regret)
        }
        
    def get_trailing_adjustment_recommendation(self) -> dict:
        """
        Data-driven trailing adjustment based on regret analysis.
        
        Rules:
        - High tail regret (>0.55) → widen trailing
        - High MAE (<-18%) → tighten trailing
        
        Returns:
            Recommendations by state/risk bucket
        """
        if len(self.completed_trades) < 20:
            return {"status": "INSUFFICIENT_DATA", "min_trades": 20}
            
        regret_metrics = self.calculate_regret()
        
        # Calculate MAE stats
        avg_mae = sum(t.mae_pct for t in self.completed_trades) / len(self.completed_trades)
        
        recommendations = {}
        
        # Overall recommendation
        if regret_metrics["tail_regret"] > 0.55:
            recommendations["action"] = "WIDEN_TRAILING"
            recommendations["multiplier"] = 1.15
            recommendations["reason"] = f"High tail regret: {regret_metrics['tail_regret']:.2%}"
        elif avg_mae < -18:
            recommendations["action"] = "TIGHTEN_TRAILING"
            recommendations["multiplier"] = 0.90
            recommendations["reason"] = f"High MAE: {avg_mae:.1f}%"
        else:
            recommendations["action"] = "MAINTAIN"
            recommendations["multiplier"] = 1.0
            recommendations["reason"] = "Regret and MAE within acceptable range"
            
        recommendations["metrics"] = {
            "tail_regret": regret_metrics["tail_regret"],
            "median_regret": regret_metrics["median_regret"],
            "avg_mae": avg_mae,
            "sample_size": len(self.completed_trades)
        }
        
        return recommendations
