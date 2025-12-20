"""
RPC Cache with TTL

Reduces Helius credit consumption by caching expensive RPC calls.

TTL Categories:
- STATIC: Token metadata, decimals, supply (if not mintable) - 24h
- SEMI_STATIC: Largest accounts, holder distribution - 5-30 min
- DYNAMIC: Account balances, pool state - 30s

Usage:
    cache = get_rpc_cache()
    
    # Check cache before RPC call
    cached = cache.get("token_supply", mint)
    if cached:
        return cached
    
    # After RPC call, cache result
    result = await client.get_token_supply(mint)
    cache.set("token_supply", mint, result)
"""

import logging
import time
from typing import Any, Dict, Optional
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Single cache entry with TTL"""
    value: Any
    timestamp: float
    ttl: float
    
    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class RPCCache:
    """
    TTL-based cache for RPC calls.
    
    Reduces credit consumption by avoiding redundant calls for:
    - Token metadata (rarely changes)
    - Supply/decimals (static once minted)
    - Holder distribution (changes slowly)
    """
    
    # TTL values in seconds
    TTLS = {
        # Static - very long TTL
        "token_decimals": 86400,      # 24 hours
        "mint_info": 86400,           # 24 hours
        "token_supply": 3600,         # 1 hour (can be minted)
        "authority_status": 3600,     # 1 hour
        
        # Semi-static - medium TTL
        "largest_accounts": 300,      # 5 minutes
        "holder_distribution": 300,   # 5 minutes
        "pool_accounts": 120,         # 2 minutes
        
        # Dynamic - short TTL
        "account_info": 30,           # 30 seconds
        "token_balance": 30,          # 30 seconds
        "pool_reserves": 15,          # 15 seconds
    }
    
    # Credit costs per method (approximate)
    CREDIT_COSTS = {
        "get_token_supply": 1,
        "get_token_largest_accounts": 5,
        "get_account_info": 1,
        "get_multiple_accounts": 1,  # Per account
        "get_token_account_balance": 1,
    }
    
    def __init__(self):
        self._cache: Dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "credits_saved": 0
        }
    
    def _make_key(self, method: str, key: str) -> str:
        """Create unique cache key"""
        return f"{method}:{key}"
    
    def get(self, method: str, key: str) -> Optional[Any]:
        """
        Get cached value if exists and not expired.
        
        Returns:
            Cached value or None if not found/expired
        """
        cache_key = self._make_key(method, key)
        
        with self._lock:
            entry = self._cache.get(cache_key)
            
            if entry is None:
                self._stats["misses"] += 1
                return None
            
            if entry.is_expired():
                # Clean up expired entry
                del self._cache[cache_key]
                self._stats["misses"] += 1
                return None
            
            self._stats["hits"] += 1
            self._stats["credits_saved"] += self.CREDIT_COSTS.get(method, 1)
            
            logger.debug(f"📦 Cache HIT: {method}:{key[:16]}...")
            return entry.value
    
    def set(self, method: str, key: str, value: Any, custom_ttl: float = None):
        """
        Cache a value with appropriate TTL.
        
        Args:
            method: RPC method name (for TTL lookup)
            key: Unique key (usually mint address)
            value: Value to cache
            custom_ttl: Override default TTL
        """
        cache_key = self._make_key(method, key)
        ttl = custom_ttl or self.TTLS.get(method, 60)  # Default 60s
        
        with self._lock:
            self._cache[cache_key] = CacheEntry(
                value=value,
                timestamp=time.time(),
                ttl=ttl
            )
        
        logger.debug(f"📦 Cache SET: {method}:{key[:16]}... (TTL={ttl}s)")
    
    def invalidate(self, key: str = None, method: str = None):
        """
        Invalidate cache entries.
        
        Args:
            key: Invalidate all entries for this key (e.g., mint)
            method: Invalidate all entries for this method
        """
        with self._lock:
            if key and method:
                cache_key = self._make_key(method, key)
                if cache_key in self._cache:
                    del self._cache[cache_key]
            elif key:
                # Invalidate all methods for this key
                to_delete = [k for k in self._cache if k.endswith(f":{key}")]
                for k in to_delete:
                    del self._cache[k]
            elif method:
                # Invalidate all keys for this method
                to_delete = [k for k in self._cache if k.startswith(f"{method}:")]
                for k in to_delete:
                    del self._cache[k]
    
    def clear(self):
        """Clear all cached entries"""
        with self._lock:
            self._cache.clear()
            logger.info("📦 Cache cleared")
    
    def cleanup_expired(self):
        """Remove all expired entries"""
        with self._lock:
            now = time.time()
            expired = [
                k for k, v in self._cache.items()
                if v.is_expired()
            ]
            for k in expired:
                del self._cache[k]
            
            if expired:
                logger.debug(f"📦 Cleaned up {len(expired)} expired cache entries")
    
    def get_stats(self) -> Dict:
        """Get cache statistics"""
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0
            
            return {
                "entries": len(self._cache),
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "hit_rate_pct": round(hit_rate, 1),
                "credits_saved": self._stats["credits_saved"]
            }
    
    def print_stats(self):
        """Print cache stats to logger"""
        stats = self.get_stats()
        logger.info(
            f"📦 Cache Stats: {stats['entries']} entries | "
            f"Hit rate: {stats['hit_rate_pct']}% | "
            f"Credits saved: ~{stats['credits_saved']}"
        )


# Singleton instance
_rpc_cache: Optional[RPCCache] = None

def get_rpc_cache() -> RPCCache:
    """Get or create singleton RPCCache instance"""
    global _rpc_cache
    if _rpc_cache is None:
        _rpc_cache = RPCCache()
        logger.info("📦 RPCCache initialized")
    return _rpc_cache


# =============================================================================
# CREDIT LIMITER
# =============================================================================

class CreditLimiter:
    """
    Enforce credit budget to prevent overuse.
    
    Target: ~4k credits/hour (96k/day)
    
    If over budget:
    - Skip non-critical checks
    - Increase poll intervals
    - Only allow quote + critical events
    """
    
    MAX_CREDITS_PER_HOUR = 4000
    THROTTLE_THRESHOLD = 0.8  # Start throttling at 80% of budget
    
    def __init__(self, max_per_hour: int = None):
        self.max_per_hour = max_per_hour or self.MAX_CREDITS_PER_HOUR
        self._calls: list = []  # List of (timestamp, credits)
        self._lock = Lock()
        self._throttle_multiplier = 1.0
    
    def record_call(self, credits: int = 1):
        """Record an RPC call"""
        with self._lock:
            now = time.time()
            self._calls.append((now, credits))
            
            # Clean old entries (older than 1 hour)
            cutoff = now - 3600
            self._calls = [(t, c) for t, c in self._calls if t > cutoff]
    
    def get_usage(self) -> Dict:
        """Get current usage stats"""
        with self._lock:
            now = time.time()
            cutoff = now - 3600
            
            # Count credits in last hour
            recent = [(t, c) for t, c in self._calls if t > cutoff]
            total_credits = sum(c for _, c in recent)
            
            usage_pct = (total_credits / self.max_per_hour) * 100
            
            return {
                "credits_last_hour": total_credits,
                "max_per_hour": self.max_per_hour,
                "usage_pct": round(usage_pct, 1),
                "is_throttled": usage_pct > self.THROTTLE_THRESHOLD * 100,
                "calls_last_hour": len(recent)
            }
    
    def should_skip(self, priority: str = "normal") -> bool:
        """
        Check if call should be skipped due to budget.
        
        Priority levels:
        - "critical": Never skip (quotes, emergency exits)
        - "high": Skip only if > 95% budget
        - "normal": Skip if > 80% budget
        - "low": Skip if > 60% budget
        """
        usage = self.get_usage()
        usage_pct = usage["usage_pct"]
        
        thresholds = {
            "critical": 100,  # Never skip
            "high": 95,
            "normal": 80,
            "low": 60
        }
        
        threshold = thresholds.get(priority, 80)
        should_skip = usage_pct > threshold
        
        if should_skip:
            logger.warning(
                f"⚠️ Credit limit: skipping {priority} call "
                f"(usage {usage_pct:.0f}% > {threshold}%)"
            )
        
        return should_skip
    
    def get_throttle_delay(self) -> float:
        """
        Get additional delay to apply based on usage.
        
        Returns additional seconds to wait between calls.
        """
        usage = self.get_usage()
        usage_pct = usage["usage_pct"] / 100
        
        if usage_pct < self.THROTTLE_THRESHOLD:
            return 0.0
        
        # Linear increase from 0 to 5 seconds as usage goes 80% -> 100%
        excess = (usage_pct - self.THROTTLE_THRESHOLD) / (1.0 - self.THROTTLE_THRESHOLD)
        delay = excess * 5.0
        
        return min(delay, 5.0)
    
    def print_status(self):
        """Print current status"""
        usage = self.get_usage()
        logger.info(
            f"💳 Credits: {usage['credits_last_hour']}/{usage['max_per_hour']} "
            f"({usage['usage_pct']:.0f}%) | "
            f"Throttled: {usage['is_throttled']}"
        )


# Singleton instance
_credit_limiter: Optional[CreditLimiter] = None

def get_credit_limiter() -> CreditLimiter:
    """Get or create singleton CreditLimiter instance"""
    global _credit_limiter
    if _credit_limiter is None:
        _credit_limiter = CreditLimiter()
        logger.info("💳 CreditLimiter initialized (max 4k credits/hour)")
    return _credit_limiter
