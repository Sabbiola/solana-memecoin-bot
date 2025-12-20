"""
🛡️ Rugcheck Module

Comprehensive token safety analysis:
1. Mint Authority (can create more tokens?)
2. Freeze Authority (can freeze accounts?)
3. Top Holders Concentration
4. Liquidity Analysis
5. Token Age
6. Dev Wallet Detection
7. Social/Metadata Check
"""

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import aiohttp
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
import time
import base64

from ..constants import PUMP_PROGRAM, RAYDIUM_V4_PROGRAM, PUMP_AMM_PROGRAM


@dataclass
class RugcheckResult:
    """Result of rugcheck analysis."""
    mint: str
    is_safe: bool
    risk_score: int  # 0-100 (0=safe, 100=dangerous)
    risk_level: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    
    # Individual checks
    mint_authority_revoked: bool = True
    freeze_authority_revoked: bool = True
    top_holder_pct: float = 0.0
    top_3_holders_pct: float = 0.0
    top_10_holders_pct: float = 0.0
    liquidity_usd: float = 0.0
    liquidity_locked: bool = False
    token_age_hours: float = 0.0
    dev_holding_pct: float = 0.0
    has_social: bool = False
    
    # Warnings
    warnings: List[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        status = "✅ SAFE" if self.is_safe else "⚠️ RISKY"
        return f"{status} | Risk: {self.risk_score}/100 ({self.risk_level})"


class Rugchecker:
    """
    Comprehensive token rugcheck analyzer.
    
    Uses configurable thresholds from config that can be overridden via environment variables.
    """
    
    def __init__(self, session: aiohttp.ClientSession, client: AsyncClient):
        self.session = session
        self.client = client
        self._cache: Dict[str, Tuple[RugcheckResult, float]] = {}
        self._cache_ttl = 60  # 1 min cache
        
        # Load thresholds from config (supports env var overrides)
        try:
            from ..config import (
                EARLY_MAX_TOP_HOLDER_PCT, EARLY_MAX_TOP3_HOLDERS_PCT, EARLY_MAX_DEV_PCT,
                EARLY_MIN_LIQUIDITY_USD, EARLY_MAX_SAFE_SCORE,
                STABLE_MAX_TOP_HOLDER_PCT, STABLE_MAX_TOP3_HOLDERS_PCT, STABLE_MAX_DEV_PCT,
                STABLE_MIN_LIQUIDITY_USD, STABLE_MAX_SAFE_SCORE
            )

            
            # STABLE thresholds (mature Jupiter tokens)
            self.THRESHOLDS = {
                "holder_max_pct": STABLE_MAX_TOP_HOLDER_PCT,
                "top3_max_pct": STABLE_MAX_TOP3_HOLDERS_PCT,
                "min_liquidity_usd": STABLE_MIN_LIQUIDITY_USD,
                "min_age_hours": 0.5,
                "dev_max_pct": STABLE_MAX_DEV_PCT,
                "max_safe_score": STABLE_MAX_SAFE_SCORE,
            }
            
            # EARLY thresholds (fresh pump.fun tokens on bonding curve)
            self.EARLY_THRESHOLDS = {
                "holder_max_pct": EARLY_MAX_TOP_HOLDER_PCT,
                "top3_max_pct": EARLY_MAX_TOP3_HOLDERS_PCT,
                "min_liquidity_usd": EARLY_MIN_LIQUIDITY_USD,
                "min_age_hours": 0.0,
                "dev_max_pct": EARLY_MAX_DEV_PCT,
                "max_safe_score": EARLY_MAX_SAFE_SCORE,
            }
            
        except ImportError:
            # Fallback to hardcoded defaults if config import fails
            self.THRESHOLDS = {
                "holder_max_pct": 10.0,
                "top3_max_pct": 30.0,
                "min_liquidity_usd": 5000,
                "min_age_hours": 0.5,
                "dev_max_pct": 5.0,
                "max_safe_score": 39,
            }
            
            self.EARLY_THRESHOLDS = {
                "holder_max_pct": 20.0,
                "top3_max_pct": 50.0,
                "min_liquidity_usd": 1000,
                "min_age_hours": 0.0,
                "dev_max_pct": 15.0,
                "max_safe_score": 49,
            }
    
    async def check(self, mint_str: str, early_mode: bool = False) -> RugcheckResult:
        """
        Perform comprehensive rugcheck on token.
        
        Args:
            mint_str: Token mint address
            early_mode: If True, use relaxed thresholds for EARLY tokens (pump.fun)
        """
        # Check cache
        if mint_str in self._cache:
            result, timestamp = self._cache[mint_str]
            if time.time() - timestamp < self._cache_ttl:
                return result
        
        result = RugcheckResult(
            mint=mint_str,
            is_safe=True,
            risk_score=0,
            risk_level="LOW"
        )
        
        warnings = []
        risk_points = 0
        
        # Select thresholds based on mode
        thresholds = self.EARLY_THRESHOLDS if early_mode else self.THRESHOLDS
        mode_label = "EARLY" if early_mode else "STABLE"
        
        try:
            # Run all checks in parallel
            checks = await asyncio.gather(
                self._check_authorities(mint_str),
                self._check_holders(mint_str),
                self._check_liquidity(mint_str),
                self._check_token_age(mint_str),
                self._check_social(mint_str),
                return_exceptions=True
            )
            
            auth_result, holder_result, liq_result, age_result, social_result = checks
            
            # =============================================
            # 1. MINT/FREEZE AUTHORITY
            # =============================================
            if isinstance(auth_result, dict):
                result.mint_authority_revoked = auth_result.get("mint_revoked", False)
                result.freeze_authority_revoked = auth_result.get("freeze_revoked", False)
                
                if not result.mint_authority_revoked:
                    warnings.append("⚠️ MINT AUTHORITY NOT REVOKED - Can create unlimited tokens!")
                    risk_points += 40
                
                if not result.freeze_authority_revoked:
                    warnings.append("⚠️ FREEZE AUTHORITY NOT REVOKED - Can freeze your tokens!")
                    risk_points += 30
            
            # =============================================
            # 2. HOLDER CONCENTRATION
            # =============================================
            if isinstance(holder_result, dict):
                result.top_holder_pct = holder_result.get("top_holder_pct", 0)
                result.top_3_holders_pct = holder_result.get("top3_pct", 0)
                result.top_10_holders_pct = holder_result.get("top10_pct", 0)
                result.dev_holding_pct = holder_result.get("dev_pct", 0)
                
                if result.top_holder_pct > thresholds["holder_max_pct"]:
                    warnings.append(f"⚠️ Top holder owns {result.top_holder_pct:.1f}%")
                    risk_points += 20
                
                if result.top_3_holders_pct > thresholds["top3_max_pct"]:
                    warnings.append(f"⚠️ Top 3 holders own {result.top_3_holders_pct:.1f}%")
                    risk_points += 15
                
                if result.dev_holding_pct > thresholds["dev_max_pct"]:
                    warnings.append(f"⚠️ Dev wallet holds {result.dev_holding_pct:.1f}%")
                    risk_points += 15
            
            # =============================================
            # 3. LIQUIDITY (DEX or Bonding Curve)
            # =============================================
            if isinstance(liq_result, dict):
                result.liquidity_usd = liq_result.get("liquidity_usd", 0)
                result.liquidity_locked = liq_result.get("locked", False)
                
                # If no DEX liquidity, check bonding curve
                if result.liquidity_usd == 0:
                    try:
                        from .validator import Validator
                        validator = Validator(self.session, self.client)
                        bc_state = await validator.get_bonding_curve_state(mint_str)
                        if bc_state and bc_state.get('sol_reserves', 0) > 0:
                            # Bonding curve has liquidity
                            sol_reserves = bc_state['sol_reserves'] / 1e9  # Convert lamports
                            result.liquidity_usd = sol_reserves * 150  # Assume SOL = $150
                            result.liquidity_locked = True  # BC is always "locked"
                            print(f"[RUGCHECK] Using bonding curve liquidity: {sol_reserves:.2f} SOL (${result.liquidity_usd:.0f})")
                    except Exception as e:
                        print(f"[RUGCHECK] BC liquidity check failed: {e}")
                
                if result.liquidity_usd < thresholds["min_liquidity_usd"]:
                    warnings.append(f"⚠️ Low liquidity: ${result.liquidity_usd:,.0f}")
                    risk_points += 15
                
                if not result.liquidity_locked and result.liquidity_usd > 0:
                    # Less penalty for unlocked liquidity in EARLY mode (BC is inherently unlocked)
                    if early_mode:
                        risk_points += 5  # Minor penalty
                    else:
                        warnings.append("⚠️ Liquidity NOT locked")
                        risk_points += 10
            
            # =============================================
            # 4. TOKEN AGE
            # =============================================
            if isinstance(age_result, (int, float)):
                result.token_age_hours = age_result
                
                if result.token_age_hours < thresholds["min_age_hours"]:
                    # Skip age warning in EARLY mode (we WANT new tokens)
                    if not early_mode:
                        warnings.append(f"⚠️ Very new token: {result.token_age_hours:.1f}h old")
                        risk_points += 10
            
            # =============================================
            # 5. SOCIAL PRESENCE
            # =============================================
            if isinstance(social_result, dict):
                result.has_social = social_result.get("has_social", False)
                
                # No social penalty in EARLY mode (new tokens don't have socials yet)
                if not result.has_social and not early_mode:
                    warnings.append("⚠️ No social links found")
                    risk_points += 5
            
        except Exception as e:
            warnings.append(f"⚠️ Check error: {e}")
            risk_points += 10
        
        # Calculate final risk (use mode-specific max safe score)
        result.risk_score = min(100, risk_points)
        result.warnings = warnings
        max_safe_score = thresholds["max_safe_score"]
        
        if result.risk_score >= 60:
            result.risk_level = "CRITICAL"
            result.is_safe = False
        elif result.risk_score >= 40:
            result.risk_level = "HIGH"
            # In EARLY mode, HIGH can still be safe if under max_safe_score
            result.is_safe = early_mode and result.risk_score <= max_safe_score
        elif result.risk_score >= 20:
            result.risk_level = "MEDIUM"
            result.is_safe = True  # Proceed with caution
        else:
            result.risk_level = "LOW"
            result.is_safe = True
        
        # Log mode used
        print(f"[RUGCHECK] Mode={mode_label} | Score={result.risk_score} | Safe={result.is_safe}")
        
        # Cache result
        self._cache[mint_str] = (result, time.time())
        
        return result
    
    async def _check_authorities(self, mint_str: str) -> Dict:
        """Check mint and freeze authorities."""
        try:
            mint = Pubkey.from_string(mint_str)
            resp = await self.client.get_account_info(mint)
            
            if not resp.value:
                return {"mint_revoked": False, "freeze_revoked": False}
            
            # Parse mint account data
            # Mint account layout: 36 bytes for mint authority (32 pubkey + 4 option)
            # Then 8 bytes supply, 1 byte decimals, 1 byte is_initialized
            # Then 36 bytes for freeze authority
            
            data = resp.value.data
            if isinstance(data, (list, tuple)):
                import base64
                data = base64.b64decode(data[0])
            elif isinstance(data, str):
                import base64
                data = base64.b64decode(data)
            else:
                data = bytes(data)
            
            # Check if mint authority is None (revoked)
            # Option<Pubkey> = 4 bytes (0 = None, 1 = Some) + 32 bytes pubkey
            mint_auth_option = data[0:4]
            mint_revoked = mint_auth_option == b'\x00\x00\x00\x00'
            
            # Freeze authority at offset 46 (36 + 8 + 1 + 1)
            freeze_auth_option = data[46:50]
            freeze_revoked = freeze_auth_option == b'\x00\x00\x00\x00'
            
            return {
                "mint_revoked": mint_revoked,
                "freeze_revoked": freeze_revoked
            }
            
        except Exception as e:
            print(f"[RUGCHECK] Authority check error: {e}")
            return {"mint_revoked": False, "freeze_revoked": False}
    
    async def _check_holders(self, mint_str: str) -> Dict:
        """Check holder concentration."""
        try:
            mint = Pubkey.from_string(mint_str)
            
            # Get largest accounts
            largest = await self.client.get_token_largest_accounts(mint)
            if not largest.value:
                return {"top_holder_pct": 0, "top3_pct": 0, "dev_pct": 0}
            
            # Get total supply
            supply_resp = await self.client.get_token_supply(mint)
            if not supply_resp.value:
                return {"top_holder_pct": 0, "top3_pct": 0, "dev_pct": 0}
            
            total_supply = int(supply_resp.value.amount)
            if total_supply == 0:
                return {"top_holder_pct": 0, "top3_pct": 0, "dev_pct": 0}
            
            # Calculate percentages
            filtered_holders = []
            
            # Calucate Bonding Curve PDA for this mint
            try:
                bonding_curve_pda, _ = Pubkey.find_program_address(
                    [b"bonding-curve", bytes(mint)], 
                    PUMP_PROGRAM
                )
                bonding_curve_str = str(bonding_curve_pda)
            except:
                bonding_curve_str = ""

            # Programs/Authorities to IGNORE (Bonding curves, Raydium Vaults, etc.)
            ignored_authorities = [
                 bonding_curve_str,
                 str(RAYDIUM_V4_PROGRAM),   # Only if vault authority matches program? Usually valid.
                 "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", # Raydium Authority
                 "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"  # Raydium V4 Program ID (sometimes authority)
            ]
            
            # Smart Check: Exclude Program Accounts (Bonding Curves, Pools)
            # Batch fetch all top 10 accounts at once
            top_accounts = largest.value[:10]
            if not top_accounts:
                return {"top_holder_pct": 0, "top3_pct": 0, "dev_pct": 0}
                
            account_pubkeys = [acc.address for acc in top_accounts]
            
            try:
                # Use get_multiple_accounts instead of loop
                resp = await self.client.get_multiple_accounts(account_pubkeys)
                account_infos = resp.value
            except Exception as e:
                print(f"[RUGCHECK] Batch account fetch failed: {e}")
                # Fallback to empty list or handle gracefully - will likely result in keeping holders
                account_infos = [None] * len(account_pubkeys)

            for i, acc in enumerate(top_accounts):
                try:
                    # Extract amount
                    if hasattr(acc, 'ui_token_amount') and hasattr(acc.ui_token_amount, 'amount'):
                        amount = int(acc.ui_token_amount.amount)
                    elif hasattr(acc, 'amount'):
                        if hasattr(acc.amount, 'amount'):
                            amount = int(acc.amount.amount)
                        else:
                            amount = int(acc.amount)
                    else:
                        amount = 0
                        
                    # Calculate pct first
                    pct = (amount / total_supply) * 100
                    
                    # Check owner/authority from batched response
                    acc_info = account_infos[i]
                    
                    if acc_info:
                        # Decode data
                        data = acc_info.data
                        if isinstance(data, (list, tuple)):
                            data_bytes = base64.b64decode(data[0])
                        elif isinstance(data, str):
                            data_bytes = base64.b64decode(data)
                        else:
                            data_bytes = bytes(data)
                            
                        # Token Account Layout:
                        # Mint (0-32)
                        # Owner/Authority (32-64)
                        # Amount (64-72)
                        if len(data_bytes) >= 64:
                            authority_bytes = data_bytes[32:64]
                            authority = Pubkey.from_bytes(authority_bytes)
                            authority_str = str(authority)
                            
                            if authority_str in ignored_authorities or authority_str == bonding_curve_str:
                                # print(f"[RUGCHECK] 🛡️ Ignoring Program Holder: {str(acc.address)[:8]}... (Auth: {authority_str[:8]}...) - Holds {pct:.1f}%")
                                continue
                            else:
                                pass
                                # print(f"[RUGCHECK] ⚠️ Keeping Holder: {str(acc.address)[:8]}... (Auth: {authority_str[:8]}...) - Holds {pct:.1f}%")
                    
                    filtered_holders.append(pct)
                    
                except Exception as e:
                    print(f"[RUGCHECK] Holder analysis error: {e}")
                    # If we fail to check, assume it's a regular holder to be safe
                    pass

            top_holder = filtered_holders[0] if filtered_holders else 0
            top_3 = sum(filtered_holders[:3]) if len(filtered_holders) >= 3 else sum(filtered_holders)
            top_10 = sum(filtered_holders[:10]) if len(filtered_holders) >= 10 else sum(filtered_holders)
            
            # Dev holding estimate (largest non-LP holder)
            # Since we filtered out LPs/Curves, the largest remaining is likely the "Dev" or "Whale"
            dev_pct = filtered_holders[0] if filtered_holders else 0
            
            return {
                "top_holder_pct": top_holder,
                "top3_pct": top_3,
                "top10_pct": top_10,
                "dev_pct": dev_pct
            }
            
        except Exception as e:
            print(f"[RUGCHECK] Holder check error: {e}")
            return {"top_holder_pct": 0, "top3_pct": 0, "dev_pct": 0}
    
    async def _check_liquidity(self, mint_str: str) -> Dict:
        """Check liquidity from DexScreener."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_str}"
            async with self.session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        # Sum liquidity across all pairs
                        total_liq = sum(
                            float(p.get("liquidity", {}).get("usd", 0))
                            for p in pairs
                        )
                        return {
                            "liquidity_usd": total_liq,
                            "locked": False  # Would need to check lock contract
                        }
        except Exception as e:
            print(f"[RUGCHECK] Liquidity check error: {e}")
        
        return {"liquidity_usd": 0, "locked": False}
    
    async def _check_token_age(self, mint_str: str) -> float:
        """Get token age in hours from DexScreener."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_str}"
            async with self.session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        created_at = pairs[0].get("pairCreatedAt", 0)
                        if created_at:
                            age_ms = time.time() * 1000 - created_at
                            return age_ms / (1000 * 60 * 60)  # Hours
        except Exception as e:
            print(f"[RUGCHECK] Age check error: {e}")
        
        return 0.0
    
    async def _check_social(self, mint_str: str) -> Dict:
        """Check for social links."""
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_str}"
            async with self.session.get(url, timeout=5) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        info = pairs[0].get("info", {})
                        socials = info.get("socials", [])
                        websites = info.get("websites", [])
                        
                        has_social = len(socials) > 0 or len(websites) > 0
                        return {"has_social": has_social}
        except Exception as e:
            print(f"[RUGCHECK] Social check error: {e}")
        
        return {"has_social": False}
    
    def format_report(self, result: RugcheckResult) -> str:
        """Format rugcheck result as readable report."""
        emoji = "✅" if result.is_safe else "🔴"
        
        report = f"""
{emoji} RUGCHECK REPORT
{'=' * 50}
Token: {result.mint[:20]}...
Risk Score: {result.risk_score}/100 ({result.risk_level})
Decision: {"✅ SAFE TO TRADE" if result.is_safe else "❌ HIGH RISK"}

📋 CHECK RESULTS:
  Mint Authority:    {"✅ Revoked" if result.mint_authority_revoked else "❌ ACTIVE"}
  Freeze Authority:  {"✅ Revoked" if result.freeze_authority_revoked else "❌ ACTIVE"}
  Top Holder:        {result.top_holder_pct:.1f}% {"✅" if result.top_holder_pct < 10 else "⚠️"}
  Top 3 Holders:     {result.top_3_holders_pct:.1f}% {"✅" if result.top_3_holders_pct < 30 else "⚠️"}
  Liquidity:         ${result.liquidity_usd:,.0f} {"✅" if result.liquidity_usd > 5000 else "⚠️"}
  Token Age:         {result.token_age_hours:.1f}h {"✅" if result.token_age_hours > 0.5 else "⚠️"}
  Social Links:      {"✅ Yes" if result.has_social else "⚠️ No"}
"""
        
        if result.warnings:
            report += f"\n⚠️ WARNINGS:\n"
            for w in result.warnings:
                report += f"  {w}\n"
        
        return report


# =============================================
# TEST
# =============================================

async def test_rugcheck(mint: str = None):
    """Test rugcheck on a token."""
    import sys
    sys.path.append('.')
    from solana_bot.config import RPC_URL
    
    print("\n" + "=" * 60)
    print("🛡️ RUGCHECK TEST")
    print("=" * 60)
    
    session = aiohttp.ClientSession()
    client = AsyncClient(RPC_URL)
    checker = Rugchecker(session, client)
    
    test_mint = mint or "FQ7B6Eq6DQE8Y3sU3dfik3PmyGvZ5LskjzySAc5ipump"
    
    try:
        print(f"\nChecking: {test_mint[:24]}...")
        result = await checker.check(test_mint)
        print(checker.format_report(result))
        
    finally:
        await session.close()
        await client.close()
    
    print("✅ Test complete!\n")


if __name__ == "__main__":
    import sys
    mint = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(test_rugcheck(mint))
