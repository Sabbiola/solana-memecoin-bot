from __future__ import annotations

from solana_bot.core.models import NarrativePhase, Position, SelectionSignals


class NarrativeAnalyzer:
    def analyze(self, position: Position, signals: SelectionSignals, pnl_pct: float) -> NarrativePhase:
        if pnl_pct > 0.2 and signals.score >= 4:
            return NarrativePhase.INFLOW
        if pnl_pct < 0 and signals.score <= 2:
            return NarrativePhase.DISTRIBUTION
        return NarrativePhase.NEUTRAL
