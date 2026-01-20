"""Entry signal detector for optimal entry timing."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List

from solana_bot.config import Settings
from solana_bot.core.models import TokenInfo


class SignalStrength(Enum):
    """Entry signal strength levels."""
    STRONG = "strong"      # High confidence entry
    MODERATE = "moderate"  # Decent entry
    WEAK = "weak"          # Risky entry
    NONE = "none"          # No entry signal


@dataclass
class EntrySignal:
    """Detected entry signal."""
    should_enter: bool
    strength: SignalStrength
    signals: List[str]  # List of detected positive signals
    warnings: List[str]  # List of warnings
    score: float  # 0-100 entry score
    reason: str


class EntrySignalDetector:
    """Detects optimal entry timing based on multiple signals.
    
    Entry Signals (positive):
    - Volume spike (volume_m5 > avg)
    - Buy pressure (buys > sells ratio)
    - Price bounce (recovering from dip)
    - Breakout (breaking resistance)
    - Fresh token (age < 3 min)
    
    Warning Signals (negative):
    - After big pump (already ran +100%)
    - Declining volume
    - Sell pressure
    - Near ATH (risky entry)
    """

    # Thresholds
    MIN_ENTRY_SCORE = 60  # Minimum score to enter
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.entry_signal")

    def detect(self, token: TokenInfo) -> EntrySignal:
        """Analyze token and detect entry signals - BUY THE DIP strategy."""
        metadata = token.metadata
        signals: List[str] = []
        warnings: List[str] = []
        score = 50.0  # Start neutral
        
        # Extract metrics
        price_change_m5 = float(metadata.get("price_change_m5", 0.0))
        price_change_h1 = float(metadata.get("price_change_h1", 0.0))
        volume_m5 = float(metadata.get("volume_m5", 0.0))
        volume_h1 = float(metadata.get("volume_h1", 0.0))
        txns_m5_buys = int(metadata.get("txns_m5_buys", 0))
        txns_m5_sells = int(metadata.get("txns_m5_sells", 0))
        market_cap = float(metadata.get("market_cap") or metadata.get("fdv") or 0.0)
        
        # Calculate derived metrics
        buy_sell_ratio = self._safe_ratio(txns_m5_buys, txns_m5_sells)
        volume_5m_hourly = volume_m5 * 12  # Project to hourly
        volume_trend = self._safe_ratio(volume_5m_hourly, volume_h1) if volume_h1 > 0 else 1.0
        total_txns_m5 = txns_m5_buys + txns_m5_sells
        
        # ===== BUY THE DIP SIGNALS (POSITIVE) =====
        
        # 1. Fresh token (age < 3 min) - Still good for early entry
        if token.age_sec < 180:
            signals.append(f"🆕 Fresh token ({token.age_sec}s old)")
            score += 15
        elif token.age_sec < 300:
            signals.append(f"⏰ Young token ({token.age_sec // 60}m old)")
            score += 8
        
        # 2. SMART DIP: Price dipped but showing RECOVERY SIGNS
        #    - Must have volume (buyers present)
        #    - Must have buy pressure returning OR price stabilizing
        is_dip = -25 < price_change_m5 < -5
        has_volume = volume_trend > 0.8
        buyers_returning = buy_sell_ratio > 1.0
        price_stabilizing = abs(price_change_m5) < 10 and price_change_h1 < -15
        
        if is_dip and has_volume and buyers_returning:
            signals.append(f"🎯 Smart dip: {price_change_m5:.0f}% + buyers returning (ratio {buy_sell_ratio:.1f})")
            score += 25
        elif is_dip and has_volume and price_stabilizing:
            signals.append(f"🔻 Dip stabilizing ({price_change_m5:.0f}% m5, was {price_change_h1:.0f}% h1)")
            score += 18
        
        # 3. PULLBACK AFTER PUMP: Healthy correction with volume
        #    - Token pumped in h1
        #    - Now pulling back (not crashing)
        #    - Volume still present = not dead
        if price_change_h1 > 50 and -20 < price_change_m5 < -5 and volume_trend > 0.7 and buy_sell_ratio > 0.9:
            signals.append(f"🎯 Healthy pullback (h1:+{price_change_h1:.0f}% → m5:{price_change_m5:.0f}%, vol OK)")
            score += 22
        
        # 4. BOTTOM CONFIRMED: Was crashing, now buyers stepping in
        #    - h1 was down big
        #    - m5 showing recovery (positive or stable)
        #    - Buy pressure increasing
        if price_change_h1 < -25 and price_change_m5 > -3 and buy_sell_ratio > 1.2:
            signals.append(f"⬆️ Bottom forming (h1:{price_change_h1:.0f}% → m5:+{price_change_m5:.0f}%, buyers in)")
            score += 25
        
        # 5. ACCUMULATION: Sells happening but price NOT dropping = strong hands
        if buy_sell_ratio < 0.7 and abs(price_change_m5) < 3:
            signals.append(f"🔄 Accumulation (heavy sells absorbed)")
            score += 15
        
        # 6. Volume during dip (smart money buying)
        if price_change_m5 < -5 and volume_trend > 1.2:
            signals.append(f"💰 Volume spike on dip (smart money?)")
            score += 15
        
        # 7. MOMENTUM RUNNER: Breaking out with intense volume
        #    - Price moving up fast
        #    - Volume confirming (+2x avg)
        #    - Not *too* overextended yet (<100% h1)
        #    - SAFETY: Only for tokens > 15 mins (fresh tokens must dip first)
        # 7. Momentum / Breakout Signals
        # We classify momentum in two tiers:
        # A) Standard Momentum: Good vol, reasonable pump (<100% h1)
        # B) Super Runner: INSANE vol (>3x), allows chasing up to +400% h1 (for moonbags)
        
        is_standard_mom = 10 < price_change_m5 < 40 and volume_trend > 2.0 and price_change_h1 < 100
        is_super_runner = 10 < price_change_m5 < 50 and volume_trend > 3.0 and price_change_h1 < 400
        is_early_runner = 5 < price_change_m5 < 20 and volume_trend > 1.5
        
        if is_super_runner:
            # High risk, high reward - allow entry even if extended
            if token.age_sec > 600:
                 signals.append(f"🚀🚀 SUPER RUNNER (+{price_change_m5:.0f}% m5, 3x vol, h1={price_change_h1:.0f}%)")
                 score += 30  # High score to override overextension penalty
            else:
                 warnings.append(f"⚠️ Ignoring super runner on potential rug ({token.age_sec}s)")
        
        elif is_standard_mom:
            # Lowered from 900s (15m) to 600s (10m)
            if token.age_sec > 600:
                signals.append(f"🚀 Momentum Breakout (+{price_change_m5:.0f}% m5, 2x vol)")
                score += 25
            else:
                warnings.append(f"⚠️ Ignoring momentum on fresh token ({token.age_sec}s) - wait for dip or age > 10m")
                
        elif is_early_runner:
            # Lowered from 600s (10m) to 300s (5m) IF volume is strong
            if token.age_sec > 300:
                signals.append(f"📈 Early runner (+{price_change_m5:.0f}% m5, 1.5x vol)")
                score += 15
            else:
                warnings.append(f"⚠️ Ignoring runner on fresh token ({token.age_sec}s) - wait for dip or age > 5m")
            
        # 8. High activity (token is alive)
        if total_txns_m5 > 30:
            signals.append(f"🔥 Active ({total_txns_m5} txns/5m)")
            score += 5
        
        # ===== AVOID MOMENTUM ENTRIES (WARNINGS) =====
        
        # 1. PUMPING NOW - DON'T CHASE (Refined for Micro-Caps)
        # For micro-caps (<$20k), we allow more volatility (up to 100%) because 
        # they often need to pump 50-80% just to reach our visibility filters.
        fomo_threshold = 100 if market_cap < 20000 else 40
        
        if price_change_m5 > fomo_threshold:
            warnings.append(f"🚫 FOMO trap! Pumping +{price_change_m5:.0f}% (> {fomo_threshold}%) - wait for dip")
            score -= 30
        elif price_change_m5 > 20 and volume_trend < 1.0:
            warnings.append(f"⚠️ Chasing pump +{price_change_m5:.0f}% without volume")
            score -= 20
            
        # 2. ALREADY OVEREXTENDED
        if price_change_h1 > 300:
            warnings.append(f"🚫 Way overextended +{price_change_h1:.0f}% h1 - AVOID")
            score -= 30
        elif price_change_h1 > 150:
            warnings.append(f"⚠️ Overextended +{price_change_h1:.0f}% h1 - cautious")
            score -= 10
        
        # 3. FREEFALL / KNIFE CATCH PROTECTION - CRITICAL
        # Prevent buying falling knives.
        # Rule: If dropping fast (-10% in 5m), require HUGE volume and buyers to catch.
        if price_change_m5 < -10:
            is_v_shape = volume_trend > 2.0 and buy_sell_ratio > 1.5
            
            if price_change_m5 < -30:
                warnings.append(f"🔴 CRITICAL FREEFALL {price_change_m5:.0f}% - DO NOT CATCH")
                score -= 100 # Absolute kill switch
            elif not is_v_shape:
                warnings.append(f"🔪 Knife catch attempt {price_change_m5:.0f}% - needs vol>2x & ratio>1.5 (got {volume_trend:.1f} & {buy_sell_ratio:.1f})")
                score -= 40 # Severe penalty preventing entry
            else:
                # This is a risky V-shape catch, allow but caution
                signals.append(f"🛡️ High risk V-Shape catch attempt (vol {volume_trend:.1f}x)")
        
        # 4. Dead token
        if total_txns_m5 < 5:
            warnings.append(f"💤 Dead token ({total_txns_m5} txns/5m)")
            score -= 20
        
        # 5. Volume dying during dip (no buyers)
        if price_change_m5 < -5 and volume_trend < 0.8: # Increased from 0.5 to 0.8 for safety
            warnings.append(f"📉 Dip with weak volume ({volume_trend:.1f}x) - no buyers")
            score -= 25
        
        # Clamp score
        score = max(0, min(100, score))
        
        # Determine entry decision
        should_enter = score >= self.MIN_ENTRY_SCORE and len(signals) >= 2
        
        if score >= 80:
            strength = SignalStrength.STRONG
        elif score >= 60:
            strength = SignalStrength.MODERATE
        elif score >= 40:
            strength = SignalStrength.WEAK
        else:
            strength = SignalStrength.NONE
        
        # Generate reason
        if should_enter:
            reason = f"ENTRY OK ({score:.0f}/100): {', '.join(signals[:2])}"
        else:
            if warnings:
                reason = f"NO ENTRY ({score:.0f}/100): {warnings[0]}"
            else:
                reason = f"NO ENTRY ({score:.0f}/100): Insufficient signals"
        
        result = EntrySignal(
            should_enter=should_enter,
            strength=strength,
            signals=signals,
            warnings=warnings,
            score=score,
            reason=reason,
        )
        
        self.logger.debug(
            "ENTRY_SIGNAL %s: score=%d strength=%s enter=%s",
            token.symbol, score, strength.value, should_enter
        )
        
        return result

    def _safe_ratio(self, a: float, b: float) -> float:
        """Calculate ratio safely."""
        if b <= 0:
            return 2.0 if a > 0 else 1.0
        return a / b
