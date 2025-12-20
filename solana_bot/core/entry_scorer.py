"""
Entry Scoring System

Probabilistic scoring for token entry decisions.
Combines multiple quality metrics into single 0-100 score.

Only enter trades with score >= threshold (default 70).

Components:
- LP Health (30pts)
- Dev Safety (25pts)  
- Volume Quality (20pts)
- Holder Distribution (15pts)
- Momentum (10pts)
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TokenScore:
    """Complete scoring breakdown"""
    lp_health: float
    dev_safety: float
    volume_quality: float
    holder_distribution: float
    asymmetry: float  # Changed from momentum
    total: float
    passed_threshold: bool
    reason: str = ""


class EntryScorer:
    """
    Score token opportunities for entry quality.
    
    Usage:
        scorer = EntryScorer(threshold=70)
        score = await scorer.score_token(opp, rugcheck, volume_metrics)
        if score.passed_threshold:
            # High quality setup, proceed with entry
    """
    
    def __init__(self, threshold: float = 50.0):  # LOWERED from 70 to 50
        self.threshold = threshold
        
    async def score_token(
        self,
        opportunity,
        rugcheck_result=None,
        volume_metrics=None,
        pool_check=None
    ) -> TokenScore:
        """
        Calculate comprehensive quality score for a token.
        
        Uses DYNAMIC thresholds based on token phase:
        - BONDING_CURVE: 40 (more aggressive for fresh tokens)
        - Others: 50 (standard)
        
        Returns TokenScore with:
            - total: 0-100 score
            - passed_threshold: True if >= threshold
            - breakdown: Dict of component scores
            - reason: Human-readable explanation
        """
        # Calculate individual components
        # FIXED WEIGHTS: Asymmetry now DOMINATES (was 10/100, now 30/100)
        
        # 🎯 DYNAMIC THRESHOLD based on phase
        phase = getattr(opportunity, 'phase', 'UNKNOWN')
        if phase == "BONDING_CURVE":
            effective_threshold = 40.0  # More aggressive for ultra-fresh tokens
        else:
            effective_threshold = self.threshold  # 50.0 for others
            
        lp_score = min(self._score_lp_health(opportunity, pool_check), 20.0)  # Reduced from 30
        dev_score = min(self._score_dev_safety(rugcheck_result), 20.0)  # Reduced from 25
        volume_score = self._score_volume_quality(volume_metrics, opportunity)  # Unchanged 20
        holder_score = min(self._score_holder_distribution(rugcheck_result), 10.0)  # Reduced from 15
        asymmetry_score = self._score_asymmetry(opportunity, pool_check)  # Increased to 30
        
        # Total possible: 100 (20+20+20+10+30)
        total = lp_score + dev_score + volume_score + holder_score + asymmetry_score
        
        # Final evaluation
        total = lp_score + dev_score + volume_score + holder_score + asymmetry_score
        passed = total >= effective_threshold  # Use dynamic threshold
        
        reason = f"LP:{lp_score:.0f} Dev:{dev_score:.0f} Vol:{volume_score:.0f} Hold:{holder_score:.0f} Asym:{asymmetry_score:.0f}"
        
        score = TokenScore(
            lp_health=lp_score,
            dev_safety=dev_score,
            volume_quality=volume_score,
            holder_distribution=holder_score,
            asymmetry=asymmetry_score,
            total=total,
            passed_threshold=passed,
            reason=reason
        )
        
        # Log result
        symbol = opportunity.symbol
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(
            f"   📊 SCORE: {symbol} = {total:.0f}/100 {status} | {reason}"
        )
        
        return score
        
    def _score_lp_health(self, opportunity, pool_check) -> float:
        """
        Score LP health and stability (0-20pts).
        
        - Baseline liquidity: 0-10pts
        - LP lock status: 0-5pts  
        - LP stability: 0-5pts
        
        MORE FORGIVING: Fresh pump.fun tokens have low liquidity by design
        """
        score = 0.0
        
        liquidity_usd = opportunity.liquidity_usd
        
        # Baseline liquidity (0-10pts) - MORE FORGIVING for low-liq pump.fun tokens
        if liquidity_usd >= 50000:
            score += 10  # Excellent
        elif liquidity_usd >= 10000:
            score += 9   # Very good
        elif liquidity_usd >= 5000:
            score += 8   # Good
        elif liquidity_usd >= 1000:
            score += 7   # Acceptable (typical pump.fun)
        elif liquidity_usd >= 500:
            score += 6   # Low but workable
        else:
            score += 4   # Very low (was 2, now more forgiving)
            
        # LP lock (0-5pts) - from pool_check if available
        if pool_check:
            lp_locked = pool_check.get("lp_locked", False)
            if lp_locked:
                score += 5
            else:
                score += 2  # Partial credit for existing LP
        else:
            score += 2  # Default partial credit
            
        # LP stability (0-5pts) - assume stable for now
        # In full version, would check historical LP changes
        score += 3  # Assume mostly stable (was 7, reduced weight)
        
        return min(score, 20.0)
        
    def _score_dev_safety(self, rugcheck_result) -> float:
        """
        Score developer safety (0-20pts).
        
        - Dev holdings: 0-12pts
        - Authority revoked: 0-8pts
        """
        if not rugcheck_result:
            return 15.0  # MORE FORGIVING: Neutral-positive score if no data (was 10)
            
        score = 0.0
        
        # Dev holdings (0-12pts)
        dev_pct = rugcheck_result.dev_holding_pct
        if dev_pct < 10:
            score += 12  # Excellent
        elif dev_pct < 15:
            score += 8   # Good
        elif dev_pct < 20:
            score += 4   # Acceptable
        else:
            score += 0   # Risky
            
        # Mint/Freeze authority (0-8pts)
        if rugcheck_result.mint_authority_revoked:
            score += 4
        if rugcheck_result.freeze_authority_revoked:
            score += 4
            
        return min(score, 20.0)
        
    def _score_volume_quality(self, volume_metrics, opportunity) -> float:
        """
        Score volume quality (0-20pts).
        
        - VQR ratio: 0-15pts
        - Unique wallets: 0-5pts
        """
        score = 0.0
        
        if volume_metrics:
            vqr = volume_metrics.vqr
            unique = volume_metrics.unique_wallets
            
            # VQR (0-15pts) - lower is better
            if vqr < 1000:
                score += 15  # Excellent organic volume
            elif vqr < 3000:
                score += 10  # Good
            elif vqr < 5000:
                score += 5   # Acceptable
            else:
                score += 0   # Suspicious
                
            # Unique wallets (0-5pts)
            if unique >= 100:
                score += 5
            elif unique >= 50:
                score += 3
            else:
                score += 1
        else:
            # No volume data - conservative score
            score += 8
            
        return min(score, 20.0)
        
    def _score_holder_distribution(self, rugcheck_result) -> float:
        """
        Score holder distribution (0-10pts).
        
        - Top holder concentration: 0-10pts
        """
        if not rugcheck_result:
            return 8.0  # MORE FORGIVING: Near-max if no data (was 7)
            
        score = 0.0
        
        top10_pct = rugcheck_result.top_10_holders_pct
        
        if top10_pct < 30:
            score += 10  # Excellent distribution
        elif top10_pct < 45:
            score += 8   # Good
        elif top10_pct < 60:
            score += 4   # Acceptable
        else:
            score += 0   # Too concentrated
            
        return min(score, 10.0)
        
    def _score_asymmetry(self, opportunity, pool_check=None) -> float:
        """
        Execution-Aware Asymmetry Score (0-30pts). CRITICAL FIX v12.3.1.
        
        OLD BUG: hard_stop was included in downside, making EAS always < 1.0
        NEW: Calculate actual execution risk (slippage + MEV), not stop loss
        
        Components:
        - ExecutableUpside: realistic profit potential with fill probability
        - ExecutableDownside: slippage + MEV risk (NOT stop loss)
        
        Threshold: EAS < 1.0 → 0 points
        """
        score = 0.0
        
        # Get liquidity data for depth estimation
        liquidity_usd = opportunity.liquidity_usd
        price_change = opportunity.price_change_24h
        
        # === EXECUTABLE UPSIDE ===
        # Based on liquidity depth and momentum
        
        if liquidity_usd > 0:
            # Position size in USD (0.05 SOL * ~$200)
            position_size_usd = 10.0  # ~$10 position
            
            # Depth ratio: how many times can we fill our position
            depth_ratio = liquidity_usd / max(position_size_usd, 1)
            
            # Base upside potential (typical pump target)
            base_upside = 25.0  # 25% target
            
            # Adjust for depth (shallow = harder to exit at profit)
            if depth_ratio > 1000:
                depth_factor = 1.0  # Deep liquidity
            elif depth_ratio > 500:
                depth_factor = 0.85
            elif depth_ratio > 100:
                depth_factor = 0.7
            else:
                depth_factor = 0.5  # Shallow, risky
                
            # Momentum factor (price_change as proxy)
            if price_change > 20:
                momentum_factor = 0.9  # Strong momentum
            elif price_change > 5:
                momentum_factor = 0.7
            elif price_change > 0:
                momentum_factor = 0.5
            else:
                momentum_factor = 0.3  # Negative momentum
                
            executable_upside = base_upside * depth_factor * momentum_factor
        else:
            executable_upside = 5.0  # Conservative default
            
        # === EXECUTABLE DOWNSIDE ===
        # Actual execution risk, NOT stop loss distance
        
        # Base slippage on exit (worse in dumps)
        base_slippage = 3.0  # 3% typical for memecoins
        
        # MEV risk (sandwich attacks, front-running)
        mev_risk = 2.0  # 2% extraction estimate
        
        # Liquidity-based risk (low liq = higher slippage)
        if liquidity_usd > 50000:
            liq_risk = 1.0
        elif liquidity_usd > 20000:
            liq_risk = 2.0
        elif liquidity_usd > 10000:
            liq_risk = 3.0
        else:
            liq_risk = 5.0  # Very low liquidity
            
        executable_downside = base_slippage + mev_risk + liq_risk
        
        # === CALCULATE EAS ===
        if executable_downside > 0:
            eas = executable_upside / executable_downside
        else:
            eas = 1.0
            
        # === SCORING ===
        # Graduated scoring, not hard gate
        if eas < 0.8:
            score = 0  # Very poor asymmetry
        elif eas < 1.0:
            score = 5  # Below break-even
        elif eas < 1.2:
            score = 10  # Marginal
        elif eas < 1.5:
            score = 15  # Acceptable
        elif eas < 2.0:
            score = 22  # Good
        else:
            score = 30  # Excellent edge
            
        # Log EAS for analysis
        logger.info(
            f"   🎯 EAS = {eas:.2f} | "
            f"Up={executable_upside:.1f}% Down={executable_downside:.1f}% | "
            f"Score={score:.0f}/30"
        )
        
        return score
