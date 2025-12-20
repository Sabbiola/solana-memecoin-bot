"""
Partial Exit Manager

Crystallizes gains during high-risk periods while maintaining upside exposure.

Philosophy:
- Tight trailing alone is NOT enough (slippage, gaps)
- Partial exits = guaranteed realization
- Remaining position captures blow-off

Risk-Based Exits:
- MEDIUM risk: 20% exit (once)
- HIGH risk: 35% exit (once)
- PARABOLIC + HIGH: Additional 25%

Total worst case: 60% realized, 40% riding with tight trailing.
"""

import logging
from dataclasses import dataclass
import time

logger = logging.getLogger(__name__)


@dataclass
class PartialExitState:
    """Track partial exit executions"""
    did_medium_derisk: bool = False
    did_high_derisk: bool = False
    did_parabolic_derisk: bool = False
    total_sold_pct: float = 0.0
    

class PartialExitManager:
    """
    Manages risk-based partial exits.
    
    Critical insight:
    - You can't predict tops
    - But you CAN guarantee partial realization
    - Then let the rest ride
    
    This beats "all or nothing" trailing.
    """
    
    def __init__(
        self,
        medium_risk_pct: float = 20.0,
        high_risk_pct: float = 35.0,
        parabolic_high_pct: float = 25.0
    ):
        self.medium_pct = medium_risk_pct
        self.high_pct = high_risk_pct
        self.parabolic_pct = parabolic_high_pct
        
        self.state = PartialExitState()
        
    def should_partial_exit(
        self,
        risk_level: str,
        runner_state: str,
        previous_risk: str = "LOW"
    ) -> tuple[bool, float, str]:
        """
        Determine if should execute partial exit.
        
        Args:
            risk_level: Current EAS risk (LOW/MEDIUM/HIGH)
            runner_state: Runner state (NORMAL/PRE_RUNNER/RUNNER/PARABOLIC)
            previous_risk: Previous risk level
            
        Returns:
            (should_exit, percentage, reason)
        """
        # MEDIUM Risk Transition
        if (risk_level == "MEDIUM" and 
            previous_risk == "LOW" and 
            not self.state.did_medium_derisk):
            
            self.state.did_medium_derisk = True
            self.state.total_sold_pct += self.medium_pct
            
            logger.info(
                f"📊 MEDIUM RISK DERISK: Selling {self.medium_pct}% | "
                f"Structural edge weakening, crystallize gains"
            )
            return (True, self.medium_pct, "MEDIUM_RISK_DERISK")
            
        # HIGH Risk Transition
        if (risk_level == "HIGH" and 
            previous_risk != "HIGH" and 
            not self.state.did_high_derisk):
            
            self.state.did_high_derisk = True
            self.state.total_sold_pct += self.high_pct
            
            logger.warning(
                f"⚠️ HIGH RISK DERISK: Selling {self.high_pct}% | "
                f"Edge gone, protect capital while maintaining exposure"
            )
            return (True, self.high_pct, "HIGH_RISK_DERISK")
            
        # PARABOLIC + HIGH (additional exit)
        if (runner_state == "PARABOLIC" and 
            risk_level == "HIGH" and 
            not self.state.did_parabolic_derisk and
            self.state.did_high_derisk):  # Only if already did HIGH
            
            self.state.did_parabolic_derisk = True
            self.state.total_sold_pct += self.parabolic_pct
            
            logger.warning(
                f"🔥 PARABOLIC HIGH RISK: Selling additional {self.parabolic_pct}% | "
                f"Blow-off likely ending, secure more gains"
            )
            return (True, self.parabolic_pct, "PARABOLIC_HIGH_DERISK")
            
        # No partial exit needed
        return (False, 0.0, "")
        
    def get_remaining_pct(self) -> float:
        """Get percentage still in position"""
        return 100.0 - self.state.total_sold_pct
        
    def get_status(self) -> dict:
        """Get current partial exit state"""
        return {
            "medium_derisk_done": self.state.did_medium_derisk,
            "high_derisk_done": self.state.did_high_derisk,
            "parabolic_derisk_done": self.state.did_parabolic_derisk,
            "total_sold_pct": self.state.total_sold_pct,
            "remaining_pct": self.get_remaining_pct()
        }
        
    def reset(self):
        """Reset state for new position"""
        self.state = PartialExitState()
