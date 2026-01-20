"""Copy Trading - Wallet Tracker Module.

Tracks leader wallets and generates copy signals when they buy/sell tokens.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from solana_bot.config import Settings
from solana_bot.utils.time import utc_ts


@dataclass
class LeaderWallet:
    """Configuration for a leader wallet to copy."""
    address: str
    alias: str
    enabled: bool = True
    copy_size_sol: float = 0.02
    max_positions: int = 3
    min_trade_sol: float = 0.1
    follow_sells: bool = True
    # Stats
    total_copies: int = 0
    successful_copies: int = 0
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "LeaderWallet":
        return cls(
            address=data.get("address", ""),
            alias=data.get("alias", ""),
            enabled=data.get("enabled", True),
            copy_size_sol=data.get("copy_size_sol", 0.02),
            max_positions=data.get("max_positions", 3),
            min_trade_sol=data.get("min_trade_sol", 0.1),
            follow_sells=data.get("follow_sells", True),
            total_copies=data.get("total_copies", 0),
            successful_copies=data.get("successful_copies", 0),
        )


@dataclass
class CopySignal:
    """Signal to copy a leader's trade."""
    
    leader_address: str
    leader_alias: str
    action: str  # "BUY" or "SELL"
    token_mint: str
    token_symbol: str
    amount_sol: float  # Leader's trade size
    copy_size_sol: float  # Our copy size
    signature: str
    amount_usd: float = 0.0  # Stablecoin-equivalent size (if applicable)
    price: float = 0.0  # Calculated price from transaction
    price_in_usd: bool = False  # True if price is USD/token (stable swaps)
    timestamp: float = field(default_factory=utc_ts)
    processed: bool = False


class WalletTracker:
    """Tracks leader wallets and manages copy signals."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.wallet_tracker")
        self._leaders: dict[str, LeaderWallet] = {}
        self._signal_queue: asyncio.Queue[CopySignal] = asyncio.Queue()
        self._recent_signals: list[CopySignal] = []  # Last N signals for UI
        self._max_recent = 100
        self._leaders_file = Path(settings.COPY_TRADING_LEADERS_FILE)
        self._sol_price_usd: float = 130.0
        self._dedup_signatures: dict[str, float] = {}
        self._dedup_ttl_sec = 300.0
        self._load_leaders()

    def set_sol_price_usd(self, price: float) -> None:
        """Update cached SOL price (USD) for stable->SOL conversions."""
        if price > 0:
            self._sol_price_usd = price
    
    def _load_leaders(self) -> None:
        """Load leader wallets from JSON file."""
        if not self._leaders_file.exists():
            self.logger.info("No leaders file found at %s", self._leaders_file)
            return
        try:
            with open(self._leaders_file, "r") as f:
                data = json.load(f)
            for item in data:
                leader = LeaderWallet.from_dict(item)
                if leader.address:
                    self._leaders[leader.address] = leader
            self.logger.info("Loaded %d leader wallets", len(self._leaders))
        except Exception as e:
            self.logger.error("Failed to load leaders: %s", e)
    
    def _save_leaders(self) -> None:
        """Save leader wallets to JSON file."""
        try:
            self._leaders_file.parent.mkdir(parents=True, exist_ok=True)
            data = [leader.to_dict() for leader in self._leaders.values()]
            with open(self._leaders_file, "w") as f:
                json.dump(data, f, indent=2)
            self.logger.debug("Saved %d leaders to file", len(self._leaders))
        except Exception as e:
            self.logger.error("Failed to save leaders: %s", e)
    
    def add_leader(
        self,
        address: str,
        alias: str,
        copy_size_sol: float | None = None,
        max_positions: int | None = None,
        min_trade_sol: float | None = None,
        follow_sells: bool | None = None,
    ) -> LeaderWallet:
        """Add a new leader wallet to track."""
        leader = LeaderWallet(
            address=address,
            alias=alias or address[:8],
            copy_size_sol=copy_size_sol or self.settings.COPY_DEFAULT_SIZE_SOL,
            max_positions=max_positions or self.settings.COPY_MAX_POSITIONS,
            min_trade_sol=min_trade_sol or self.settings.COPY_MIN_LEADER_TRADE_SOL,
            follow_sells=follow_sells if follow_sells is not None else self.settings.COPY_FOLLOW_SELLS,
        )
        self._leaders[address] = leader
        self._save_leaders()
        self.logger.info("Added leader: %s (%s)", alias, address[:16])
        return leader
    
    def remove_leader(self, address: str) -> bool:
        """Remove a leader wallet."""
        if address in self._leaders:
            del self._leaders[address]
            self._save_leaders()
            self.logger.info("Removed leader: %s", address[:16])
            return True
        return False
    
    def update_leader(self, address: str, **kwargs: Any) -> LeaderWallet | None:
        """Update leader settings."""
        leader = self._leaders.get(address)
        if not leader:
            return None
        for key, value in kwargs.items():
            if hasattr(leader, key) and value is not None:
                setattr(leader, key, value)
        self._save_leaders()
        return leader
    
    def get_leader(self, address: str) -> LeaderWallet | None:
        """Get a leader by address."""
        return self._leaders.get(address)
    
    def get_leaders(self) -> list[LeaderWallet]:
        """Get all leader wallets."""
        return list(self._leaders.values())
    
    def get_active_leaders(self) -> list[LeaderWallet]:
        """Get enabled leader wallets."""
        return [l for l in self._leaders.values() if l.enabled]
    
    def is_leader(self, address: str) -> bool:
        """Check if an address is a tracked leader."""
        return address in self._leaders

    def _is_duplicate_signature(self, signature: str, now: float) -> bool:
        if not signature:
            return False
        last_seen = self._dedup_signatures.get(signature)
        if last_seen is not None and now - last_seen < self._dedup_ttl_sec:
            return True
        self._dedup_signatures[signature] = now
        if len(self._dedup_signatures) > 1000:
            cutoff = now - self._dedup_ttl_sec
            for sig, ts in list(self._dedup_signatures.items()):
                if ts < cutoff:
                    del self._dedup_signatures[sig]
        return False
    
    def process_transaction(
        self,
        wallet_address: str,
        action: str,
        token_mint: str,
        token_symbol: str,
        amount_sol: float,
        signature: str,
        amount_usd: float = 0.0,
        price: float = 0.0,  # Add price parameter
        price_in_usd: bool = False,
    ) -> CopySignal | None:
        """
        Process a transaction from Helius webhook.
        Returns a CopySignal if the transaction should be copied.
        """
        leader = self._leaders.get(wallet_address)
        if not leader:
            return None
        
        if not leader.enabled:
            self.logger.debug("Leader %s is disabled, skipping", leader.alias)
            return None
        
        now = utc_ts()
        if self._is_duplicate_signature(signature, now):
            self.logger.debug("Duplicate signal ignored for signature %s", signature[:16])
            return None

        # Convert stablecoin amount to SOL equivalent if needed
        if amount_sol <= 0 and amount_usd > 0:
            sol_price = self._sol_price_usd if self._sol_price_usd > 0 else 130.0
            amount_sol = amount_usd / sol_price
        
        # Check minimum trade size
        if amount_sol < leader.min_trade_sol:
            self.logger.debug(
                "Trade too small: %.4f SOL < %.4f SOL min",
                amount_sol, leader.min_trade_sol
            )
            return None
        
        # Check if we should follow sells
        if action == "SELL" and not leader.follow_sells:
            self.logger.debug("Not following sells for %s", leader.alias)
            return None
        
        # Fallback for unknown symbol
        display_symbol = token_symbol
        if not display_symbol or display_symbol == "UNKNOWN":
            display_symbol = f"{token_mint[:4]}...{token_mint[-4:]} (Mint)"

        signal = CopySignal(
            leader_address=wallet_address,
            leader_alias=leader.alias,
            action=action,
            token_mint=token_mint,
            token_symbol=display_symbol,
            amount_sol=amount_sol,
            amount_usd=amount_usd,
            copy_size_sol=leader.copy_size_sol,
            signature=signature,
            price=price,  # Include calculated price in signal
            price_in_usd=price_in_usd,
        )
        
        # Add to queue for bot processing
        try:
            self._signal_queue.put_nowait(signal)
        except asyncio.QueueFull:
            self.logger.warning("Signal queue full, dropping signal")
            return None
        
        # Track recent signals for UI
        self._recent_signals.append(signal)
        if len(self._recent_signals) > self._max_recent:
            self._recent_signals.pop(0)
        
        self.logger.info(
            "COPY SIGNAL: %s %s %s (%.4f SOL) -> copy %.4f SOL @ $%.9f",
            leader.alias, action, display_symbol, amount_sol, leader.copy_size_sol, price
        )
        
        return signal
    
    async def get_signal(self) -> CopySignal | None:
        """Get next copy signal from queue (non-blocking)."""
        try:
            return self._signal_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
    
    async def drain_signals(self) -> list[CopySignal]:
        """Get all pending signals from queue."""
        signals: list[CopySignal] = []
        while True:
            try:
                signals.append(self._signal_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return signals
    
    def get_recent_signals(self, limit: int = 50) -> list[dict]:
        """Get recent signals for dashboard display."""
        return [
            {
                "leader_alias": s.leader_alias,
                "action": s.action,
                "token_mint": s.token_mint,
                "token_symbol": s.token_symbol,
                "amount_sol": s.amount_sol,
                "amount_usd": s.amount_usd,
                "copy_size_sol": s.copy_size_sol,
                "signature": s.signature,
                "timestamp": s.timestamp,
                "processed": s.processed,
            }
            for s in reversed(self._recent_signals[-limit:])
        ]
    
    def mark_signal_processed(self, signal: CopySignal, success: bool = True) -> None:
        """Mark a signal as processed and update leader stats."""
        signal.processed = True
        leader = self._leaders.get(signal.leader_address)
        if leader:
            leader.total_copies += 1
            if success:
                leader.successful_copies += 1
            self._save_leaders()
