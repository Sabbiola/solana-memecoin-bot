from __future__ import annotations

from solana_bot.core.models import RiskLevel, SelectionSignals


class EASTracker:
    def __init__(self) -> None:
        self._low_to_medium = 1.15
        self._medium_to_low = 1.25
        self._medium_to_high = 0.92
        self._high_to_medium = 1.02

    def compute(self, signals: SelectionSignals, pnl_pct: float, exec_penalty: float = 0.0) -> float:
        base = 1.0 + (signals.score / 10.0)
        momentum = max(0.0, pnl_pct) * 0.4
        return max(0.1, base + momentum - exec_penalty)

    def update_risk_level(self, current: RiskLevel, eas_value: float) -> RiskLevel:
        if current == RiskLevel.LOW:
            return RiskLevel.MEDIUM if eas_value < self._low_to_medium else RiskLevel.LOW
        if current == RiskLevel.MEDIUM:
            if eas_value < self._medium_to_high:
                return RiskLevel.HIGH
            if eas_value > self._medium_to_low:
                return RiskLevel.LOW
            return RiskLevel.MEDIUM
        if eas_value > self._high_to_medium:
            return RiskLevel.MEDIUM
        return RiskLevel.HIGH
