from __future__ import annotations

import logging

from solana_bot.config import Settings
from solana_bot.core.models import Phase, RugcheckResult, TokenInfo

logger = logging.getLogger(__name__)


class Rugchecker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_client = None
        if settings.RUGCHECK_API_ENABLED:
            from solana_bot.core.rugcheck_client import RugCheckClient
            self.api_client = RugCheckClient(settings)

    async def check(
        self, 
        token: TokenInfo, 
        phase: Phase, 
        mode: str,
        current_pnl_pct: float = 0.0
    ) -> RugcheckResult:
        """
        Enhanced rugcheck with PnL-based grace period and detailed logging.
        
        Args:
            token: Token to check
            phase: Current phase (BONDING_CURVE or RAYDIUM)
            mode: Position state (SCOUT, CONFIRM, CONVICTION, MOONBAG)
            current_pnl_pct: Current PnL percentage (e.g., 0.23 for +23%)
        """
        metadata = token.metadata
        dev_holding_raw = metadata.get("dev_holding")
        top10_raw = metadata.get("top10_holding")
        missing_holders = dev_holding_raw is None or top10_raw is None
        dev_holding = float(dev_holding_raw) if dev_holding_raw is not None else 0.0
        top10 = float(top10_raw) if top10_raw is not None else 0.0
        mint_active = bool(metadata.get("mint_authority_active", False))
        freeze_active = bool(metadata.get("freeze_authority_active", False))
        is_bonding = (phase == Phase.BONDING_CURVE)
        allow_mint = is_bonding
        mint_ok = (not mint_active) or allow_mint

        flags: list[str] = []
        
        # Check for missing holder stats
        if missing_holders and mode != "SCOUT":
            flags.append("HOLDER_STATS_MISSING")
        
        # Check freeze authority
        if freeze_active and not (phase == Phase.BONDING_CURVE and self.settings.ALLOW_FREEZE_ON_PUMPFUN):
            flags.append("FREEZE_AUTHORITY_ACTIVE")
        
        # Check mint authority
        if mint_active and mode in ("CONFIRM", "CONVICTION", "MOONBAG"):
            flags.append("MINT_AUTHORITY_ACTIVE")

        # Calculate risk score
        risk_score = 100.0 * (
            0.45 * dev_holding + 0.35 * top10 + 0.1 * (1.0 if mint_active else 0.0)
            + 0.1 * (1.0 if freeze_active else 0.0)
        )
        risk_score = max(0.0, min(100.0, risk_score))

        # Determine risk level
        if risk_score < 40:
            risk_level = "LOW"
        elif risk_score < 60:
            risk_level = "MEDIUM"
        elif risk_score < 80:
            risk_level = "HIGH"
        else:
            risk_level = "CRITICAL"

        # Grace period logic - BYPASS rugcheck if profitable enough
        is_safe = False # Default to unsafe, will be set to True if conditions met
        grace_period_applied = False
        grace_reason = None
        
        if current_pnl_pct >= self.settings.RUGCHECK_GRACE_PNL_HIGH:
            # High profit - ignore rugcheck completely
            is_safe = True
            grace_period_applied = True
            grace_reason = f"HIGH_PROFIT_GRACE (PnL: +{current_pnl_pct*100:.1f}%)"
            flags.append(grace_reason)
            
            if self.settings.RUGCHECK_DETAILED_LOGGING:
                logger.info(
                    f"🎯 Rugcheck BYPASSED for {token.symbol} - High profit grace period "
                    f"(PnL: +{current_pnl_pct*100:.1f}% >= {self.settings.RUGCHECK_GRACE_PNL_HIGH*100:.0f}%)"
                )
        
        elif current_pnl_pct >= self.settings.RUGCHECK_GRACE_PNL_LOW:
            # Medium profit - apply lenient thresholds
            grace_period_applied = True
            grace_reason = f"LENIENT_GRACE (PnL: +{current_pnl_pct*100:.1f}%)"
            
            # Lenient thresholds (roughly 1.5x more permissive)
            if mode == "SCOUT":
                is_safe = risk_score <= 80 and dev_holding <= 0.12 and top10 <= 0.85
            elif mode == "CONFIRM":
                is_safe = mint_ok and risk_score <= 60 and dev_holding <= 0.08 and top10 <= 0.70
            elif mode == "CONVICTION":
                if self.settings.RUGCHECK_DISABLE_ON_CONVICTION:
                    is_safe = True
                else:
                     is_safe = mint_ok and risk_score <= 50 and dev_holding <= 0.05 and top10 <= 0.60
            else: # MOONBAG
                 is_safe = True
            
            if self.settings.RUGCHECK_DETAILED_LOGGING:
                 logger.info(f"🛡️ Rugcheck LENIENT for {token.symbol} (PnL +{current_pnl_pct*100:.1f}%) -> Safe={is_safe}")
        
        elif mode in ("CONVICTION", "MOONBAG") and self.settings.RUGCHECK_DISABLE_ON_CONVICTION:
            # Disable rugcheck on CONVICTION if configured
            is_safe = True
            grace_period_applied = True
            grace_reason = "CONVICTION_BYPASS"
            flags.append(grace_reason)
            
            if self.settings.RUGCHECK_DETAILED_LOGGING:
                logger.info(f"🎯 Rugcheck DISABLED for {token.symbol} - CONVICTION mode bypass")
        
        else:
            # No grace period - apply standard rugcheck
            if flags:
                # Hard flags present (missing stats, freeze/mint authority)
                is_safe = False
            else:
                # Standard thresholds
                if mode == "SCOUT":
                    is_safe = risk_score < 50 and dev_holding <= 0.09 and top10 < 0.65 and mint_ok
                else:  # CONVICTION/MOONBAG
                    mint_ok = (not mint_active) or allow_mint
                    is_safe = risk_score < 40 and dev_holding < 0.09 and top10 < 0.60 and mint_ok

        # API Check Integration (Move before logging)
        if is_safe and self.api_client and mode in ("SCOUT", "CONFIRM", "CONVICTION"):
             try:
                 report = await self.api_client.get_report(token.mint)
                 if report:
                     if report.score > self.settings.RUGCHECK_MAX_SCORE:
                          is_safe = False
                          flags.append(f"API_RISK_HIGH(score={report.score})")
                          risk_level = "CRITICAL"
                     
                     if report.rugs_detected:
                          is_safe = False
                          flags.append("API_RUG_DETECTED")
                          risk_level = "CRITICAL"
                     
                     if not is_safe and self.settings.RUGCHECK_DETAILED_LOGGING:
                         logger.warning(f"🎯 RugCheck API REJECTED {token.symbol}: Score {report.score}")
             except Exception as e:
                 logger.error(f"RugCheck API failed: {e}")

        # Detailed logging of rugcheck results
        if self.settings.RUGCHECK_DETAILED_LOGGING:
            status = "✅ PASS" if is_safe else "❌ FAIL"
            logger.info(
                f"🔍 Rugcheck {status} for {token.symbol} ({mode}) | "
                f"Risk: {risk_score:.1f} | Dev: {dev_holding*100:.1f}% | Top10: {top10*100:.1f}% | "
                f"Mint: {'✓' if mint_active else '✗'} | Freeze: {'✓' if freeze_active else '✗'} | "
                f"PnL: {'+' if current_pnl_pct >= 0 else ''}{current_pnl_pct*100:.1f}% | "
                f"Grace: {grace_reason if grace_period_applied else 'None'}"
            )
            
            # Log specific failure reasons if failed
            if not is_safe and not grace_period_applied:
                failure_reasons = []
                
                if mode == "SCOUT":
                    if risk_score > 65:
                        failure_reasons.append(f"risk_score {risk_score:.1f} > 65")
                    if dev_holding > 0.09:
                        failure_reasons.append(f"dev_holding {dev_holding*100:.1f}% > 9%")
                    if top10 > 0.75:
                        failure_reasons.append(f"top10 {top10*100:.1f}% > 75%")
                
                elif mode == "CONFIRM":
                    if risk_score >= 50:
                        failure_reasons.append(f"risk_score {risk_score:.1f} >= 50")
                    if dev_holding > 0.09:
                        failure_reasons.append(f"dev_holding {dev_holding*100:.1f}% > 9%")
                    if top10 >= 0.65:
                        failure_reasons.append(f"top10 {top10*100:.1f}% >= 65%")
                
                else:  # CONVICTION/MOONBAG
                    if risk_score >= 40:
                        failure_reasons.append(f"risk_score {risk_score:.1f} >= 40")
                
                # Add API flags to failure reasons for the log
                for f in flags:
                    if "API" in f:
                        failure_reasons.append(f)
                
                if failure_reasons:
                    logger.warning(
                        f"❌ Rugcheck FAILED for {token.symbol} - Reasons: {', '.join(failure_reasons)}"
                    )

        return RugcheckResult(
            is_safe=is_safe,
            risk_score=risk_score,
            risk_level=risk_level,
            flags=flags,
            details={
                "dev_holding": dev_holding,
                "top10": top10,
                "mint_active": mint_active,
                "freeze_active": freeze_active,
                "current_pnl_pct": current_pnl_pct,
                "grace_period_applied": grace_period_applied,
                "grace_reason": grace_reason,
            },
        )
