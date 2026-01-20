from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    BONDING_CURVE = "BONDING_CURVE"
    PUMPSWAP = "PUMPSWAP"
    RAYDIUM = "RAYDIUM"
    JUPITER = "JUPITER"
    UNKNOWN = "UNKNOWN"


class PositionState(str, Enum):
    SCOUT = "SCOUT"
    CONFIRM = "CONFIRM"
    CONVICTION = "CONVICTION"
    MOONBAG = "MOONBAG"
    EXIT = "EXIT"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RunnerState(str, Enum):
    NORMAL = "NORMAL"
    PRE_RUNNER = "PRE_RUNNER"
    RUNNER = "RUNNER"
    PARABOLIC = "PARABOLIC"


class NarrativePhase(str, Enum):
    INFLOW = "INFLOW"
    DISTRIBUTION = "DISTRIBUTION"
    NEUTRAL = "NEUTRAL"


@dataclass
class TokenInfo:
    mint: str
    symbol: str
    age_sec: int
    liquidity_usd: float
    volume_usd: float
    price: float
    source: str
    phase: Phase = Phase.UNKNOWN
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RugcheckResult:
    is_safe: bool
    risk_score: float
    risk_level: str
    flags: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelectionSignals:
    tx_rate_accel: float
    wallet_influx_accel: float
    hh_confirmed: bool
    curve_slope_accel: float
    sell_absorption: bool
    score: int
    anti_fake_ok: bool


@dataclass
class TradeFill:
    mint: str
    side: str
    size_sol: float
    price: float
    ts: float = 0.0
    reason: str = ""
    success: bool = True
    signature: str = ""  # Transaction signature for live trades
    token_amount_raw: int = 0  # Raw token amount (for sells)


@dataclass
class Position:
    token: TokenInfo
    state: PositionState
    size_sol: float
    entry_price: float
    opened_at: float
    last_update: float
    peak_price: float
    last_price: float
    scout_deadline: float
    initial_size_sol: float
    selection_score: int = 0
    selection_consecutive: int = 0
    conviction_consecutive: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    runner_state: RunnerState = RunnerState.NORMAL
    narrative_phase: NarrativePhase = NarrativePhase.NEUTRAL
    eas_value: float = 1.0
    eas_risk_level: RiskLevel = RiskLevel.LOW
    partial_exit_flags: set[str] = field(default_factory=set)
    realized_pct: float = 0.0
    realized_pnl_sol: float = 0.0
    insightx_data: dict[str, Any] = field(default_factory=dict)
    bounce_reentry_count: int = 0  # Track how many times this position is a bounce re-entry
    is_breakeven: bool = False  # Track if break-even stop has been activated
    token_amount_raw: int = 0  # Raw token amount (with decimals) received from buy - for instant sells


@dataclass
class BotStats:
    daily_loss_sol: float = 0.0
    daily_trades: int = 0
    cash_sol: float = 0.0
    scout_failures: int = 0
    realized_pnl_sol: float = 0.0
    trades_won: int = 0
    trades_lost: int = 0
