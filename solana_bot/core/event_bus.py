"""
Event-Driven Kill Switches

Critical events bypass all models and trigger immediate exit.

Philosophy:
- Models are continuous (EAS, VQR, etc.)
- Events are irreversible (LP pull, dev dump)
- When certainty is HIGH, exit IMMEDIATELY

Event Types:
- CRITICAL: Single event = instant exit
- MAJOR: 2 events = instant exit

This prevents "waiting for trailing" when the outcome is already decided.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Set
import time

logger = logging.getLogger(__name__)


class EventSeverity(Enum):
    """Event severity levels"""
    CRITICAL = "CRITICAL"  # Single event = exit
    MAJOR = "MAJOR"        # 2 events = exit
    MINOR = "MINOR"        # Informational


class EventType(Enum):
    """Critical event types"""
    # LP Events
    LP_DROP_SEVERE = "LP_DROP_SEVERE"              # >15% drop
    LP_VARIANCE_ANOMALY = "LP_VARIANCE_ANOMALY"    # Unstable
    
    # Dev Events
    DEV_SELL_CONFIRMED = "DEV_SELL_CONFIRMED"      # Dev sold
    DEV_TO_CEX = "DEV_TO_CEX"                      # Dev → exchange
    DEV_TO_BRIDGE = "DEV_TO_BRIDGE"                # Dev → bridge
    
    # Trading Anomalies
    SWAP_ERRORS_SPIKE = "SWAP_ERRORS_SPIKE"        # Multiple fails
    ROUTE_UNSTABLE = "ROUTE_UNSTABLE"              # Jupiter routing bad
    PRICE_IMPACT_EXTREME = "PRICE_IMPACT_EXTREME"  # >12% impact
    
    # Execution Danger
    SLIPPAGE_SPIKE = "SLIPPAGE_SPIKE"              # Estimated >8%
    SANDWICH_LIKELY = "SANDWICH_LIKELY"            # MEV risk high
    TX_FAIL_RATE_HIGH = "TX_FAIL_RATE_HIGH"        # Network congestion


@dataclass
class CriticalEvent:
    """Single critical event"""
    event_type: EventType
    severity: EventSeverity
    timestamp: float
    details: dict
    

class EventBus:
    """
    Central event bus for critical kill-switches.
    
    Prevents over-reliance on continuous models when
    discrete events provide certainty.
    
    Usage:
        bus.publish(EventType.LP_DROP_SEVERE, {...})
        should_exit, reason = bus.should_emergency_exit()
    """
    
    def __init__(self, lookback_seconds: float = 30.0):
        self.lookback_seconds = lookback_seconds
        self.events: List[CriticalEvent] = []
        
        # Event severity mapping
        self.severity_map = {
            # CRITICAL (1 = exit)
            EventType.LP_DROP_SEVERE: EventSeverity.CRITICAL,
            EventType.DEV_SELL_CONFIRMED: EventSeverity.CRITICAL,
            EventType.DEV_TO_CEX: EventSeverity.CRITICAL,
            
            # MAJOR (2 different root causes = exit)
            EventType.LP_VARIANCE_ANOMALY: EventSeverity.MAJOR,
            EventType.DEV_TO_BRIDGE: EventSeverity.MAJOR,
            EventType.ROUTE_UNSTABLE: EventSeverity.MAJOR,
            EventType.PRICE_IMPACT_EXTREME: EventSeverity.MAJOR,
            EventType.SLIPPAGE_SPIKE: EventSeverity.MAJOR,
            EventType.SANDWICH_LIKELY: EventSeverity.MAJOR,
            
            # MINOR (informational)
            EventType.SWAP_ERRORS_SPIKE: EventSeverity.MINOR,
            EventType.TX_FAIL_RATE_HIGH: EventSeverity.MINOR,
        }
        
        # v12.3 ROOT CAUSE GROUPING
        # Multiple events from same root cause = 1 logical signal
        self.root_cause_map = {
            # Execution degradation (same underlying cause)
            EventType.ROUTE_UNSTABLE: "EXECUTION_DEGRADATION",
            EventType.PRICE_IMPACT_EXTREME: "EXECUTION_DEGRADATION",
            EventType.SLIPPAGE_SPIKE: "EXECUTION_DEGRADATION",
            EventType.SANDWICH_LIKELY: "EXECUTION_DEGRADATION",
            EventType.SWAP_ERRORS_SPIKE: "EXECUTION_DEGRADATION",
            EventType.TX_FAIL_RATE_HIGH: "EXECUTION_DEGRADATION",
            
            # Liquidity removal
            EventType.LP_DROP_SEVERE: "LIQUIDITY_REMOVAL",
            EventType.LP_VARIANCE_ANOMALY: "LIQUIDITY_REMOVAL",
            
            # Dev risk
            EventType.DEV_SELL_CONFIRMED: "DEV_RISK",
            EventType.DEV_TO_CEX: "DEV_RISK",
            EventType.DEV_TO_BRIDGE: "DEV_RISK",
        }
        
    def publish(self, event_type: EventType, details: dict = None):
        """
        Publish a new event to the bus.
        
        Args:
            event_type: Type of event
            details: Additional context
        """
        severity = self.severity_map.get(event_type, EventSeverity.MINOR)
        
        event = CriticalEvent(
            event_type=event_type,
            severity=severity,
            timestamp=time.time(),
            details=details or {}
        )
        
        self.events.append(event)
        
        # Clean old events
        self._cleanup_old_events()
        
        # Log by severity
        if severity == EventSeverity.CRITICAL:
            logger.error(f"🔴 CRITICAL EVENT: {event_type.value} | {details}")
        elif severity == EventSeverity.MAJOR:
            logger.warning(f"⚠️ MAJOR EVENT: {event_type.value} | {details}")
        else:
            logger.info(f"ℹ️ MINOR EVENT: {event_type.value}")
            
    def should_emergency_exit(self) -> tuple[bool, str]:
        """
        Determine if critical events warrant immediate exit.
        
        v12.3 FIX: Root-cause grouping prevents double-counting.
        
        Rules:
        - 1 CRITICAL event = EXIT
        - 2+ distinct ROOT CAUSES (MAJOR) = EXIT
        - Otherwise = HOLD
        
        Returns:
            (should_exit, reason)
        """
        recent = self._get_recent_events()
        
        # Group by severity
        critical = [e for e in recent if e.severity == EventSeverity.CRITICAL]
        major = [e for e in recent if e.severity == EventSeverity.MAJOR]
        
        # Rule 1: Any critical = instant exit
        if critical:
            event = critical[0]
            reason = f"CRITICAL_{event.event_type.value}"
            logger.error(
                f"🚨 EMERGENCY EXIT: {reason} | "
                f"Details={event.details}"
            )
            return (True, reason)
            
        # Rule 2: 2+ DISTINCT root causes (not just 2 events)
        if major:
            # Extract unique root causes
            root_causes = set()
            for e in major:
                rc = self.root_cause_map.get(e.event_type, "UNKNOWN")
                root_causes.add(rc)
                
            if len(root_causes) >= 2:
                reason = f"MAJOR_ROOT_CAUSES_{'_'.join(sorted(root_causes))}"
                logger.error(
                    f"🚨 EMERGENCY EXIT: {reason} | "
                    f"{len(major)} events from {len(root_causes)} distinct causes"
                )
                return (True, reason)
            else:
                # Only 1 root cause, even with multiple events
                logger.warning(
                    f"⚠️ {len(major)} MAJOR event(s) but only 1 root cause "
                    f"({list(root_causes)[0]}), need 2 distinct causes for exit"
                )
            
        return (False, "")
        
    def _get_recent_events(self) -> List[CriticalEvent]:
        """Get events within lookback window"""
        cutoff = time.time() - self.lookback_seconds
        return [e for e in self.events if e.timestamp > cutoff]
        
    def _cleanup_old_events(self):
        """Remove events older than lookback"""
        cutoff = time.time() - self.lookback_seconds
        self.events = [e for e in self.events if e.timestamp > cutoff]
        
    def get_active_events(self) -> dict:
        """Get summary of active events"""
        recent = self._get_recent_events()
        
        return {
            "total": len(recent),
            "critical": len([e for e in recent if e.severity == EventSeverity.CRITICAL]),
            "major": len([e for e in recent if e.severity == EventSeverity.MAJOR]),
            "minor": len([e for e in recent if e.severity == EventSeverity.MINOR]),
            "types": [e.event_type.value for e in recent]
        }
        
    def reset(self):
        """Clear all events"""
        self.events = []
