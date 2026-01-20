"""Pattern analyzer for detecting pump & dump and other trading patterns."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List

from solana_bot.config import Settings
from solana_bot.core.models import TokenInfo


class PatternType(Enum):
    """Detected pattern types."""
    HEALTHY_PUMP = "healthy_pump"      # Good entry, organic growth
    PUMP_AND_DUMP = "pump_and_dump"    # Avoid - artificial pump then dump
    DISTRIBUTION = "distribution"       # Whales selling, avoid
    ACCUMULATION = "accumulation"       # Whales buying, good
    CONSOLIDATION = "consolidation"     # Sideways, wait
    BREAKOUT = "breakout"              # Breaking resistance, good entry
    BREAKDOWN = "breakdown"            # Breaking support, exit signal


@dataclass
class PatternResult:
    """Result of pattern analysis."""
    pattern: PatternType
    confidence: float  # 0.0 - 1.0
    entry_safe: bool
    reason: str
    suggested_action: str


class PatternAnalyzer:
    """Analyzes price/volume patterns to detect pump & dump and other patterns.
    
    Key indicators:
    - Price velocity (how fast price moves)
    - Volume profile (is volume decreasing after pump?)
    - Buy/sell ratio trend
    - Dev/whale activity
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.pattern")

    def analyze(self, token: TokenInfo) -> PatternResult:
        """Analyze token for trading patterns."""
        metadata = token.metadata
        
        # Extract metrics
        price_change_m5 = float(metadata.get("price_change_m5", 0.0))
        price_change_h1 = float(metadata.get("price_change_h1", 0.0))
        volume_m5 = float(metadata.get("volume_m5", 0.0))
        volume_h1 = float(metadata.get("volume_h1", 0.0))
        txns_m5_buys = int(metadata.get("txns_m5_buys", 0))
        txns_m5_sells = int(metadata.get("txns_m5_sells", 0))
        txns_h1_buys = int(metadata.get("txns_h1_buys", 0))
        txns_h1_sells = int(metadata.get("txns_h1_sells", 0))
        market_cap = float(metadata.get("market_cap") or metadata.get("fdv") or 0.0)
        
        # Calculate derived metrics
        buy_sell_ratio_m5 = self._safe_ratio(txns_m5_buys, txns_m5_sells)
        buy_sell_ratio_h1 = self._safe_ratio(txns_h1_buys, txns_h1_sells)
        volume_trend = self._safe_ratio(volume_m5 * 12, volume_h1)  # Normalize to hourly
        price_velocity = abs(price_change_m5) / 5.0 if price_change_m5 else 0.0  # %/min
        
        # Pattern detection logic
        pattern = self._detect_pattern(
            price_change_m5=price_change_m5,
            price_change_h1=price_change_h1,
            buy_sell_ratio_m5=buy_sell_ratio_m5,
            buy_sell_ratio_h1=buy_sell_ratio_h1,
            volume_trend=volume_trend,
            price_velocity=price_velocity,
            market_cap=market_cap,
        )
        
        self.logger.debug(
            "PATTERN %s: %s (%.0f%% conf) - %s",
            token.symbol, pattern.pattern.value, pattern.confidence * 100, pattern.reason
        )
        
        return pattern

    def _detect_pattern(
        self,
        price_change_m5: float,
        price_change_h1: float,
        buy_sell_ratio_m5: float,
        buy_sell_ratio_h1: float,
        volume_trend: float,
        price_velocity: float,
        market_cap: float,
    ) -> PatternResult:
        """Core pattern detection logic."""
        
        # 🔴 PUMP AND DUMP: Big pump in 1h, but now dumping (negative 5m, sells > buys)
        if (price_change_h1 > 100 and  # Big pump in last hour
            price_change_m5 < -10 and  # Now dropping
            buy_sell_ratio_m5 < 0.8):  # More sells than buys
            return PatternResult(
                pattern=PatternType.PUMP_AND_DUMP,
                confidence=0.85,
                entry_safe=False,
                reason=f"Pump +{price_change_h1:.0f}% h1, now dumping {price_change_m5:.0f}% m5",
                suggested_action="AVOID - Wait for bottom or skip"
            )
        
        # 🔴 DISTRIBUTION: Volume decreasing while price drops
        if (price_change_m5 < -5 and
            volume_trend < 0.5 and  # Volume dying
            buy_sell_ratio_m5 < 0.7):
            return PatternResult(
                pattern=PatternType.DISTRIBUTION,
                confidence=0.75,
                entry_safe=False,
                reason=f"Sells dominating, volume dying (trend={volume_trend:.1f})",
                suggested_action="AVOID - Whales exiting"
            )
        
        # 🟢 HEALTHY PUMP: Price up with strong buy pressure and volume
        if (price_change_m5 > 10 and
            buy_sell_ratio_m5 > 1.3 and  # More buys than sells
            volume_trend > 0.8):  # Volume holding
            return PatternResult(
                pattern=PatternType.HEALTHY_PUMP,
                confidence=0.80,
                entry_safe=True,
                reason=f"Strong buy pressure (ratio={buy_sell_ratio_m5:.1f}), volume healthy",
                suggested_action="ENTRY OK - Momentum positive"
            )
        
        # 🟢 ACCUMULATION: Buys > sells but price stable (whales loading)
        if (abs(price_change_m5) < 5 and
            buy_sell_ratio_m5 > 1.5 and
            buy_sell_ratio_h1 > 1.2):
            return PatternResult(
                pattern=PatternType.ACCUMULATION,
                confidence=0.70,
                entry_safe=True,
                reason=f"Accumulation detected, buy ratio {buy_sell_ratio_m5:.1f}",
                suggested_action="ENTRY OK - Whales accumulating"
            )
        
        # 🟢 BREAKOUT: Price velocity high with volume
        if (price_velocity > 5 and  # >5% per minute
            price_change_m5 > 20 and
            buy_sell_ratio_m5 > 1.0):
            return PatternResult(
                pattern=PatternType.BREAKOUT,
                confidence=0.75,
                entry_safe=True,
                reason=f"Breakout! Velocity {price_velocity:.1f}%/min",
                suggested_action="ENTRY OK - Breakout momentum"
            )
        
        # 🔴 BREAKDOWN: Fast drop
        if (price_velocity > 3 and
            price_change_m5 < -15):
            return PatternResult(
                pattern=PatternType.BREAKDOWN,
                confidence=0.80,
                entry_safe=False,
                reason=f"Breakdown! Dropping {price_change_m5:.0f}% fast",
                suggested_action="AVOID/EXIT - Support broken"
            )
        
        # 🟡 CONSOLIDATION: Sideways movement
        if abs(price_change_m5) < 5 and abs(price_change_h1) < 20:
            return PatternResult(
                pattern=PatternType.CONSOLIDATION,
                confidence=0.60,
                entry_safe=True,  # Can enter during consolidation
                reason="Price consolidating, low volatility",
                suggested_action="WAIT - Look for breakout direction"
            )
        
        # Default: Healthy if nothing bad detected
        return PatternResult(
            pattern=PatternType.HEALTHY_PUMP,
            confidence=0.50,
            entry_safe=True,
            reason="No negative patterns detected",
            suggested_action="ENTRY OK - Neutral"
        )

    def _safe_ratio(self, a: float, b: float) -> float:
        """Calculate ratio safely, avoiding division by zero."""
        if b <= 0:
            return 2.0 if a > 0 else 1.0
        return a / b

    def is_entry_safe(self, token: TokenInfo) -> tuple[bool, str]:
        """Quick check if entry is safe based on pattern analysis."""
        result = self.analyze(token)
        return result.entry_safe, result.reason
