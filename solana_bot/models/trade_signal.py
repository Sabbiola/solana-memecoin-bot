"""
Trade Signal Model

Represents a parsed trading signal from a transaction.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeSignal:
    """
    A trade signal extracted from a transaction.
    
    Attributes:
        wallet: Wallet address that made the trade
        mint: Token mint address
        action: "BUY" or "SELL"
        amount_sol: Amount in SOL (approximate)
        dex: DEX used ("BONDING_CURVE", "PUMPSWAP", "RAYDIUM", "JUPITER")
        signature: Transaction signature
        slot: Blockchain slot
        timestamp: Unix timestamp
    """
    wallet: str
    mint: str
    action: str  # "BUY" or "SELL"
    amount_sol: float
    dex: str
    signature: str
    slot: int
    timestamp: Optional[float] = None
    
    # Optional metadata
    token_amount: Optional[float] = None
    price: Optional[float] = None
    
    def __str__(self) -> str:
        return (
            f"TradeSignal({self.action} {self.mint[:8]}... "
            f"for {self.amount_sol} SOL via {self.dex})"
        )
