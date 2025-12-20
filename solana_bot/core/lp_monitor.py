"""
LP Integrity Monitor

Real-time monitoring of liquidity pool reserves to detect:
- LP removal (rug pulls)
- Significant liquidity drops
- Suspicious reserve changes

Checks pool every 5s and triggers emergency exit if LP changes > threshold.
"""

import asyncio
import logging
import time
from typing import Optional, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LPSnapshot:
    """Snapshot of LP reserves at a point in time"""
    timestamp: float
    reserve_sol: float
    reserve_token: float
    total_liquidity_usd: float


class LPMonitor:
    """
    Monitor liquidity pool integrity in real-time.
    
    Usage:
        monitor = LPMonitor(mint, phase, validator)
        await monitor.start()
        
        # Later, in monitoring loop:
        if not await monitor.check_integrity():
            # LP compromised, exit immediately
    """
    
    def __init__(
        self,
        mint: str,
        phase: str,
        validator,
        check_interval: float = 60.0,  # OPTIMIZED: Was 5s, now 60s to save credits
        alert_threshold_pct: float = 5.0
    ):
        self.mint = mint
        self.phase = phase
        self.validator = validator
        self.check_interval = check_interval
        self.alert_threshold_pct = alert_threshold_pct
        
        # State
        self.snapshots: list[LPSnapshot] = []
        self.baseline_snapshot: Optional[LPSnapshot] = None
        self.is_running = False
        self.monitoring_task: Optional[asyncio.Task] = None
        
        # Alerts
        self.integrity_compromised = False
        self.last_alert_reason = ""
        
    async def start(self):
        """Start monitoring in background"""
        if self.is_running:
            return
            
        logger.info(f"🔍 Starting LP monitor for {self.mint[:8]}... (check every {self.check_interval}s)")
        
        # Take initial snapshot as baseline
        self.baseline_snapshot = await self._take_snapshot()
        if self.baseline_snapshot:
            logger.info(
                f"   📊 Baseline LP: {self.baseline_snapshot.reserve_sol:.2f} SOL | "
                f"${self.baseline_snapshot.total_liquidity_usd:,.0f}"
            )
        else:
            logger.warning(f"   ⚠️ Failed to get baseline snapshot for {self.mint[:8]}")
            
        self.is_running = True
        self.monitoring_task = asyncio.create_task(self._monitor_loop())
        
    async def stop(self):
        """Stop monitoring"""
        self.is_running = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
                
    async def check_integrity(self) -> bool:
        """
        Check if LP is still intact.
        
        Returns:
            True if LP is safe, False if compromised
        """
        if self.integrity_compromised:
            return False
            
        # If no snapshots yet, assume OK (grace period)
        if len(self.snapshots) < 2:
            return True
            
        # Check latest snapshot against baseline
        latest = self.snapshots[-1]
        
        if not self.baseline_snapshot:
            return True
            
        # Calculate LP change (protect against division by zero)
        if self.baseline_snapshot.total_liquidity_usd > 0:
            lp_delta_pct = (
                (latest.total_liquidity_usd - self.baseline_snapshot.total_liquidity_usd) 
                / self.baseline_snapshot.total_liquidity_usd 
                * 100
            )
        else:
            # No baseline liquidity - can't calculate % change, skip monitoring
            logger.debug(f"Skipping LP check for {self.mint[:8]} - zero baseline liquidity")
            return True
        
        # Check if drop exceeds threshold
        if lp_delta_pct < -self.alert_threshold_pct:
            self.integrity_compromised = True
            self.last_alert_reason = f"LP_DROP_{abs(lp_delta_pct):.1f}%"
            logger.error(
                f"🚨 LP INTEGRITY COMPROMISED for {self.mint[:8]}: "
                f"Drop {lp_delta_pct:.1f}% (${self.baseline_snapshot.total_liquidity_usd:,.0f} → "
                f"${latest.total_liquidity_usd:,.0f})"
            )
            return False
            
        return True
        
    async def _monitor_loop(self):
        """Background monitoring loop"""
        while self.is_running:
            try:
                await asyncio.sleep(self.check_interval)
                
                snapshot = await self._take_snapshot()
                if snapshot:
                    self.snapshots.append(snapshot)
                    
                    # Keep only last 60 snapshots (5 mins at 5s interval)
                    if len(self.snapshots) > 60:
                        self.snapshots = self.snapshots[-60:]
                        
                    # Auto-check integrity
                    await self.check_integrity()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"LP monitor error for {self.mint[:8]}: {e}")
                await asyncio.sleep(self.check_interval)
                
    async def _take_snapshot(self) -> Optional[LPSnapshot]:
        """Take a snapshot of current LP state"""
        try:
            # Use pool_quality_filter to get reserve data
            pool_check = await self.validator.pool_quality_filter(self.phase, self.mint)
            
            if not pool_check or not pool_check.get("passed"):
                return None
                
            reserve_sol = pool_check.get("liquidity_sol", 0)
            
            # Estimate USD value (SOL price ~$200 as rough estimate)
            # In production, should fetch real SOL/USD price
            total_liquidity_usd = reserve_sol * 200
            
            return LPSnapshot(
                timestamp=time.time(),
                reserve_sol=reserve_sol,
                reserve_token=0,  # Not always available
                total_liquidity_usd=total_liquidity_usd
            )
            
        except Exception as e:
            logger.debug(f"Failed to take LP snapshot for {self.mint[:8]}: {e}")
            return None
            
    def get_status(self) -> Dict:
        """Get current monitoring status"""
        if not self.baseline_snapshot:
            return {"status": "INITIALIZING"}
            
        latest = self.snapshots[-1] if self.snapshots else None
        
        if not latest:
            return {"status": "NO_DATA"}
            
        # Protect against division by zero
        if self.baseline_snapshot.total_liquidity_usd > 0:
            lp_delta_pct = (
                (latest.total_liquidity_usd - self.baseline_snapshot.total_liquidity_usd) 
                / self.baseline_snapshot.total_liquidity_usd 
                * 100
            )
        else:
            lp_delta_pct = 0.0  # Can't calculate change without baseline
        
        return {
            "status": "COMPROMISED" if self.integrity_compromised else "OK",
            "baseline_lp_usd": self.baseline_snapshot.total_liquidity_usd,
            "current_lp_usd": latest.total_liquidity_usd,
            "delta_pct": lp_delta_pct,
            "snapshots_count": len(self.snapshots),
            "alert_reason": self.last_alert_reason if self.integrity_compromised else None
        }
