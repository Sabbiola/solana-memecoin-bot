"""
Market Data Collector

Fetches and processes market data for technical analysis.
Calculates indicators: RSI, EMA, volume analysis.
"""

import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class Candle:
    """OHLCV candle data"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataCollector:
    """
    Collects and processes market data for technical analysis.
    
    Features:
    - OHLCV candle construction from price data
    - RSI calculation (Relative Strength Index)
    - EMA calculation (Exponential Moving Average)
    - Volume analysis
    """
    
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self._price_cache: Dict[str, List[float]] = {}  # mint -> [prices]
        self._volume_cache: Dict[str, List[float]] = {}  # mint -> [volumes]
        
    async def get_ohlcv_candles(
        self, 
        mint: str, 
        timeframe: str = "1m",
        limit: int = 100
    ) -> List[Candle]:
        """
        Get OHLCV candles for a token.
        
        Note: DexScreener doesn't provide historical candles directly.
        This implementation uses Birdeye API when available.
        
        Args:
            mint: Token mint address
            timeframe: Timeframe (1m, 5m, 15m)
            limit: Number of candles
            
        Returns:
            List of Candle objects
        """
        from ..config import BIRDEYE_API_KEY, BIRDEYE_API_URL

        if not BIRDEYE_API_KEY:
            logger.debug("BIRDEYE_API_KEY not set; skipping OHLCV fetch.")
            return []

        interval_map = {
            "1m": ("1m", 60),
            "5m": ("5m", 300),
            "15m": ("15m", 900),
            "1h": ("1H", 3600),
            "4h": ("4H", 14400),
            "1d": ("1D", 86400)
        }
        birdeye_interval, interval_seconds = interval_map.get(timeframe, ("1m", 60))

        end_ts = int(time.time())
        start_ts = end_ts - (interval_seconds * max(limit, 1))

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

        timeout = aiohttp.ClientTimeout(total=15)
        retries = 3
        backoff = 1.5

        for attempt in range(1, retries + 1):
            try:
                async with self.session.get(url, params=params, headers=headers, timeout=timeout) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.warning(
                            "Birdeye OHLCV error %s for %s: %s",
                            resp.status,
                            mint[:8],
                            error_text
                        )
                        if resp.status in {401, 403, 429}:
                            return []
                    else:
                        data = await resp.json()
                        if not data.get("success"):
                            logger.warning(
                                "Birdeye OHLCV failed for %s: %s",
                                mint[:8],
                                data.get("message", "Unknown error")
                            )
                            return []

                        items = data.get("data", {}).get("items", [])
                        if not items:
                            logger.info("No OHLCV items for %s", mint[:8])
                            return []

                        candles = [
                            Candle(
                                timestamp=item.get("unixTime", 0),
                                open=float(item.get("o", 0)),
                                high=float(item.get("h", 0)),
                                low=float(item.get("l", 0)),
                                close=float(item.get("c", 0)),
                                volume=float(item.get("v", 0))
                            )
                            for item in items
                        ]
                        return candles
            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                logger.warning(
                    "Birdeye OHLCV request failed (attempt %s/%s) for %s: %s",
                    attempt,
                    retries,
                    mint[:8],
                    exc
                )

            if attempt < retries:
                await asyncio.sleep(backoff ** attempt)

        logger.warning("OHLCV candles unavailable for %s; using spot cache fallback.", mint[:8])
        return []
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> Optional[float]:
        """
        Calculate RSI (Relative Strength Index).
        
        RSI = 100 - (100 / (1 + RS))
        where RS = Average Gain / Average Loss
        
        Args:
            prices: List of prices (oldest first)
            period: RSI period (default 14)
            
        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(prices) < period + 1:
            return None
        
        # Calculate price changes
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        
        # Separate gains and losses
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        
        # Calculate average gain and loss
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100.0  # All gains, max RSI
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_ema(self, prices: List[float], period: int = 20) -> Optional[float]:
        """
        Calculate EMA (Exponential Moving Average).
        
        EMA = Price(t) * k + EMA(t-1) * (1 - k)
        where k = 2 / (period + 1)
        
        Args:
            prices: List of prices (oldest first)
            period: EMA period (default 20)
            
        Returns:
            EMA value or None if insufficient data
        """
        if len(prices) < period:
            return None
        
        # Start with SMA for first EMA value
        sma = sum(prices[:period]) / period
        ema = sma
        
        # Calculate EMA
        k = 2 / (period + 1)
        for price in prices[period:]:
            ema = price * k + ema * (1 - k)
        
        return ema
    
    async def get_token_indicators(
        self, 
        mint: str,
        price: float,
        volume_24h: float
    ) -> Dict[str, Optional[float]]:
        """
        Get technical indicators for a token.
        
        Args:
            mint: Token mint address
            price: Current price
            volume_24h: 24h volume
            
        Returns:
            Dict with RSI, EMA, avg_volume
        """
        # Add current price to cache
        if mint not in self._price_cache:
            self._price_cache[mint] = []
        
        self._price_cache[mint].append(price)
        
        # Keep last 100 prices
        if len(self._price_cache[mint]) > 100:
            self._price_cache[mint].pop(0)
        
        # Add volume to cache
        if mint not in self._volume_cache:
            self._volume_cache[mint] = []
        
        self._volume_cache[mint].append(volume_24h)
        
        # Keep last 20 volumes
        if len(self._volume_cache[mint]) > 20:
            self._volume_cache[mint].pop(0)

        candles = await self.get_ohlcv_candles(mint, limit=100)
        if candles:
            prices = [c.close for c in candles]
            volumes = [c.volume for c in candles]
        else:
            prices = self._price_cache[mint]
            volumes = self._volume_cache[mint]

        # Calculate indicators
        rsi = self.calculate_rsi(prices)
        ema = self.calculate_ema(prices)
        avg_volume = sum(volumes) / len(volumes) if volumes else None
        
        return {
            'rsi': rsi,
            'ema_20': ema,
            'avg_volume': avg_volume
        }
    
    def clear_cache(self, mint: Optional[str] = None):
        """Clear price/volume cache for a token or all tokens."""
        if mint:
            self._price_cache.pop(mint, None)
            self._volume_cache.pop(mint, None)
        else:
            self._price_cache.clear()
            self._volume_cache.clear()
