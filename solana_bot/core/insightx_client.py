import logging
import aiohttp
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class InsightXClient:
    """Client for interacting with InsightX API for token security analysis."""
    
    BASE_URL = "https://api.insightx.network/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }
        
    async def get_token_security(self, mint: str, network: str = "solana") -> Optional[Dict[str, Any]]:
        """
        Fetch security analysis for a token.
        
        Args:
            mint: Token mint address
            network: Network identifier (default: "solana")
            
        Returns:
            Dict containing security metrics or None if failed
        """
        if not self.api_key:
            return None
            
        url = f"{self.BASE_URL}/tokens/{network}/{mint}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers, timeout=10) as response:
                    if response.status == 200:
                        data = await response.json()
                        return self._parse_security_data(data)
                    elif response.status == 404:
                        logger.warning(f"InsightX: Token {mint} not found")
                        return None
                    elif response.status == 429:
                        logger.warning("InsightX: Rate limit exceeded")
                        return None
                    else:
                        logger.error(f"InsightX API error {response.status}: {await response.text()}")
                        return None
                        
        except Exception as e:
            logger.error(f"Failed to fetch InsightX data for {mint}: {e}")
            return None
            
    def _parse_security_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract key metrics from raw API response."""
        try:
            # Note: Adjust fields based on actual API response structure
            # Based on docs, looking for risk score and key indicators
            
            payload = data.get("data", {})
            security = payload.get("security", {})
            
            return {
                "risk_score": security.get("score", 0),  # Assuming 0-100 score
                "is_mintable": security.get("mintable", False),
                "is_mutable": security.get("mutable_metadata", True),
                "is_freezable": security.get("freezable", False),
                "top_10_holders_pct": security.get("top_10_holders_pct", 0),
                "is_rugged": security.get("is_rugged", False),
                "warnings": security.get("warnings", [])
            }
        except Exception as e:
            logger.error(f"Error parsing InsightX data: {e}")
            return {}
