"""
Safe wrappers for external module integrations.

Provides fault-tolerant interfaces to RiskManager and DataCollector
that never crash the main bot.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from ..exceptions import RiskManagerException, DataCollectorException

logger = logging.getLogger(__name__)


class SafeRiskManager:
    """
    Fault-tolerant wrapper for RiskManager.
    
    Never crashes the bot - always returns safe defaults on errors.
    """
    
    def __init__(self, inner: Optional[Any] = None, timeout: float = 5.0):
        """
        Args:
            inner: Actual RiskManager instance (or None)
            timeout: Max seconds to wait for RiskManager operations
        """
        self.inner = inner
        self.timeout = timeout
        self.learning_mode = getattr(inner, 'learning_mode', False) if inner else False
        self._error_count = 0
        self._max_errors = 5
    
    async def should_execute_trade(
        self,
        estimated_trade_sol: float,
        reason: str = "",
        extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Check if trade should be executed.
        
        Returns safe default (EXECUTE) if RiskManager unavailable or errors.
        """
        if not self.inner:
            return {
                "decision": "EXECUTE",
                "reason": "no_risk_manager",
                "equity": 0.0,
                "exposure": 0.0
            }
        
        if self._error_count >= self._max_errors:
            logger.warning(
                "RiskManager disabled due to excessive errors",
                error_count=self._error_count
            )
            return {
                "decision": "EXECUTE",
                "reason": "risk_manager_disabled",
                "equity": 0.0,
                "exposure": 0.0
            }
        
        try:
            result = await asyncio.wait_for(
                self.inner.should_execute_trade(
                    estimated_trade_sol=estimated_trade_sol,
                    reason=reason,
                    extra=extra
                ),
                timeout=self.timeout
            )
            
            # Reset error count on success
            self._error_count = 0
            return result
            
        except asyncio.TimeoutError:
            self._error_count += 1
            logger.error(
                "RiskManager timeout",
                timeout=self.timeout,
                error_count=self._error_count
            )
            return {
                "decision": "EXECUTE",
                "reason": "risk_manager_timeout",
                "equity": 0.0,
                "exposure": 0.0
            }
        
        except Exception as e:
            self._error_count += 1
            logger.error(
                "RiskManager error",
                error=str(e),
                error_type=type(e).__name__,
                error_count=self._error_count,
                exc_info=True
            )
            return {
                "decision": "EXECUTE",
                "reason": f"risk_manager_error: {type(e).__name__}",
                "equity": 0.0,
                "exposure": 0.0
            }
    
    async def refresh_equity(self) -> float:
        """Refresh equity calculation."""
        if not self.inner or not hasattr(self.inner, 'refresh_equity'):
            return 0.0
        
        try:
            return await asyncio.wait_for(
                self.inner.refresh_equity(),
                timeout=self.timeout
            )
        except Exception as e:
            logger.warning("RiskManager refresh_equity failed", error=str(e))
            return 0.0
    
    async def status(self) -> Dict[str, Any]:
        """Get RiskManager status."""
        if not self.inner or not hasattr(self.inner, 'status'):
            return {
                "available": False,
                "learning_mode": self.learning_mode
            }
        
        try:
            result = await asyncio.wait_for(
                self.inner.status(),
                timeout=self.timeout
            )
            return {**result, "available": True}
        except Exception as e:
            logger.warning("RiskManager status failed", error=str(e))
            return {
                "available": False,
                "error": str(e),
                "learning_mode": self.learning_mode
            }
    
    def force_stop(self):
        """Force stop trading."""
        if self.inner and hasattr(self.inner, 'force_stop'):
            try:
                self.inner.force_stop()
            except Exception as e:
                logger.error("RiskManager force_stop failed", error=str(e))
    
    def force_resume(self):
        """Force resume trading."""
        if self.inner and hasattr(self.inner, 'force_resume'):
            try:
                self.inner.force_resume()
            except Exception as e:
                logger.error("RiskManager force_resume failed", error=str(e))


class SafeDataCollector:
    """
    Fault-tolerant wrapper for DataCollector.
    
    Never crashes the bot - silently skips data collection on errors.
    """
    
    def __init__(self, inner: Optional[Any] = None, timeout: float = 2.0):
        """
        Args:
            inner: Actual DataCollector module (or None)
            timeout: Max seconds to wait for operations
        """
        self.inner = inner
        self.timeout = timeout
        self._error_count = 0
        self._max_errors = 10
    
    async def record_trade(self, trade_data: Dict[str, Any]):
        """
        Record trade data.
        
        Silently fails if collector unavailable.
        """
        if not self.inner or self._error_count >= self._max_errors:
            return
        
        try:
            if hasattr(self.inner, 'record_trade'):
                func = self.inner.record_trade
                
                # Handle both sync and async
                if asyncio.iscoroutinefunction(func):
                    await asyncio.wait_for(func(trade_data), timeout=self.timeout)
                else:
                    await asyncio.get_event_loop().run_in_executor(
                        None, func, trade_data
                    )
        except Exception as e:
            self._error_count += 1
            logger.warning(
                "DataCollector record_trade failed",
                error=str(e),
                error_count=self._error_count
            )
    
    async def record_validation(self, validation_data: Dict[str, Any]):
        """Record validation attempt."""
        if not self.inner or self._error_count >= self._max_errors:
            return
        
        try:
            if hasattr(self.inner, 'record_validation'):
                func = self.inner.record_validation
                
                if asyncio.iscoroutinefunction(func):
                    await asyncio.wait_for(func(validation_data), timeout=self.timeout)
                else:
                    await asyncio.get_event_loop().run_in_executor(
                        None, func, validation_data
                    )
        except Exception as e:
            self._error_count += 1
            logger.warning(
                "DataCollector record_validation failed",
                error=str(e),
                error_count=self._error_count
            )
    
    def init_db(self):
        """Initialize database."""
        if self.inner and hasattr(self.inner, 'init_db'):
            try:
                self.inner.init_db()
                logger.info("DataCollector initialized")
            except Exception as e:
                logger.error("DataCollector init_db failed", error=str(e), exc_info=True)
