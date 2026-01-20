from __future__ import annotations

from solana_bot.config import Settings
from solana_bot.core.models import NarrativePhase, RiskLevel, RunnerState


class TrailingCalculator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def compute(
        self,
        runner_state: RunnerState,
        risk_level: RiskLevel,
        narrative_phase: NarrativePhase,
        roi_pct: float = 0.0,
    ) -> float:
        """Calculate dynamic trailing stop percentage based on multivariables.
        
        Logic:
        - Base trailing stop from settings
        - Tightens as ROI increases (lock profit)
        - Tightens if risk is high
        - Loosens for early runners to give breathing room
        """
        base = self.settings.BASE_TRAILING_PCT
        
        # 1. ROI Multiplier: As profit increases, tighten the stop
        if roi_pct <= 0:
            # NEGATIVE PNL: Relax trailing stop significantly to let Hard Stop Loss take precedence
            # Logic: If we are losing, don't let a tight trailing stop wick us out.
            # Return 30% (0.30) to ensure it's looser than the default 25% SCOUT_STOP
            return 0.30
        elif roi_pct < 20:
            roi_mult = 1.2  # Looser stop early to avoid shakeout
        elif roi_pct < 50:
            roi_mult = 1.0  # Normal
        elif roi_pct < 100:
            roi_mult = 0.8  # Tighter to protect gains
        elif roi_pct < 200:
            roi_mult = 0.6  # Very tight
        else:
            roi_mult = 0.4  # Extremely tight for moonbags
            
        runner_mult = {
            RunnerState.NORMAL: 1.0,
            RunnerState.PRE_RUNNER: 0.9,
            RunnerState.RUNNER: 0.8,
            RunnerState.PARABOLIC: 0.6,
        }[runner_state]
        
        risk_mult = {
            RiskLevel.LOW: 1.0,
            RiskLevel.MEDIUM: 0.85,
            RiskLevel.HIGH: 0.7,
        }[risk_level]
        
        narrative_mult = {
            NarrativePhase.INFLOW: 1.0,
            NarrativePhase.NEUTRAL: 0.9,
            NarrativePhase.DISTRIBUTION: 0.8,
        }[narrative_phase]

        trailing = base * roi_mult * runner_mult * risk_mult * narrative_mult
        
        # Hard limits
        trailing = max(self.settings.MIN_TRAILING_PCT, trailing)
        trailing = min(self.settings.MAX_TRAILING_PCT, trailing)
        
        return trailing
