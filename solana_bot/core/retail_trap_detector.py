"""
Retail Trap Detector

Detects distribution patterns:
- Price increasing
- Holder count increasing
- Average trade size DECREASING

Signal: Smart money distributing to retail
Action: IMMEDIATE EXIT
"""

import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class RetailTrapSignal:
    """Retail trap detection result"""
    detected: bool
    price_trend: str  # UP, DOWN, FLAT
    holder_trend: str  # INCREASING, DECREASING, FLAT
    size_trend: str   # INCREASING, DECREASING, FLAT
    confidence: float  # 0-1
    reason: str = ""


class RetailTrapDetector:
    """
    Detect retail trap patterns (distribution to small buyers).
    
    Pattern:
    - Price ↑ (attracts retail)
    - Holders ↑ (retail entering)
    - Avg size ↓ (small buys, big sells)
    
    = Smart money distributing
    """
    
    def __init__(self):
        self.price_history: List[float] = []
        self.holder_history: List[int] = []
        self.avg_size_history: List[float] = []
        
    def add_datapoint(
        self,
        price: float,
        holder_count: int = 0,
        avg_trade_size: float = 0
    ):
        """Add new datapoint to history"""
        self.price_history.append(price)
        self.holder_history.append(holder_count)
        self.avg_size_history.append(avg_trade_size)
        
        # Keep only last 20 datapoints (10s history at 0.5s intervals)
        if len(self.price_history) > 20:
            self.price_history = self.price_history[-20:]
            self.holder_history = self.holder_history[-20:]
            self.avg_size_history = self.avg_size_history[-20:]
            
    def detect(self) -> RetailTrapSignal:
        """
        Detect if retail trap is forming.
        
        Returns:
            RetailTrapSignal with detection result
        """
        if len(self.price_history) < 10:
            # Not enough data
            return RetailTrapSignal(
                detected=False,
                price_trend="UNKNOWN",
                holder_trend="UNKNOWN",
                size_trend="UNKNOWN",
                confidence=0.0,
                reason="Insufficient data"
            )
            
        # Calculate trends
        price_trend = self._calc_trend(self.price_history)
        holder_trend = self._calc_trend(self.holder_history)
        size_trend = self._calc_trend(self.avg_size_history)
        
        # Retail trap pattern:
        # Price UP + Holders UP + Size DOWN = distribution
        is_trap = (
            price_trend == "UP" and
            holder_trend == "INCREASING" and
            size_trend == "DECREASING"
        )
        
        # Calculate confidence
        if is_trap:
            # Strong signal if all 3 conditions met
            confidence = 0.85
            reason = "Distribution pattern: retail buying, smart money selling"
        else:
            confidence = 0.0
            reason = f"No trap: price={price_trend}, holders={holder_trend}, size={size_trend}"
            
        signal = RetailTrapSignal(
            detected=is_trap,
            price_trend=price_trend,
            holder_trend=holder_trend,
            size_trend=size_trend,
            confidence=confidence,
            reason=reason
        )
        
        if is_trap:
            logger.warning(
                f"🚨 RETAIL TRAP DETECTED: {reason} | "
                f"Confidence={confidence:.0%}"
            )
            
        return signal
        
    def _calc_trend(self, data: List[float]) -> str:
        """Calculate trend direction from data"""
        if len(data) < 5:
            return "FLAT"
            
        # Compare first half to second half
        mid = len(data) // 2
        first_half_avg = sum(data[:mid]) / mid
        second_half_avg = sum(data[mid:]) / (len(data) - mid)
        
        if second_half_avg > first_half_avg * 1.05:  # +5% threshold
            return "UP" if data[0] < 1000 else "INCREASING"  # Contextual label
        elif second_half_avg < first_half_avg * 0.95:  # -5% threshold
            return "DOWN" if data[0] < 1000 else "DECREASING"
        else:
            return "FLAT"
            
    def reset(self):
        """Reset all history"""
        self.price_history = []
        self.holder_history = []
        self.avg_size_history = []
