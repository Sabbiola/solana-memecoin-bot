from __future__ import annotations

from solana_bot.config import Settings
from solana_bot.core.models import Position, RiskLevel, RunnerState


class PartialExitManager:
    """Manages both risk-based and profit-based partial exits."""
    
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
    
    def maybe_take_partials(
        self,
        position: Position,
        risk_level: RiskLevel,
        runner_state: RunnerState,
        pnl_pct: float = 0.0,
    ) -> list[tuple[float, str]]:
        """Check for both risk-based and profit-based partial exits.
        
        Args:
            position: Current position
            risk_level: Current EAS risk level
            runner_state: Current runner state
            pnl_pct: Current PNL percentage (e.g., 0.75 = 75% profit)
            
        Returns:
            List of (exit_percentage, reason) tuples
        """
        partials: list[tuple[float, str]] = []
        
        if self.settings and self.settings.PARTIAL_EXIT_ENABLED:
            # Hybrid Moonbag Logic: Sell 50% at +100% (2x)
            profit_pct = pnl_pct * 100
            trigger_pct = self.settings.MOONBAG_SELL_TRIGGER_PCT * 100 # e.g. 100.0
            if profit_pct >= trigger_pct and "MOONBAG_ENTRY" not in position.partial_exit_flags:
                position.partial_exit_flags.add("MOONBAG_ENTRY")
                exit_pct = self.settings.MOONBAG_SELL_PCT # e.g. 0.50
                partials.append((exit_pct, "MOONBAG_50PCT_PROFIT"))
        
        # 2. RISK-BASED PARTIALS (Original logic - kept for safety)
        if risk_level == RiskLevel.MEDIUM and "MEDIUM" not in position.partial_exit_flags:
            position.partial_exit_flags.add("MEDIUM")
            partials.append((0.20, "PARTIAL_MEDIUM_RISK"))

        if risk_level == RiskLevel.HIGH and "HIGH" not in position.partial_exit_flags:
            position.partial_exit_flags.add("HIGH")
            partials.append((0.35, "PARTIAL_HIGH_RISK"))

        if (
            risk_level == RiskLevel.HIGH
            and runner_state == RunnerState.PARABOLIC
            and "PARABOLIC_HIGH" not in position.partial_exit_flags
        ):
            position.partial_exit_flags.add("PARABOLIC_HIGH")
            partials.append((0.25, "PARTIAL_PARABOLIC_HIGH"))

        return partials

