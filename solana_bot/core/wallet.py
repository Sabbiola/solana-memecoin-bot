
import asyncio
from typing import Optional
from solders.keypair import Keypair # type: ignore
from solders.pubkey import Pubkey # type: ignore
from solana.rpc.async_api import AsyncClient
from spl.token.instructions import get_associated_token_address

from ..config import PRIVATE_KEY

class WalletManager:
    def __init__(self, client: AsyncClient):
        self.client = client
        if not PRIVATE_KEY:
             raise ValueError("SOLANA_PRIVATE_KEY not found in environment")
        self.payer = Keypair.from_base58_string(PRIVATE_KEY)
        self.pubkey = self.payer.pubkey()

    async def get_sol_balance(self) -> float:
        """Returns available SOL balance."""
        try:
            resp = await self.client.get_balance(self.pubkey)
            if resp.value is not None:
                return resp.value / 1e9
        except Exception as e:
            print(f"[WARN] Failed to get SOL balance: {e}")
        return 0.0

    async def get_token_balance(self, mint_str: str) -> int:
        """
        Returns token balance (raw amount).
        
        IMPORTANT: Uses get_token_accounts_by_owner to find ALL token accounts,
        not just the standard ATA. This is necessary because DEXs like PumpSwap
        and Jupiter may create token accounts with non-standard addresses.
        """
        from solana.rpc.types import TokenAccountOpts
        
        try:
            mint = Pubkey.from_string(mint_str)
            
            # Method 1: Find ALL token accounts for this mint (most reliable)
            resp = await self.client.get_token_accounts_by_owner_json_parsed(
                self.pubkey,
                TokenAccountOpts(mint=mint)
            )
            
            if resp.value:
                total_balance = 0
                for acc in resp.value:
                    try:
                        balance = int(acc.account.data.parsed['info']['tokenAmount']['amount'])
                        total_balance += balance
                    except (KeyError, TypeError):
                        continue
                
                if total_balance > 0:
                    return total_balance
            
            # Method 2: Fallback to standard ATA (for compatibility)
            ata = get_associated_token_address(self.pubkey, mint)
            resp_ata = await self.client.get_token_account_balance(ata)
            if resp_ata.value:
                return int(resp_ata.value.amount)
                
        except Exception as e:
            # Log the error for debugging but don't crash
            print(f"[WARN] Token balance check failed for {mint_str[:8]}...: {e}")
        
        return 0


    async def get_token_account_address(self, mint_str: str) -> Pubkey:
         return get_associated_token_address(self.pubkey, Pubkey.from_string(mint_str))
    
    async def get_token_decimals(self, mint_str: str) -> int:
        """
        Fetch token decimals from SPL token mint account.
        
        Args:
            mint_str: Token mint address
            
        Returns:
            Token decimals (default 6 if fetch fails)
        """
        try:
            mint_pubkey = Pubkey.from_string(mint_str)
            account_info = await self.client.get_account_info(mint_pubkey)
            
            if account_info and account_info.value:
                # SPL Token Mint layout:
                # - 0-35: mint_authority (Option<Pubkey>)
                # - 36-43: supply (u64)
                # - 44: decimals (u8) ← This is what we need!
                # - 45: is_initialized (bool)
                # - 46-81: freeze_authority (Option<Pubkey>)
                data = account_info.value.data
                
                if len(data) >= 45:
                    decimals = data[44]
                    print(f"[INFO] Token {mint_str[:8]}... has {decimals} decimals")
                    return decimals
                else:
                    print(f"[WARN] Mint account data too short for {mint_str[:8]}")
                    
        except Exception as e:
            print(f"[WARN] Failed to fetch decimals for {mint_str[:8]}: {e}")
        
        # Default fallback (most SPL tokens use 6)
        print(f"[INFO] Using default decimals=6 for {mint_str[:8]}")
        return 6


    # Helper for synchronous access if needed (though avoided in async code)
    # def get_sol_balance_sync(self) -> float:
    #     return asyncio.get_event_loop().run_until_complete(self.get_sol_balance())
