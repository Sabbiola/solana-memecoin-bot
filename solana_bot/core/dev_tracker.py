from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from solana_bot.core.event_bus import Event
from solana_bot.core.models import Position
from solana_bot.utils.time import utc_ts

if TYPE_CHECKING:
    from solana_bot.config import Settings
    from solana_bot.core.rpc_client import RPCClient


class DevTracker:
    """Track developer wallet activity for a token.
    
    Modes:
    1. Real monitoring (ENABLE_DEV_MONITOR=true): Tracks dev wallet balance changes
       via RPC. Triggers CRITICAL event if dev sells > threshold.
    2. Simulation mode (SIM_DEV_EVENT_PROBABILITY > 0): Random events for testing.
    3. Disabled (default): No events generated.
    """

    def __init__(
        self,
        settings: "Settings",
        rpc_client: "RPCClient | None" = None,
        seed: int | None = None,
    ) -> None:
        self.logger = logging.getLogger("solana_bot.dev_tracker")
        self.settings = settings
        self._rpc_client = rpc_client
        
        # Simulation mode
        self._sim_probability = getattr(settings, 'SIM_DEV_EVENT_PROBABILITY', 0.0)
        self._rng = random.Random(seed) if self._sim_probability > 0 else None
        
        # Real monitoring state: track dev holdings per token
        self._dev_holdings: dict[str, float] = {}  # mint -> last known dev holding %
        self._sell_threshold = 0.10  # Trigger if dev sells > 10% of their holdings

    def set_rpc_client(self, rpc_client: "RPCClient") -> None:
        """Set RPC client for real monitoring (called after bot initialization)."""
        self._rpc_client = rpc_client

    async def check_async(self, position: Position) -> Event | None:
        """Async check for real on-chain monitoring."""
        if not self.settings.ENABLE_DEV_MONITOR:
            return None
            
        # Real monitoring logic
        mint = position.token.mint
        current_dev_holding = position.token.metadata.get("dev_holding")
        
        if current_dev_holding is None:
            return None
            
        current_dev_holding = float(current_dev_holding)
        previous_holding = self._dev_holdings.get(mint)
        
        if previous_holding is not None:
            # Check if dev sold a significant portion
            sell_amount = previous_holding - current_dev_holding
            if sell_amount > self._sell_threshold:
                self.logger.warning(
                    "Dev sold %.1f%% of holdings for %s (%.1f%% -> %.1f%%)",
                    sell_amount * 100,
                    position.token.symbol,
                    previous_holding * 100,
                    current_dev_holding * 100,
                )
                self._dev_holdings[mint] = current_dev_holding
                return Event(
                    "CRITICAL",
                    "DEV_SELL",
                    f"Dev sold {sell_amount*100:.1f}% of holdings",
                    utc_ts(),
                )
        
        # Update tracked holding
        self._dev_holdings[mint] = current_dev_holding
        return None

    def check(self, position: Position) -> Event | None:
        """Sync check - simulation mode or disabled."""
        # Simulation mode: generate random events for testing
        if self._rng is not None and self._sim_probability > 0:
            if self._rng.random() < self._sim_probability:
                self.logger.debug("SIM: Dev sell event triggered for %s", position.token.mint)
                return Event("CRITICAL", "DEV_SELL", "Dev activity detected (simulated)", utc_ts())
        
        # If real monitoring enabled but no RPC, use metadata from token scanner
        if self.settings.ENABLE_DEV_MONITOR:
            mint = position.token.mint
            current_dev_holding = position.token.metadata.get("dev_holding")
            
            if current_dev_holding is not None:
                current_dev_holding = float(current_dev_holding)
                previous_holding = self._dev_holdings.get(mint)
                
                if previous_holding is not None:
                    sell_amount = previous_holding - current_dev_holding
                    if sell_amount > self._sell_threshold:
                        self.logger.warning(
                            "Dev sold %.1f%% of holdings for %s",
                            sell_amount * 100,
                            position.token.symbol,
                        )
                        self._dev_holdings[mint] = current_dev_holding
                        return Event(
                            "CRITICAL",
                            "DEV_SELL",
                            f"Dev sold {sell_amount*100:.1f}% of holdings",
                            utc_ts(),
                        )
                
                self._dev_holdings[mint] = current_dev_holding
        
        return None

    def clear(self, mint: str) -> None:
        """Clear tracking state for a closed position."""
        self._dev_holdings.pop(mint, None)
