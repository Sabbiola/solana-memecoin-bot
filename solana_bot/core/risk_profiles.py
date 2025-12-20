"""
Risk-Based Trading Profiles

Adapts trailing stop and position parameters based on token risk level.
"""

from dataclasses import dataclass
from typing import Optional
from .rugcheck import RugcheckResult


@dataclass
class TradingProfile:
    """Trading parameters adapted to risk level."""
    
    name: str
    risk_level: str
    
    # Position sizing
    max_position_sol: float
    position_pct_of_balance: float  # % of wallet to use
    
    # Trailing stop settings
    initial_trailing_pct: float     # Before break-even
    post_be_trailing_pct: float     # After break-even
    hard_stop_pct: float            # Emergency stop
    
    # Break-even settings
    break_even_buffer_pct: float    # Buffer above break-even
    break_even_sell_pct: float      # % to sell at break-even
    
    # Entry filters
    min_liquidity_usd: float
    min_age_hours: float
    max_holder_pct: float
    
    # Trade behavior
    enabled: bool = True
    require_social: bool = False


# =============================================
# PREDEFINED PROFILES
# =============================================

PROFILES = {
    # 🟢 LOW RISK - Safe tokens, larger positions
    "LOW": TradingProfile(
        name="Conservative",
        risk_level="LOW",
        max_position_sol=0.05,
        position_pct_of_balance=10.0,
        initial_trailing_pct=10.0,
        post_be_trailing_pct=7.0,
        hard_stop_pct=-15.0,
        break_even_buffer_pct=0.5,
        break_even_sell_pct=50.0,
        min_liquidity_usd=10000,
        min_age_hours=1.0,
        max_holder_pct=15.0,
        enabled=True,
        require_social=False
    ),
    
    # 🟡 MEDIUM RISK - Standard tokens
    "MEDIUM": TradingProfile(
        name="Standard",
        risk_level="MEDIUM",
        max_position_sol=0.02,
        position_pct_of_balance=5.0,
        initial_trailing_pct=7.0,
        post_be_trailing_pct=5.0,
        hard_stop_pct=-10.0,
        break_even_buffer_pct=0.5,
        break_even_sell_pct=50.0,
        min_liquidity_usd=5000,
        min_age_hours=0.5,
        max_holder_pct=25.0,
        enabled=True,
        require_social=False
    ),
    
    # 🟠 HIGH RISK - Risky tokens, small positions, tight stops
    "HIGH": TradingProfile(
        name="Aggressive Scalp",
        risk_level="HIGH",
        max_position_sol=0.01,
        position_pct_of_balance=2.0,
        initial_trailing_pct=5.0,        # Stretto!
        post_be_trailing_pct=4.0,
        hard_stop_pct=-7.0,              # Stretto!
        break_even_buffer_pct=0.3,
        break_even_sell_pct=60.0,        # Vendi di più subito
        min_liquidity_usd=3000,
        min_age_hours=0.1,               # 6 min ok
        max_holder_pct=50.0,
        enabled=True,                    # Attivo per scalping
        require_social=False
    ),
    
    # 🔴 CRITICAL RISK - Very risky, micro positions
    "CRITICAL": TradingProfile(
        name="Extreme Scalp",
        risk_level="CRITICAL",
        max_position_sol=0.005,
        position_pct_of_balance=1.0,
        initial_trailing_pct=3.0,        # Molto stretto!
        post_be_trailing_pct=3.0,
        hard_stop_pct=-5.0,              # Molto stretto!
        break_even_buffer_pct=0.2,
        break_even_sell_pct=70.0,        # Vendi molto subito
        min_liquidity_usd=2000,
        min_age_hours=0.0,               # Anche nuovo
        max_holder_pct=100.0,            # Accetta tutto
        enabled=True,                    # ✅ ABILITATO per scalping
        require_social=False
    ),
    
    # ⛔ SKIP - Token da evitare
    "SKIP": TradingProfile(
        name="Skip",
        risk_level="SKIP",
        max_position_sol=0.0,
        position_pct_of_balance=0.0,
        initial_trailing_pct=0.0,
        post_be_trailing_pct=0.0,
        hard_stop_pct=0.0,
        break_even_buffer_pct=0.0,
        break_even_sell_pct=0.0,
        min_liquidity_usd=0,
        min_age_hours=0.0,
        max_holder_pct=0.0,
        enabled=False,
        require_social=False
    )
}


class RiskProfileManager:
    """
    Manages trading profiles based on token risk.
    """
    
    def __init__(self, enable_critical: bool = False):
        """
        Initialize with optional critical risk trading.
        
        Args:
            enable_critical: If True, allows trading CRITICAL risk tokens
        """
        self.profiles = PROFILES.copy()
        if enable_critical:
            self.profiles["CRITICAL"].enabled = True
    
    def get_profile(self, rugcheck_result: RugcheckResult) -> TradingProfile:
        """
        Get trading profile based on rugcheck result.
        """
        risk_level = rugcheck_result.risk_level
        
        # Special cases
        if not rugcheck_result.mint_authority_revoked:
            return self.profiles["SKIP"]  # Never trade mintable tokens
        
        if not rugcheck_result.freeze_authority_revoked:
            return self.profiles["SKIP"]  # Never trade freezable tokens
        
        return self.profiles.get(risk_level, self.profiles["SKIP"])
    
    def should_trade(self, rugcheck_result: RugcheckResult) -> tuple[bool, str, Optional[TradingProfile]]:
        """
        Determine if we should trade this token.
        
        Returns:
            (should_trade, reason, profile)
        """
        profile = self.get_profile(rugcheck_result)
        
        # Check if profile is enabled
        if not profile.enabled:
            return False, f"Profile {profile.name} disabled", None
        
        # Check liquidity
        if rugcheck_result.liquidity_usd < profile.min_liquidity_usd:
            return False, f"Liquidity ${rugcheck_result.liquidity_usd:.0f} < ${profile.min_liquidity_usd:.0f}", None
        
        # Check holder concentration
        if rugcheck_result.top_holder_pct > profile.max_holder_pct:
            return False, f"Top holder {rugcheck_result.top_holder_pct:.1f}% > {profile.max_holder_pct:.1f}%", None
        
        # Check age
        if rugcheck_result.token_age_hours < profile.min_age_hours:
            return False, f"Token age {rugcheck_result.token_age_hours:.1f}h < {profile.min_age_hours:.1f}h", None
        
        # Check social requirement
        if profile.require_social and not rugcheck_result.has_social:
            return False, "No social links found", None
        
        return True, f"Using profile: {profile.name}", profile
    
    def format_profile(self, profile: TradingProfile) -> str:
        """Format profile as readable string."""
        return f"""
⚙️ TRADING PROFILE: {profile.name}
{'=' * 40}
Risk Level: {profile.risk_level}
Enabled: {'✅' if profile.enabled else '❌'}

📊 Position Sizing:
  Max Position: {profile.max_position_sol} SOL
  % of Balance: {profile.position_pct_of_balance}%

🛡️ Stop Loss / Trailing:
  Initial Trailing: {profile.initial_trailing_pct}%
  Post-BE Trailing: {profile.post_be_trailing_pct}%
  Hard Stop: {profile.hard_stop_pct}%

💰 Break-Even:
  Buffer: +{profile.break_even_buffer_pct}%
  Sell at BE: {profile.break_even_sell_pct}%

🔍 Filters:
  Min Liquidity: ${profile.min_liquidity_usd:,.0f}
  Min Age: {profile.min_age_hours}h
  Max Holder: {profile.max_holder_pct}%
"""


# =============================================
# QUICK TEST
# =============================================

def test_profiles():
    """Test profile selection."""
    print("\n" + "=" * 60)
    print("🎯 RISK-BASED TRADING PROFILES")
    print("=" * 60)
    
    manager = RiskProfileManager(enable_critical=False)
    
    # Simulate different risk levels
    test_cases = [
        ("LOW RISK Token", RugcheckResult(
            mint="abc", is_safe=True, risk_score=10, risk_level="LOW",
            liquidity_usd=50000, token_age_hours=5, top_holder_pct=5
        )),
        ("MEDIUM RISK Token", RugcheckResult(
            mint="def", is_safe=True, risk_score=25, risk_level="MEDIUM",
            liquidity_usd=15000, token_age_hours=2, top_holder_pct=20
        )),
        ("HIGH RISK Token", RugcheckResult(
            mint="ghi", is_safe=False, risk_score=50, risk_level="HIGH",
            liquidity_usd=8000, token_age_hours=0.5, top_holder_pct=40
        )),
        ("CRITICAL Token", RugcheckResult(
            mint="jkl", is_safe=False, risk_score=75, risk_level="CRITICAL",
            liquidity_usd=3000, token_age_hours=0.1, top_holder_pct=60
        )),
        ("MINTABLE Token (SKIP)", RugcheckResult(
            mint="mno", is_safe=False, risk_score=90, risk_level="CRITICAL",
            mint_authority_revoked=False
        )),
    ]
    
    for name, rugcheck in test_cases:
        print(f"\n📋 {name}")
        print("-" * 40)
        
        should_trade, reason, profile = manager.should_trade(rugcheck)
        
        if should_trade:
            print(f"✅ TRADE: {reason}")
            print(f"   Max Position: {profile.max_position_sol} SOL")
            print(f"   Trailing: {profile.initial_trailing_pct}%")
            print(f"   Hard Stop: {profile.hard_stop_pct}%")
        else:
            print(f"❌ SKIP: {reason}")
    
    print("\n" + "=" * 60)
    print("✅ Profile test complete!")


if __name__ == "__main__":
    test_profiles()
