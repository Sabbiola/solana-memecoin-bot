"""
Narrative Analyzer

Measures FOMO/mania patterns without social scraping.

Philosophy:
- Structural edge (EAS) ≠ Narrative edge
- Blow-off tops are narrative-driven
- Need proxies for "attention" and "mania"

Metrics (all on-chain/DEX):
1. Wallet Influx Velocity (WIV)
2. Buy/Sell Count Skew (BSS)
3. Median Trade Size Trend (MTS)
4. Attention Proxy (tx/sec delta)
5. Churn Ratio (CR)

Usage: Modulate trailing, NOT entry signals.
"""

import logging
from dataclasses import dataclass
from typing import List
import time

logger = logging.getLogger(__name__)


@dataclass
class NarrativeSnapshot:
    """Narrative metrics at a point in time"""
    timestamp: float
    wallet_influx_velocity: float  # New wallets/min
    buy_sell_skew: float          # Buy count / sell count
    median_trade_size: float      # SOL
    tx_per_second: float          # Transaction rate
    churn_ratio: float            # Quick flip %
    

class NarrativeAnalyzer:
    """
    Detects FOMO/mania patterns for narrative edge measurement.
    
    Key insight:
    - Structural edge can die while narrative accelerates
    - This is THE blow-off pattern
    - Measure it, don't guess
    """
    
    def __init__(self):
        self.snapshots: List[NarrativeSnapshot] = []
        self.last_unique_wallets = 0
        
    async def calculate_metrics(
        self,
        recent_txs: list = None,  # From DexScreener or on-chain
        unique_wallets: int = 0,
        buy_count: int = 0,
        sell_count: int = 0
    ) -> NarrativeSnapshot:
        """
        Calculate all narrative metrics.
        
        Args:
            recent_txs: Recent transaction data
            unique_wallets: Count of unique wallets (from VolumeAnalyzer)
            buy_count: Number of buy transactions
            sell_count: Number of sell transactions
            
        Returns:
            NarrativeSnapshot with all metrics
        """
        # Wallet Influx Velocity
        if len(self.snapshots) > 0:
            time_delta = (time.time() - self.snapshots[-1].timestamp) / 60  # minutes
            wiv = (unique_wallets - self.last_unique_wallets) / max(time_delta, 0.01)
        else:
            wiv = 0.0
            
        self.last_unique_wallets = unique_wallets
            
        # Buy/Sell Count Skew
        if sell_count > 0:
            bss = buy_count / sell_count
        else:
            bss = buy_count if buy_count > 0 else 1.0
            
        # Median Trade Size (simplified - would need tx parsing)
        mts = 0.05  # Placeholder - need actual median from txs
        
        # TX per second
        if recent_txs and len(recent_txs) > 0:
            tx_per_sec = len(recent_txs) / 60  # Assume 60s window
        else:
            tx_per_sec = (buy_count + sell_count) / 60
            
        # Churn Ratio (simplified - need wallet tracking)
        cr = 0.0  # Placeholder
        
        snapshot = NarrativeSnapshot(
            timestamp=time.time(),
            wallet_influx_velocity=wiv,
            buy_sell_skew=bss,
            median_trade_size=mts,
            tx_per_second=tx_per_sec,
            churn_ratio=cr
        )
        
        self.snapshots.append(snapshot)
        
        # Keep last 20
        if len(self.snapshots) > 20:
            self.snapshots = self.snapshots[-20:]
            
        return snapshot
        
    def detect_mania_phase(self) -> tuple[str, float]:
        """
        v12.3 FIX: Distinguish INFLOW from DISTRIBUTION mania.
        
        INFLOW (favorable - widen trailing):
        - WIV accelerating
        - Buy count >> sell count
        - Price making higher highs
        
        DISTRIBUTION (dangerous - tighten trailing):
        - WIV accelerating BUT
        - Sell size increasing
        - Price failing to make higher highs
        
        Returns:
            (phase, confidence): "INFLOW", "DISTRIBUTION", or "NONE"
        """
        if len(self.snapshots) < 3:
            return ("NONE", 0.0)
            
        recent = self.snapshots[-3:]
        older = self.snapshots[:3] if len(self.snapshots) >= 6 else recent
        
        # WIV trend
        wiv_recent = sum(s.wallet_influx_velocity for s in recent) / len(recent)
        wiv_older = sum(s.wallet_influx_velocity for s in older) / len(older)
        wiv_accelerating = wiv_recent > wiv_older * 1.2
        
        # BSS current
        bss_current = recent[-1].buy_sell_skew
        high_buy_pressure = bss_current > 2.0
        
        # TX/sec trend
        txps_recent = sum(s.tx_per_second for s in recent) / len(recent)
        txps_older = sum(s.tx_per_second for s in older) / len(older)
        tx_accelerating = txps_recent > txps_older * 1.3
        
        # Price trend (simplified via tx metrics)
        # If buy pressure high + tx accelerating = likely HH
        making_higher_highs = high_buy_pressure and tx_accelerating
        
        # Check for distribution signals
        bss_weakening = bss_current < 1.5  # Sells catching up
        
        # Mania phase detection
        if wiv_accelerating and tx_accelerating:
            if making_higher_highs and high_buy_pressure:
                # INFLOW: All signs positive
                confidence = 0.75 + (0.05 if bss_current > 2.5 else 0)
                logger.info(
                    f"🌊 MANIA INFLOW | "
                    f"WIV accel, BSS={bss_current:.1f}, TX accel, HH=True | "
                    f"→ WIDEN trailing"
                )
                return ("INFLOW", confidence)
            else:
                # DISTRIBUTION: Activity up but structure weak
                confidence = 0.7 + (0.1 if bss_weakening else 0)
                logger.warning(
                    f"⚠️ MANIA DISTRIBUTION | "
                    f"WIV accel BUT HH broken, BSS={bss_current:.1f} | "
                    f"→ TIGHTEN trailing (exit liquidity risk)"
                )
                return ("DISTRIBUTION", confidence)
        
        return ("NONE", 0.0)
        
    def get_mania_end_signals(self) -> tuple[bool, float]:
        """
        Detect if mania is ending.
        
        End signals:
        - WIV decaying
        - BSS dropping
        - tx/sec decaying
        
        Returns:
            (is_ending, confidence)
        """
        if len(self.snapshots) < 3:
            return (False, 0.0)
            
        recent = self.snapshots[-3:]
        older = self.snapshots[:3] if len(self.snapshots) >= 6 else recent
        
        wiv_recent = sum(s.wallet_influx_velocity for s in recent) / len(recent)
        wiv_older = sum(s.wallet_influx_velocity for s in older) / len(older)
        wiv_decaying = wiv_recent < wiv_older * 0.8
        
        bss_current = recent[-1].buy_sell_skew
        bss_low = bss_current < 1.2  # Sells catching up
        
        txps_recent = sum(s.tx_per_second for s in recent) / len(recent)
        txps_older = sum(s.tx_per_second for s in older) / len(older)
        tx_decaying = txps_recent < txps_older * 0.7
        
        signals = [wiv_decaying, bss_low, tx_decaying]
        signal_count = sum(signals)
        
        if signal_count >= 2:
            confidence = 0.6 + (signal_count - 2) * 0.2
            logger.warning(
                f"📉 MANIA ENDING | "
                f"Signals={signal_count}/3 | "
                f"WIV_decay={wiv_decaying} BSS={bss_current:.1f} TX_decay={tx_decaying}"
            )
            return (True, confidence)
            
        return (False, 0.0)
        
    def get_trailing_multiplier(self) -> float:
        """
        Get trailing adjustment based on narrative PHASE.
        
        v12.3 FIX: Different behavior for INFLOW vs DISTRIBUTION
        
        INFLOW: widen (1.5x) - favorable mania
        DISTRIBUTION: tighten (0.7x) - exit liquidity trap
        NONE: normal (1.0x)
        
        Returns:
            Multiplier for trailing (0.7 to 1.5)
        """
        phase, confidence = self.detect_mania_phase()
        is_ending, end_conf = self.get_mania_end_signals()
        
        if phase == "INFLOW" and confidence > 0.7:
            # Favorable mania - widen trailing
            return 1.5
        elif phase == "DISTRIBUTION" and confidence > 0.6:
            # Dangerous mania - TIGHTEN (not widen!)
            return 0.7
        elif is_ending and end_conf > 0.6:
            # Mania ending - tighten
            return 0.7
        else:
            # Normal - no adjustment
            return 1.0
            
    def get_status(self) -> dict:
        """Get current narrative state"""
        if not self.snapshots:
            return {"status": "NO_DATA"}
            
        phase, mania_conf = self.detect_mania_phase()
        is_ending, end_conf = self.get_mania_end_signals()
        current = self.snapshots[-1]
        
        return {
            "mania_phase": phase,
            "mania_active": phase != "NONE",
            "mania_confidence": mania_conf,
            "mania_ending": is_ending,
            "end_confidence": end_conf,
            "wiv": current.wallet_influx_velocity,
            "bss": current.buy_sell_skew,
            "tx_per_sec": current.tx_per_second,
            "trailing_multiplier": self.get_trailing_multiplier()
        }
        
    def reset(self):
        """Reset snapshots"""
        self.snapshots = []
