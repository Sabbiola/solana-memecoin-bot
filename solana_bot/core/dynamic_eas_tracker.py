"""
Dynamic EAS Tracker

Continuously recalculates Execution-Aware Asymmetry.

Philosophy:
- EAS is not static at entry
- Edge decays or improves over time
- React to edge changes, not just price

Implementation:
- Recalculate every 30s
- Track EAS trend (decay/improve)
- Trigger actions based on edge state
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EASSnapshot:
    """EAS measurement at a point in time"""
    timestamp: float
    eas_value: float
    executable_upside: float
    executable_downside: float
    liquidity_usd: float
    depth_ratio: float


class DynamicEASTracker:
    """
    Track EAS evolution during position holding.
    
    Critical insight:
    - Entry EAS = snapshot
    - Current EAS = reality
    
    If EAS decays below threshold → edge is gone → exit
    If EAS improves → widen protection → let it run
    """
    
    def __init__(
        self,
        initial_eas: float,
        mint: str,
        position_size_sol: float = 0.05
    ):
        self.mint = mint
        self.position_size_sol = position_size_sol
        self.snapshots: list[EASSnapshot] = []
        self.initial_eas = initial_eas
        self._current_risk_state = "LOW"
        
    def calculate_current_eas(
        self,
        current_price: float,
        liquidity_usd: float,
        price_momentum: float = 0
    ) -> float:
        """
        Recalculate EAS based on current conditions.
        v12.3.1: Aligned with entry_scorer formula for consistency.
        
        Args:
            current_price: Current token price
            liquidity_usd: Current liquidity
            price_momentum: Recent price change %
            
        Returns:
            Current EAS value
        """
        # === EXECUTABLE UPSIDE ===
        # Same formula as entry_scorer
        if liquidity_usd > 0:
            position_size_usd = self.position_size_sol * 200  # ~$200/SOL
            depth_ratio = liquidity_usd / max(position_size_usd, 1)
            
            base_upside = 25.0  # 25% target
            
            # Depth factor (same as entry_scorer)
            if depth_ratio > 1000:
                depth_factor = 1.0
            elif depth_ratio > 500:
                depth_factor = 0.85
            elif depth_ratio > 100:
                depth_factor = 0.7
            else:
                depth_factor = 0.5
                
            # Momentum factor (same as entry_scorer)
            if price_momentum > 20:
                momentum_factor = 0.9
            elif price_momentum > 5:
                momentum_factor = 0.7
            elif price_momentum > 0:
                momentum_factor = 0.5
            else:
                momentum_factor = 0.3
                
            executable_upside = base_upside * depth_factor * momentum_factor
        else:
            executable_upside = 5.0
            depth_ratio = 0
            
        # === EXECUTABLE DOWNSIDE ===
        # Same formula as entry_scorer (NO panic multiplier, NO support distance)
        base_slippage = 3.0
        mev_risk = 2.0
        
        # Liquidity-based risk
        if liquidity_usd > 50000:
            liq_risk = 1.0
        elif liquidity_usd > 20000:
            liq_risk = 2.0
        elif liquidity_usd > 10000:
            liq_risk = 3.0
        else:
            liq_risk = 5.0
            
        executable_downside = base_slippage + mev_risk + liq_risk
        
        # Calculate EAS
        if executable_downside > 0:
            eas = executable_upside / executable_downside
        else:
            eas = 1.0
            
        # Store snapshot
        snapshot = EASSnapshot(
            timestamp=time.time(),
            eas_value=eas,
            executable_upside=executable_upside,
            executable_downside=executable_downside,
            liquidity_usd=liquidity_usd,
            depth_ratio=depth_ratio if liquidity_usd > 0 else 0
        )
        self.snapshots.append(snapshot)
        
        # Keep only last 20 snapshots (10 minutes at 30s interval)
        if len(self.snapshots) > 20:
            self.snapshots = self.snapshots[-20:]
            
        return eas
        
    def get_eas_trend(self) -> str:
        """
        Determine if EAS is improving, decaying, or stable.
        
        Returns:
            "IMPROVING", "DECAYING", "STABLE"
        """
        if len(self.snapshots) < 3:
            return "STABLE"
            
        # Compare recent to older
        recent_eas = sum(s.eas_value for s in self.snapshots[-3:]) / 3
        older_eas = sum(s.eas_value for s in self.snapshots[:3]) / 3
        
        if recent_eas > older_eas * 1.1:  # 10% improvement
            return "IMPROVING"
        elif recent_eas < older_eas * 0.9:  # 10% decay
            return "DECAYING"
        else:
            return "STABLE"
            
    def get_risk_level(self, threshold: float = 1.2) -> str:
        """
        CRITICAL FIX: EAS decay = de-risk, NOT exit.
        
        v12.3 HYSTERESIS FIX:
        - MEDIUM activates at <1.15, deactivates at >1.25
        - HIGH activates at <0.92, deactivates at >1.02
        - Prevents flapping on noisy EAS
        
        Philosophy:
        - EAS measures STRUCTURAL edge (execution)
        - Blow-off top is NARRATIVE edge (FOMO)
        - These are DIFFERENT timescales
        
        Returns:
            "LOW", "MEDIUM", "HIGH" risk level
        """
        if not self.snapshots:
            return "LOW"
            
        current_eas = self.snapshots[-1].eas_value
        trend = self.get_eas_trend()
        prev_state = self._current_risk_state
        
        # HYSTERESIS LOGIC
        # Different thresholds for entry vs exit from each state
        
        if prev_state == "LOW":
            # Entry to MEDIUM: strict
            if current_eas < 1.15:
                new_state = "MEDIUM"
            elif current_eas < 0.92:
                new_state = "HIGH"
            else:
                new_state = "LOW"
                
        elif prev_state == "MEDIUM":
            # Exit to LOW: need clear improvement
            if current_eas > 1.25:
                new_state = "LOW"
            # Entry to HIGH: strict
            elif current_eas < 0.92:
                new_state = "HIGH"
            else:
                new_state = "MEDIUM"  # Stay in MEDIUM
                
        elif prev_state == "HIGH":
            # Exit to MEDIUM: need improvement
            if current_eas > 1.02:
                new_state = "MEDIUM"
            # Exit to LOW: need clear improvement
            elif current_eas > 1.25:
                new_state = "LOW"
            else:
                new_state = "HIGH"  # Stay in HIGH
        else:
            new_state = "LOW"
            
        # Log state changes
        if new_state != prev_state:
            logger.info(
                f"🔄 RISK STATE: {prev_state} → {new_state} | "
                f"EAS={current_eas:.2f} (hysteresis applied)"
            )
            
        self._current_risk_state = new_state
        
        # Additional logging for awareness
        if new_state == "HIGH":
            logger.warning(
                f"⚠️ HIGH RISK: EAS={current_eas:.2f} | "
                f"Structural edge gone, narrative may continue"
            )
        elif new_state == "MEDIUM":
            logger.info(
                f"⚠️ MEDIUM RISK: EAS={current_eas:.2f} | "
                f"Edge weakening, tighten protection"
            )
            
        return new_state
        
    def get_trailing_adjustment(self, base_trailing: float, current_risk: str = None) -> float:
        """
        Risk-based trailing adjustment.
        
        CRITICAL: Uses RISK LEVEL, not exit trigger.
        
        HIGH risk (EAS <0.96):
        - Tighten dramatically (0.5x)
        - Structural edge gone, protect capital
        
        MEDIUM risk (EAS <1.2 or DECAYING):
        - Tighten moderately (0.75x)
        - Prepare for potential dump
        
        LOW risk:
        - Standard behavior (1.0x to 1.5x)
        
        Args:
            base_trailing: Base trailing percentage
            current_risk: Override risk level
            
        Returns:
            Adjusted trailing
        """
        if current_risk is None:
            current_risk = self.get_risk_level()
            
        trend = self.get_eas_trend()
        
        # Risk-based base adjustment
        if current_risk == "HIGH":
            risk_multi = 0.5  # Dramatic tightening
        elif current_risk == "MEDIUM":
            risk_multi = 0.75  # Moderate tightening
        else:  # LOW
            # Use trend if risk is low
            if trend == "IMPROVING":
                risk_multi = 1.5
            elif trend == "DECAYING":
                risk_multi = 0.9
            else:
                risk_multi = 1.0
                
        adjusted = base_trailing * risk_multi
        
        if adjusted != base_trailing:
            logger.debug(
                f"📊 Trailing adjusted: {base_trailing:.1f}% → {adjusted:.1f}% | "
                f"Risk={current_risk} Trend={trend}"
            )
            
        return adjusted
            
    def get_status(self) -> dict:
        """Get current EAS tracking status"""
        if not self.snapshots:
            return {"current_eas": self.initial_eas, "trend": "UNKNOWN"}
            
        current = self.snapshots[-1]
        return {
            "initial_eas": self.initial_eas,
            "current_eas": current.eas_value,
            "eas_change_pct": ((current.eas_value - self.initial_eas) / self.initial_eas * 100) if self.initial_eas > 0 else 0,
            "trend": self.get_eas_trend(),
            "snapshots_count": len(self.snapshots),
            "upside": current.executable_upside,
            "downside": current.executable_downside
        }
