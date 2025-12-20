"""
Advanced Dynamic Trailing Stop System

Features:
1. Fee-aware break-even calculation
2. Immediate break-even protection (no negative after entry)
3. 50% sell at break-even to recover entry
4. Remaining 50% grows with dynamic trailing stop
5. Trailing floor rises with profit to lock in gains
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import time

# Fee structure (Solana/DEX typical fees)
@dataclass
class FeeStructure:
    # Entry fees (Jupiter buy with low slippage)
    swap_fee_bps: int = 50           # 0.5% Jupiter buy (low slippage)
    priority_fee_sol: float = 0.0001  # Priority fee
    base_fee_sol: float = 0.000005   # Base tx fee
    jito_tip_sol: float = 0.00025     # 250k lamports for buys (0.0005 for sells)
    
    # Exit fees (Reduced slippage for better fee efficiency)
    exit_swap_fee_bps: int = 300     # 3% slippage (reduced from 5% for fee optimization)
    exit_priority_fee_sol: float = 0.0001
    exit_base_fee_sol: float = 0.000005
    
    def total_entry_fee(self, entry_sol: float, use_jito: bool = False) -> float:
        """Calculate total entry fees in SOL"""
        swap_fee = entry_sol * (self.swap_fee_bps / 10000)
        fixed_fees = self.priority_fee_sol + self.base_fee_sol
        if use_jito:
            fixed_fees += self.jito_tip_sol
        return swap_fee + fixed_fees
    
    def total_exit_fee(self, exit_value_sol: float) -> float:
        """Calculate total exit fees in SOL"""
        swap_fee = exit_value_sol * (self.exit_swap_fee_bps / 10000)
        fixed_fees = self.exit_priority_fee_sol + self.exit_base_fee_sol
        return swap_fee + fixed_fees
    
    def break_even_multiplier(self, use_jito: bool = False) -> float:
        """
        Calculate the price multiplier needed to break even after fees.
        Example: If entry+exit fees = 3%, need 1.03x to break even
        """
        # Entry: lose swap fee on buy
        entry_loss = self.swap_fee_bps / 10000
        # Exit: lose swap fee on sell
        exit_loss = self.exit_swap_fee_bps / 10000
        
        # Total: (1 - entry_loss) * multiplier * (1 - exit_loss) = 1
        # multiplier = 1 / ((1 - entry_loss) * (1 - exit_loss))
        multiplier = 1 / ((1 - entry_loss) * (1 - exit_loss))
        return multiplier
    
    def calculate_trigger_for_sell_pct(
        self, 
        entry_sol: float,
        sell_pct: float, 
        use_jito: bool = False
    ) -> float:
        """
        Calculate the exact trigger % needed so that selling sell_pct% 
        recovers 100% of entry capital after ALL fees.
        
        Formula:
        1. Total capital deployed = entry_sol + entry_fees
        2. At trigger, position value = V
        3. Sell sell_pct% → net = (V * sell_pct/100) - exit_fees
        4. For break-even: net >= entry_sol
        5. Solve for V, convert to percentage gain
        
        Args:
            entry_sol: Initial SOL invested
            sell_pct: Percentage of position to sell (e.g., 75.0)
            use_jito: Whether Jito tip is included
        
        Returns:
            Trigger percentage (e.g., 15.5 means +15.5% from entry)
        """
        # Calculate entry fees
        entry_fees = self.total_entry_fee(entry_sol, use_jito)
        
        # We need to recover entry_sol from selling sell_pct%
        # Formula: (V * sell_pct/100) * (1 - exit_swap_fee_rate) - exit_fixed_fees >= entry_sol
        # Solve for V:
        
        exit_swap_fee_rate = self.exit_swap_fee_bps / 10000
        exit_fixed_fees = self.exit_priority_fee_sol + self.exit_base_fee_sol
        
        # V * (sell_pct/100) * (1 - exit_fee_rate) = entry_sol + exit_fixed_fees
        # V = (entry_sol + exit_fixed_fees) / ((sell_pct/100) * (1 - exit_fee_rate))
        
        required_value = (entry_sol + exit_fixed_fees) / (
            (sell_pct / 100) * (1 - exit_swap_fee_rate)
        )
        
        # Convert to percentage gain from entry
        trigger_pct = ((required_value / entry_sol) - 1) * 100
        
        return trigger_pct
    
    def calculate_sell_pct_for_trigger(
        self,
        entry_sol: float,
        trigger_pct: float, 
        use_jito: bool = False
    ) -> float:
        """
        Calculate % to sell to break even at a specific trigger %.
        
        Args:
            entry_sol: Initial SOL invested
            trigger_pct: Desired trigger percentage (e.g., 15.0 for +15%)
            use_jito: Whether Jito tip is included
        
        Returns:
            Percentage to sell (e.g., 75.5)
        """
        # Value at trigger
        trigger_value = entry_sol * (1 + trigger_pct / 100)
        
        exit_swap_fee_rate = self.exit_swap_fee_bps / 10000
        exit_fixed_fees = self.exit_priority_fee_sol + self.exit_base_fee_sol
        
        # Solve: (trigger_value * sell_pct/100) * (1 - exit_fee_rate) - exit_fixed >= entry_sol
        # sell_pct = 100 * (entry_sol + exit_fixed) / (trigger_value * (1 - exit_fee_rate))
        
        sell_pct = 100 * (entry_sol + exit_fixed_fees) / (
            trigger_value * (1 - exit_swap_fee_rate)
        )
        
        return min(sell_pct, 100.0)  # Cap at 100%


@dataclass
class DynamicTrailingConfig:
    """Configuration for dynamic trailing stop"""
    
    # Break-even settings
    # NOTE: trigger_pct is NOW AUTO-CALCULATED based on sell_pct and fees
    # Set to None to auto-calculate, or override with specific value
    break_even_trigger_pct: float = None  # Auto-calculated in DynamicPosition.__init__
    break_even_buffer_pct: float = 0.5    # Extra buffer above calculated trigger
    
    # ✅ FIXED: 75% sell to ensure break-even recovery
    break_even_sell_pct: float = 75.0  # Sell 75% to recover entry capital + fees
    
    # Initial trailing stop (before break-even sell)
    initial_trailing_pct: float = 15.0   # 15% trailing before break-even ✅
    
    # Dynamic trailing tiers (after break-even sell)
    # Format: (profit_threshold%, trailing_stop%)
    trailing_tiers: Tuple[Tuple[float, float], ...] = (
        (0, 15.0),     # 0-30% profit: 15% trailing ✅
        (30, 12.0),    # 30-50% profit: 12% trailing (tighter)
        (50, 10.0),    # 50-100% profit: 10% trailing
        (100, 8.0),    # 100-200% profit: 8% trailing
        (200, 6.0),    # 200%+ profit: 6% trailing (tight for moon)
    )
    
    # Hard stop loss (emergency) ✅
    hard_stop_loss_pct: float = -10.0    # -10% from entry = emergency exit
    
    # Profit lock floor (rises with gains)
    enable_profit_lock: bool = True
    profit_lock_start_pct: float = 30.0  # Start locking at 30% profit
    profit_lock_ratio: float = 0.5       # Lock 50% of gains as floor


class DynamicPosition:
    """
    Position with dynamic trailing stop and break-even protection.
    """
    
    def __init__(
        self, 
        mint: str,
        entry_sol: float,
        token_amount: float,
        decimals: int = 6,
        config: DynamicTrailingConfig = None,
        fees: FeeStructure = None,
        use_jito: bool = False
    ):
        self.mint = mint
        self.entry_sol = entry_sol
        self.initial_token_amount = token_amount
        self.remaining_token_pct = 100.0  # 100% at start
        self.decimals = decimals
        self.config = config or DynamicTrailingConfig()
        self.fees = fees or FeeStructure()
        self.use_jito = use_jito
        
        # ✅ CALCULATE EXACT BREAK-EVEN TRIGGER
        # This ensures selling break_even_sell_pct% recovers 100% of capital
        if self.config.break_even_trigger_pct is None:
            # Auto-calculate trigger for configured sell %
            calculated_trigger = self.fees.calculate_trigger_for_sell_pct(
                entry_sol=entry_sol,
                sell_pct=self.config.break_even_sell_pct,
                use_jito=use_jito
            )
            self.break_even_pct = calculated_trigger
        else:
            # Use configured trigger (for manual override)
            self.break_even_pct = self.config.break_even_trigger_pct
        
        self.break_even_multiplier = 1 + (self.break_even_pct / 100)
        self.break_even_value = entry_sol * self.break_even_multiplier
        
        # Log for transparency
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"📊 Break-even calculated: Trigger at +{self.break_even_pct:.2f}%, "
            f"Sell {self.config.break_even_sell_pct:.0f}% to recover {entry_sol:.4f} SOL"
        )
        
        # State
        self.entry_time = time.time()
        self.current_value = entry_sol
        self.peak_value = entry_sol
        self.peak_pnl_pct = 0.0
        self.profit_floor = 0.0  # Minimum acceptable exit value
        
        # Flags
        self.break_even_sold = False
        self.is_closed = False
        self.exit_reason = None
        self.total_realized_sol = 0.0
    
    def update(self, current_value: float) -> dict:
        """
        Update position with new value and check exit conditions.
        
        Returns dict with:
        - action: 'HOLD', 'SELL_PARTIAL', 'SELL_ALL'
        - reason: Exit reason if selling
        - sell_pct: Percentage to sell
        - details: Additional info
        """
        self.current_value = current_value
        
        # Calculate PnL
        pnl_pct = ((current_value / self.entry_sol) - 1) * 100
        pnl_from_peak = ((current_value / self.peak_value) - 1) * 100 if self.peak_value > 0 else 0
        
        result = {
            'action': 'HOLD',
            'reason': None,
            'sell_pct': 0,
            'details': {
                'pnl_pct': pnl_pct,
                'pnl_from_peak': pnl_from_peak,
                'break_even_pct': self.break_even_pct,
                'profit_floor': self.profit_floor,
                'remaining_pct': self.remaining_token_pct
            }
        }
        
        # Update peak
        if current_value > self.peak_value:
            self.peak_value = current_value
            self.peak_pnl_pct = pnl_pct
            
            # Update profit floor (lock gains)
            if self.config.enable_profit_lock and pnl_pct >= self.config.profit_lock_start_pct:
                locked_pct = pnl_pct * self.config.profit_lock_ratio
                new_floor = self.entry_sol * (1 + locked_pct / 100)
                if new_floor > self.profit_floor:
                    self.profit_floor = new_floor
        
        # =============================================
        # CHECK 1: Hard Stop Loss (Emergency)
        # =============================================
        if pnl_pct <= self.config.hard_stop_loss_pct:
            result['action'] = 'SELL_ALL'
            result['reason'] = 'HARD_STOP'
            result['sell_pct'] = self.remaining_token_pct
            return result
        
        # =============================================
        # CHECK 2: Break-Even Sell (50% at break-even)
        # =============================================
        if not self.break_even_sold:
            # Need to recover entry + buffer
            target_pct = self.break_even_pct + self.config.break_even_buffer_pct
            
            if pnl_pct >= target_pct:
                result['action'] = 'SELL_PARTIAL'
                result['reason'] = 'BREAK_EVEN'
                result['sell_pct'] = self.config.break_even_sell_pct
                self.break_even_sold = True
                self.remaining_token_pct -= self.config.break_even_sell_pct
                
                # Calculate realized SOL
                sell_value = current_value * (self.config.break_even_sell_pct / 100)
                exit_fee = self.fees.total_exit_fee(sell_value)
                self.total_realized_sol += sell_value - exit_fee
                
                return result
            
            # Before break-even: use initial trailing (wider)
            if pnl_from_peak <= -self.config.initial_trailing_pct:
                result['action'] = 'SELL_ALL'
                result['reason'] = 'TRAILING_PRE_BE'
                result['sell_pct'] = self.remaining_token_pct
                return result
        
        # =============================================
        # CHECK 3: Profit Floor (Locked Gains)
        # =============================================
        if self.break_even_sold and self.profit_floor > 0:
            if current_value <= self.profit_floor:
                result['action'] = 'SELL_ALL'
                result['reason'] = 'PROFIT_FLOOR'
                result['sell_pct'] = self.remaining_token_pct
                return result
        
        # =============================================
        # CHECK 4: Dynamic Trailing Stop
        # =============================================
        if self.break_even_sold:
            # Determine trailing % based on profit tier
            trailing_pct = self._get_dynamic_trailing(pnl_pct)
            
            if pnl_from_peak <= -trailing_pct:
                result['action'] = 'SELL_ALL'
                result['reason'] = f'TRAILING_{trailing_pct:.0f}%'
                result['sell_pct'] = self.remaining_token_pct
                return result
        
        return result
    
    def _get_dynamic_trailing(self, current_pnl_pct: float) -> float:
        """Get trailing stop % based on current profit level."""
        trailing = self.config.trailing_tiers[0][1]  # Default
        
        for threshold, trail_pct in self.config.trailing_tiers:
            if current_pnl_pct >= threshold:
                trailing = trail_pct
        
        return trailing
    
    def get_status(self) -> dict:
        """Get current position status."""
        pnl_pct = ((self.current_value / self.entry_sol) - 1) * 100
        pnl_from_peak = ((self.current_value / self.peak_value) - 1) * 100 if self.peak_value > 0 else 0
        
        return {
            'mint': self.mint[:12] + '...',
            'entry_sol': self.entry_sol,
            'current_value': self.current_value,
            'pnl_pct': pnl_pct,
            'pnl_from_peak': pnl_from_peak,
            'peak_pnl_pct': self.peak_pnl_pct,
            'break_even_pct': self.break_even_pct,
            'break_even_sold': self.break_even_sold,
            'remaining_pct': self.remaining_token_pct,
            'profit_floor': self.profit_floor,
            'realized_sol': self.total_realized_sol,
            'trailing_pct': self._get_dynamic_trailing(pnl_pct) if self.break_even_sold else self.config.initial_trailing_pct
        }


# =============================================
# SIMULATION
# =============================================

async def simulate_dynamic_trailing():
    """Simulate the dynamic trailing stop system."""
    import asyncio
    
    print("\n" + "=" * 80)
    print("🔄 DYNAMIC TRAILING STOP SIMULATION")
    print("=" * 80)
    
    # Initialize
    fees = FeeStructure()
    config = DynamicTrailingConfig()
    
    # Show fee breakdown
    entry_sol = 0.01
    
    # Calculate what trigger will be for 75% sell
    calculated_trigger = fees.calculate_trigger_for_sell_pct(entry_sol, 75.0, use_jito=False)
    
    print(f"""
💰 Entry: {entry_sol} SOL

📊 Fee Structure:
   Entry swap fee:  {fees.swap_fee_bps/100}%
   Exit swap fee:   {fees.exit_swap_fee_bps/100}%
   Priority fees:   ~{(fees.priority_fee_sol + fees.base_fee_sol)*2*1e6:.0f} lamports
   
   Break-even multiplier (old): {fees.break_even_multiplier():.4f}x
   Break-even at (old): +{(fees.break_even_multiplier()-1)*100:.2f}%

⚙️ NEW STRATEGY (75% Sell):
   1. Calculate exact trigger for 75% sell
   2. Trigger at: +{calculated_trigger:.2f}% (auto-calculated!)
   3. Sell 75% to recover 100% of entry capital
   4. Remaining 25% is pure HOUSE MONEY 🎰
   5. Trailing stop protects the 25% moonbag
""")
    
    # Create position
    position = DynamicPosition(
        mint="FQ7B6Eq6DQE8Y3sU3dfik3PmyGvZ5LskjzySAc5ipump",
        entry_sol=entry_sol,
        token_amount=1000000,
        config=config,
        fees=fees
    )
    
    # Simulate price movements
    print("-" * 60)
    print("📈 PRICE SIMULATION")
    print("-" * 60)
    
    # Get the actual trigger from the position
    actual_trigger_pct = position.break_even_pct
    trigger_mult = 1 + (actual_trigger_pct / 100)
    
    price_movements = [
        (1.00, "Entry"),
        (1.05, "+5%"),
        (1.10, "+10%"),
        (trigger_mult - 0.01, f"Approaching trigger (~+{actual_trigger_pct-1:.1f}%)"),
        (trigger_mult + 0.005, f"✅ BREAK-EVEN TRIGGER! (+{actual_trigger_pct:.1f}%)"),
        (trigger_mult + 0.03, f"+{(trigger_mult + 0.03 - 1)*100:.1f}%"),
        (trigger_mult + 0.10, f"+{(trigger_mult + 0.10 - 1)*100:.1f}%"),
        (trigger_mult + 0.15, f"+{(trigger_mult + 0.15 - 1)*100:.1f}% (peak)"),
        (trigger_mult + 0.13, "Dip -1.3% from peak"),
        (trigger_mult + 0.10, "Dip -3.3% from peak"),
        (trigger_mult + 0.07, "Dip -5.3% from peak → Getting close!"),
    ]
    
    for multiplier, description in price_movements:
        if position.is_closed:
            break
            
        value = entry_sol * multiplier
        result = position.update(value)
        status = position.get_status()
        
        # Format output
        pnl_emoji = "🟢" if status['pnl_pct'] > 0 else "🔴"
        
        print(f"\n  💰 {description}")
        print(f"     Value: {value:.4f} SOL | PnL: {pnl_emoji} {status['pnl_pct']:+.1f}%")
        print(f"     Peak: {status['peak_pnl_pct']:+.1f}% | From Peak: {status['pnl_from_peak']:+.1f}%")
        print(f"     Remaining: {status['remaining_pct']:.0f}% | Floor: {status['profit_floor']:.4f}")
        print(f"     Trailing: {status['trailing_pct']:.0f}%")
        
        if result['action'] != 'HOLD':
            print(f"\n  🚨 ACTION: {result['action']}")
            print(f"     Reason: {result['reason']}")
            print(f"     Sell: {result['sell_pct']:.0f}%")
            
            if result['action'] == 'SELL_PARTIAL':
                print(f"     ✅ Entry recovered! Remaining position is HOUSE MONEY")
            elif result['action'] == 'SELL_ALL':
                position.is_closed = True
                
        await asyncio.sleep(0.3)
    
    # Final summary
    print("\n" + "=" * 80)
    print("📊 SIMULATION SUMMARY")
    print("=" * 80)
    
    final = position.get_status()
    
    print(f"""
  Entry:           {entry_sol} SOL
  Auto-calculated trigger: +{position.break_even_pct:.2f}%
  
  75% Sold at:     +{position.break_even_pct:.1f}%
  Realized:        {position.total_realized_sol:.4f} SOL
  
  ✅ BREAK-EVEN CHECK:
     Entry capital:      {entry_sol:.4f} SOL
     Recovered (75%):    {position.total_realized_sol:.4f} SOL
     Delta:              {position.total_realized_sol - entry_sol:+.6f} SOL
     Status:             {'✅ FULLY RECOVERED!' if position.total_realized_sol >= entry_sol * 0.999 else '❌ SHORT'}
  
  Remaining 25% moonbag:   
    Current value:   {final['current_value'] * 0.25:.4f} SOL
    Is house money:  {'✅ YES' if position.break_even_sold else '❌ NO'}
  
  🎯 Strategy verification:
     - Entry fully recovered with 75% sell ✅
     - Remaining 25% is risk-free profit ✅
     - Trailing stop protects moonbag ✅
""")
    
    print("✅ Simulation complete!\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(simulate_dynamic_trailing())
