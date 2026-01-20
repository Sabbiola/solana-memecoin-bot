from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from solana_bot.config import Settings
from solana_bot.core.models import Position, PositionState, TokenInfo, Phase, RiskLevel, RunnerState, NarrativePhase

if TYPE_CHECKING:
    from solana_bot.core.models import BotStats


class PositionMonitor:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.positions")
        self.snapshot_path = Path(settings.POSITION_SNAPSHOT_PATH)
        self._last_log_ts = 0.0

    def maybe_log(
        self,
        positions: dict[str, Position],
        now: float,
        stats: "BotStats | None" = None,
    ) -> None:
        if now - self._last_log_ts < self.settings.POSITION_LOG_EVERY_SEC:
            return
        self._last_log_ts = now

        snapshot = []
        for position in positions.values():
            entry_price = position.entry_price or 0.0
            pnl_pct = (position.last_price / entry_price) - 1.0 if entry_price else 0.0
            
            # Extract rich metadata from token
            meta = position.token.metadata or {}
            
            snapshot.append(
                {
                    "mint": position.token.mint,
                    "symbol": position.token.symbol,
                    "state": position.state.value,
                    "size_sol": position.size_sol,
                    "entry_price": position.entry_price,
                    "last_price": position.last_price,
                    "peak_price": position.peak_price,
                    "pnl_pct": pnl_pct,
                    "opened_at": position.opened_at,
                    # Token Metrics (for dashboard detail panel)
                    "market_cap": meta.get("market_cap", 0),
                    "liquidity": position.token.liquidity_usd,
                    "volume_m5": meta.get("volume_m5", 0),
                    "volume_h1": meta.get("volume_h1", 0),
                    "volume_h24": meta.get("volume_h24", 0),
                    "txns_m5_buys": meta.get("txns_m5_buys", 0),
                    "txns_m5_sells": meta.get("txns_m5_sells", 0),
                    "txns_h1_buys": meta.get("txns_h1_buys", 0),
                    "txns_h1_sells": meta.get("txns_h1_sells", 0),
                    "price_change_m5": meta.get("price_change_m5", 0),
                    "price_change_h1": meta.get("price_change_h1", 0),
                    "price_change_h24": meta.get("price_change_h24", 0),
                    "dev_holding": meta.get("dev_holding", 0),
                    "top10_holding": meta.get("top10_holding", 0),
                    "holder_count": meta.get("holder_count", 0),
                    "insightx": position.insightx_data,
                    "dex_id": meta.get("dex_id", ""),
                    "phase": position.token.phase.value if hasattr(position.token.phase, 'value') else str(position.token.phase),
                    "bonding_pct": meta.get("bonding_pct", 0),
                }
            )

        # Build payload with account info
        payload = {"ts": now, "open_positions": snapshot}
        
        # Add account stats if provided
        if stats:
            total_trades = stats.trades_won + stats.trades_lost
            win_rate = (stats.trades_won / total_trades * 100) if total_trades > 0 else 0.0
            payload["account"] = {
                "paper_mode": self.settings.PAPER_TRADING_MODE,
                "initial_balance": self.settings.SIM_STARTING_BALANCE_SOL,
                "current_balance": stats.cash_sol,
                "realized_pnl_sol": stats.realized_pnl_sol,
                "daily_loss_sol": stats.daily_loss_sol,
                "trades_total": total_trades,
                "trades_won": stats.trades_won,
                "trades_lost": stats.trades_lost,
                "win_rate": win_rate,
            }
        
        self._write_snapshot(payload)
        # self.logger.info("Positions open=%d", len(snapshot))

    def _write_snapshot(self, payload: dict) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load_positions(self) -> dict[str, Position]:
        """Load positions from snapshot file. Returns empty dict if file doesn't exist or is invalid."""
        if not self.snapshot_path.exists():
            self.logger.info("No positions snapshot found, starting fresh")
            return {}
        
        try:
            content = self.snapshot_path.read_text(encoding="utf-8")
            data = json.loads(content)
            positions_data = data.get("open_positions", [])
            
            if not positions_data:
                self.logger.info("Positions snapshot is empty")
                return {}
            
            positions: dict[str, Position] = {}
            for pos_data in positions_data:
                try:
                    # Reconstruct TokenInfo
                    token = TokenInfo(
                        mint=pos_data["mint"],
                        symbol=pos_data.get("symbol", "???"),
                        age_sec=0,
                        liquidity_usd=pos_data.get("liquidity", 0.0),
                        volume_usd=pos_data.get("volume_h24", 0.0),
                        price=pos_data.get("last_price", 0.0),
                        source="RESTORED",
                        phase=Phase(pos_data.get("phase", "UNKNOWN")),
                        metadata={
                            "market_cap": pos_data.get("market_cap", 0),
                            "volume_m5": pos_data.get("volume_m5", 0),
                            "volume_h1": pos_data.get("volume_h1", 0),
                            "volume_h24": pos_data.get("volume_h24", 0),
                            "is_restored": True,
                        }
                    )
                    
                    # Reconstruct Position
                    state_str = pos_data.get("state", "SCOUT")
                    state = PositionState(state_str) if state_str in [s.value for s in PositionState] else PositionState.SCOUT
                    
                    position = Position(
                        token=token,
                        state=state,
                        size_sol=pos_data.get("size_sol", 0.0),
                        entry_price=pos_data.get("entry_price", 0.0),
                        opened_at=pos_data.get("opened_at", 0.0),
                        last_update=data.get("ts", 0.0),
                        peak_price=pos_data.get("peak_price", 0.0),
                        last_price=pos_data.get("last_price", 0.0),
                        scout_deadline=pos_data.get("opened_at", 0.0) + self.settings.CONVEX_SCOUT_TIMEOUT_SEC,
                        initial_size_sol=pos_data.get("size_sol", 0.0),
                    )
                    
                    positions[pos_data["mint"]] = position
                    self.logger.info(
                        "Restored position: %s (%s) - Size: %.4f SOL, State: %s",
                        token.symbol, token.mint[:8], position.size_sol, state.value
                    )
                    
                except Exception as e:
                    self.logger.warning("Failed to restore position %s: %s", pos_data.get("mint", "?"), e)
                    continue
            
            self.logger.info("Restored %d positions from snapshot", len(positions))
            return positions
            
        except Exception as e:
            self.logger.error("Failed to load positions snapshot: %s", e)
            return {}

    def clear_snapshot(self) -> None:
        """Clear the positions snapshot file."""
        if self.snapshot_path.exists():
            empty_payload = {"ts": 0, "open_positions": []}
            self._write_snapshot(empty_payload)
            self.logger.info("Positions snapshot cleared")
