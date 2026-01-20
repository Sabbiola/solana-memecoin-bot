"""Client for RugCheck.xyz API."""
from __future__ import annotations

import logging
import aiohttp
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from solana_bot.config import Settings

@dataclass
class RugCheckReport:
    """Standardized report from RugCheck."""
    score: int  # 0 to ??? (Total danger score)
    risks: List[Dict[str, Any]]
    token_program: str
    mint: str
    rugs_detected: bool

class RugCheckClient:
    """Client for RugCheck.xyz public API."""
    
    BASE_URL = "https://api.rugcheck.xyz/v1"
    
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.rugcheck_api")
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self.session

    async def get_report(self, mint: str) -> Optional[RugCheckReport]:
        """Fetch token report from RugCheck."""
        try:
            session = await self._get_session()
            url = f"{self.BASE_URL}/tokens/{mint}/report/summary"
            
            async with session.get(url) as response:
                if response.status == 404:
                    self.logger.warning(f"RugCheck: Report not found for {mint}")
                    return None
                
                if response.status != 200:
                    self.logger.error(f"RugCheck API Error {response.status}: {await response.text()}")
                    return None
                
                data = await response.json()
                
                # Parse risks
                risks = data.get("risks", [])
                
                # Prefer normalised score (0-100 scale) if available
                score_normalised = data.get("score_normalised")
                score_raw = data.get("score", 0)
                
                if score_normalised is not None and score_normalised > 0:
                    # Use normalised score (0-100)
                    score = int(score_normalised)
                    self.logger.debug(f"RugCheck {mint[:8]}: Using normalised score {score}")
                else:
                    # Use raw score - note: 501 seems to be default for new/unknown tokens
                    score = int(score_raw)
                    if score == 501:
                        # 501 appears to be a "unknown/new token" score, treat as low risk
                        self.logger.debug(f"RugCheck {mint[:8]}: Score 501 (likely new token), treating as low risk")
                    else:
                        self.logger.debug(f"RugCheck {mint[:8]}: Using raw score {score}")
                
                # Detect critical risks (only if explicitly flagged as danger)
                critical_risks = [
                    r for r in risks 
                    if r.get("level") == "danger"
                ]
                
                # Don't flag as rug if only risk is score=501 (unknown token)
                rugs_detected = len(critical_risks) > 0 and score != 501
                
                return RugCheckReport(
                    score=score,
                    risks=risks,
                    token_program=data.get("tokenProgram", ""),
                    mint=mint,
                    rugs_detected=rugs_detected
                )
                
        except Exception as e:
            self.logger.error(f"RugCheck Exception: {e}")
            return None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
