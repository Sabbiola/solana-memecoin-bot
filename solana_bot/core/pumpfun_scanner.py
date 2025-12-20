"""
Pump.fun Token Scanner

Fetches newly created tokens from pump.fun for early entry opportunities.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class PumpToken:
    """Token data from pump.fun"""
    mint: str
    symbol: str
    name: str
    market_cap: float
    virtual_sol_reserves: float
    virtual_token_reserves: float
    created_timestamp: int
    complete: bool  # True if migrated to Raydium
    
    @property
    def age_minutes(self) -> float:
        """Age in minutes since creation"""
        return (time.time() - self.created_timestamp) / 60
    
    @property
    def price_sol(self) -> float:
        """Estimated price in SOL"""
        if self.virtual_token_reserves > 0:
            return self.virtual_sol_reserves / self.virtual_token_reserves
        return 0


class PumpFunScanner:
    """
    Scanner for pump.fun tokens.
    
    Fetches newly created tokens directly from pump.fun API
    to find early entry opportunities on bonding curve.
    """
    
    # Pump.fun API endpoints
    PUMP_API_URL = "https://frontend-api.pump.fun"
    
    def __init__(
        self,
        session: aiohttp.ClientSession,
        min_age_minutes: float = 5,
        max_age_minutes: float = 60,
        min_market_cap: float = 1000,
        max_market_cap: float = 100000
    ):
        self.session = session
        self.min_age_minutes = min_age_minutes
        self.max_age_minutes = max_age_minutes
        self.min_market_cap = min_market_cap
        self.max_market_cap = max_market_cap
        
        logger.info(
            f"🎰 PumpFunScanner initialized: "
            f"Age={min_age_minutes}-{max_age_minutes}min, "
            f"MC=${min_market_cap}-${max_market_cap}"
        )
    
    async def fetch_new_tokens(self, limit: int = 50) -> List[PumpToken]:
        """
        Fetch recently created tokens from pump.fun.
        
        Returns:
            List of PumpToken objects matching filters
        """
        tokens = []
        
        # Retry configuration
        max_retries = 3
        backoff_times = [0.5, 1.0, 2.0]
        
        for attempt in range(max_retries):
            try:
                # Get latest coins from pump.fun
                url = f"{self.PUMP_API_URL}/coins"
                params = {
                    "offset": 0,
                    "limit": limit,
                    "sort": "created_timestamp",
                    "order": "DESC",  # Newest first
                    "includeNsfw": "false"
                }
                
                async with self.session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        if attempt < max_retries - 1:
                            wait_time = backoff_times[attempt]
                            logger.warning(f"Pump.fun API returned {resp.status}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                            await asyncio.sleep(wait_time)
                            continue
                        logger.warning(f"Pump.fun API returned {resp.status} after {max_retries} attempts")
                        return []
                    
                    data = await resp.json()
                    
                    if not isinstance(data, list):
                        logger.warning(f"Unexpected pump.fun response format")
                        return []
                    
                    for coin in data:
                        try:
                            token = PumpToken(
                                mint=coin.get("mint", ""),
                                symbol=coin.get("symbol", "???"),
                                name=coin.get("name", ""),
                                market_cap=float(coin.get("usd_market_cap", 0) or 0),
                                virtual_sol_reserves=float(coin.get("virtual_sol_reserves", 0) or 0) / 1e9,
                                virtual_token_reserves=float(coin.get("virtual_token_reserves", 0) or 0),
                                created_timestamp=int(coin.get("created_timestamp", 0) / 1000),  # ms to sec
                                complete=coin.get("complete", False)
                            )
                            
                            # Apply filters
                            if self._passes_filters(token):
                                tokens.append(token)
                                
                        except Exception as e:
                            logger.debug(f"Error parsing pump token: {e}")
                            continue
                    
                    logger.info(f"🎰 Pump.fun: Found {len(tokens)} tokens matching filters out of {len(data)}")
                    break  # Success, exit retry loop
                    
            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    wait_time = backoff_times[attempt]
                    logger.warning(f"Pump.fun API timeout, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.warning(f"Pump.fun API timeout after {max_retries} attempts")
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = backoff_times[attempt]
                    logger.warning(f"Pump.fun API error: {e}, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Pump.fun API error after {max_retries} attempts: {e}")
        
        return tokens
    
    def _passes_filters(self, token: PumpToken) -> bool:
        """Check if token passes all filters"""
        
        # Skip migrated tokens (not on bonding curve anymore)
        if token.complete:
            logger.debug(f"[{token.symbol}] Skipped: Already migrated")
            return False
        
        # Age filter
        if token.age_minutes < self.min_age_minutes:
            logger.debug(f"[{token.symbol}] Skipped: Too new ({token.age_minutes:.1f}m)")
            return False
        
        if token.age_minutes > self.max_age_minutes:
            logger.debug(f"[{token.symbol}] Skipped: Too old ({token.age_minutes:.1f}m)")
            return False
        
        # Market cap filter
        if token.market_cap < self.min_market_cap:
            logger.debug(f"[{token.symbol}] Skipped: Low MC (${token.market_cap:.0f})")
            return False
        
        if token.market_cap > self.max_market_cap:
            logger.debug(f"[{token.symbol}] Skipped: High MC (${token.market_cap:.0f})")
            return False
        
        # Liquidity check (should have some SOL in reserves)
        if token.virtual_sol_reserves < 1.0:
            logger.debug(f"[{token.symbol}] Skipped: Low liquidity ({token.virtual_sol_reserves:.1f} SOL)")
            return False
        
        logger.info(
            f"   🎰 [{token.symbol}] PUMP.FUN: "
            f"Age={token.age_minutes:.0f}m | MC=${token.market_cap:,.0f} | "
            f"Liq={token.virtual_sol_reserves:.1f} SOL"
        )
        return True
    
    async def get_token_details(self, mint: str) -> Optional[Dict]:
        """
        Get detailed info for a specific token.
        
        Returns:
            Token details dict or None
        """
        try:
            url = f"{self.PUMP_API_URL}/coins/{mint}"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
                    
        except Exception as e:
            logger.debug(f"Error fetching pump token details: {e}")
        
        return None
