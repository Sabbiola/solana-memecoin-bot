"""Criminology module for analyzing developer history."""
from __future__ import annotations

import logging
import aiohttp
import asyncio
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

from solana_bot.config import Settings

@dataclass
class DevReport:
    """Report on developer history."""
    creator_address: str
    total_coins_created: int
    successful_coins: int  # Reached Raydium or King
    rugs_detected: int     # Failed quickly
    win_rate: float        # success / total
    is_serial_rugger: bool
    last_coin_ts: int
    details: List[str]

class DevDetective:
    """Investigates developer history for Pump.fun tokens."""
    
    BASE_URL = "https://frontend-api.pump.fun"
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.criminology")
        self.session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, DevReport] = {}  # Cache by creator address

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/json",
                    "Origin": "https://pump.fun",
                    "Referer": "https://pump.fun/"
                }
            )
        return self.session

    async def get_token_creator(self, mint: str) -> Optional[str]:
        """Fetch creator address for a mint if not known."""
        try:
            session = await self._get_session()
            url = f"{self.BASE_URL}/coins/{mint}"
            
            async with session.get(url) as response:
                if response.status != 200:
                    return None
                
                data = await response.json()
                return data.get("creator")
        except Exception:
            return None

    async def investigate(self, creator: str) -> Optional[DevReport]:
        """Analyze a creator's history."""
        if not creator:
            return None
            
        self.logger.info(f"🕵️ Investigo sviluppatore: {creator[:8]}...")
            
        if creator in self._cache:
            return self._cache[creator]

        try:
            session = await self._get_session()
            # Fetch coins created by user
            # Endpoint: /coins/user-created-coins/{user_id}?offset=0&limit=50&includeNsfw=false
            url = f"{self.BASE_URL}/coins/user-created-coins/{creator}?offset=0&limit=50&includeNsfw=true"
            
            async with session.get(url) as response:
                if response.status != 200:
                    self.logger.warning(f"Failed to fetch dev history for {creator}: {response.status}")
                    return None
                
                coins = await response.json()
                
                if not isinstance(coins, list):
                    return None
                
                total_coins = len(coins)
                successful = 0
                rugs = 0
                
                # Analyze past coins
                # A "successful" coin on Pump is one that completed bonding curve (king_of_the_hill check or raydium check)
                
                for coin in coins:
                    metrics = coin.get("king_of_the_hill_timestamp") or coin.get("complete")
                    if metrics:
                        successful += 1
                        
                    # Improved Rug Check Logic can go here (e.g., check price history? Too heavy).
                    # For now, we assume if he creates MANY coins and FEW succeed, he is a spammer/rugger.

                win_rate = successful / total_coins if total_coins > 0 else 0.0
                
                # Heuristic:
                # - If created > 10 coins and win_rate < 10% -> Serial Rugger/Spammer
                # - If created > 3 coins and 0 successful -> Risk
                
                # Check against configurable thresholds
                threshold_coins = getattr(self.settings, 'CRIMINOLOGY_MAX_SERIAL_RUGS', 5)
                threshold_rate = getattr(self.settings, 'CRIMINOLOGY_MIN_WIN_RATE', 0.1)
                
                is_serial_rugger = (total_coins >= threshold_coins and win_rate < threshold_rate)
                
                details = []
                if is_serial_rugger:
                    details.append(f"SERIAL_RUGGER: {successful}/{total_coins} successful ({win_rate:.0%})")
                elif total_coins == 1:
                    details.append("New Dev (First Coin)")
                else:
                    details.append(f"Experienced Dev: {successful}/{total_coins} successful")

                last_coin_ts = 0
                if coins:
                     # Coins are usually sorted newest first
                     last_coin_ts = coins[0].get("created_timestamp", 0)

                report = DevReport(
                    creator_address=creator,
                    total_coins_created=total_coins,
                    successful_coins=successful,
                    rugs_detected=rugs, # Placeholder
                    win_rate=win_rate,
                    is_serial_rugger=is_serial_rugger,
                    last_coin_ts=last_coin_ts,
                    details=details
                )
                
                self._cache[creator] = report
                return report
                
        except Exception as e:
            self.logger.error(f"DevDetective error: {e}")
            return None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
