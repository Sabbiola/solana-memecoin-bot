"""
Trade Metrics Logger

JSONL logging for comprehensive trade analysis and KPI tracking.

Logs every trade with:
- Selection signals and scores
- Rugcheck results per phase
- EAS transitions
- Runner states
- Partial exits
- MFE/MAE/Realized PnL
- Exit reasons and events

Enables post-trade analysis:
- Scout success rate
- Runner capture efficiency
- Partial exit effectiveness
- Event bus quality
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class SelectionMetrics:
    """Selection phase metrics"""
    score: int = 0
    tx_rate_accel: float = 0.0
    wallet_influx_accel: float = 0.0
    hh_confirmed: bool = False
    curve_slope_accel: float = 0.0
    sell_absorption: bool = False
    consecutive_windows: int = 0
    baseline_samples: int = 0


@dataclass
class RugcheckMetrics:
    """Rugcheck results for logging"""
    score: int = 0
    dev_pct: float = 0.0
    top10_pct: float = 0.0
    mint_revoked: bool = True
    freeze_revoked: bool = True
    liquidity_usd: float = 0.0
    phase: str = ""


@dataclass
class EASMetrics:
    """EAS tracking metrics"""
    entry: float = 0.0
    current: float = 0.0
    min: float = 0.0
    max: float = 0.0
    trend: str = "STABLE"
    risk_transitions: List[str] = field(default_factory=list)


@dataclass
class PartialMetrics:
    """Single partial exit record"""
    pct: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""
    timestamp: float = 0.0


@dataclass
class TradeMetrics:
    """Complete trade record for JSONL logging"""
    # Identity
    ts: str = ""
    mint: str = ""
    symbol: str = ""
    venue: str = ""  # PUMPFUN|RAYDIUM|JUPITER
    
    # Phase tracking
    final_phase: str = ""  # SCOUT|CONFIRM|CONVICTION|MOONBAG
    phases_visited: List[str] = field(default_factory=list)
    
    # Entry
    scout_size_sol: float = 0.0
    confirm_size_sol: float = 0.0
    total_entry_sol: float = 0.0
    
    # Selection
    selection: SelectionMetrics = field(default_factory=SelectionMetrics)
    time_to_selection_sec: float = 0.0
    
    # Rugcheck
    scout_rugcheck: RugcheckMetrics = field(default_factory=RugcheckMetrics)
    confirm_rugcheck: RugcheckMetrics = field(default_factory=RugcheckMetrics)
    
    # EAS
    eas: EASMetrics = field(default_factory=EASMetrics)
    
    # Runner
    runner_states: List[str] = field(default_factory=list)
    max_runner_state: str = "NORMAL"
    
    # Partials
    partials: List[PartialMetrics] = field(default_factory=list)
    remaining_pct: float = 100.0
    
    # Performance
    mfe_pct: float = 0.0  # Max Favorable Excursion
    mae_pct: float = 0.0  # Max Adverse Excursion
    realized_pnl_pct: float = 0.0
    realized_pnl_sol: float = 0.0
    
    # Exit
    exit_reason: str = ""
    exit_events: List[str] = field(default_factory=list)
    
    # Duration
    total_duration_sec: float = 0.0
    scout_duration_sec: float = 0.0
    conviction_duration_sec: float = 0.0
    
    def to_dict(self) -> Dict:
        """Convert to dict for JSON serialization"""
        data = {}
        for key, value in asdict(self).items():
            if hasattr(value, 'to_dict'):
                data[key] = value.to_dict()
            else:
                data[key] = value
        return data


class TradeMetricsLogger:
    """
    JSONL logger for trade metrics and KPI analysis.
    
    File: logs/trade_metrics.jsonl
    """
    
    DEFAULT_LOG_DIR = "logs"
    DEFAULT_LOG_FILE = "trade_metrics.jsonl"
    
    def __init__(self, log_dir: str = None, log_file: str = None):
        self.log_dir = log_dir or self.DEFAULT_LOG_DIR
        self.log_file = log_file or self.DEFAULT_LOG_FILE
        self.log_path = Path(self.log_dir) / self.log_file
        
        # Ensure log directory exists
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        
        logger.info(f"📊 TradeMetricsLogger initialized: {self.log_path}")
    
    def log_trade(self, metrics: TradeMetrics):
        """Append trade record to JSONL file"""
        # Set timestamp if not set
        if not metrics.ts:
            metrics.ts = datetime.utcnow().isoformat() + "Z"
        
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                json.dump(metrics.to_dict(), f)
                f.write("\n")
            
            logger.info(
                f"📝 Logged trade: {metrics.symbol} | "
                f"Phase={metrics.final_phase} | "
                f"PnL={metrics.realized_pnl_pct:+.1f}%"
            )
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
    
    def load_trades(self, days: int = 7) -> List[TradeMetrics]:
        """Load trades from last N days"""
        trades = []
        cutoff = time.time() - (days * 24 * 60 * 60)
        
        if not self.log_path.exists():
            return trades
        
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        # Parse timestamp
                        ts_str = data.get("ts", "")
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if ts.timestamp() >= cutoff:
                                trades.append(data)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Failed to load trades: {e}")
        
        return trades
    
    # =========================================================================
    # KPI CALCULATIONS
    # =========================================================================
    
    def get_kpis(self, days: int = 7) -> Dict:
        """
        Calculate key performance indicators.
        
        Returns:
            dict with:
            - scout_success_rate
            - runner_capture_rate
            - avg_mfe_top10
            - efficiency_top10
            - scout_cost_per_day
            - false_exit_rate
        """
        trades = self.load_trades(days)
        
        if not trades:
            return {"error": "No trades found", "days": days}
        
        # Basic counts
        total = len(trades)
        scouts_only = [t for t in trades if t.get("final_phase") == "SCOUT"]
        confirmeds = [t for t in trades if t.get("final_phase") in ["CONFIRM", "CONVICTION", "MOONBAG"]]
        convictions = [t for t in trades if t.get("final_phase") in ["CONVICTION", "MOONBAG"]]
        moonbags = [t for t in trades if t.get("final_phase") == "MOONBAG"]
        
        # 1. Scout Success Rate
        scout_success_rate = len(confirmeds) / total * 100 if total > 0 else 0
        
        # 2. Runner Capture Rate (reached RUNNER or PARABOLIC state)
        runners = [
            t for t in trades 
            if t.get("max_runner_state") in ["RUNNER", "PARABOLIC"]
        ]
        runner_rate = len(runners) / len(convictions) * 100 if convictions else 0
        
        # 3. Average MFE for top 10% of trades
        mfes = [t.get("mfe_pct", 0) for t in trades]
        mfes_sorted = sorted(mfes, reverse=True)
        top_10_count = max(1, len(mfes_sorted) // 10)
        avg_mfe_top10 = sum(mfes_sorted[:top_10_count]) / top_10_count if mfes_sorted else 0
        
        # 4. Efficiency (realized / MFE) for top 10%
        top_10_trades = sorted(trades, key=lambda x: x.get("mfe_pct", 0), reverse=True)[:top_10_count]
        efficiencies = []
        for t in top_10_trades:
            mfe = t.get("mfe_pct", 0)
            realized = t.get("realized_pnl_pct", 0)
            if mfe > 0:
                efficiencies.append(realized / mfe * 100)
        efficiency_top10 = sum(efficiencies) / len(efficiencies) if efficiencies else 0
        
        # 5. Scout Cost Per Day
        scout_losses = [
            t.get("realized_pnl_sol", 0) 
            for t in scouts_only 
            if t.get("realized_pnl_sol", 0) < 0
        ]
        total_scout_loss = abs(sum(scout_losses))
        scout_cost_per_day = total_scout_loss / days if days > 0 else 0
        
        # 6. Dead Rate
        dead_rate = len(scouts_only) / total * 100 if total > 0 else 0
        
        # 7. Time to Selection (average)
        selection_times = [
            t.get("time_to_selection_sec", 0) 
            for t in confirmeds 
            if t.get("time_to_selection_sec", 0) > 0
        ]
        avg_selection_time = sum(selection_times) / len(selection_times) if selection_times else 0
        
        # 8. PnL Stats
        pnls = [t.get("realized_pnl_pct", 0) for t in trades]
        total_pnl_sol = sum(t.get("realized_pnl_sol", 0) for t in trades)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        
        return {
            "period_days": days,
            "total_trades": total,
            "scouts_only": len(scouts_only),
            "confirmeds": len(confirmeds),
            "convictions": len(convictions),
            "moonbags": len(moonbags),
            
            # Key rates
            "scout_success_rate_pct": round(scout_success_rate, 1),
            "dead_rate_pct": round(dead_rate, 1),
            "runner_capture_rate_pct": round(runner_rate, 1),
            "win_rate_pct": round(win_rate, 1),
            
            # Performance
            "avg_mfe_top10_pct": round(avg_mfe_top10, 1),
            "efficiency_top10_pct": round(efficiency_top10, 1),
            "total_pnl_sol": round(total_pnl_sol, 4),
            
            # Costs
            "scout_cost_per_day_sol": round(scout_cost_per_day, 4),
            "avg_selection_time_sec": round(avg_selection_time, 1),
            
            # Distribution
            "pnl_distribution": {
                "wins": len(wins),
                "losses": len(losses),
                "avg_win_pct": round(sum(wins) / len(wins), 1) if wins else 0,
                "avg_loss_pct": round(sum(losses) / len(losses), 1) if losses else 0,
            }
        }
    
    def get_selection_analysis(self, days: int = 7) -> Dict:
        """
        Analyze selection score distribution.
        
        Useful for tuning SELECTION_THRESHOLD.
        """
        trades = self.load_trades(days)
        
        # Group by selection score
        by_score = {}
        for t in trades:
            score = t.get("selection", {}).get("score", 0)
            if score not in by_score:
                by_score[score] = {"count": 0, "pnls": []}
            by_score[score]["count"] += 1
            by_score[score]["pnls"].append(t.get("realized_pnl_pct", 0))
        
        # Calculate stats per score
        score_stats = {}
        for score, data in by_score.items():
            pnls = data["pnls"]
            score_stats[score] = {
                "count": data["count"],
                "avg_pnl_pct": round(sum(pnls) / len(pnls), 1) if pnls else 0,
                "win_rate_pct": round(len([p for p in pnls if p > 0]) / len(pnls) * 100, 1) if pnls else 0
            }
        
        return {
            "period_days": days,
            "total_trades": len(trades),
            "by_selection_score": score_stats
        }
    
    def print_report(self, days: int = 7):
        """Print formatted KPI report to console"""
        kpis = self.get_kpis(days)
        
        if "error" in kpis:
            print(f"📊 No data: {kpis['error']}")
            return
        
        print("\n" + "=" * 60)
        print(f"📊 CONVEX STRATEGY KPIs ({kpis['period_days']} days)")
        print("=" * 60)
        
        print(f"\n📈 VOLUME:")
        print(f"   Total trades:      {kpis['total_trades']}")
        print(f"   Scouts only:       {kpis['scouts_only']} ({kpis['dead_rate_pct']:.1f}% dead)")
        print(f"   Confirmed:         {kpis['confirmeds']}")
        print(f"   Conviction:        {kpis['convictions']}")
        print(f"   Moonbags:          {kpis['moonbags']}")
        
        print(f"\n🎯 RATES:")
        print(f"   Scout success:     {kpis['scout_success_rate_pct']:.1f}%")
        print(f"   Runner capture:    {kpis['runner_capture_rate_pct']:.1f}%")
        print(f"   Win rate:          {kpis['win_rate_pct']:.1f}%")
        
        print(f"\n💰 PERFORMANCE:")
        print(f"   Total PnL:         {kpis['total_pnl_sol']:+.4f} SOL")
        print(f"   Avg MFE (top 10%): {kpis['avg_mfe_top10_pct']:.1f}%")
        print(f"   Efficiency:        {kpis['efficiency_top10_pct']:.1f}%")
        
        print(f"\n💸 COSTS:")
        print(f"   Scout cost/day:    {kpis['scout_cost_per_day_sol']:.4f} SOL")
        print(f"   Avg selection:     {kpis['avg_selection_time_sec']:.1f}s")
        
        dist = kpis['pnl_distribution']
        print(f"\n📊 PNL DISTRIBUTION:")
        print(f"   Wins:    {dist['wins']} (avg {dist['avg_win_pct']:+.1f}%)")
        print(f"   Losses:  {dist['losses']} (avg {dist['avg_loss_pct']:.1f}%)")
        
        print("\n" + "=" * 60)


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================

def create_trade_metrics_from_position(
    position,  # ConvexPosition
    realized_pnl_sol: float,
    realized_pnl_pct: float,
    mfe_pct: float,
    mae_pct: float,
    exit_reason: str,
    exit_events: List[str] = None
) -> TradeMetrics:
    """
    Create TradeMetrics from a ConvexPosition after exit.
    
    This is the main entry point for logging completed trades.
    """
    from .convex_state_machine import ConvexState
    
    # Build phases visited list
    phases = ["SCOUT"]
    if position.confirm_entry_sol > 0:
        phases.append("CONFIRM")
    if position.state in [ConvexState.CONVICTION, ConvexState.MOONBAG]:
        phases.append("CONVICTION")
    if position.state == ConvexState.MOONBAG:
        phases.append("MOONBAG")
    
    # Determine venue from phase
    # This would need the original opportunity's phase
    venue = "PUMPFUN"  # Default, should be passed in
    
    # Build selection metrics
    selection = SelectionMetrics(
        score=position.selection_score,
        consecutive_windows=position.consecutive_selection_windows,
        baseline_samples=position.baseline.samples
    )
    
    # Calculate durations from transitions
    scout_duration = 0
    conviction_duration = 0
    for t in position.transitions:
        if t.to_state == ConvexState.SCOUT_EVAL:
            scout_start = t.timestamp
        elif t.to_state == ConvexState.CONFIRM_ADD:
            scout_duration = t.timestamp - position.entry_time
        elif t.to_state == ConvexState.CONVICTION:
            conviction_start = t.timestamp
        elif t.to_state == ConvexState.EXITED and position.state == ConvexState.CONVICTION:
            conviction_duration = t.timestamp - conviction_start if 'conviction_start' in locals() else 0
    
    # Time to selection
    time_to_selection = scout_duration if scout_duration > 0 else position.get_total_duration()
    
    metrics = TradeMetrics(
        ts=datetime.utcnow().isoformat() + "Z",
        mint=position.mint,
        symbol=position.symbol,
        venue=venue,
        final_phase=position.state.value,
        phases_visited=phases,
        scout_size_sol=position.scout_entry_sol,
        confirm_size_sol=position.confirm_entry_sol,
        total_entry_sol=position.total_entry_sol,
        selection=selection,
        time_to_selection_sec=time_to_selection,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        realized_pnl_pct=realized_pnl_pct,
        realized_pnl_sol=realized_pnl_sol,
        exit_reason=exit_reason,
        exit_events=exit_events or [],
        total_duration_sec=position.get_total_duration(),
        scout_duration_sec=scout_duration,
        conviction_duration_sec=conviction_duration
    )
    
    return metrics


# Singleton instance
_metrics_logger = None

def get_metrics_logger() -> TradeMetricsLogger:
    """Get or create singleton TradeMetricsLogger"""
    global _metrics_logger
    if _metrics_logger is None:
        _metrics_logger = TradeMetricsLogger()
    return _metrics_logger
