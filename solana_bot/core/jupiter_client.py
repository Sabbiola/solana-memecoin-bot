"""
Jupiter Aggregator Client

Enables swaps across ALL Solana DEXs:
- Raydium, Orca, Meteora, PumpSwap, Lifinity, etc.
- Automatic best route finding
- Optimal pricing
- Ultra-fast execution via Helius Sender
"""

import aiohttp
import logging
from typing import Optional, Dict, Any
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solana.rpc.async_api import AsyncClient
from solana.rpc.types import TxOpts
from solana.rpc.commitment import Confirmed
import base64

# Circuit breaker for rate limiting protection
from ..utils.retry import jupiter_circuit_breaker

logger = logging.getLogger(__name__)


class JupiterClient:
    """
    Client for Jupiter Aggregator API v6.
    
    Features:
    - Get quotes across all DEXs
    - Execute swaps with optimal routing
    - Automatic slippage protection
    - Ultra-fast execution via Helius Sender
    - DNS bypass for Telecom Italia hijacking
    """
    
    # DNS Bypass Configuration
    # Use direct IP to bypass DNS hijacking issues
    USE_DNS_BYPASS = False  # Disabled - using hosts file
    JUPITER_IP = "104.21.93.98"
    JUPITER_HOST = "quote-api.jup.ag"
    BASE_URL = f"https://{JUPITER_IP}/v6" if USE_DNS_BYPASS else "https://quote-api.jup.ag/v6"
    
    # Helius Sender Configuration  
    USE_HELIUS_SENDER = True  # ENABLED: Using /swap-instructions + manual tip injection
    DEFAULT_JITO_TIP_LAMPORTS = 500000  # 0.0005 SOL - Higher tip for faster confirmation
    
    def __init__(
        self,
        session: aiohttp.ClientSession,
        rpc_client: AsyncClient,
        payer: Keypair
    ):
        """
        Initialize Jupiter client.
        
        Args:
            session: aiohttp session for API calls
            rpc_client: Solana RPC client
            payer: Wallet keypair for signing
        """
        self.session = session
        self.rpc = rpc_client
        self.payer = payer
        
        # Headers for DNS bypass (SNI)
        self.headers = {}
        if self.USE_DNS_BYPASS:
            self.headers["Host"] = self.JUPITER_HOST
            logger.info(f"✅ Jupiter DNS bypass enabled (using IP: {self.JUPITER_IP})")
        
        # Initialize Helius Sender (with feeAccount tip support)
        self.helius_sender = None
        if self.USE_HELIUS_SENDER:
            from .helius_sender import HeliusSender
            import os
            
            # Extract API key from RPC URL or environment
            rpc_url = os.getenv("RPC_URL", "")
            api_key = rpc_url.split('api-key=')[-1] if 'api-key=' in rpc_url else ""
            
            if api_key:
                self.helius_sender = HeliusSender(
                    session=session,
                    api_key=api_key,
                    default_tip_lamports=self.DEFAULT_JITO_TIP_LAMPORTS
                )
                logger.info("✅ Helius Sender enabled (Jupiter feeAccount tip)")
            else:
                logger.warning("⚠️ Helius API key not found, using standard RPC")
    
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 50
    ) -> Optional[Dict[str, Any]]:
        """
        Get swap quote from Jupiter.
        
        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount in smallest units (lamports for SOL)
            slippage_bps: Slippage tolerance in basis points (50 = 0.5%)
        
        Returns:
            Quote data or None if failed
        """
        try:
            # CHECK CIRCUIT BREAKER before making API call
            if not jupiter_circuit_breaker.can_execute():
                logger.warning("⚠️ Jupiter circuit breaker OPEN - skipping quote request")
                return None
            
            url = f"{self.BASE_URL}/quote"
            params = {
                "inputMint": str(input_mint),
                "outputMint": str(output_mint),
                "amount": str(amount),
                "slippageBps": slippage_bps
            }
            
            logger.info(f"Getting Jupiter quote: {amount} {input_mint[:8]}... → {output_mint[:8]}...")
            
            timeout = aiohttp.ClientTimeout(total=15)
            async with self.session.get(
                url, 
                params=params, 
                timeout=timeout,
                headers=self.headers,  # DNS bypass header
                ssl=False if self.USE_DNS_BYPASS else None  # Disable SSL verification for IP
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"Quote failed: {resp.status} - {error}")
                    return None
                
                quote = await resp.json()
                
                # Log quote info
                in_amount = int(quote.get("inAmount", 0))
                out_amount = int(quote.get("outAmount", 0))
                price_impact = float(quote.get("priceImpactPct", 0))
                
                logger.info(
                    f"Quote: {in_amount} → {out_amount} "
                    f"(impact: {price_impact:.2f}%)"
                )
                
                # Record success for circuit breaker
                jupiter_circuit_breaker.record_success()
                
                return quote
                
        except aiohttp.ClientResponseError as e:
            # Handle rate limiting specifically
            if e.status == 429:
                jupiter_circuit_breaker.record_failure()
                logger.warning(f"⚠️ Jupiter rate limited (429) - circuit breaker: {jupiter_circuit_breaker.failures}/{jupiter_circuit_breaker.failure_threshold}")
            else:
                jupiter_circuit_breaker.record_failure()
            logger.error(f"Jupiter API error: {e.status} - {e.message}")
            return None
        except Exception as e:
            jupiter_circuit_breaker.record_failure()
            logger.error(f"Error getting quote: {e}")
            return None
    
    async def swap(
        self,
        quote: Dict[str, Any],
        max_retries: int = 2,
        use_helius_sender: bool = True
    ) -> Optional[tuple[str, int]]:
        """
        Execute swap using Jupiter quote.
        
        Args:
            quote: Quote from get_quote()
            max_retries: Max retry attempts
            use_helius_sender: Use Helius Sender for ultra-fast execution
        
        Returns:
            Tuple of (transaction_signature, tokens_received) or None if failed
        """
        import random
        from base64 import b64decode
        from solders.pubkey import Pubkey as SoldersPubkey
        from solders.instruction import Instruction, AccountMeta
        from solders.transaction import Transaction as SoldersTransaction
        from solders.system_program import transfer, TransferParams
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Executing swap (attempt {attempt + 1}/{max_retries})...")
                
                # Use /swap-instructions endpoint for Helius Sender (allows manual tip)
                if use_helius_sender and self.helius_sender:
                    logger.info("Using Jupiter /swap-instructions for Helius Sender...")
                    
                    url = f"{self.BASE_URL}/swap-instructions"
                    payload = {
                        "quoteResponse": quote,
                        "userPublicKey": str(self.payer.pubkey()),
                        "wrapAndUnwrapSol": True,
                        "dynamicComputeUnitLimit": True,
                        "prioritizationFeeLamports": "auto"
                    }
                    
                    async with self.session.post(url, json=payload, timeout=10) as resp:
                        if resp.status != 200:
                            logger.error(f"/swap-instructions failed: {resp.status}")
                            continue
                        swap_data = await resp.json()
                    
                    # Parse instructions from response
                    def parse_ix(ix_data):
                        program_id = SoldersPubkey.from_string(ix_data["programId"])
                        data = b64decode(ix_data["data"])
                        accounts = [
                            AccountMeta(
                                pubkey=SoldersPubkey.from_string(acc["pubkey"]),
                                is_signer=acc.get("isSigner", False),
                                is_writable=acc.get("isWritable", False)
                            ) for acc in ix_data["accounts"]
                        ]
                        return Instruction(program_id=program_id, accounts=accounts, data=data)
                    
                    instructions = []
                    if "setupInstructions" in swap_data and swap_data["setupInstructions"]:
                        for ix in swap_data["setupInstructions"]:
                            instructions.append(parse_ix(ix))
                    if "swapInstruction" in swap_data:
                        instructions.append(parse_ix(swap_data["swapInstruction"]))
                    if "cleanupInstructions" in swap_data and swap_data["cleanupInstructions"]:
                        for ix in swap_data["cleanupInstructions"]:
                            instructions.append(parse_ix(ix))
                    
                    # Add Jito tip as LAST instruction
                    tip_account_str = self.helius_sender.get_random_tip_account()
                    tip_pubkey = SoldersPubkey.from_string(tip_account_str)
                    tip_ix = transfer(TransferParams(
                        from_pubkey=self.payer.pubkey(),
                        to_pubkey=tip_pubkey,
                        lamports=self.DEFAULT_JITO_TIP_LAMPORTS
                    ))
                    instructions.append(tip_ix)
                    
                    logger.info(f"Built transaction with {len(instructions)} instructions (inc. tip)")
                    
                    # Build and sign transaction
                    blockhash_resp = await self.rpc.get_latest_blockhash()
                    recent_blockhash = blockhash_resp.value.blockhash
                    
                    signed_tx = SoldersTransaction.new_signed_with_payer(
                        instructions,
                        self.payer.pubkey(),
                        [self.payer],
                        recent_blockhash
                    )
                    
                    # Send via Helius Sender
                    logger.info("⚡ Sending via Helius Sender...")
                    tx_signature = await self.helius_sender.send_transaction(
                        bytes(signed_tx),
                        skip_preflight=True,
                        max_retries=1
                    )
                    
                    if not tx_signature:
                        logger.warning("Helius Sender failed, falling back to RPC...")
                        continue
                    
                else:
                    # Standard RPC path - use /swap endpoint
                    swap_tx = await self._get_swap_transaction(quote)
                    if not swap_tx:
                        logger.error("Failed to get swap transaction")
                        continue
                    
                    # Deserialize and sign
                    tx_bytes = base64.b64decode(swap_tx)
                    tx = VersionedTransaction.from_bytes(tx_bytes)
                    message_bytes = bytes(tx.message)
                    signature_obj = self.payer.sign_message(message_bytes)
                    signed_tx = VersionedTransaction.populate(tx.message, [signature_obj])
                    
                    # Send via RPC
                    logger.info("Sending via standard RPC...")
                    result = await self.rpc.send_raw_transaction(
                        bytes(signed_tx),
                        opts=TxOpts(skip_preflight=True, preflight_commitment=Confirmed)
                    )
                    tx_signature = str(result.value)
                
                if not tx_signature:
                    logger.error("No transaction signature returned")
                    continue
                
                logger.info(f"✅ Swap executed: {tx_signature}")
                
                # Wait for confirmation
                confirmed = await self._confirm_transaction(tx_signature)
                tokens_received = int(quote.get('outAmount', 0))
                
                if confirmed:
                    logger.info(f"✅ Transaction confirmed on-chain")
                    return (tx_signature, tokens_received)
                else:
                    # TX was sent but confirmation timeout - still return signature
                    # User can verify on Solscan manually
                    logger.warning(f"⚠️ Confirmation timeout but TX was sent - returning signature")
                    logger.warning(f"   Verify on Solscan: https://solscan.io/tx/{tx_signature}")
                    return (tx_signature, tokens_received)
                    
            except Exception as e:
                error_msg = str(e).lower()
                logger.error(f"Swap attempt {attempt + 1} failed: {e}")
                
                # Don't retry on permanent failures (save fees!)
                if any(perm_err in error_msg for perm_err in [
                    "insufficient funds",
                    "invalid",
                    "blocklisted", 
                    "slippage",
                    "account not found"
                ]):
                    logger.error(f"Permanent error detected, skipping retries")
                    return None
                
                # Only retry on transient errors (network, timeout, etc)
                if attempt < max_retries - 1:
                    logger.info("Transient error, retrying...")
                    continue
                else:
                    logger.error("Max retries reached")
                    return None
        
        return None
    
    async def _get_swap_transaction(
        self,
        quote: Dict[str, Any]
    ) -> Optional[str]:
        """Get serialized swap transaction from Jupiter."""
        try:
            url = f"{self.BASE_URL}/swap"
            
            payload = {
                "quoteResponse": quote,
                "userPublicKey": str(self.payer.pubkey()),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": {
                    "priorityLevelWithMaxLamports": {
                        "maxLamports": self.DEFAULT_JITO_TIP_LAMPORTS,
                        "priorityLevel": "veryHigh"
                    }
                },
            }
            
            # Note: Using priorityLevelWithMaxLamports for better TX landing
            # veryHigh priority should compete with Jito bundles
            
            async with self.session.post(
                url, 
                json=payload, 
                timeout=10,
                headers=self.headers,  # DNS bypass header
                ssl=False if self.USE_DNS_BYPASS else None  # Disable SSL verification for IP
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"Swap transaction failed: {error}")
                    return None
                
                data = await resp.json()
                return data.get("swapTransaction")
                
        except Exception as e:
            logger.error(f"Error getting swap transaction: {e}")
            return None
    
    async def _confirm_transaction(
        self, 
        signature: str, 
        max_attempts: int = 60,  # Increased to 60s for network congestion tolerance
        poll_interval: float = 1.0
    ) -> bool:
        """
        Wait for transaction confirmation.
        
        Args:
            signature: Transaction signature
            max_attempts: Maximum polling attempts
            poll_interval: Seconds between polls
            
        Returns:
            True if confirmed, False if timeout/error
        """
        import asyncio
        from solders.signature import Signature as SoldersSignature
        
        sig_obj = SoldersSignature.from_string(signature)
        
        for attempt in range(max_attempts):
            try:
                result = await self.rpc.get_signature_statuses([sig_obj])
                
                if result.value and result.value[0]:
                    status = result.value[0]
                    
                    # Check for errors
                    if status.err:
                        logger.error(f"Transaction failed: {status.err}")
                        return False
                    
                    # Check confirmation status - handle both Enum and string
                    conf_status = str(status.confirmation_status) if status.confirmation_status else None
                    
                    # DEBUG: Log what we receive
                    if attempt < 3 or attempt % 10 == 0:  # Log first 3 attempts and every 10th
                        logger.debug(f"   Attempt {attempt + 1}: status={conf_status}, slot={status.slot if hasattr(status, 'slot') else 'N/A'}")
                    
                    # Fix: Check if 'confirmed' or 'finalized' appears IN the status string
                    if conf_status:
                        conf_lower = conf_status.lower()
                        if "confirmed" in conf_lower or "finalized" in conf_lower:
                            logger.info(f"Transaction confirmed: {signature[:16]}... (status: {conf_status})")
                            return True
                else:
                    # No status yet - TX might not be processed
                    if attempt < 3 or attempt % 10 == 0:
                        logger.debug(f"   Attempt {attempt + 1}: No status yet (TX not processed)")
            
            except Exception as e:
                logger.debug(f"Confirmation check attempt {attempt + 1} error: {e}")
            
            if attempt < max_attempts - 1:
                await asyncio.sleep(poll_interval)
        
        logger.warning(f"Transaction confirmation timeout after {max_attempts}s: {signature[:16]}...")
        return False
    
    # -------------------------------------------------------------------------
    # Convenience methods
    # -------------------------------------------------------------------------
    
    async def buy_token(
        self,
        token_mint: str,
        amount_sol: float,
        slippage_bps: int = 50
    ) -> Optional[tuple[str, int]]:
        """
        Buy token with SOL.
        
        Args:
            token_mint: Token to buy
            amount_sol: Amount of SOL to spend
            slippage_bps: Slippage tolerance (50 = 0.5%)
        
        Returns:
            Tuple of (transaction_signature, tokens_received) or None
        """
        # Convert SOL to lamports
        amount_lamports = int(amount_sol * 1e9)
        
        # WSOL mint
        wsol_mint = "So11111111111111111111111111111111111111112"
        
        # Get quote
        quote = await self.get_quote(
            input_mint=wsol_mint,
            output_mint=token_mint,
            amount=amount_lamports,
            slippage_bps=slippage_bps
        )
        
        if not quote:
            return None
        
        # Execute swap (returns tuple)
        return await self.swap(quote)
    
    async def sell_token(
        self,
        token_mint: str,
        token_amount: int,
        decimals: int = 6,
        slippage_bps: int = 100
    ) -> Optional[str]:
        """
        Sell token for SOL.
        
        Args:
            token_mint: Token to sell
            token_amount: Amount of tokens (in smallest units)
            decimals: Token decimals
            slippage_bps: Slippage tolerance (100 = 1%)
        
        Returns:
            Transaction signature or None
        """
        # WSOL mint
        wsol_mint = "So11111111111111111111111111111111111111112"
        
        # Get quote
        quote = await self.get_quote(
            input_mint=token_mint,
            output_mint=wsol_mint,
            amount=token_amount,
            slippage_bps=slippage_bps
        )
        
        if not quote:
            return None
        
        # Execute swap (returns tuple of (signature, sol_received))
        result = await self.swap(quote)
        if result:
            signature, _ = result  # Extract just the signature
            return signature
        return None
