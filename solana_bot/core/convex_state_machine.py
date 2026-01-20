from __future__ import annotations

from dataclasses import dataclass

from solana_bot.config import Settings
from solana_bot.core.models import Position, PositionState, SelectionSignals


@dataclass
class StateTransition:
    new_state: PositionState
    reason: str


class ConvexStateMachine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(
        self,
        position: Position,
        signals: SelectionSignals,
        pnl_pct: float,
        now_ts: float,
    ) -> StateTransition | None:
        if position.state == PositionState.SCOUT:
            if now_ts >= position.scout_deadline:
                return StateTransition(PositionState.EXIT, "SCOUT_TIMEOUT")

            # SAFETY STOP LOSS
            if pnl_pct <= -self.settings.SCOUT_STOP_LOSS_PCT:
                return StateTransition(PositionState.EXIT, "SCOUT_STOP_LOSS")

            if signals.score >= self.settings.CONVEX_SELECTION_THRESHOLD and signals.anti_fake_ok:
                position.selection_consecutive += 1
            else:
                position.selection_consecutive = 0

            if position.selection_consecutive >= 2:
                # FIX: Only confirm if we are actually in profit (>10%)
                # This prevents sizing up on "choppy" bounces that look like signals
                if pnl_pct >= 0.10:
                    return StateTransition(PositionState.CONFIRM, "SELECTION_CONFIRMED")

        if position.state == PositionState.CONFIRM:
            if pnl_pct <= -self.settings.CONFIRM_STOP_LOSS_PCT:
                return StateTransition(PositionState.EXIT, "CONFIRM_STOP")
            if signals.score >= self.settings.CONVICTION_SCORE_THRESHOLD and signals.anti_fake_ok:
                position.conviction_consecutive += 1
            else:
                position.conviction_consecutive = 0
            if position.conviction_consecutive >= self.settings.CONVICTION_CONSECUTIVE_WINDOWS:
                return StateTransition(PositionState.CONVICTION, "CONFIRM_SIGNAL_CONVICTION")
            if pnl_pct >= self.settings.CONFIRM_TO_CONVICTION_PNL_PCT:
                return StateTransition(PositionState.CONVICTION, "CONFIRM_TO_CONVICTION")

        if position.state == PositionState.CONVICTION:
            if pnl_pct <= -self.settings.CONVICTION_STOP_LOSS_PCT:
                return StateTransition(PositionState.EXIT, "CONVICTION_STOP")

        if position.state == PositionState.MOONBAG:
            if pnl_pct <= -self.settings.CONVICTION_STOP_LOSS_PCT:
                return StateTransition(PositionState.EXIT, "MOONBAG_STOP")

        return None
