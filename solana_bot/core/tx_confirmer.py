"""
Transaction Confirmation Logic

Provides robust transaction confirmation with retry, timeout, and status tracking.
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment, Confirmed, Finalized
from solders.signature import Signature

logger = logging.getLogger(__name__)


class TxStatus(Enum):
    """Transaction status"""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FINALIZED = "finalized"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass
class TxResult:
    """Transaction confirmation result"""
    signature: str
    status: TxStatus
    slot: Optional[int] = None
    confirmations: int = 0
    error: Optional[str] = None
    elapsed_seconds: float = 0.0
    
    @property
    def is_success(self) -> bool:
        return self.status in (TxStatus.CONFIRMED, TxStatus.FINALIZED)
    
    @property
    def is_final(self) -> bool:
        return self.status in (TxStatus.FINALIZED, TxStatus.FAILED, TxStatus.EXPIRED)


class TransactionConfirmer:
    """
    Robust transaction confirmation with retry and timeout handling.
    
    Features:
    - Configurable timeout
    - Exponential backoff polling
    - Multiple confirmation levels
    - Blockhash expiry detection
    - Detailed status tracking
    
    Usage:
        confirmer = TransactionConfirmer(client)
        result = await confirmer.confirm(signature, timeout=60)
        
        if result.is_success:
            print(f"Confirmed in {result.elapsed_seconds:.1f}s")
        else:
            print(f"Failed: {result.error}")
    """
    
    # Default timeouts
    DEFAULT_TIMEOUT = 60.0  # seconds
    MIN_POLL_INTERVAL = 0.5
    MAX_POLL_INTERVAL = 2.0
    
    def __init__(self, client: AsyncClient):
        """
        Initialize confirmer.
        
        Args:
            client: Solana RPC client
        """
        self.client = client
        self._pending: Dict[str, TxResult] = {}
    
    async def confirm(
        self,
        signature: str,
        commitment: Commitment = Confirmed,
        timeout: float = DEFAULT_TIMEOUT
    ) -> TxResult:
        """
        Confirm transaction with the given commitment level.
        
        Args:
            signature: Transaction signature
            commitment: Confirmation level (Confirmed or Finalized)
            timeout: Maximum time to wait (seconds)
        
        Returns:
            TxResult with status and details
        """
        start_time = time.time()
        poll_interval = self.MIN_POLL_INTERVAL
        
        result = TxResult(
            signature=signature,
            status=TxStatus.PENDING
        )
        
        self._pending[signature] = result
        
        try:
            while True:
                elapsed = time.time() - start_time
                
                # Check timeout
                if elapsed >= timeout:
                    result.status = TxStatus.EXPIRED
                    result.error = f"Timeout after {timeout}s"
                    result.elapsed_seconds = elapsed
                    logger.warning(f"Transaction {signature[:20]}... expired after {elapsed:.1f}s")
                    break
                
                # Check status
                try:
                    status = await self._check_status(signature)
                    
                    if status:
                        result.slot = status.get("slot")
                        result.confirmations = status.get("confirmations", 0)
                        
                        if status.get("err"):
                            result.status = TxStatus.FAILED
                            result.error = str(status["err"])
                            result.elapsed_seconds = time.time() - start_time
                            logger.error(f"Transaction {signature[:20]}... failed: {result.error}")
                            break
                        
                        # Check confirmation level
                        confirmation_status = status.get("confirmationStatus", "")
                        
                        if confirmation_status == "finalized":
                            result.status = TxStatus.FINALIZED
                            result.elapsed_seconds = time.time() - start_time
                            logger.info(f"Transaction {signature[:20]}... finalized in {result.elapsed_seconds:.1f}s")
                            break
                        
                        elif confirmation_status == "confirmed":
                            if commitment == Confirmed:
                                result.status = TxStatus.CONFIRMED
                                result.elapsed_seconds = time.time() - start_time
                                logger.info(f"Transaction {signature[:20]}... confirmed in {result.elapsed_seconds:.1f}s")
                                break
                            # If waiting for finalized, continue polling
                
                except Exception as e:
                    logger.debug(f"Status check error: {e}")
                
                # Wait before next poll (with exponential backoff)
                await asyncio.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.5, self.MAX_POLL_INTERVAL)
        
        finally:
            self._pending.pop(signature, None)
        
        return result
    
    async def _check_status(self, signature: str) -> Optional[Dict[str, Any]]:
        """Check transaction status from RPC"""
        try:
            sig = Signature.from_string(signature)
            response = await self.client.get_signature_statuses([sig])
            
            if response and response.value:
                status = response.value[0]
                if status:
                    return {
                        "slot": status.slot,
                        "confirmations": status.confirmations,
                        "err": status.err,
                        "confirmationStatus": status.confirmation_status
                    }
            
            return None
        
        except Exception as e:
            logger.debug(f"Status check error: {e}")
            return None
    
    async def confirm_multiple(
        self,
        signatures: List[str],
        commitment: Commitment = Confirmed,
        timeout: float = DEFAULT_TIMEOUT
    ) -> Dict[str, TxResult]:
        """
        Confirm multiple transactions in parallel.
        
        Args:
            signatures: List of transaction signatures
            commitment: Confirmation level
            timeout: Maximum time per transaction
        
        Returns:
            Dict of signature -> TxResult
        """
        tasks = [
            self.confirm(sig, commitment, timeout)
            for sig in signatures
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return {
            sig: (result if isinstance(result, TxResult) else TxResult(
                signature=sig,
                status=TxStatus.FAILED,
                error=str(result)
            ))
            for sig, result in zip(signatures, results)
        }
    
    async def wait_for_finalization(
        self,
        signature: str,
        timeout: float = 120.0
    ) -> TxResult:
        """
        Wait for transaction to be finalized (max confirmation).
        
        Args:
            signature: Transaction signature
            timeout: Maximum time to wait
        
        Returns:
            TxResult with finalization status
        """
        return await self.confirm(signature, Finalized, timeout)
    
    def get_pending(self) -> List[TxResult]:
        """Get all pending transactions"""
        return list(self._pending.values())


class BlockhashTracker:
    """
    Track blockhash validity for transaction retries.
    
    Blockhashes expire after ~60 seconds on Solana.
    This helps track when to refresh blockhash for retries.
    """
    
    BLOCKHASH_TTL = 60.0  # seconds (approximate)
    
    def __init__(self, client: AsyncClient):
        self.client = client
        self._current_blockhash: Optional[str] = None
        self._blockhash_time: float = 0.0
    
    async def get_fresh_blockhash(self) -> str:
        """Get a fresh blockhash"""
        response = await self.client.get_latest_blockhash()
        
        self._current_blockhash = str(response.value.blockhash)
        self._blockhash_time = time.time()
        
        return self._current_blockhash
    
    async def get_blockhash(self, max_age: float = 30.0) -> str:
        """
        Get blockhash, refreshing if too old.
        
        Args:
            max_age: Maximum age in seconds before refreshing
        
        Returns:
            Valid blockhash
        """
        age = time.time() - self._blockhash_time
        
        if not self._current_blockhash or age > max_age:
            return await self.get_fresh_blockhash()
        
        return self._current_blockhash
    
    def is_likely_valid(self) -> bool:
        """Check if current blockhash is likely still valid"""
        age = time.time() - self._blockhash_time
        return age < self.BLOCKHASH_TTL * 0.8  # 80% of TTL
    
    @property
    def current_age(self) -> float:
        """Get age of current blockhash in seconds"""
        return time.time() - self._blockhash_time
