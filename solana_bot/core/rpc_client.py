"""
RPC Client with Fallback Support

Provides robust RPC access with automatic failover between multiple endpoints.
"""

import asyncio
import logging
import time
from typing import List, Optional, Any, Dict, Callable
from dataclasses import dataclass
from enum import Enum

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment

from ..utils.retry import async_retry, CircuitBreaker
from ..utils.rate_limiter import TokenBucket

logger = logging.getLogger(__name__)


class RPCHealth(Enum):
    """RPC endpoint health status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class RPCEndpoint:
    """RPC endpoint configuration"""
    url: str
    name: str
    priority: int = 0  # Lower = higher priority
    rate_limit: float = 10.0  # Requests per second
    weight: int = 1  # For weighted selection
    
    # Runtime state
    health: RPCHealth = RPCHealth.UNKNOWN
    latency_ms: float = 0.0
    last_check: float = 0.0
    consecutive_failures: int = 0
    total_requests: int = 0
    failed_requests: int = 0


class RPCClientWithFallback:
    """
    RPC client with automatic fallover between multiple endpoints.
    
    Features:
    - Priority-based endpoint selection
    - Health monitoring
    - Automatic failover on errors
    - Circuit breaker per endpoint
    - Rate limiting per endpoint
    - Latency tracking
    
    Usage:
        client = RPCClientWithFallback([
            RPCEndpoint("https://api.mainnet-beta.solana.com", "Solana", priority=2),
            RPCEndpoint("https://rpc.helius.xyz/?api-key=xxx", "Helius", priority=0),
            RPCEndpoint("https://solana-mainnet.g.alchemy.com/v2/xxx", "Alchemy", priority=1),
        ])
        
        await client.initialize()
        balance = await client.get_balance(pubkey)
    """
    
    def __init__(self, endpoints: List[RPCEndpoint]):
        """
        Initialize RPC client with multiple endpoints.
        
        Args:
            endpoints: List of RPC endpoint configurations
        """
        if not endpoints:
            raise ValueError("At least one endpoint required")
        
        self.endpoints = sorted(endpoints, key=lambda e: e.priority)
        self._clients: Dict[str, AsyncClient] = {}
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._rate_limiters: Dict[str, TokenBucket] = {}
        self._current_endpoint: Optional[RPCEndpoint] = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize all RPC clients"""
        for endpoint in self.endpoints:
            # Create client
            self._clients[endpoint.url] = AsyncClient(endpoint.url)
            
            # Create circuit breaker
            self._circuit_breakers[endpoint.url] = CircuitBreaker(
                failure_threshold=3,
                recovery_timeout=30.0,
                name=endpoint.name
            )
            
            # Create rate limiter
            self._rate_limiters[endpoint.url] = TokenBucket(
                rate=endpoint.rate_limit,
                capacity=int(endpoint.rate_limit * 2)
            )
        
        # Health check all endpoints
        await self._health_check_all()
        
        # Select best endpoint
        self._current_endpoint = self._select_best_endpoint()
        
        if self._current_endpoint:
            logger.info(f"✅ RPC initialized with {self._current_endpoint.name}")
        else:
            logger.error("❌ No healthy RPC endpoints available")
        
        self._initialized = True
    
    async def _health_check_all(self):
        """Health check all endpoints"""
        tasks = [self._health_check(ep) for ep in self.endpoints]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _health_check(self, endpoint: RPCEndpoint):
        """Health check single endpoint"""
        client = self._clients.get(endpoint.url)
        if not client:
            return
        
        try:
            start = time.time()
            result = await asyncio.wait_for(
                client.get_slot(),
                timeout=5.0
            )
            latency = (time.time() - start) * 1000
            
            endpoint.latency_ms = latency
            endpoint.last_check = time.time()
            
            if latency < 500:
                endpoint.health = RPCHealth.HEALTHY
            elif latency < 2000:
                endpoint.health = RPCHealth.DEGRADED
            else:
                endpoint.health = RPCHealth.UNHEALTHY
            
            endpoint.consecutive_failures = 0
            
            logger.debug(f"Health check {endpoint.name}: {endpoint.health.value} ({latency:.0f}ms)")
        
        except Exception as e:
            endpoint.health = RPCHealth.UNHEALTHY
            endpoint.consecutive_failures += 1
            logger.warning(f"Health check failed for {endpoint.name}: {e}")
    
    def _select_best_endpoint(self) -> Optional[RPCEndpoint]:
        """Select best available endpoint"""
        for endpoint in self.endpoints:
            cb = self._circuit_breakers.get(endpoint.url)
            
            # Skip if circuit breaker is open
            if cb and not cb.can_execute():
                continue
            
            # Skip unhealthy endpoints
            if endpoint.health == RPCHealth.UNHEALTHY:
                continue
            
            return endpoint
        
        # Fallback to first endpoint even if unhealthy
        return self.endpoints[0] if self.endpoints else None
    
    async def _execute_with_fallback(
        self,
        method: str,
        *args,
        **kwargs
    ) -> Any:
        """
        Execute RPC method with automatic fallback.
        
        Args:
            method: RPC method name
            *args: Method arguments
            **kwargs: Method keyword arguments
        
        Returns:
            RPC response
        """
        last_error = None
        
        for endpoint in self.endpoints:
            client = self._clients.get(endpoint.url)
            cb = self._circuit_breakers.get(endpoint.url)
            limiter = self._rate_limiters.get(endpoint.url)
            
            if not client:
                continue
            
            # Check circuit breaker
            if cb and not cb.can_execute():
                logger.debug(f"Skipping {endpoint.name}: circuit breaker open")
                continue
            
            try:
                # Rate limit
                if limiter:
                    await limiter.acquire()
                
                # Execute
                endpoint.total_requests += 1
                start = time.time()
                
                rpc_method = getattr(client, method)
                result = await asyncio.wait_for(
                    rpc_method(*args, **kwargs),
                    timeout=10.0
                )
                
                latency = (time.time() - start) * 1000
                endpoint.latency_ms = (endpoint.latency_ms + latency) / 2  # Moving average
                
                # Success
                if cb:
                    cb.record_success()
                
                return result
            
            except Exception as e:
                last_error = e
                endpoint.failed_requests += 1
                endpoint.consecutive_failures += 1
                
                if cb:
                    cb.record_failure()
                
                logger.warning(f"RPC {method} failed on {endpoint.name}: {e}")
                
                # Try next endpoint
                continue
        
        # All endpoints failed
        logger.error(f"All RPC endpoints failed for {method}")
        raise last_error or RuntimeError("All RPC endpoints failed")
    
    # Wrapped RPC methods
    async def get_balance(self, pubkey, commitment: Commitment = None):
        """Get account balance"""
        return await self._execute_with_fallback("get_balance", pubkey, commitment=commitment)
    
    async def get_slot(self, commitment: Commitment = None):
        """Get current slot"""
        return await self._execute_with_fallback("get_slot", commitment=commitment)
    
    async def get_block_height(self, commitment: Commitment = None):
        """Get block height"""
        return await self._execute_with_fallback("get_block_height", commitment=commitment)
    
    async def get_latest_blockhash(self, commitment: Commitment = None):
        """Get latest blockhash"""
        return await self._execute_with_fallback("get_latest_blockhash", commitment=commitment)
    
    async def get_account_info(self, pubkey, commitment: Commitment = None):
        """Get account info"""
        return await self._execute_with_fallback("get_account_info", pubkey, commitment=commitment)
    
    async def get_token_accounts_by_owner(self, owner, opts):
        """Get token accounts by owner"""
        return await self._execute_with_fallback("get_token_accounts_by_owner", owner, opts)
    
    async def get_signature_statuses(self, signatures):
        """Get signature statuses"""
        return await self._execute_with_fallback("get_signature_statuses", signatures)
    
    async def send_transaction(self, txn, opts=None):
        """Send transaction"""
        return await self._execute_with_fallback("send_transaction", txn, opts=opts)
    
    async def confirm_transaction(self, signature, commitment: Commitment = None):
        """Confirm transaction"""
        return await self._execute_with_fallback("confirm_transaction", signature, commitment=commitment)
    
    async def get_transaction(self, signature, encoding: str = "json"):
        """Get transaction details"""
        return await self._execute_with_fallback("get_transaction", signature, encoding=encoding)
    
    # Status methods
    def get_status(self) -> Dict[str, Any]:
        """Get RPC client status"""
        return {
            "current_endpoint": self._current_endpoint.name if self._current_endpoint else None,
            "endpoints": [
                {
                    "name": ep.name,
                    "url": ep.url[:30] + "...",
                    "health": ep.health.value,
                    "latency_ms": round(ep.latency_ms, 1),
                    "total_requests": ep.total_requests,
                    "failed_requests": ep.failed_requests,
                    "circuit_breaker": self._circuit_breakers[ep.url].state if ep.url in self._circuit_breakers else "N/A"
                }
                for ep in self.endpoints
            ]
        }
    
    async def close(self):
        """Close all clients"""
        for client in self._clients.values():
            await client.close()


# Helper function to create client from config
def create_rpc_client_from_config() -> RPCClientWithFallback:
    """Create RPC client from environment config"""
    import os
    
    endpoints = []
    
    # Primary RPC (required)
    primary_url = os.getenv("RPC_URL")
    if primary_url:
        endpoints.append(RPCEndpoint(
            url=primary_url,
            name="Primary",
            priority=0,
            rate_limit=10.0
        ))
    
    # Fallback RPCs (optional)
    fallback_urls = os.getenv("RPC_FALLBACK_URLS", "").split(",")
    for i, url in enumerate(fallback_urls):
        url = url.strip()
        if url:
            endpoints.append(RPCEndpoint(
                url=url,
                name=f"Fallback_{i+1}",
                priority=i + 1,
                rate_limit=5.0
            ))
    
    # Default public RPC as last resort
    endpoints.append(RPCEndpoint(
        url="https://api.mainnet-beta.solana.com",
        name="Solana_Public",
        priority=99,
        rate_limit=2.0  # Very limited
    ))
    
    return RPCClientWithFallback(endpoints)
