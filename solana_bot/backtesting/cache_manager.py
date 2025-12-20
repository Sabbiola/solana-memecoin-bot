"""
Cache Manager for Historical Data

Stores fetched OHLCV data in SQLite to avoid repeated API calls.
Reduces Birdeye API usage from 100+ to ~10-20 requests/month.
"""

import sqlite3
import logging
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class CacheManager:
    """
    Manages local SQLite cache for historical candle data.
    
    Features:
    - Stores candles by token + timeframe + date range
    - Automatic cache invalidation (7 days TTL)
    - Reduces API calls by 90%+
    """
    
    def __init__(self, db_path: str = "backtest_cache.db"):
        self.db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with candles table."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    data TEXT NOT NULL,
                    cached_at INTEGER NOT NULL,
                    UNIQUE(mint, interval, start_date, end_date)
                )
            """)
            
            # Index for faster lookups
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_candles_lookup 
                ON candles(mint, interval, start_date, end_date)
            """)
            
            conn.commit()
        
        logger.info(f"📦 Cache initialized: {self.db_path}")
    
    def get_cached_candles(
        self,
        mint: str,
        interval: str,
        start_date: str,
        end_date: str,
        max_age_days: int = 7
    ) -> Optional[List[dict]]:
        """
        Get candles from cache if available and not expired.
        
        Args:
            mint: Token mint address
            interval: Candle interval (1h, 1d, etc.)
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            max_age_days: Max age before cache invalidation
            
        Returns:
            List of candle dicts or None if not cached
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT data, cached_at FROM candles
                WHERE mint = ? AND interval = ? 
                AND start_date = ? AND end_date = ?
            """, (mint, interval, start_date, end_date))
            
            row = cursor.fetchone()
            
            if not row:
                return None
            
            data_json, cached_at = row
            
            # Check if cache is expired
            now = int(datetime.now().timestamp())
            age_seconds = now - cached_at
            age_days = age_seconds / 86400
            
            if age_days > max_age_days:
                logger.info(f"🗑️  Cache expired ({age_days:.1f} days old), refetching...")
                self.invalidate_cache(mint, interval, start_date, end_date)
                return None
            
            candles = json.loads(data_json)
            logger.info(f"✅ Cache HIT: {len(candles)} candles ({age_days:.1f} days old)")
            return candles
    
    def save_candles(
        self,
        mint: str,
        interval: str,
        start_date: str,
        end_date: str,
        candles: List[dict]
    ):
        """
        Save candles to cache.
        
        Args:
            mint: Token mint address
            interval: Candle interval
            start_date: Start date
            end_date: End date
            candles: List of candle dicts
        """
        if not candles:
            return
        
        data_json = json.dumps(candles)
        cached_at = int(datetime.now().timestamp())
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO candles 
                (mint, interval, start_date, end_date, data, cached_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (mint, interval, start_date, end_date, data_json, cached_at))
            
            conn.commit()
        
        logger.info(f"💾 Cached {len(candles)} candles for {mint[:8]}...")
    
    def invalidate_cache(
        self,
        mint: str,
        interval: str,
        start_date: str,
        end_date: str
    ):
        """Remove specific cache entry."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                DELETE FROM candles
                WHERE mint = ? AND interval = ?
                AND start_date = ? AND end_date = ?
            """, (mint, interval, start_date, end_date))
            
            conn.commit()
    
    def clear_all_cache(self):
        """Clear entire cache (use with caution)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM candles")
            conn.commit()
        
        logger.warning("🗑️  Entire cache cleared!")
    
    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT COUNT(*), 
                       COUNT(DISTINCT mint),
                       SUM(LENGTH(data))
                FROM candles
            """)
            
            total_entries, unique_tokens, total_size = cursor.fetchone()
            
            return {
                "total_entries": total_entries or 0,
                "unique_tokens": unique_tokens or 0,
                "total_size_bytes": total_size or 0,
                "db_file_size": self.db_path.stat().st_size if self.db_path.exists() else 0
            }
