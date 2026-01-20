from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from solana_bot.core.event_bus import Event
from solana_bot.core.models import Position
from solana_bot.utils.time import utc_ts

if TYPE_CHECKING:
    from solana_bot.config import Settings


class LPMonitor:
    """Monitor liquidity pool changes for a token.
    
    Modes:
    1. Real monitoring (ENABLE_LP_MONITOR=true): Tracks liquidity_usd changes.
       Triggers MAJOR event if liquidity drops > threshold.
    2. Simulation mode (SIM_LP_EVENT_PROBABILITY > 0): Random events for testing.
    3. Disabled (default): No events generated.
    """

    def __init__(
        self,
        settings: "Settings",
        seed: int | None = None,
    ) -> None:
        self.logger = logging.getLogger("solana_bot.lp_monitor")
        self.settings = settings
        
        # Simulation mode
        self._sim_probability = getattr(settings, 'SIM_LP_EVENT_PROBABILITY', 0.0)
        self._rng = random.Random(seed) if self._sim_probability > 0 else None
        
        # Real monitoring state: track liquidity per token
        self._liquidity: dict[str, float] = {}  # mint -> last known liquidity USD
        self._drop_threshold_pct = 0.30  # Trigger if LP drops > 30%
        self._min_liquidity_drop = 500.0  # Minimum absolute drop in USD

    def check(self, position: Position) -> Event | None:
        """Check for LP changes."""
        # Simulation mode: generate random events for testing
        if self._rng is not None and self._sim_probability > 0:
            if self._rng.random() < self._sim_probability:
                self.logger.debug("SIM: LP removal event triggered for %s", position.token.mint)
                return Event("MAJOR", "LP_REMOVAL", "LP change detected (simulated)", utc_ts())
        
        # Real monitoring
        if not self.settings.ENABLE_LP_MONITOR:
            return None
            
        mint = position.token.mint
        current_liquidity = position.token.liquidity_usd
        
        if current_liquidity <= 0:
            return None
            
        previous_liquidity = self._liquidity.get(mint)
        
        if previous_liquidity is not None and previous_liquidity > 0:
            drop_pct = (previous_liquidity - current_liquidity) / previous_liquidity
            drop_abs = previous_liquidity - current_liquidity
            
            if drop_pct > self._drop_threshold_pct and drop_abs > self._min_liquidity_drop:
                self.logger.warning(
                    "LP dropped %.1f%% for %s ($%.0f -> $%.0f)",
                    drop_pct * 100,
                    position.token.symbol,
                    previous_liquidity,
                    current_liquidity,
                )
                self._liquidity[mint] = current_liquidity
                return Event(
                    "MAJOR",
                    "LP_REMOVAL",
                    f"LP dropped {drop_pct*100:.1f}% (${drop_abs:.0f})",
                    utc_ts(),
                )
        
        # Update tracked liquidity
        self._liquidity[mint] = current_liquidity
        return None

    def clear(self, mint: str) -> None:
        """Clear tracking state for a closed position."""
        self._liquidity.pop(mint, None)
