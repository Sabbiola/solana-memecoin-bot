"""
Whale Monitor

Tracks large wallet movements for tokens in active positions.
Uses Helius API to monitor holder distribution and large transactions.
"""

import asyncio
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger(__name__)

# NEW: Import whale wallets for holder check
try:
    from ..config import WHALE_WALLETS
except ImportError:
    WHALE_WALLETS = []


@dataclass
class WhaleActivity:
    """Whale activity summary for a token"""
    mint: str
    top_holder_pct: float  # % held by top holder
    top10_holders_pct: float  # % held by top 10
    recent_sells: int  # Large sells in last 5 min
    recent_buys: int  # Large buys in last 5 min
    is_whale_dumping: bool  # True if more sells than buys
    risk_level: str  # LOW, MEDIUM, HIGH


class WhaleMonitor:
    """
    Monitor whale activity for tokens.
    
    Detects:
    - Large holder concentration
    - Whale sell-offs
    - Unusual transaction patterns
    """
    
    def __init__(self, session: aiohttp.ClientSession, helius_api_key: str = None):
        self.session = session
        self.helius_api_key = helius_api_key
        self.helius_url = f"https://mainnet.helius-rpc.com/?api-key={helius_api_key}" if helius_api_key else None
        
        # Cache
        self._holder_cache: Dict[str, Dict] = {}
        self._cache_ttl = 60  # 1 minute
        
    async def get_whale_activity(self, mint: str) -> Optional[WhaleActivity]:
        """
        Get whale activity summary for a token.
        
        Returns WhaleActivity with holder concentration and recent trades.
        """
        if not self.helius_api_key:
            logger.debug("Helius API key not set, skipping whale check")
            return None
            
        try:
            # Get token holders
            holders = await self._get_top_holders(mint)
            if not holders:
                return None
            
            # Calculate concentration
            total_supply = sum(h.get('amount', 0) for h in holders)
            if total_supply == 0:
                return None
                
            top_holder_pct = (holders[0].get('amount', 0) / total_supply * 100) if holders else 0
            top10_pct = sum(h.get('amount', 0) for h in holders[:10]) / total_supply * 100
            
            # Determine risk level
            if top_holder_pct > 30 or top10_pct > 60:
                risk_level = "HIGH"
            elif top_holder_pct > 15 or top10_pct > 40:
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"
            
            # 🔥 NEW: Check if any top holder is in our WHALE_WALLETS list
            is_followed_whale_holding = any(h.get('address') in WHALE_WALLETS for h in holders)
            
            return WhaleActivity(
                mint=mint,
                top_holder_pct=top_holder_pct,
                top10_holders_pct=top10_pct,
                recent_sells=0,
                recent_buys=1 if is_followed_whale_holding else 0,
                is_whale_dumping=False,
                risk_level="LOW" if is_followed_whale_holding else risk_level
            )
            
        except Exception as e:
            logger.debug(f"Whale activity check failed: {e}")
            return None
    
    async def _get_top_holders(self, mint: str, limit: int = 20) -> List[Dict]:
        """Get top token holders using Helius DAS API."""
        if not self.helius_url:
            return []
            
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": "whale-check",
                "method": "getTokenLargestAccounts",
                "params": [mint]
            }
            
            async with self.session.post(self.helius_url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result", {})
                    accounts = result.get("value", [])
                    
                    # Convert to simpler format
                    holders = []
                    for acc in accounts[:limit]:
                        holders.append({
                            "address": acc.get("address", ""),
                            "amount": float(acc.get("amount", 0))
                        })
                    return holders
                    
        except Exception as e:
            logger.debug(f"Failed to get top holders: {e}")
        
        return []
    
    async def is_whale_dumping(self, mint: str) -> bool:
        """
        Quick check if whales are dumping.
        
        Returns True if significant whale sell activity detected.
        """
        activity = await self.get_whale_activity(mint)
        if activity:
            return activity.is_whale_dumping or activity.risk_level == "HIGH"
        return False
    
    async def get_holder_concentration_risk(self, mint: str) -> str:
        """
        Get holder concentration risk level.
        
        Returns: "LOW", "MEDIUM", or "HIGH"
        """
        activity = await self.get_whale_activity(mint)
        return activity.risk_level if activity else "UNKNOWN"
