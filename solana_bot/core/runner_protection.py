"""
Runner Protection Layer

CRITICAL FIX: Gradual states, not binary.

States:
- NORMAL: PnL < +30%
- PRE-RUNNER: +30% to +80% (early protection)
- RUNNER: +80% to +200% (strong protection)
- PARABOLIC: +200%+ (maximum protection)

Each state has different:
- Exit signal tolerance
- Trailing adjustment
- Drawdown acceptance
"""

import logging
from dataclasses import dataclass
from typing import Optional
from enum import Enum
import time

logger = logging.getLogger(__name__)


class RunnerState(Enum):
    """Runner protection states"""
    NORMAL = "NORMAL"
    PRE_RUNNER = "PRE_RUNNER"      # +30% to +80%
    RUNNER = "RUNNER"                # +80% to +200%
    PARABOLIC = "PARABOLIC"          # +200%+


@dataclass
class StateMetrics:
    """Metrics for current state"""
    state: RunnerState
    entry_time: float
    entry_pnl: float
    highest_pnl: float
    momentum_trend: str
    signals_active: dict


class RunnerProtectionLayer:
    """
    Gradual runner protection with multi-level states.
    
    Philosophy shift:
    - OLD: Binary (normal vs runner at +80%)
    - NEW: Gradual escalation as PnL grows
    
    Impact: Protect winners EARLIER, not after obvious.
    """
    
    def __init__(self):
        self.metrics = StateMetrics(
            state=RunnerState.NORMAL,
            entry_time=0,
            entry_pnl=0,
            highest_pnl=0,
            momentum_trend="FLAT",
            signals_active={}
        )
        
    def update(
        self,
        current_pnl_pct: float,
        liquidity_stable: bool = True,
        eas: float = 1.0
    ):
        """
        Update state based on current PnL.
        
        State transitions:
        - NORMAL → PRE_RUNNER at +30%
        - PRE_RUNNER → RUNNER at +80%
        - RUNNER → PARABOLIC at +200%
        """
        old_state = self.metrics.state
        
        # Determine new state
        if current_pnl_pct >= 200:
            new_state = RunnerState.PARABOLIC
        elif current_pnl_pct >= 80:
            new_state = RunnerState.RUNNER
        elif current_pnl_pct >= 30:
            new_state = RunnerState.PRE_RUNNER
        else:
            new_state = RunnerState.NORMAL
            
        # State transition logging
        if new_state != old_state:
            if self.metrics.entry_time == 0:
                self.metrics.entry_time = time.time()
                self.metrics.entry_pnl = current_pnl_pct
                
            logger.info(
                f"🏃 STATE TRANSITION: {old_state.value} → {new_state.value} | "
                f"PnL={current_pnl_pct:.1f}%"
            )
            
        self.metrics.state = new_state
        
        # Update highest
        if current_pnl_pct > self.metrics.highest_pnl:
            self.metrics.highest_pnl = current_pnl_pct
            self.metrics.momentum_trend = "UP"
        elif current_pnl_pct < self.metrics.highest_pnl * 0.9:
            self.metrics.momentum_trend = "DOWN"
        else:
            self.metrics.momentum_trend = "FLAT"
            
    def should_exit(
        self,
        lp_failed: bool = False,
        dev_dump: bool = False,
        retail_trap: bool = False,
        liquidity_drop: bool = False,
        price_crash: bool = False
    ) -> tuple[bool, str]:
        """
        State-dependent exit logic.
        
        NORMAL: Any signal exits (default behavior)
        PRE_RUNNER: Need 2/3 signals (early protection starts)
        RUNNER: Need 2/3 signals, ignore retail trap alone
        PARABOLIC: Need 3/4 signals (maximum tolerance)
        
        LP/Dev always exit (critical).
        """
        # Critical signals always exit
        if lp_failed or dev_dump:
            return (True, "CRITICAL_LP_DEV")
            
        signals = {
            "retail_trap": retail_trap,
            "liquidity": liquidity_drop,
            "crash": price_crash
        }
        
        self.metrics.signals_active = {k: v for k, v in signals.items() if v}
        active_count = sum(signals.values())
        
        state = self.metrics.state
        
        # State-specific exit rules
        if state == RunnerState.NORMAL:
            # Default: any signal exits
            if active_count >= 1:
                active = next((k for k, v in signals.items() if v), "unknown")
                return (True, f"NORMAL_EXIT_{active}")
            return (False, "")
            
        elif state == RunnerState.PRE_RUNNER:
            # Early protection: need 2/3
            if active_count >= 2:
                reason = f"PRE_RUNNER_CLUSTER_{active_count}"
                logger.warning(f"⚠️ {reason}: Protecting early runner")
                return (True, reason)
            return (False, "PRE_RUNNER_HOLD")
            
        elif state == RunnerState.RUNNER:
            # Strong protection: need 2/3, ignore retail trap alone
            if retail_trap and active_count == 1:
                logger.info("🏃 RUNNER ignoring retail trap alone")
                return (False, "RUNNER_RETAIL_IGNORED")
                
            if active_count >= 2:
                reason = f"RUNNER_CLUSTER_{active_count}"
                logger.warning(f"🚨 {reason}: Cluster exit")
                return (True, reason)
            return (False, "RUNNER_PROTECTED")
            
        elif state == RunnerState.PARABOLIC:
            # Maximum protection: need 3/4
            # (Only LP/Dev + 2 others would trigger)
            if active_count >= 3:
                reason = f"PARABOLIC_CLUSTER_{active_count}"
                logger.error(f"🔥 {reason}: Extreme cluster, exiting parabolic")
                return (True, reason)
                
            logger.info(f"🚀 PARABOLIC HOLD: {active_count}/3 signals, continuing")
            return (False, "PARABOLIC_PROTECTED")
            
        return (False, "")
        
    def get_dynamic_trailing(self, base_trailing_pct: float) -> float:
        """
        State-dependent trailing adjustment.
        
        Each state has progressively wider trailing:
        - NORMAL: base
        - PRE_RUNNER: base × 1.3
        - RUNNER: base × 1.5
        - PARABOLIC: base × 2.0
        
        + Momentum adjustment
        """
        state = self.metrics.state
        
        # Base state multiplier
        if state == RunnerState.PARABOLIC:
            state_multi = 2.0
        elif state == RunnerState.RUNNER:
            state_multi = 1.5
        elif state == RunnerState.PRE_RUNNER:
            state_multi = 1.3
        else:
            state_multi = 1.0
            
        # Momentum adjustment
        if self.metrics.momentum_trend == "UP":
            momentum_multi = 1.2
        elif self.metrics.momentum_trend == "DOWN":
            momentum_multi = 0.9
        else:
            momentum_multi = 1.0
            
        adjusted = base_trailing_pct * state_multi * momentum_multi
        
        if adjusted != base_trailing_pct:
            logger.debug(
                f"📊 Trailing adjusted: {base_trailing_pct:.1f}% → {adjusted:.1f}% | "
                f"State={state.value} Momentum={self.metrics.momentum_trend}"
            )
            
        return adjusted
        
    def get_status(self) -> dict:
        """Get current state and metrics"""
        return {
            "state": self.metrics.state.value,
            "highest_pnl": self.metrics.highest_pnl,
            "momentum": self.metrics.momentum_trend,
            "signals_active": self.metrics.signals_active,
            "time_in_state": time.time() - self.metrics.entry_time if self.metrics.entry_time > 0 else 0,
            "protection_level": self._get_protection_description()
        }
        
    def _get_protection_description(self) -> str:
        """Human-readable protection level"""
        state = self.metrics.state
        if state == RunnerState.PARABOLIC:
            return "MAXIMUM (need 3+ signals)"
        elif state == RunnerState.RUNNER:
            return "STRONG (need 2+ signals, ignore retail alone)"
        elif state == RunnerState.PRE_RUNNER:
            return "EARLY (need 2+ signals)"
        else:
            return "NONE (any signal exits)"
            
    def reset(self):
        """Reset state"""
        self.metrics = StateMetrics(
            state=RunnerState.NORMAL,
            entry_time=0,
            entry_pnl=0,
            highest_pnl=0,
            momentum_trend="FLAT",
            signals_active={}
        )

