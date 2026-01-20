from __future__ import annotations

from solana_bot.core.models import RunnerState


class RunnerProtection:
    def get_state(self, pnl_pct: float) -> RunnerState:
        if pnl_pct >= 2.0:
            return RunnerState.PARABOLIC
        if pnl_pct >= 0.8:
            return RunnerState.RUNNER
        if pnl_pct >= 0.3:
            return RunnerState.PRE_RUNNER
        return RunnerState.NORMAL
