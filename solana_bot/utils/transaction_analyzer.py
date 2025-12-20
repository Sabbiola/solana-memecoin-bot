"""
Transaction analyzer module - Detects BUY and SELL from transaction data
"""
from typing import Dict, Optional, Tuple
from solders.pubkey import Pubkey


class TransactionAnalyzer:
    """
    Analyzes Solana transactions to detect BUY/SELL actions.
    Separated from bot.py for modularity and testability.
    """
    
    WSOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    
    @staticmethod
    def detect_trade_action(
        meta,
        wallet_address: str
    ) -> Optional[Dict[str, any]]:
        """
        Detect BUY or SELL action from transaction metadata.
        
        Args:
            meta: Transaction meta object (from tx.value.transaction.meta)
            wallet_address: Address of the wallet to monitor
            
        Returns:
            Dictionary with trade info or None if no trade detected:
            {
                "action": "BUY" | "SELL",
                "mint": str,
                "token_change": int,  # Absolute value
                "decimals": int
            }
        """
        if not meta:
            return None
        
        wallet_pubkey_str = str(Pubkey.from_string(wallet_address))
        
        # Locate token balance change for the target wallet
        for post in meta.post_token_balances:
            if post.owner == wallet_pubkey_str:
                # Find pre balance
                pre_amt = 0
                decimals = post.ui_token_amount.decimals
                
                for pre in meta.pre_token_balances:
                    if pre.mint == post.mint and pre.owner == post.owner:
                        pre_amt = int(pre.ui_token_amount.amount)
                        break
                
                diff = int(post.ui_token_amount.amount) - pre_amt
                
                # Ignore zero change
                if diff == 0:
                    continue
                
                mint = post.mint
                
                # Ignore WSOL/USDC (these are swap currency, not target tokens)
                if mint in [TransactionAnalyzer.WSOL_MINT, TransactionAnalyzer.USDC_MINT]:
                    continue
                
                # Determine action
                if diff > 0:
                    return {
                        "action": "BUY",
                        "mint": mint,
                        "token_change": abs(diff),
                        "decimals": decimals
                    }
                elif diff < 0:
                    return {
                        "action": "SELL",
                        "mint": mint,
                        "token_change": abs(diff),
                        "decimals": decimals
                    }
        
        return None
