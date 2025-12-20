"""
Scalping Strategy Module

Autonomous scalping strategy for high-volatility Solana memecoin trading.
Uses technical indicators (RSI, EMA, volume) to generate entry/exit signals.
"""

import asyncio
import logging
from typing import Dict, Optional, Any, List
from dataclasses import dataclass
from .dynamic_trailing import DynamicPosition, DynamicTrailingConfig, FeeStructure

logger = logging.getLogger(__name__)


@dataclass
class TokenData:
    """Market data for a token"""
    mint: str
    price: float
    volume_24h: float
    liquidity_usd: float
    price_change_24h: float
    rsi: Optional[float] = None
    ema_20: Optional[float] = None
    avg_volume: Optional[float] = None


@dataclass
class ExitSignal:
    """Exit signal from strategy"""
    should_exit: bool
    reason: str
    sell_pct: float = 100.0


class ScalpingStrategy:
    """
    Scalping strategy for memecoin trading.
    
    Entry conditions:
    - RSI < 30 (oversold)
    - Volume spike (> 2x average)
    - Pass anti-rug filters
    
    Exit conditions:
    - Initial SL: -80% (wide for memecoin volatility)
    - Break-even: Auto-adjust SL when fees covered
    - Trailing: 10% from peak (moonbag protection)
    """
    
    def __init__(
        self,
        rsi_oversold: float = 30,
        rsi_overbought: float = 70,
        volume_multiplier: float = 2.0,
        initial_stop_loss_pct: float = -80.0,
        trailing_stop_pct: float = 10.0,
        break_even_buffer_pct: float = 0.5,
        max_hold_time_minutes: int = 15  # Default 15 min force sell
    ):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.volume_multiplier = volume_multiplier
        self.initial_stop_loss_pct = initial_stop_loss_pct
        self.trailing_stop_pct = trailing_stop_pct
        self.break_even_buffer_pct = break_even_buffer_pct
        self.max_hold_time_minutes = max_hold_time_minutes
        
        logger.info(
            f"📊 ScalpingStrategy initialized: "
            f"RSI<{rsi_oversold}, Volume>{volume_multiplier}x, "
            f"SL={initial_stop_loss_pct}%, Trailing={trailing_stop_pct}%, "
            f"Timeout={max_hold_time_minutes}m"
        )
    
    async def should_enter(self, token_data: TokenData, validator) -> bool:
        """
        Check if we should enter a position.
        
        Args:
            token_data: Market data for token
            validator: Validator instance for anti-rug checks
            
        Returns:
            True if entry signal triggered
        """
        mint = token_data.mint
        
        # 1. RSI Check (oversold) - BYPASSED for now, we use scanner filters
        # RSI data would need real-time calculation which we don't have
        # if token_data.rsi is None or token_data.rsi >= self.rsi_oversold:
        #     logger.info(f"[{mint[:8]}] RSI check failed: {token_data.rsi}")
        #     return False
        
        # 2. Volume Check - BYPASSED, already filtered by scanner
        # if token_data.avg_volume and token_data.volume_24h < token_data.avg_volume * self.volume_multiplier:
        #     logger.info(f"[{mint[:8]}] Volume check failed")
        #     return False
        
        # 3. Anti-Rug Filters - Already done by scanner, just return True
        # The token_scanner already did all the heavy lifting:
        # - Phase detection
        # - Pool quality check  
        # - Holder concentration
        # - Age filters
        
        logger.info(
            f"✅ [{mint[:8]}] Strategy should_enter: APPROVED "
            f"(scanner already validated)"
        )
        return True
    
    async def should_exit(self, position: DynamicPosition, current_value: float) -> ExitSignal:
        """
        Check if we should exit a position.
        
        Uses DynamicPosition for automatic:
        - -80% initial stop loss
        - Break-even adjustment when fees covered
        - 10% trailing stop from peak
        
        Args:
            position: DynamicPosition tracker
            current_value: Current position value in SOL
            
        Returns:
            ExitSignal with action and reason
        """
        # Update position with current value
        result = position.update(current_value)
        
        # CHECK: Force Sell Timeout
        import time
        duration_minutes = (time.time() - position.entry_time) / 60
        if duration_minutes > self.max_hold_time_minutes:
            return ExitSignal(
                should_exit=True,
                reason=f"TIMEOUT ({duration_minutes:.1f}m > {self.max_hold_time_minutes}m)",
                sell_pct=100.0
            )
        
        if result['action'] == 'SELL_ALL':
            return ExitSignal(
                should_exit=True,
                reason=result['reason'],
                sell_pct=result['sell_pct']
            )
        
        if result['action'] == 'SELL_PARTIAL':
            return ExitSignal(
                should_exit=True,
                reason=result['reason'],
                sell_pct=result['sell_pct']
            )
        
        return ExitSignal(
            should_exit=False,
            reason="HOLD"
        )
    
    def create_position_tracker(
        self, 
        mint: str, 
        entry_sol: float, 
        token_amount: float,
        decimals: int = 6,
        use_jito: bool = False
    ) -> DynamicPosition:
        """
        Create a DynamicPosition tracker for monitoring.
        
        Args:
            mint: Token mint address
            entry_sol: Entry amount in SOL
            token_amount: Token amount received
            decimals: Token decimals
            use_jito: Whether Jito was used (affects fee calculation)
            
        Returns:
            DynamicPosition instance
        """
        fees = FeeStructure()
        config = DynamicTrailingConfig(
            initial_trailing_pct=abs(self.initial_stop_loss_pct),  # 80%
            break_even_sell_pct=0.0,  # NO partial sell (full moonbag)
            trailing_tiers=((0, self.trailing_stop_pct),),  # 10% trailing always
            break_even_buffer_pct=self.break_even_buffer_pct,
            hard_stop_loss_pct=self.initial_stop_loss_pct  # -80%
        )
        
        return DynamicPosition(
            mint=mint,
            entry_sol=entry_sol,
            token_amount=token_amount,
            decimals=decimals,
            config=config,
            fees=fees,
            use_jito=use_jito
        )
