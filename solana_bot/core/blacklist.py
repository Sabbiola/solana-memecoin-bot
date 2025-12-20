"""
Blacklist Manager Module

Prevents re-entry into tokens that were recently traded to avoid churning fees.
Manages temporary timeouts and permanent blocks.
"""

import time
import logging
from typing import Dict, Set

logger = logging.getLogger(__name__)

class BlacklistManager:
    def __init__(self):
        # Maps mint -> expiration_timestamp
        self.timeouts: Dict[str, float] = {}
        # Permanent blacklist
        self.permanent_blocks: Set[str] = set()
        
    def add_timeout(self, mint: str, duration_minutes: float = 60.0):
        """Add a temporary timeout for a token."""
        expiration = time.time() + (duration_minutes * 60)
        self.timeouts[mint] = expiration
        logger.info(f"🚫 Added timeout for {mint[:8]}... ({duration_minutes}m)")
        
    def add_permanent_block(self, mint: str):
        """Permanently block a token."""
        self.permanent_blocks.add(mint)
        logger.info(f"🛑 Permanently blocked {mint[:8]}...")
        
    def is_blocked(self, mint: str) -> bool:
        """Check if a token is currently blocked."""
        # Check permanent blocks
        if mint in self.permanent_blocks:
            return True
            
        # Check timeouts
        if mint in self.timeouts:
            if time.time() < self.timeouts[mint]:
                return True
            else:
                # Expired
                del self.timeouts[mint]
                return False
                
        return False
    
    def cleanup(self):
        """Remove expired timeouts."""
        now = time.time()
        expired = [k for k, v in self.timeouts.items() if v < now]
        for k in expired:
            del self.timeouts[k]
