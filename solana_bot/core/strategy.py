
from typing import Dict, Any, Optional

from ..config import (
    FRESH_TOKEN_MAX_AGE_HOURS, FRESH_TOKEN_MAX_MC, 
    FRESH_BUY_AMOUNT, FRESH_TRAILING_STOP,
    MATURE_BUY_AMOUNT, MATURE_TRAILING_STOP
)
from .validator import Validator

def compute_trust_score_robust(dev_holding_pct: float, dev_sold: bool) -> int:
    score = 100
    if dev_holding_pct < 1.0: score -= 20
    if dev_sold: score -= 30
    return max(0, score)

def model_scoring_stub(tx_data: Dict) -> float:
    # 0.0 - 1.0 score based on volume/impact
    return 0.85 # Mock

class StrategyManager:
    def __init__(self, validator: Validator):
        self.validator = validator

    async def select_strategy(self, mint: str, market_cap: float) -> Dict[str, Any]:
        """Select trading strategy based on token characteristics - MOONBAG approach."""
        age_hours = await self.validator.get_token_age_hours(mint)
        
        # Fresh token strategy - aggressive trailing stop for quick pumps
        if age_hours < FRESH_TOKEN_MAX_AGE_HOURS and market_cap < FRESH_TOKEN_MAX_MC:
            return {
                "type": "FRESH_MOONBAG",
                "buy_amount": FRESH_BUY_AMOUNT,
                "trailing_stop": FRESH_TRAILING_STOP,
            }
        
        # Mature token strategy - wider trailing stop for trend riding
        else:
            return {
                "type": "MATURE_MOONBAG",
                "buy_amount": MATURE_BUY_AMOUNT,
                "trailing_stop": MATURE_TRAILING_STOP,
            }

    def calculate_dynamic_score(self, trust_score: int, quality_score: int) -> Dict[str, Any]:
        """
        Combine Trust Score and Quality Score to determine trade recommendation.
        Returns: {'score': int, 'recommendation': str}
        """
        # Weighted Score: 60% Trust, 40% Quality
        final_score = int((trust_score * 0.6) + (quality_score * 0.4))
        
        # Sizing Logic
        if final_score >= 75:
            rec = "FULL_SIZE"
        elif final_score >= 50:
            rec = "HALF_SIZE"
        elif final_score >= 30:
            rec = "QUARTER_SIZE"
        else:
            rec = "SKIP"
            
        return {"score": final_score, "recommendation": rec}
