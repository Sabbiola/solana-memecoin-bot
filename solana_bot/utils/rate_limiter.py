"""
Rate Limiting Utilities

Provides request rate limiting and queuing for API calls.
"""

import asyncio
import time
import logging
from typing import Optional, Callable, TypeVar, Any
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class RateLimitConfig:
    """Rate limit configuration"""
    requests_per_second: float = 10.0
    requests_per_minute: float = 300.0
    burst_limit: int = 20  # Max requests in burst
    
    @property
    def min_interval(self) -> float:
        """Minimum interval between requests"""
        return 1.0 / self.requests_per_second


class TokenBucket:
    """
    Token bucket rate limiter.
    
    Allows burst traffic while enforcing average rate.
    
    Usage:
        limiter = TokenBucket(rate=10.0, capacity=20)
        
        async def make_request():
            await limiter.acquire()
            # Request is now allowed
            return await actual_request()
    """
    
    def __init__(self, rate: float = 10.0, capacity: int = 20):
        """
        Initialize token bucket.
        
        Args:
            rate: Tokens per second (refill rate)
            capacity: Maximum tokens (burst capacity)
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> float:
        """
        Acquire tokens, waiting if necessary.
        
        Args:
            tokens: Number of tokens to acquire
        
        Returns:
            Seconds waited
        """
        async with self._lock:
            await self._refill()
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0
            
            # Calculate wait time
            tokens_needed = tokens - self.tokens
            wait_time = tokens_needed / self.rate
            
            logger.debug(f"Rate limited, waiting {wait_time:.2f}s")
            await asyncio.sleep(wait_time)
            
            await self._refill()
            self.tokens -= tokens
            
            return wait_time
    
    async def _refill(self):
        """Refill tokens based on elapsed time"""
        now = time.time()
        elapsed = now - self.last_update
        
        self.tokens = min(
            self.capacity,
            self.tokens + elapsed * self.rate
        )
        self.last_update = now
    
    def get_status(self) -> dict:
        """Get current status"""
        return {
            "tokens": self.tokens,
            "capacity": self.capacity,
            "rate": self.rate,
            "available": self.tokens >= 1
        }


class SlidingWindowRateLimiter:
    """
    Sliding window rate limiter.
    
    More accurate but uses more memory than token bucket.
    """
    
    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: float = 60.0,
        name: str = "default"
    ):
        """
        Initialize sliding window limiter.
        
        Args:
            max_requests: Maximum requests in window
            window_seconds: Window duration
            name: Limiter name (for logging)
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.name = name
        self.request_times: deque = deque()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> bool:
        """
        Try to acquire rate limit slot.
        
        Returns:
            True if acquired, False if rate limited
        """
        async with self._lock:
            now = time.time()
            
            # Remove old entries
            cutoff = now - self.window_seconds
            while self.request_times and self.request_times[0] < cutoff:
                self.request_times.popleft()
            
            # Check limit
            if len(self.request_times) >= self.max_requests:
                oldest = self.request_times[0]
                wait_time = oldest + self.window_seconds - now
                logger.warning(f"Rate limiter '{self.name}': limit reached, need to wait {wait_time:.1f}s")
                return False
            
            # Record request
            self.request_times.append(now)
            return True
    
    async def wait_and_acquire(self) -> float:
        """
        Wait until rate limit allows, then acquire.
        
        Returns:
            Seconds waited
        """
        total_waited = 0.0
        
        while True:
            if await self.acquire():
                return total_waited
            
            # Calculate wait time
            async with self._lock:
                if self.request_times:
                    oldest = self.request_times[0]
                    wait_time = oldest + self.window_seconds - time.time() + 0.1
                else:
                    wait_time = 0.1
            
            await asyncio.sleep(wait_time)
            total_waited += wait_time
    
    def get_status(self) -> dict:
        """Get current status"""
        now = time.time()
        cutoff = now - self.window_seconds
        
        # Count recent requests
        recent = sum(1 for t in self.request_times if t > cutoff)
        
        return {
            "name": self.name,
            "recent_requests": recent,
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "available": recent < self.max_requests
        }


class RequestQueue:
    """
    Request queue with rate limiting.
    
    Queues requests and processes them at controlled rate.
    """
    
    def __init__(
        self,
        rate_limiter: Optional[TokenBucket] = None,
        max_queue_size: int = 100,
        name: str = "default"
    ):
        """
        Initialize request queue.
        
        Args:
            rate_limiter: Rate limiter to use
            max_queue_size: Maximum queue size
            name: Queue name
        """
        self.rate_limiter = rate_limiter or TokenBucket()
        self.max_queue_size = max_queue_size
        self.name = name
        
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._processing = False
    
    async def submit(self, func: Callable, *args, **kwargs) -> Any:
        """
        Submit request to queue.
        
        Args:
            func: Async function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
        
        Returns:
            Function result
        """
        future = asyncio.Future()
        
        await self._queue.put((func, args, kwargs, future))
        
        if not self._processing:
            asyncio.create_task(self._process_queue())
        
        return await future
    
    async def _process_queue(self):
        """Process queued requests"""
        self._processing = True
        
        try:
            while not self._queue.empty():
                func, args, kwargs, future = await self._queue.get()
                
                # Wait for rate limit
                await self.rate_limiter.acquire()
                
                try:
                    result = await func(*args, **kwargs)
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)
        finally:
            self._processing = False
    
    def get_status(self) -> dict:
        """Get queue status"""
        return {
            "name": self.name,
            "queue_size": self._queue.qsize(),
            "max_queue_size": self.max_queue_size,
            "processing": self._processing,
            "rate_limiter": self.rate_limiter.get_status()
        }


# Pre-configured rate limiters
rpc_rate_limiter = TokenBucket(rate=10.0, capacity=20)  # 10 RPC/sec, burst 20
jupiter_rate_limiter = TokenBucket(rate=5.0, capacity=10)  # 5 Jupiter/sec
rugcheck_rate_limiter = TokenBucket(rate=2.0, capacity=5)  # 2 Rugcheck/sec


def rate_limited(limiter: TokenBucket):
    """
    Decorator for rate-limited async functions.
    
    Usage:
        @rate_limited(rpc_rate_limiter)
        async def rpc_call():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        async def wrapper(*args, **kwargs) -> T:
            await limiter.acquire()
            return await func(*args, **kwargs)
        return wrapper
    return decorator
