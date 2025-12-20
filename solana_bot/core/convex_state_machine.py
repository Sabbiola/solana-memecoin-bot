"""
Convex State Machine

Phased entry architecture for capturing +200%/+1000% gains.

Philosophy:
- SCOUT: Pay small fee to discover if token is ALIVE
- CONFIRM: Add size only when Selection proves life
- CONVICTION: Full v12.3 mode for runners
- MOONBAG: Tail management for max upside

States:
- SCAN: Radar, no position
- SCOUT_OPEN: Micro-entry placed
- SCOUT_EVAL: 3-5 min observation window
- CONFIRM_ADD: Adding size after selection
- CONVICTION: Full trading mode (EAS, partials, events)
- MOONBAG: Tail riding after partials
- EXITED: Cooldown/blacklist
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


class ConvexState(Enum):
    """Position lifecycle states"""
    SCAN = "SCAN"
    SCOUT_OPEN = "SCOUT_OPEN"
    SCOUT_EVAL = "SCOUT_EVAL"
    CONFIRM_ADD = "CONFIRM_ADD"
    CONVICTION = "CONVICTION"
    MOONBAG = "MOONBAG"
    EXITED = "EXITED"


@dataclass
class SelectionSignals:
    """Signals for determining if token is ALIVE"""
    tx_rate_accel: float = 0.0          # current_txps / baseline_txps
    wallet_influx_accel: float = 0.0    # current_new_buyers / baseline_new_buyers
    hh_confirmed: bool = False          # higher high in last window
    curve_slope_accel: float = 0.0      # (pumpfun) progress/min acceleration
    sell_absorption: bool = False       # red candles bought back
    
    def calculate_score(self) -> int:
        """
        Returns 0-5 score based on active signals.
        Need ≥2 for CONFIRM_ADD transition.
        """
        score = 0
        if self.tx_rate_accel >= 1.8:
            score += 1
        if self.wallet_influx_accel >= 1.6:
            score += 1
        if self.hh_confirmed:
            score += 1
        if self.curve_slope_accel >= 1.5:
            score += 1
        if self.sell_absorption:
            score += 1
        return score


@dataclass
class BaselineMetrics:
    """Baseline metrics captured in first 45-60s for comparison"""
    tx_per_second: float = 0.0
    new_buyers_per_min: float = 0.0
    curve_progress_per_min: float = 0.0
    initial_price: float = 0.0
    highest_price: float = 0.0
    capture_time: float = 0.0
    samples: int = 0


@dataclass
class StateTransition:
    """Record of state change"""
    from_state: ConvexState
    to_state: ConvexState
    timestamp: float
    reason: str
    metrics: Dict = field(default_factory=dict)


@dataclass 
class ConvexPosition:
    """
    Tracks a position through all phases.
    
    Created at SCOUT_OPEN, destroyed at EXITED.
    """
    mint: str
    symbol: str
    state: ConvexState = ConvexState.SCOUT_OPEN
    
    # Entry tracking
    scout_entry_sol: float = 0.0
    scout_entry_time: float = 0.0
    scout_tokens: int = 0
    
    confirm_entry_sol: float = 0.0
    confirm_entry_time: float = 0.0
    confirm_tokens: int = 0
    
    total_entry_sol: float = 0.0
    total_tokens: int = 0
    
    # Selection tracking
    baseline: BaselineMetrics = field(default_factory=BaselineMetrics)
    selection_score: int = 0
    selection_score_history: List[int] = field(default_factory=list)
    consecutive_selection_windows: int = 0
    
    # State machine history
    transitions: List[StateTransition] = field(default_factory=list)
    entry_time: float = field(default_factory=time.time)
    
    # Rugcheck results per phase
    scout_rugcheck: Dict = field(default_factory=dict)
    confirm_rugcheck: Dict = field(default_factory=dict)
    
    # v12.3 components (activated in CONVICTION)
    eas_tracker: Any = None
    runner_layer: Any = None
    partial_manager: Any = None
    event_bus: Any = None
    narrative_analyzer: Any = None
    
    def transition_to(self, new_state: ConvexState, reason: str, metrics: Dict = None):
        """Record state transition"""
        transition = StateTransition(
            from_state=self.state,
            to_state=new_state,
            timestamp=time.time(),
            reason=reason,
            metrics=metrics or {}
        )
        self.transitions.append(transition)
        
        old_state = self.state
        self.state = new_state
        
        logger.info(
            f"🔄 [{self.symbol}] State: {old_state.value} → {new_state.value} | "
            f"Reason: {reason}"
        )
    
    def get_time_in_state(self) -> float:
        """Seconds since last state transition"""
        if not self.transitions:
            return time.time() - self.entry_time
        return time.time() - self.transitions[-1].timestamp
    
    def get_total_duration(self) -> float:
        """Total seconds since scout entry"""
        return time.time() - self.entry_time


# =========================================================================
# RUGCHECK PHASE THRESHOLDS
# =========================================================================

PHASE_THRESHOLDS = {
    "SCOUT": {
        "risk_max": 65,
        "dev_max": 35.0,
        "top10_max": 75.0,
        "freeze_required": True,   # Hard reject if present
        "mint_required": False,    # WARN ok for scout
        "min_liquidity_usd": 500,
    },
    "CONFIRM": {
        "risk_max": 50,
        "dev_max": 25.0,
        "top10_max": 65.0,
        "freeze_required": True,   # MUST be revoked
        "mint_required": True,     # MUST be revoked
        "min_liquidity_usd": 1000,
    },
    "CONVICTION": {
        "risk_max": 40,
        "dev_max": 15.0,
        "top10_max": 60.0,
        "freeze_required": True,
        "mint_required": True,
        "lp_locked_preferred": True,
        "min_liquidity_usd": 2000,
    }
}


def get_phase_thresholds(phase: str) -> Dict:
    """Get rugcheck thresholds for a specific phase"""
    return PHASE_THRESHOLDS.get(phase, PHASE_THRESHOLDS["SCOUT"])


# =========================================================================
# CONVEX STATE MACHINE
# =========================================================================

class ConvexStateMachine:
    """
    Manages position lifecycle through SCOUT → CONFIRM → CONVICTION → MOONBAG.
    
    Key principles:
    1. SCOUT is a scouting fee, not a real trade
    2. Only add size in CONFIRM if token proves alive
    3. CONVICTION activates full v12.3 (EAS, events, partials)
    4. MOONBAG is pure tail management
    """
    
    # Config (can be overridden from config/__init__.py)
    SCOUT_SIZE_SOL = 0.01
    CONFIRM_SIZE_SOL = 0.04
    MAX_TOTAL_SOL = 0.15
    SCOUT_TIMEOUT_SEC = 180  # 3 minutes
    SELECTION_THRESHOLD = 2  # Min signals
    SELECTION_WINDOWS_REQUIRED = 2  # Consecutive windows needed
    BASELINE_CAPTURE_SEC = 45  # Time to capture baseline
    EVAL_WINDOW_SEC = 30  # Selection evaluation window
    
    def __init__(self):
        self.positions: Dict[str, ConvexPosition] = {}
        self._load_config()
    
    def _load_config(self):
        """Load config values if available"""
        try:
            from ..config import (
                CONVEX_SCOUT_SIZE_SOL,
                CONVEX_CONFIRM_SIZE_SOL,
                CONVEX_MAX_TOTAL_SOL,
                CONVEX_SCOUT_TIMEOUT_SEC,
                CONVEX_SELECTION_THRESHOLD,
                CONVEX_SELECTION_WINDOWS
            )
            self.SCOUT_SIZE_SOL = CONVEX_SCOUT_SIZE_SOL
            self.CONFIRM_SIZE_SOL = CONVEX_CONFIRM_SIZE_SOL
            self.MAX_TOTAL_SOL = CONVEX_MAX_TOTAL_SOL
            self.SCOUT_TIMEOUT_SEC = CONVEX_SCOUT_TIMEOUT_SEC
            self.SELECTION_THRESHOLD = CONVEX_SELECTION_THRESHOLD
            self.SELECTION_WINDOWS_REQUIRED = CONVEX_SELECTION_WINDOWS
            logger.info("✅ ConvexStateMachine: Config loaded from config/__init__.py")
        except ImportError:
            logger.info("ℹ️ ConvexStateMachine: Using default config values")
    
    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================
    
    def create_position(self, mint: str, symbol: str) -> ConvexPosition:
        """Create new position in SCOUT_OPEN state"""
        position = ConvexPosition(
            mint=mint,
            symbol=symbol,
            state=ConvexState.SCOUT_OPEN,
            entry_time=time.time()
        )
        self.positions[mint] = position
        logger.info(f"🆕 Created ConvexPosition for {symbol} ({mint[:8]}...)")
        return position
    
    def get_position(self, mint: str) -> Optional[ConvexPosition]:
        """Get position by mint"""
        return self.positions.get(mint)
    
    def remove_position(self, mint: str):
        """Remove position (after EXITED)"""
        if mint in self.positions:
            symbol = self.positions[mint].symbol
            del self.positions[mint]
            logger.info(f"🗑️ Removed ConvexPosition for {symbol}")
    
    # =========================================================================
    # PHASE CHECKS
    # =========================================================================
    
    async def check_rugcheck_for_phase(
        self, 
        rugchecker, 
        mint: str, 
        phase: str
    ) -> Tuple[bool, Dict]:
        """
        Run rugcheck with phase-specific thresholds.
        
        Returns: (passed, result_dict)
        """
        thresholds = get_phase_thresholds(phase)
        
        try:
            # Use EARLY mode for SCOUT, stricter for CONFIRM/CONVICTION
            early_mode = (phase == "SCOUT")
            result = await rugchecker.check(mint, early_mode=early_mode)
            
            # Apply phase-specific threshold checks
            passed = True
            failures = []
            
            if result.risk_score > thresholds["risk_max"]:
                passed = False
                failures.append(f"Risk {result.risk_score} > {thresholds['risk_max']}")
            
            if result.dev_holding_pct > thresholds["dev_max"]:
                passed = False
                failures.append(f"Dev {result.dev_holding_pct:.1f}% > {thresholds['dev_max']}%")
            
            if result.top_10_holders_pct > thresholds["top10_max"]:
                passed = False
                failures.append(f"Top10 {result.top_10_holders_pct:.1f}% > {thresholds['top10_max']}%")
            
            # Authority checks
            if thresholds.get("freeze_required") and not result.freeze_authority_revoked:
                passed = False
                failures.append("Freeze authority NOT revoked")
            
            if thresholds.get("mint_required") and not result.mint_authority_revoked:
                if phase != "SCOUT":  # SCOUT allows mint authority with warning
                    passed = False
                    failures.append("Mint authority NOT revoked")
            
            # Liquidity check
            if result.liquidity_usd < thresholds.get("min_liquidity_usd", 0):
                passed = False
                failures.append(f"Liquidity ${result.liquidity_usd:.0f} < ${thresholds['min_liquidity_usd']}")
            
            result_dict = {
                "passed": passed,
                "failures": failures,
                "risk_score": result.risk_score,
                "dev_pct": result.dev_holding_pct,
                "top10_pct": result.top_10_holders_pct,
                "mint_revoked": result.mint_authority_revoked,
                "freeze_revoked": result.freeze_authority_revoked,
                "liquidity_usd": result.liquidity_usd,
                "phase": phase,
                "thresholds": thresholds
            }
            
            logger.info(
                f"🔍 [{phase}] Rugcheck: {'✅ PASSED' if passed else '❌ FAILED'} | "
                f"Risk={result.risk_score}, Dev={result.dev_holding_pct:.1f}%"
            )
            if failures:
                for f in failures:
                    logger.warning(f"   ⚠️ {f}")
            
            return passed, result_dict
            
        except Exception as e:
            logger.error(f"Rugcheck error for {mint[:8]}: {e}")
            return False, {"passed": False, "error": str(e), "phase": phase}
    
    # =========================================================================
    # SELECTION EVALUATION
    # =========================================================================
    
    def update_baseline(
        self, 
        position: ConvexPosition,
        tx_per_second: float,
        new_buyers_per_min: float,
        curve_progress_per_min: float,
        current_price: float
    ):
        """
        Update baseline metrics during first 45-60s.
        Uses exponential moving average for stability.
        """
        baseline = position.baseline
        now = time.time()
        
        # First sample
        if baseline.samples == 0:
            baseline.tx_per_second = tx_per_second
            baseline.new_buyers_per_min = new_buyers_per_min
            baseline.curve_progress_per_min = curve_progress_per_min
            baseline.initial_price = current_price
            baseline.highest_price = current_price
            baseline.capture_time = now
        else:
            # EMA with alpha = 0.3
            alpha = 0.3
            baseline.tx_per_second = alpha * tx_per_second + (1 - alpha) * baseline.tx_per_second
            baseline.new_buyers_per_min = alpha * new_buyers_per_min + (1 - alpha) * baseline.new_buyers_per_min
            baseline.curve_progress_per_min = alpha * curve_progress_per_min + (1 - alpha) * baseline.curve_progress_per_min
        
        # Track highest price for HH detection
        if current_price > baseline.highest_price:
            baseline.highest_price = current_price
        
        baseline.samples += 1
    
    def evaluate_selection(
        self,
        position: ConvexPosition,
        current_txps: float,
        current_new_buyers: float,
        current_curve_slope: float,
        current_price: float,
        had_red_candle_bought: bool = False
    ) -> Tuple[bool, SelectionSignals, int]:
        """
        Evaluate if token should transition from SCOUT_EVAL to CONFIRM_ADD.
        
        Returns: (should_confirm, signals, score)
        """
        baseline = position.baseline
        
        # Avoid division by zero
        base_txps = max(baseline.tx_per_second, 0.1)
        base_buyers = max(baseline.new_buyers_per_min, 0.1)
        base_slope = max(baseline.curve_progress_per_min, 0.01)
        
        # Calculate signals
        signals = SelectionSignals(
            tx_rate_accel=current_txps / base_txps,
            wallet_influx_accel=current_new_buyers / base_buyers,
            hh_confirmed=current_price > baseline.highest_price,
            curve_slope_accel=current_curve_slope / base_slope,
            sell_absorption=had_red_candle_bought
        )
        
        score = signals.calculate_score()
        
        # Update history
        position.selection_score = score
        position.selection_score_history.append(score)
        
        # Anti-flapping: require score >= threshold for N consecutive windows
        if score >= self.SELECTION_THRESHOLD:
            position.consecutive_selection_windows += 1
        else:
            position.consecutive_selection_windows = 0
        
        should_confirm = (
            position.consecutive_selection_windows >= self.SELECTION_WINDOWS_REQUIRED
        )
        
        logger.info(
            f"📊 [{position.symbol}] Selection: score={score}/5 | "
            f"consecutive={position.consecutive_selection_windows}/{self.SELECTION_WINDOWS_REQUIRED} | "
            f"confirm={should_confirm}"
        )
        logger.debug(
            f"   Signals: tx_accel={signals.tx_rate_accel:.2f}, "
            f"buyer_accel={signals.wallet_influx_accel:.2f}, "
            f"HH={signals.hh_confirmed}, curve_accel={signals.curve_slope_accel:.2f}, "
            f"absorption={signals.sell_absorption}"
        )
        
        return should_confirm, signals, score
    
    def should_scout_timeout(self, position: ConvexPosition) -> bool:
        """Check if SCOUT_EVAL should timeout"""
        if position.state != ConvexState.SCOUT_EVAL:
            return False
        
        time_in_eval = position.get_time_in_state()
        return time_in_eval >= self.SCOUT_TIMEOUT_SEC
    
    # =========================================================================
    # STATE TRANSITIONS
    # =========================================================================
    
    async def execute_scout_entry(
        self,
        position: ConvexPosition,
        trading_manager,
        phase: str = "BONDING_CURVE"
    ) -> Tuple[bool, Optional[str]]:
        """
        Execute SCOUT entry (micro-size).
        
        Returns: (success, signature)
        """
        try:
            result = await trading_manager.execute_buy(
                mint=position.mint,
                symbol=position.symbol,
                amount_sol=self.SCOUT_SIZE_SOL,
                phase=phase
            )
            
            if result and result[0]:
                success, signature = result
                position.scout_entry_sol = self.SCOUT_SIZE_SOL
                position.scout_entry_time = time.time()
                position.total_entry_sol = self.SCOUT_SIZE_SOL
                
                # Transition to SCOUT_EVAL
                position.transition_to(
                    ConvexState.SCOUT_EVAL,
                    "Scout entry filled",
                    {"entry_sol": self.SCOUT_SIZE_SOL, "signature": signature}
                )
                
                logger.info(
                    f"🎯 [{position.symbol}] SCOUT entry: {self.SCOUT_SIZE_SOL} SOL | "
                    f"TX: {signature[:12]}..."
                )
                return True, signature
            else:
                logger.warning(f"❌ [{position.symbol}] SCOUT entry failed")
                return False, None
                
        except Exception as e:
            logger.error(f"SCOUT entry error for {position.symbol}: {e}")
            return False, None
    
    async def execute_confirm_add(
        self,
        position: ConvexPosition,
        trading_manager,
        rugchecker,
        phase: str = "BONDING_CURVE"
    ) -> Tuple[bool, Optional[str]]:
        """
        Execute CONFIRM add (additional size after selection).
        
        First runs strict rugcheck, then adds size.
        Returns: (success, signature)
        """
        # Run CONFIRM-level rugcheck
        passed, rugcheck_result = await self.check_rugcheck_for_phase(
            rugchecker, position.mint, "CONFIRM"
        )
        position.confirm_rugcheck = rugcheck_result
        
        if not passed:
            logger.warning(
                f"⛔ [{position.symbol}] CONFIRM rugcheck FAILED - exiting scout"
            )
            position.transition_to(
                ConvexState.EXITED,
                "CONFIRM rugcheck failed",
                {"rugcheck": rugcheck_result}
            )
            return False, None
        
        # Calculate add size (capped at max)
        add_size = min(
            self.CONFIRM_SIZE_SOL,
            self.MAX_TOTAL_SOL - position.total_entry_sol
        )
        
        if add_size <= 0:
            logger.warning(f"⚠️ [{position.symbol}] No room to add (at max)")
            # Still transition to CONVICTION even without adding
            position.transition_to(
                ConvexState.CONVICTION,
                "At max size, proceeding to conviction",
                {}
            )
            return True, None
        
        try:
            result = await trading_manager.execute_buy(
                mint=position.mint,
                symbol=position.symbol,
                amount_sol=add_size,
                phase=phase
            )
            
            if result and result[0]:
                success, signature = result
                position.confirm_entry_sol = add_size
                position.confirm_entry_time = time.time()
                position.total_entry_sol += add_size
                
                # Transition to CONVICTION
                position.transition_to(
                    ConvexState.CONVICTION,
                    "CONFIRM add successful",
                    {
                        "add_sol": add_size,
                        "total_sol": position.total_entry_sol,
                        "signature": signature,
                        "selection_score": position.selection_score
                    }
                )
                
                logger.info(
                    f"🚀 [{position.symbol}] CONFIRM add: +{add_size} SOL | "
                    f"Total: {position.total_entry_sol} SOL | TX: {signature[:12]}..."
                )
                return True, signature
            else:
                logger.warning(f"❌ [{position.symbol}] CONFIRM add failed")
                # Don't exit - we still have scout position
                return False, None
                
        except Exception as e:
            logger.error(f"CONFIRM add error for {position.symbol}: {e}")
            return False, None
    
    def transition_to_moonbag(self, position: ConvexPosition, reason: str = "Partial exit done"):
        """Transition to MOONBAG after partial exit"""
        if position.state != ConvexState.CONVICTION:
            logger.warning(f"Cannot transition to MOONBAG from {position.state}")
            return
        
        position.transition_to(ConvexState.MOONBAG, reason)
    
    def exit_position(self, position: ConvexPosition, reason: str):
        """Mark position as EXITED"""
        position.transition_to(
            ConvexState.EXITED,
            reason,
            {
                "total_duration_sec": position.get_total_duration(),
                "final_state_before_exit": position.state.value
            }
        )
    
    # =========================================================================
    # UTILITY
    # =========================================================================
    
    def get_status(self, mint: str) -> Optional[Dict]:
        """Get human-readable status for position"""
        position = self.positions.get(mint)
        if not position:
            return None
        
        return {
            "symbol": position.symbol,
            "state": position.state.value,
            "total_entry_sol": position.total_entry_sol,
            "selection_score": position.selection_score,
            "consecutive_windows": position.consecutive_selection_windows,
            "time_in_state_sec": position.get_time_in_state(),
            "total_duration_sec": position.get_total_duration(),
            "transitions": len(position.transitions)
        }
    
    def get_all_active(self) -> List[Dict]:
        """Get all active positions (not EXITED)"""
        return [
            self.get_status(mint) 
            for mint, pos in self.positions.items()
            if pos.state != ConvexState.EXITED
        ]
