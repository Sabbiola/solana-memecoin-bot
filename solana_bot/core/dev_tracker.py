"""
Dev Wallet Tracker

Monitors developer/creator wallet for suspicious activity:
- Token sells
- Large transfers
- LP removal

Triggers immediate alerts for exit signals.
"""

import asyncio
import logging
from typing import Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DevActivity:
    """Dev wallet activity event"""
    timestamp: float
    activity_type: str  # SELL, TRANSFER, LP_REMOVE
    amount: float
    signature: str


class DevTracker:
    """
    Monitor dev wallet for dangerous activities.
    
    Usage:
        tracker = DevTracker(dev_wallet, mint, client)
        tracker.on_sell_callback = lambda: emergency_exit()
        await tracker.start()
    """
    
    def __init__(
        self,
        dev_wallet: str,
        mint: str,
        rpc_client,
        rugcheck_result=None
    ):
        self.dev_wallet = dev_wallet
        self.mint = mint
        self.client = rpc_client
        self.rugcheck_result = rugcheck_result
        
        # State
        self.is_monitoring = False
        self.dev_sold = False
        self.activities: list[DevActivity] = []
        
        # Callbacks
        self.on_sell_callback: Optional[Callable] = None
        
        # Initial dev holding
        self.initial_dev_pct = (
            rugcheck_result.dev_holding_pct 
            if rugcheck_result 
            else 0
        )
        
    async def start(self, webhook_server=None):
        """
        Start monitoring dev wallet.
        
        Args:
            webhook_server: Optional HeliusWebhookServer instance.
                           If provided, uses event-driven monitoring (no RPC polling).
                           If not provided, falls back to polling (high RPC cost).
        """
        if not self.dev_wallet or self.dev_wallet == "unknown":
            logger.debug(f"No dev wallet to monitor for {self.mint[:8]}")
            return
            
        logger.info(f"👁️ Monitoring dev wallet {self.dev_wallet[:8]}... (initial holding: {self.initial_dev_pct:.1f}%)")
        self.is_monitoring = True
        
        # Prefer event-driven monitoring (zero RPC cost)
        if webhook_server:
            self._webhook_server = webhook_server
            webhook_server.watch_dev_wallet(self.mint, self.dev_wallet)
            logger.info(f"   ✅ Using event-driven monitoring (zero RPC cost)")
        else:
            # Fallback to polling (high RPC cost - should be avoided)
            logger.warning(f"   ⚠️ No webhook server - falling back to polling (high RPC cost)")
            asyncio.create_task(self._poll_loop())
        
    async def stop(self):
        """Stop monitoring"""
        self.is_monitoring = False
        
        # Unsubscribe from webhook
        if hasattr(self, '_webhook_server') and self._webhook_server:
            self._webhook_server.unwatch_dev_wallet(self.mint)
        
    async def check_dev_activity(self) -> bool:
        """
        Check if dev has sold or transferred tokens.
        
        Returns:
            True if dev activity detected (sell signal), False otherwise
        """
        if self.dev_sold:
            return True
            
        # In event-driven mode, this is just a cache check
        # Events are pushed by webhook handler
        return self.dev_sold
        
    async def _poll_loop(self):
        """Polling loop to check dev wallet (DEPRECATED - use webhook instead)"""
        while self.is_monitoring:
            try:
                await asyncio.sleep(60)  # OPTIMIZED: Was 10s, now 60s (fallback only)
                
                # Check dev token balance
                # This requires implementing getTokenAccountsByOwner
                # Skipped for now as webhook is preferred
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Dev tracker poll error: {e}")
                
    def get_status(self) -> dict:
        """Get tracking status"""
        return {
            "dev_wallet": self.dev_wallet[:8] + "..." if self.dev_wallet else None,
            "monitoring": self.is_monitoring,
            "dev_sold": self.dev_sold,
            "initial_holding_pct": self.initial_dev_pct,
            "activities_count": len(self.activities)
        }
