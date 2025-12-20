"""
Helius Sender Client

Ultra-low latency transaction submission via Helius Sender.
Dual routing to both Staked Connections and Jito for optimal speed.
"""

import aiohttp
import logging
import random
import base64
from typing import Optional, List

logger = logging.getLogger(__name__)


class HeliusSender:
    """
    Helius Sender for ultra-fast transaction submission.
    
    Features:
    - Dual routing to validators and Jito
    - Global endpoints for minimal latency
    - No credits consumed
    - Automatic Jito tip support
    """
    
    # Helius Sender endpoint (API key appended at runtime)
    SENDER_BASE_URL = "https://sender.helius-rpc.com/fast"
    
    # Helius tip accounts - REQUIRED for Helius Sender endpoint
    # These are the official Helius tip wallets (NOT Jito accounts)
    HELIUS_TIP_ACCOUNTS = [
        "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or",
        "D1Mc6j9xQWgR1o1Z7yU5nVVXFQiAYx7FG9AW1aVfwrUM",
        "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ",
        "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF",
        "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE",
        "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
        "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn",
        "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta",
        "2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD",
        "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
        "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey"
    ]
    
    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        default_tip_lamports: int = 250000  # 0.00025 SOL (safe minimum for Helius)
    ):
        """
        Initialize Helius Sender.
        
        Args:
            session: aiohttp session
            api_key: Helius API key (required)
            default_tip_lamports: Default Jito tip amount
        """
        self.session = session
        self.api_key = api_key
        self.default_tip_lamports = default_tip_lamports
        self.sender_url = f"{self.SENDER_BASE_URL}?api-key={api_key}"
    
    def get_random_tip_account(self) -> str:
        """Get a random Jito tip account for load balancing."""
        return random.choice(self.HELIUS_TIP_ACCOUNTS)
    
    async def send_transaction(
        self,
        transaction_bytes: bytes,
        skip_preflight: bool = True,
        max_retries: int = 3
    ) -> Optional[str]:
        """
        Send transaction via Helius Sender.
        
        Args:
            transaction_bytes: Signed transaction bytes
            skip_preflight: Skip preflight checks for speed
            max_retries: Max retry attempts
            
        Returns:
            Transaction signature or None
        """
        tx_base64 = base64.b64encode(transaction_bytes).decode('utf-8')
        
        for attempt in range(max_retries):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        tx_base64,
                        {
                            "encoding": "base64",
                            "skipPreflight": skip_preflight,
                            "preflightCommitment": "confirmed",
                            "maxRetries": 0  # Let Helius handle retries
                        }
                    ]
                }
                
                async with self.session.post(
                    self.sender_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    result = await resp.json()
                    
                    if "error" in result:
                        error = result["error"]
                        logger.error(f"Helius Sender error: {error}")
                        
                        # Check if retryable
                        error_msg = str(error.get("message", ""))
                        if "blockhash" in error_msg.lower():
                            logger.info("Blockhash expired, needs refresh")
                            return None  # Caller needs to rebuild tx
                        
                        continue
                    
                    # Log full response for debugging
                    logger.debug(f"Helius Sender response: {result}")
                    
                    signature = result.get("result")
                    if signature:
                        logger.info(f"✅ TX sent via Helius Sender: {signature[:16]}...")
                        return signature
                    else:
                        # Log why we didn't get a signature
                        logger.warning(f"⚠️ Helius Sender: no 'result' in response: {result}")
                    
            except Exception as e:
                logger.error(f"Helius Sender attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    continue
        
        return None
    
    async def send_smart_transaction(
        self,
        transaction_bytes: bytes,
        skip_preflight: bool = True
    ) -> Optional[str]:
        """
        Send transaction via Helius sendSmartTransaction.
        
        This method automatically:
        - Refreshes blockhash if needed
        - Optimizes compute units
        - Sets optimal priority fee
        - Retries on failure
        
        Args:
            transaction_bytes: Signed transaction bytes
            skip_preflight: Skip preflight checks
            
        Returns:
            Transaction signature or None
        """
        tx_base64 = base64.b64encode(transaction_bytes).decode('utf-8')
        
        try:
            # Use the standard Helius RPC endpoint (not Sender) for smart TX
            smart_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendSmartTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": skip_preflight,
                        "maxRetries": 5,  # Helius auto-retries
                    }
                ]
            }
            
            async with self.session.post(
                smart_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)  # Longer timeout for retries
            ) as resp:
                result = await resp.json()
                
                if "error" in result:
                    error = result["error"]
                    logger.error(f"sendSmartTransaction error: {error}")
                    return None
                
                signature = result.get("result")
                if signature:
                    logger.info(f"✅ Smart TX sent: {signature[:16]}...")
                    return signature
                else:
                    logger.warning(f"⚠️ Smart TX: no 'result': {result}")
                    
        except Exception as e:
            logger.error(f"sendSmartTransaction failed: {e}")
        
        return None
    
    async def send_with_tip(
        self,
        transaction_bytes: bytes,
        tip_lamports: Optional[int] = None
    ) -> Optional[str]:
        """
        Send transaction with Jito tip for priority.
        
        Note: The tip instruction should be added to the transaction
        BEFORE signing. This method just sends it via the fast endpoint.
        
        Args:
            transaction_bytes: Signed transaction with tip instruction
            tip_lamports: Tip amount (for logging)
            
        Returns:
            Transaction signature or None
        """
        tip = tip_lamports or self.default_tip_lamports
        logger.info(f"Sending with Jito tip: {tip / 1e9:.6f} SOL")
        
        return await self.send_transaction(transaction_bytes)


def create_jito_tip_instruction(
    payer_pubkey,
    tip_lamports: int = 10000
) -> dict:
    """
    Create a Jito tip instruction to add to a transaction.
    
    This should be added to the transaction BEFORE signing.
    
    Args:
        payer_pubkey: Payer's public key (Pubkey object)
        tip_lamports: Tip amount in lamports
        
    Returns:
        Instruction dict for Jito tip
    """
    from solders.pubkey import Pubkey
    from solders.system_program import transfer, TransferParams
    
    # Get random tip account
    tip_account = random.choice(HeliusSender.HELIUS_TIP_ACCOUNTS)
    
    # Create transfer instruction
    tip_instruction = transfer(
        TransferParams(
            from_pubkey=payer_pubkey,
            to_pubkey=Pubkey.from_string(tip_account),
            lamports=tip_lamports
        )
    )
    
    return tip_instruction
