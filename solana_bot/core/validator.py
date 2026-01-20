from __future__ import annotations

from solana_bot.config import Settings
from solana_bot.core.models import Phase, TokenInfo


class Validator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def detect_phase(self, token: TokenInfo) -> Phase:
        dex_id = (token.metadata.get("dex_id") or "").lower()
        mint_suffix_is_pump = token.mint.lower().endswith("pump")
        if token.source == "pumpfun" or "pump" in dex_id or mint_suffix_is_pump:
            return Phase.BONDING_CURVE
        if token.source == "raydium" or "raydium" in dex_id:
            return Phase.RAYDIUM
        if token.source == "jupiter" or "jupiter" in dex_id:
            return Phase.JUPITER
        return Phase.PUMPSWAP

    def pool_quality_ok(self, token: TokenInfo, phase: Phase) -> bool:
        if phase == Phase.BONDING_CURVE:
            return token.liquidity_usd >= 100.0
        return token.liquidity_usd >= 500.0

    def validate(self, token: TokenInfo) -> bool:
        phase = self.detect_phase(token)
        token.phase = phase
        return self.pool_quality_ok(token, phase)
