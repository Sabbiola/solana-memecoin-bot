"""
Retry decorator for robust async operations.

Provides automatic retry with exponential backoff for network and RPC operations.
"""

import asyncio
import logging
from functools import wraps
from typing import TypeVar, Callable, Any

logger = logging.getLogger(__name__)

T = TypeVar('T')


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Retry async function with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries (seconds)
        backoff: Multiplier for delay on each retry
        exceptions: Tuple of exception types to catch
    
    Example:
        @async_retry(max_attempts=3, delay=0.5)
        async def fetch_data():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts - 1:
                        # Last attempt failed, raise
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts",
                            extra={
                                "function": func.__name__,
                                "attempts": max_attempts,
                                "error": str(e)
                            }
                        )
                        raise
                    
                    # Calculate delay with backoff
                    current_delay = delay * (backoff ** attempt)
                    
                    logger.warning(
                        f"{func.__name__} failed, retrying in {current_delay:.1f}s",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt + 1,
                            "max_attempts": max_attempts,
                            "delay": current_delay,
                            "error": str(e)
                        }
                    )
                    
                    await asyncio.sleep(current_delay)
            
            # Should never reach here
            raise last_exception
        
        return wrapper
    return decorator


def sync_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Retry synchronous function with exponential backoff.
    
    Same as async_retry but for sync functions.
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts - 1:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts",
                            extra={
                                "function": func.__name__,
                                "attempts": max_attempts,
                                "error": str(e)
                            }
                        )
                        raise
                    
                    current_delay = delay * (backoff ** attempt)
                    
                    logger.warning(
                        f"{func.__name__} failed, retrying in {current_delay:.1f}s",
                        extra={
                            "function": func.__name__,
                            "attempt": attempt + 1,
                            "max_attempts": max_attempts,
                            "delay": current_delay,
                            "error": str(e)
                        }
                    )
                    
                    import time
                    time.sleep(current_delay)
            
            raise last_exception
        
        return wrapper
    return decorator


class CircuitBreaker:
    """
    Circuit breaker to prevent cascading failures.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failures exceeded threshold, requests blocked
    - HALF_OPEN: Testing if service recovered
    
    Usage:
        cb = CircuitBreaker(name="RPC", failure_threshold=5)
        
        if cb.can_execute():
            try:
                result = await rpc_call()
                cb.record_success()
            except Exception as e:
                logger.debug(f"Circuit breaker check failed: {e}")
                cb.record_failure()
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        name: str = "default"
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening
            recovery_timeout: Seconds before attempting recovery
            name: Circuit breaker name (for logging)
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        
        self.failures = 0
        self.last_failure_time = 0
        self.state = "CLOSED"
    
    def record_success(self):
        """Record successful request"""
        self.failures = 0
        self.state = "CLOSED"
    
    def record_failure(self):
        """Record failed request"""
        import time
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"
            logger.warning(f"Circuit breaker '{self.name}' OPENED after {self.failures} failures")
    
    def can_execute(self) -> bool:
        """Check if request can be executed"""
        import time
        
        if self.state == "CLOSED":
            return True
        
        if self.state == "OPEN":
            # Check if recovery timeout passed
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self.state = "HALF_OPEN"
                logger.info(f"Circuit breaker '{self.name}' entering HALF_OPEN state")
                return True
            return False
        
        # HALF_OPEN - allow one request to test
        return True
    
    def get_status(self) -> dict:
        """Get circuit breaker status"""
        return {
            "name": self.name,
            "state": self.state,
            "failures": self.failures,
            "threshold": self.failure_threshold,
            "can_execute": self.can_execute()
        }


# Global circuit breakers for common services
rpc_circuit_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30.0,
    name="RPC"
)

jupiter_circuit_breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=60.0,
    name="Jupiter"
)

rugcheck_circuit_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=30.0,
    name="Rugcheck"
)
