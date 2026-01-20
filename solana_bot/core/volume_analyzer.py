from __future__ import annotations

from solana_bot.core.models import TokenInfo


class VolumeAnalyzer:
    def volume_mcap_ratio(self, token: TokenInfo) -> float:
        if token.liquidity_usd <= 0:
            return 0.0
        return token.volume_usd / token.liquidity_usd
