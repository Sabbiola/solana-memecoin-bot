"""Bounce Recovery Manager for re-entering positions after stop-loss exits."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solana_bot.config import Settings
    from solana_bot.core.models import Position
    from solana_bot.core.price_feed import PriceFeed


@dataclass
class BounceWatchlistEntry:
    """Tracks an exited position for bounce recovery opportunities."""
    mint: str
    symbol: str
    original_size_sol: float
    exit_price: float
    exit_time: float
    bottom_price: float  # Lowest price seen since exit
    reentry_count: int = 0  # How many times we've re-entered
    original_loss_sol: float = 0.0


@dataclass
class BounceSignal:
    """Signal to re-enter a position after bounce detection."""
    mint: str
    symbol: str
    reentry_size_sol: float
    current_price: float
    bounce_pct: float  # Percentage bounce from bottom
    volume_spike_pct: float  # Volume increase percentage


class BounceRecoveryManager:
    """Manages bounce detection and re-entry logic for stopped-out positions."""
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.bounce_recovery")
        self.watchlist: dict[str, BounceWatchlistEntry] = {}
    
    def add_to_watchlist(
        self,
        position: Position,
        exit_price: float,
        exit_time: float,
        loss_sol: float,
    ) -> None:
        """Add a stopped-out position to the bounce watchlist."""
        entry = BounceWatchlistEntry(
            mint=position.token.mint,
            symbol=position.token.symbol,
            original_size_sol=position.initial_size_sol,
            exit_price=exit_price,
            exit_time=exit_time,
            bottom_price=exit_price,  # Initially, exit price is the bottom
            reentry_count=0,
            original_loss_sol=loss_sol,
        )
        self.watchlist[position.token.mint] = entry
        self.logger.info(
            "BOUNCE_WATCHLIST_ADD %s: Exit=%.8f, Loss=%.4f SOL, Monitor for %ds",
            entry.symbol,
            exit_price,
            loss_sol,
            self.settings.BOUNCE_MONITOR_DURATION_SEC,
        )
    
    async def update_and_check_bounces(
        self,
        now: float,
        price_feed: PriceFeed,
    ) -> list[BounceSignal]:
        """Update watchlist prices and detect bounce opportunities."""
        signals: list[BounceSignal] = []
        
        # Cleanup expired entries first
        self._cleanup_expired(now)
        
        for mint, entry in list(self.watchlist.items()):
            # Get current price (we'll need to fetch it from price feed or token data)
            # For now, we'll assume price_feed has a method to get price by mint
            try:
                current_price = await self._get_current_price(mint, price_feed)
                if current_price is None:
                    continue
                
                # Update bottom price
                if current_price < entry.bottom_price:
                    entry.bottom_price = current_price
                    self.logger.debug(
                        "BOUNCE_BOTTOM_UPDATE %s: New bottom=%.8f",
                        entry.symbol,
                        current_price,
                    )
                
                # Check for bounce
                bounce_pct = (current_price / entry.bottom_price) - 1.0
                
                if bounce_pct >= self.settings.BOUNCE_THRESHOLD_PCT:
                    # Validate volume spike (simplified - in production, fetch actual volume)
                    volume_spike = await self._check_volume_spike(mint, entry)
                    
                    if volume_spike >= self.settings.BOUNCE_MIN_VOLUME_SPIKE:
                        # Check if we haven't exceeded max re-entries
                        if entry.reentry_count < self.settings.BOUNCE_MAX_REENTRIES:
                            reentry_size = entry.original_size_sol * self.settings.BOUNCE_REENTRY_SIZE_MULTIPLIER
                            
                            signal = BounceSignal(
                                mint=mint,
                                symbol=entry.symbol,
                                reentry_size_sol=reentry_size,
                                current_price=current_price,
                                bounce_pct=bounce_pct * 100,
                                volume_spike_pct=volume_spike * 100,
                            )
                            signals.append(signal)
                            
                            # Increment re-entry count
                            entry.reentry_count += 1
                            
                            self.logger.info(
                                "BOUNCE_DETECTED %s: +%.1f%% from bottom (%.8f → %.8f), Volume spike +%.1f%%",
                                entry.symbol,
                                bounce_pct * 100,
                                entry.bottom_price,
                                current_price,
                                volume_spike * 100,
                            )
                            
                            # If max re-entries reached, remove from watchlist
                            if entry.reentry_count >= self.settings.BOUNCE_MAX_REENTRIES:
                                self.logger.info(
                                    "BOUNCE_MAX_REENTRIES %s: Removing from watchlist",
                                    entry.symbol,
                                )
                                self.watchlist.pop(mint, None)
                        else:
                            self.logger.debug(
                                "BOUNCE_SKIP %s: Max re-entries (%d) reached",
                                entry.symbol,
                                self.settings.BOUNCE_MAX_REENTRIES,
                            )
            
            except Exception as e:
                self.logger.error("Error checking bounce for %s: %s", mint, e)
                continue
        
        return signals
    
    def _cleanup_expired(self, now: float) -> None:
        """Remove entries that have exceeded the monitoring duration."""
        expired = [
            mint
            for mint, entry in self.watchlist.items()
            if now - entry.exit_time > self.settings.BOUNCE_MONITOR_DURATION_SEC
        ]
        
        for mint in expired:
            entry = self.watchlist.pop(mint)
            self.logger.info(
                "BOUNCE_TIMEOUT %s: Removing from watchlist after %ds",
                entry.symbol,
                self.settings.BOUNCE_MONITOR_DURATION_SEC,
            )
    
    async def _get_current_price(self, mint: str, price_feed: PriceFeed) -> float | None:
        """Get current price for a token mint."""
        # This is a simplified version - in production, we'd need to:
        # 1. Check if token is in active positions (use price_feed)
        # 2. Otherwise, fetch from external API (DexScreener, Jupiter, etc.)
        # For now, we'll return None and implement this in the integration
        try:
            # Attempt to get price from price feed's cache or live data
            # This will need to be implemented based on your PriceFeed architecture
            if hasattr(price_feed, 'get_price_by_mint'):
                return await price_feed.get_price_by_mint(mint)
            else:
                # Fallback: we'll need to implement external price fetching
                self.logger.debug("Price feed doesn't support get_price_by_mint for %s", mint)
                return None
        except Exception as e:
            self.logger.error("Failed to get price for %s: %s", mint, e)
            return None
    
    async def _check_volume_spike(self, mint: str, entry: BounceWatchlistEntry) -> float:
        """Check for volume spike (simplified version)."""
        # In production, this should:
        # 1. Fetch recent volume data from DexScreener or similar
        # 2. Compare to baseline volume
        # 3. Return spike percentage
        
        # For now, we'll return a placeholder value
        # This will be properly implemented in integration
        # TODO: Implement real volume spike detection
        return 0.6  # Placeholder: 60% spike
    
    def remove_from_watchlist(self, mint: str) -> None:
        """Manually remove a token from the watchlist."""
        entry = self.watchlist.pop(mint, None)
        if entry:
            self.logger.info("BOUNCE_REMOVED %s: Manually removed from watchlist", entry.symbol)
    
    def get_watchlist_count(self) -> int:
        """Get the current number of tokens being monitored."""
        return len(self.watchlist)
