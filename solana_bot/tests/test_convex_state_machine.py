"""
Unit tests for Convex State Machine

Tests core functionality:
1. State transitions
2. Selection score calculation
3. Anti-flapping behavior
4. Phase-specific rugcheck thresholds
"""

import pytest
import asyncio
import time
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solana_bot.core.convex_state_machine import (
    ConvexStateMachine,
    ConvexState,
    ConvexPosition,
    SelectionSignals,
    BaselineMetrics,
    get_phase_thresholds,
    PHASE_THRESHOLDS
)


class TestSelectionSignals:
    """Test SelectionSignals scoring"""
    
    def test_empty_signals_score_zero(self):
        """All defaults should give score 0"""
        signals = SelectionSignals()
        assert signals.calculate_score() == 0
    
    def test_all_signals_score_five(self):
        """All positive signals should give score 5"""
        signals = SelectionSignals(
            tx_rate_accel=2.0,      # >= 1.8
            wallet_influx_accel=2.0, # >= 1.6
            hh_confirmed=True,
            curve_slope_accel=2.0,   # >= 1.5
            sell_absorption=True
        )
        assert signals.calculate_score() == 5
    
    def test_partial_signals(self):
        """Some signals should give partial score"""
        signals = SelectionSignals(
            tx_rate_accel=1.9,       # 1 point
            wallet_influx_accel=1.0, # 0 points (< 1.6)
            hh_confirmed=True,       # 1 point
            curve_slope_accel=0.5,   # 0 points (< 1.5)
            sell_absorption=False    # 0 points
        )
        assert signals.calculate_score() == 2
    
    def test_threshold_boundaries(self):
        """Test exact threshold values"""
        # Exactly at threshold
        signals = SelectionSignals(tx_rate_accel=1.8)
        assert signals.calculate_score() == 1
        
        # Just below threshold
        signals = SelectionSignals(tx_rate_accel=1.79)
        assert signals.calculate_score() == 0


class TestConvexPosition:
    """Test ConvexPosition state tracking"""
    
    def test_position_creation(self):
        """Position should start in SCOUT_OPEN state"""
        pos = ConvexPosition(
            mint="test_mint_12345",
            symbol="TEST"
        )
        assert pos.state == ConvexState.SCOUT_OPEN
        assert pos.total_entry_sol == 0.0
        assert len(pos.transitions) == 0
    
    def test_state_transition(self):
        """Transitions should be recorded"""
        pos = ConvexPosition(
            mint="test_mint",
            symbol="TEST"
        )
        pos.transition_to(ConvexState.SCOUT_EVAL, "Test transition")
        
        assert pos.state == ConvexState.SCOUT_EVAL
        assert len(pos.transitions) == 1
        assert pos.transitions[0].from_state == ConvexState.SCOUT_OPEN
        assert pos.transitions[0].to_state == ConvexState.SCOUT_EVAL
        assert pos.transitions[0].reason == "Test transition"
    
    def test_time_tracking(self):
        """Time in state should be tracked"""
        pos = ConvexPosition(
            mint="test_mint",
            symbol="TEST"
        )
        time.sleep(0.1)
        duration = pos.get_total_duration()
        assert duration >= 0.1


class TestConvexStateMachine:
    """Test ConvexStateMachine operations"""
    
    def test_create_position(self):
        """Should create and track position"""
        machine = ConvexStateMachine()
        pos = machine.create_position("mint123", "TEST")
        
        assert pos.mint == "mint123"
        assert pos.symbol == "TEST"
        assert machine.get_position("mint123") is pos
    
    def test_remove_position(self):
        """Should remove position"""
        machine = ConvexStateMachine()
        machine.create_position("mint123", "TEST")
        machine.remove_position("mint123")
        
        assert machine.get_position("mint123") is None
    
    def test_baseline_update(self):
        """Baseline should accumulate samples"""
        machine = ConvexStateMachine()
        pos = machine.create_position("mint123", "TEST")
        
        machine.update_baseline(
            pos,
            tx_per_second=1.0,
            new_buyers_per_min=10,
            curve_progress_per_min=0.5,
            current_price=0.001
        )
        
        assert pos.baseline.samples == 1
        assert pos.baseline.tx_per_second > 0
    
    def test_selection_evaluation_anti_flapping(self):
        """Should require consecutive windows for confirmation"""
        machine = ConvexStateMachine()
        machine.SELECTION_WINDOWS_REQUIRED = 2
        machine.SELECTION_THRESHOLD = 2
        
        pos = machine.create_position("mint123", "TEST")
        pos.baseline.tx_per_second = 1.0
        pos.baseline.new_buyers_per_min = 10
        pos.baseline.curve_progress_per_min = 0.5
        pos.baseline.highest_price = 0.001
        pos.baseline.samples = 10
        
        # First window with high score
        should_confirm, signals, score = machine.evaluate_selection(
            pos,
            current_txps=2.0,       # High relative to baseline
            current_new_buyers=20,
            current_curve_slope=1.0,
            current_price=0.002,    # Higher high
            had_red_candle_bought=True
        )
        
        assert score >= 2
        assert should_confirm is False  # Need 2 consecutive windows
        assert pos.consecutive_selection_windows == 1
        
        # Second window with high score
        should_confirm, signals, score = machine.evaluate_selection(
            pos,
            current_txps=2.5,
            current_new_buyers=25,
            current_curve_slope=1.2,
            current_price=0.003,
            had_red_candle_bought=True
        )
        
        assert score >= 2
        assert should_confirm is True  # Now we have 2 consecutive
        assert pos.consecutive_selection_windows == 2
    
    def test_selection_resets_on_low_score(self):
        """Consecutive window count should reset on low score"""
        machine = ConvexStateMachine()
        machine.SELECTION_WINDOWS_REQUIRED = 2
        machine.SELECTION_THRESHOLD = 3  # Need 3+ signals
        
        pos = machine.create_position("mint123", "TEST")
        pos.baseline.tx_per_second = 1.0
        pos.baseline.new_buyers_per_min = 10
        pos.baseline.curve_progress_per_min = 0.5
        pos.baseline.highest_price = 0.001
        pos.baseline.samples = 10
        
        # First window with score 1 (way below threshold of 3)
        # Only tx_rate_accel triggers (1.9 >= 1.8)
        machine.evaluate_selection(
            pos,
            current_txps=1.9,         # >= 1.8, gives 1 point
            current_new_buyers=5,     # 0.5x baseline, gives 0 points
            current_curve_slope=0.2,  # 0.4x baseline, gives 0 points
            current_price=0.0005,     # Below highest, no HH
            had_red_candle_bought=False
        )
        
        # Count should be 0 (score 1 < threshold 3)
        assert pos.consecutive_selection_windows == 0


class TestPhaseThresholds:
    """Test phase-specific rugcheck thresholds"""
    
    def test_scout_thresholds(self):
        """SCOUT should have permissive thresholds"""
        t = get_phase_thresholds("SCOUT")
        assert t["risk_max"] == 65
        assert t["dev_max"] == 35.0
        assert t["mint_required"] is False  # WARN ok
    
    def test_confirm_thresholds(self):
        """CONFIRM should be stricter"""
        t = get_phase_thresholds("CONFIRM")
        assert t["risk_max"] == 50
        assert t["dev_max"] == 25.0
        assert t["mint_required"] is True
    
    def test_conviction_thresholds(self):
        """CONVICTION should be strictest"""
        t = get_phase_thresholds("CONVICTION")
        assert t["risk_max"] == 40
        assert t["dev_max"] == 15.0
        assert t.get("lp_locked_preferred") is True
    
    def test_unknown_phase_defaults_to_scout(self):
        """Unknown phase should default to SCOUT"""
        t = get_phase_thresholds("UNKNOWN_PHASE")
        assert t == PHASE_THRESHOLDS["SCOUT"]


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
