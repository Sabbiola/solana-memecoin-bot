from dataclasses import dataclass


@dataclass(frozen=True)
class RiskProfile:
    name: str
    entry_min_sol: float
    entry_max_sol: float
    target_pct: float
    stop_loss_pct: float
    token_age_max_sec: int
    min_liquidity_usd: float
    token_age_min_sec: int = 0  # Default 0 (no min age)


EARLY_PROFILE = RiskProfile(
    name="EARLY",
    entry_min_sol=0.001,
    entry_max_sol=0.005,
    target_pct=1.0,
    stop_loss_pct=0.80,
    token_age_max_sec=2 * 60 * 60,
    min_liquidity_usd=100.0,
)

STABLE_PROFILE = RiskProfile(
    name="STABLE",
    entry_min_sol=0.01,
    entry_max_sol=0.05,
    target_pct=0.30,
    stop_loss_pct=0.10,
    token_age_min_sec=0,            # Reverted to 0 (Sniper allowed)
    token_age_max_sec=60 * 60,
    min_liquidity_usd=5000.0,
)
