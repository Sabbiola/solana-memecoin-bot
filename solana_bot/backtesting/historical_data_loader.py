"""
Historical Data Loader

Fetches and caches historical price/volume data for backtesting.
Uses DexScreener API and stores in SQLite for replay.
"""

import asyncio
import logging
import time
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class HistoricalCandle:
    """Historical OHLCV candle"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class HistoricalDataLoader:
    """
    Loads historical price data for backtesting.
    
    Note: DexScreener doesn't provide historical OHLCV directly.
    This implementation simulates historical data using:
    1. Current price snapshots
    2. Price change percentages
    3. Volume data
    
    For production backtesting, consider Birdeye or similar APIs.
    """
    
    def __init__(self, session: aiohttp.ClientSession, db_manager=None):
        self.session = session
        self.db = db_manager
        
        # Initialize cache manager
        from .cache_manager import CacheManager
        self.cache = CacheManager()
    
    async def fetch_historical_data(
        self, 
        mint: str,
        start_date: str,  # "2025-01-01"
        end_date: str,    # "2025-01-15"
        interval: str = "1h"
    ) -> List[HistoricalCandle]:
        """
        Fetch historical data for a token.
        
        Args:
            mint: Token mint address
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            interval: Candle interval (1m, 5m, 1h, 1d)
            
        Returns:
            List of HistoricalCandle objects
        """
        logger.info(f"📥 Fetching historical data for {mint[:8]} ({start_date} to {end_date})")
        
        # Check cache first (SQLite)
        cached_candles = self.cache.get_cached_candles(mint, interval, start_date, end_date)
        if cached_candles:
            return [HistoricalCandle(**c) for c in cached_candles]
        
        # Fetch from API (Birdeye)
        logger.info("💸 Cache MISS - using Birdeye API request")
        candles = await self._fetch_from_api(mint, start_date, end_date, interval)
        
        # Save to cache for future use
        if candles:
            candles_dict = [
                {
                    "timestamp": c.timestamp,
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume
                }
                for c in candles
            ]
            self.cache.save_candles(mint, interval, start_date, end_date, candles_dict)
        
        return candles
    
    async def _fetch_from_api(
        self,
        mint: str,
        start_date: str,
        end_date: str,
        interval: str
    ) -> List[HistoricalCandle]:
        """
        Fetch OHLCV data from Birdeye API.
        
        Birdeye provides real historical data for Solana tokens.
        Free tier: 100 requests/month
        Paid tiers: https://birdeye.so/pricing
        """
        from ..config import BIRDEYE_API_KEY, BIRDEYE_API_URL
        
        if not BIRDEYE_API_KEY:
            logger.error(
                "⚠️ BIRDEYE_API_KEY not set! "
                "Get a free key at https://birdeye.so and add to .env file"
            )
            return []
        
        try:
            # Convert dates to Unix timestamps
            from datetime import datetime
            start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
            end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())
            
            # Birdeye interval format: 1m, 5m, 15m, 1H, 4H, 1D, 1W
            interval_map = {
                "1m": "1m",
                "5m": "5m",
                "15m": "15m",
                "1h": "1H",
                "4h": "4H",
                "1d": "1D"
            }
            birdeye_interval = interval_map.get(interval, "1H")
            
            # Birdeye OHLCV endpoint
            url = f"{BIRDEYE_API_URL}/defi/ohlcv"
            
            params = {
                "address": mint,
                "type": birdeye_interval,
                "time_from": start_ts,
                "time_to": end_ts
            }
            
            headers = {
                "X-API-KEY": BIRDEYE_API_KEY,
                "x-chain": "solana"
            }
            
            logger.info(f"📡 Fetching Birdeye OHLCV: {mint[:8]}... ({start_date} to {end_date}, {birdeye_interval})")
            
            async with self.session.get(url, params=params, headers=headers, timeout=30) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Birdeye API error {resp.status}: {error_text}")
                    
                    if resp.status == 401:
                        logger.error("❌ Invalid API key! Check BIRDEYE_API_KEY in .env")
                    elif resp.status == 429:
                        logger.error("❌ Rate limit exceeded! Upgrade plan at https://birdeye.so/pricing")
                    
                    return []
                
                data = await resp.json()
                
                if not data.get("success"):
                    logger.warning(f"Birdeye request failed: {data.get('message', 'Unknown error')}")
                    return []
                
                items = data.get("data", {}).get("items", [])
                
                if not items:
                    logger.warning(f"No historical data available for {mint[:8]}")
                    return []
                
                # Convert to HistoricalCandle objects
                candles = []
                for item in items:
                    # Birdeye response format:
                    # {
                    #   "unixTime": 1234567890,
                    #   "o": 0.000123,  # open
                    #   "h": 0.000125,  # high
                    #   "l": 0.000120,  # low
                    #   "c": 0.000122,  # close
                    #   "v": 1234567    # volume
                    # }
                    candle = HistoricalCandle(
                        timestamp=item.get("unixTime", 0),
                        open=float(item.get("o", 0)),
                        high=float(item.get("h", 0)),
                        low=float(item.get("l", 0)),
                        close=float(item.get("c", 0)),
                        volume=float(item.get("v", 0))
                    )
                    candles.append(candle)
                
                logger.info(f"✅ Fetched {len(candles)} real historical candles from Birdeye")
                return candles
                
        except Exception as e:
            logger.error(f"Failed to fetch from Birdeye: {e}")
            return []
    
    def _simulate_historical_candles(
        self,
        pair_data: Dict,
        start_date: str,
        end_date: str,
        interval: str
    ) -> List[HistoricalCandle]:
        """
        DEPRECATED: Simulate historical candles from current price data.
        
        This method is kept as fallback but should not be used for real backtesting.
        Use Birdeye API instead for accurate historical data.
        """
        logger.warning("⚠️ Using DEPRECATED simulated data! Use Birdeye API for real backtesting.")
        candles = []
        
        current_price = float(pair_data.get("priceUsd", 0) or 0)
        volume_24h = float(pair_data.get("volume", {}).get("h24", 0) or 0)
        price_change_24h = float(pair_data.get("priceChange", {}).get("h24", 0) or 0)
        
        if current_price == 0:
            return []
        
        # Parse dates
        start_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())
        
        # Interval in seconds
        interval_map = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "1d": 86400
        }
        interval_seconds = interval_map.get(interval, 3600)
        
        # Generate candles backwards from current price
        # Simulate price movement using random walk
        import random
        
        timestamp = start_ts
        price = current_price * (1 - price_change_24h / 100)  # Start from ~24h ago price
        
        while timestamp <= end_ts:
            # Random price movement (-5% to +5%)
            price_change = random.uniform(-0.05, 0.05)
            new_price = price * (1 + price_change)
            
            # Generate OHLC
            high = max(price, new_price) * random.uniform(1.0, 1.02)
            low = min(price, new_price) * random.uniform(0.98, 1.0)
            
            candle = HistoricalCandle(
                timestamp=timestamp,
                open=price,
                high=high,
                low=low,
                close=new_price,
                volume=volume_24h / 24  # Simulated hourly volume
            )
            
            candles.append(candle)
            
            timestamp += interval_seconds
            price = new_price
        
        logger.warning(
            "⚠️ Using SIMULATED historical data! "
            "For real backtesting, use Birdeye or actual historical data."
        )
        
        return candles
    
    async def _load_from_db(
        self,
        mint: str,
        start_date: str,
        end_date: str
    ) -> Optional[List[HistoricalCandle]]:
        """Load historical candles from database cache."""
        # TODO: Implement database loading
        return None
    
    async def _save_to_db(self, mint: str, candles: List[HistoricalCandle]):
        """Save historical candles to database cache."""
        # TODO: Implement database saving
        pass
    
    async def get_token_list_for_backtest(
        self,
        min_volume: float = 1000,
        min_liquidity: float = 5000,
        limit: int = 10
    ) -> List[str]:
        """
        Get list of tradeable tokens for backtesting.
        
        Args:
            min_volume: Minimum 24h volume (USD)
            min_liquidity: Minimum liquidity (USD)
            limit: Max number of tokens
            
        Returns:
            List of mint addresses
        """
        try:
            url = "https://api.dexscreener.com/latest/dex/search?q=solana"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                
                data = await resp.json()
                pairs = data.get("pairs", [])
                
                # Filter by criteria
                filtered = []
                for pair in pairs:
                    volume = float(pair.get("volume", {}).get("h24", 0) or 0)
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    
                    if volume >= min_volume and liquidity >= min_liquidity:
                        mint = pair.get("baseToken", {}).get("address")
                        if mint:
                            filtered.append(mint)
                
                logger.info(f"Found {len(filtered[:limit])} tokens for backtesting")
                return filtered[:limit]
                
        except Exception as e:
            logger.error(f"Failed to get token list: {e}")
            return []
