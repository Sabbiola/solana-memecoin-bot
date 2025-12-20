"""
Jito Bundle Client

Provides MEV protection through Jito bundle submission.
Transactions are sent privately to Jito block engines.
"""

import asyncio
import aiohttp
import logging
import time
import base64
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

from solana.rpc.async_api import AsyncClient
from solders.keypair import Keypair
from solders.signature import Signature
from solders.transaction import Transaction, VersionedTransaction

logger = logging.getLogger(__name__)


class JitoRegion(Enum):
    """Jito block engine regions"""
    MAINNET = "mainnet"
    AMSTERDAM = "amsterdam"
    FRANKFURT = "frankfurt"
    NY = "ny"
    TOKYO = "tokyo"


# Jito Block Engine endpoints
JITO_ENDPOINTS = {
    JitoRegion.MAINNET: "https://mainnet.block-engine.jito.wtf",
    JitoRegion.AMSTERDAM: "https://amsterdam.mainnet.block-engine.jito.wtf",
    JitoRegion.FRANKFURT: "https://frankfurt.mainnet.block-engine.jito.wtf",
    JitoRegion.NY: "https://ny.mainnet.block-engine.jito.wtf",
    JitoRegion.TOKYO: "https://tokyo.mainnet.block-engine.jito.wtf",
}

# Jito tip accounts (official)
JITO_TIP_ACCOUNTS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4bVo2iPnF4eTcMY8dE3fkDN",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvJ8t",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
]


@dataclass
class BundleResult:
    """Result of bundle submission"""
    bundle_id: str
    status: str
    signatures: List[str]
    landed_slot: Optional[int] = None
    error: Optional[str] = None
    tip_lamports: int = 0
    
    @property
    def is_success(self) -> bool:
        return self.status == "landed"


class JitoClient:
    """
    Jito Bundle Client for MEV-protected transactions.
    
    Features:
    - Private transaction submission
    - Bundle creation and management
    - Automatic tip calculation
    - Multi-region support
    - Bundle status tracking
    
    Usage:
        jito = JitoClient(session, keypair)
        
        # Send single transaction with MEV protection
        result = await jito.send_transaction(tx, tip_lamports=10000)
        
        # Send bundle of transactions
        result = await jito.send_bundle([tx1, tx2], tip_lamports=50000)
    
    Note:
        Jito requires a tip to be included in the bundle.
        Minimum tip is typically 1000 lamports (0.000001 SOL).
    """
    
    DEFAULT_TIP = 10000  # 0.00001 SOL
    MIN_TIP = 1000  # 0.000001 SOL
    
    def __init__(
        self,
        session: aiohttp.ClientSession,
        payer: Keypair,
        region: JitoRegion = JitoRegion.FRANKFURT,
        rpc_client: Optional[AsyncClient] = None
    ):
        """
        Initialize Jito client.
        
        Args:
            session: aiohttp session for requests
            payer: Keypair for signing transactions
            region: Jito block engine region
            rpc_client: Solana RPC client (for blockhash)
        """
        self.session = session
        self.payer = payer
        self.region = region
        self.endpoint = JITO_ENDPOINTS[region]
        self.rpc_client = rpc_client
        
        # Select random tip account
        import random
        self.tip_account = random.choice(JITO_TIP_ACCOUNTS)
        
        logger.info(f"Jito client initialized (region: {region.value})")
    
    async def send_transaction(
        self,
        transaction: Transaction,
        tip_lamports: int = DEFAULT_TIP,
        max_retries: int = 3
    ) -> BundleResult:
        """
        Send single transaction with MEV protection.
        
        Args:
            transaction: Signed transaction to send
            tip_lamports: Tip amount in lamports
            max_retries: Maximum retry attempts
        
        Returns:
            BundleResult with submission status
        """
        return await self.send_bundle([transaction], tip_lamports, max_retries)
    
    async def send_bundle(
        self,
        transactions: List[Transaction],
        tip_lamports: int = DEFAULT_TIP,
        max_retries: int = 3
    ) -> BundleResult:
        """
        Send bundle of transactions with MEV protection.
        
        Args:
            transactions: List of signed transactions
            tip_lamports: Total tip amount in lamports
            max_retries: Maximum retry attempts
        
        Returns:
            BundleResult with submission status
        """
        if tip_lamports < self.MIN_TIP:
            tip_lamports = self.MIN_TIP
            logger.warning(f"Tip increased to minimum: {self.MIN_TIP} lamports")
        
        # Serialize transactions
        serialized_txs = []
        signatures = []
        
        for tx in transactions:
            try:
                # Get signature
                if hasattr(tx, 'signatures') and tx.signatures:
                    sig = str(tx.signatures[0])
                    signatures.append(sig)
                
                # Serialize
                if isinstance(tx, VersionedTransaction):
                    serialized = bytes(tx)
                else:
                    serialized = tx.serialize()
                
                encoded = base64.b64encode(serialized).decode('utf-8')
                serialized_txs.append(encoded)
            
            except Exception as e:
                logger.error(f"Failed to serialize transaction: {e}")
                return BundleResult(
                    bundle_id="",
                    status="failed",
                    signatures=[],
                    error=f"Serialization error: {e}"
                )
        
        # Submit bundle
        for attempt in range(max_retries):
            try:
                result = await self._submit_bundle(serialized_txs, tip_lamports)
                
                if result.get("bundle_id"):
                    bundle_id = result["bundle_id"]
                    
                    # Wait for confirmation
                    status = await self._wait_for_bundle(bundle_id)
                    
                    return BundleResult(
                        bundle_id=bundle_id,
                        status=status.get("status", "unknown"),
                        signatures=signatures,
                        landed_slot=status.get("landed_slot"),
                        tip_lamports=tip_lamports
                    )
                
                else:
                    error = result.get("error", "Unknown error")
                    
                    if attempt < max_retries - 1:
                        logger.warning(f"Bundle submission failed, retrying: {error}")
                        await asyncio.sleep(1)
                        continue
                    
                    return BundleResult(
                        bundle_id="",
                        status="failed",
                        signatures=signatures,
                        error=error,
                        tip_lamports=tip_lamports
                    )
            
            except Exception as e:
                logger.error(f"Bundle submission error: {e}")
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                
                return BundleResult(
                    bundle_id="",
                    status="failed",
                    signatures=signatures,
                    error=str(e),
                    tip_lamports=tip_lamports
                )
        
        return BundleResult(
            bundle_id="",
            status="failed",
            signatures=signatures,
            error="Max retries exceeded",
            tip_lamports=tip_lamports
        )
    
    async def _submit_bundle(
        self,
        serialized_txs: List[str],
        tip_lamports: int
    ) -> Dict[str, Any]:
        """Submit bundle to Jito block engine"""
        url = f"{self.endpoint}/api/v1/bundles"
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [
                serialized_txs,
                {
                    "encoding": "base64",
                    "tip": tip_lamports
                }
            ]
        }
        
        try:
            async with self.session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                data = await resp.json()
                
                if "result" in data:
                    return {"bundle_id": data["result"]}
                
                if "error" in data:
                    return {"error": data["error"].get("message", str(data["error"]))}
                
                return {"error": "Unknown response format"}
        
        except Exception as e:
            return {"error": str(e)}
    
    async def _wait_for_bundle(
        self,
        bundle_id: str,
        timeout: float = 30.0
    ) -> Dict[str, Any]:
        """Wait for bundle confirmation"""
        url = f"{self.endpoint}/api/v1/bundles"
        
        start = time.time()
        poll_interval = 0.5
        
        while time.time() - start < timeout:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBundleStatuses",
                    "params": [[bundle_id]]
                }
                
                async with self.session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()
                    
                    if "result" in data and data["result"]:
                        status = data["result"]["value"][0] if data["result"].get("value") else None
                        
                        if status:
                            confirmation_status = status.get("confirmation_status", "")
                            
                            if confirmation_status in ["confirmed", "finalized"]:
                                return {
                                    "status": "landed",
                                    "landed_slot": status.get("slot")
                                }
                            
                            if confirmation_status == "failed":
                                return {
                                    "status": "failed",
                                    "error": status.get("err", "Unknown error")
                                }
            
            except Exception as e:
                logger.debug(f"Bundle status check error: {e}")
            
            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.2, 2.0)
        
        return {"status": "timeout", "error": "Bundle confirmation timeout"}
    
    async def get_tip_accounts(self) -> List[str]:
        """Get list of Jito tip accounts"""
        return JITO_TIP_ACCOUNTS.copy()
    
    async def estimate_tip(self, priority: str = "medium") -> int:
        """
        Estimate appropriate tip based on priority.
        
        Args:
            priority: low, medium, high, or urgent
        
        Returns:
            Recommended tip in lamports
        """
        tips = {
            "low": 1000,       # 0.000001 SOL
            "medium": 10000,   # 0.00001 SOL
            "high": 50000,     # 0.00005 SOL
            "urgent": 100000,  # 0.0001 SOL
        }
        return tips.get(priority, tips["medium"])
    
    def get_status(self) -> Dict[str, Any]:
        """Get client status"""
        return {
            "region": self.region.value,
            "endpoint": self.endpoint,
            "tip_account": self.tip_account[:20] + "...",
            "default_tip": self.DEFAULT_TIP
        }


class MEVProtector:
    """
    High-level MEV protection wrapper.
    
    Automatically routes transactions through Jito when beneficial.
    Falls back to regular RPC if Jito fails.
    
    Usage:
        protector = MEVProtector(jito_client, rpc_client)
        
        # Auto-route: uses Jito for large trades, RPC for small
        result = await protector.send_protected(tx, value_sol=0.5)
    """
    
    MEV_THRESHOLD_SOL = 0.1  # Use Jito for trades > 0.1 SOL
    
    def __init__(
        self,
        jito_client: JitoClient,
        rpc_client: AsyncClient
    ):
        self.jito = jito_client
        self.rpc = rpc_client
    
    async def send_protected(
        self,
        transaction: Transaction,
        value_sol: float = 0.0,
        force_jito: bool = False,
        tip_lamports: Optional[int] = None
    ) -> Tuple[bool, str]:
        """
        Send transaction with automatic MEV protection routing.
        
        Args:
            transaction: Signed transaction
            value_sol: Transaction value (for routing decision)
            force_jito: Force Jito even for small transactions
            tip_lamports: Custom tip amount
        
        Returns:
            Tuple of (success, signature_or_error)
        """
        use_jito = force_jito or value_sol >= self.MEV_THRESHOLD_SOL
        
        if use_jito:
            logger.info(f"Using Jito for MEV protection (value: {value_sol} SOL)")
            
            tip = tip_lamports or self.jito.DEFAULT_TIP
            result = await self.jito.send_transaction(transaction, tip_lamports=tip)
            
            if result.is_success:
                sig = result.signatures[0] if result.signatures else ""
                return True, sig
            
            logger.warning(f"Jito failed: {result.error}, falling back to RPC")
        
        # Fallback to regular RPC
        try:
            response = await self.rpc.send_transaction(transaction)
            
            if hasattr(response, 'value'):
                return True, str(response.value)
            
            return False, "Unknown RPC response"
        
        except Exception as e:
            return False, str(e)
