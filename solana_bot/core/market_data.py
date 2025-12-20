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
        This is a simplified implementation using current price snapshots.
        For production, consider using Birdeye API or similar.
        
        Args:
            mint: Token mint address
            timeframe: Timeframe (1m, 5m, 15m)
            limit: Number of candles
            
        Returns:
            List of Candle objects
        """
        # TODO: Implement actual historical data fetching
        # For now, return empty list (will use spot price + volume)
        logger.warning(f"OHLCV candles not implemented yet for {mint[:8]}")
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
        
        # Calculate indicators
        rsi = self.calculate_rsi(self._price_cache[mint])
        ema = self.calculate_ema(self._price_cache[mint])
        avg_volume = sum(self._volume_cache[mint]) / len(self._volume_cache[mint]) if self._volume_cache[mint] else None
        
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
