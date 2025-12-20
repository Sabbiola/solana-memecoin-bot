"""
Account Protector - Daily Loss & Balance Protection
Complements existing RiskManager with account-level protections
"""

import time
import asyncio
import logging
from datetime import date, datetime
from typing import Optional, Tuple, Dict

logger = logging.getLogger(__name__)


class AccountProtector:
    """
    Account-level protection system (complements RiskManager).
    
    While RiskManager handles equity and exposure tracking,
    AccountProtector provides:
    - Daily loss limits (SOL and percentage)
    - Reserve balance protection  
    - Per-trade size limits
    - Consecutive loss circuit breaker
    - Daily trade count limits
    """
    
    def __init__(
        self,
        wallet_manager,
        telegram_notifier=None,
        max_daily_loss_sol: float = 0.05,
        max_daily_loss_pct: float = 10.0,
        max_daily_trades: int = 10,
        min_reserve_sol: float = 0.2,
        max_trade_pct: float = 5.0,
        max_consecutive_losses: int = 3,
        cooldown_seconds: int = 30
    ):
        """
        Initialize Risk Manager.
        
        Args:
            wallet_manager: WalletManager instance for balance checks
            telegram_notifier: Optional TelegramNotifier for alerts
            max_daily_loss_sol: Max SOL loss per day
            max_daily_loss_pct: Max % loss per day
            max_daily_trades: Max trades per day
            min_reserve_sol: Minimum SOL to always keep
            max_trade_pct: Max % of balance per trade
            max_consecutive_losses: Consecutive losses before cooldown
            cooldown_seconds: Cooldown duration after consecutive losses
        """
        self.wallet = wallet_manager
        self.telegram = telegram_notifier
        
        # Configuration
        self.max_daily_loss_sol = max_daily_loss_sol
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_daily_trades = max_daily_trades
        self.min_reserve_sol = min_reserve_sol
        self.max_trade_pct = max_trade_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_seconds = cooldown_seconds
        
        # Daily stats tracking
        self.daily_stats = {
            'date': date.today(),
            'start_balance': 0.0,
            'current_balance': 0.0,
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'trade_count': 0,
            'wins': 0,
            'losses': 0
        }
        
        # Safety state
        self.consecutive_losses = 0
        self.in_cooldown = False
        self.cooldown_until = 0
        self.trading_halted = False
        
        logger.info("🛡️ RiskManager initialized with protections:")
        logger.info(f"   Max daily loss: {max_daily_loss_sol} SOL or {max_daily_loss_pct}%")
        logger.info(f"   Reserve: {min_reserve_sol} SOL")
        logger.info(f"   Max trade: {max_trade_pct}% of balance")
        logger.info(f"   Max daily trades: {max_daily_trades}")
        logger.info(f"   Consecutive loss limit: {max_consecutive_losses}")
    
    async def initialize(self):
        """Initialize daily stats with current balance"""
        try:
            start_balance = await self.wallet.get_sol_balance()
            self.daily_stats['start_balance'] = start_balance
            self.daily_stats['current_balance'] = start_balance
            logger.info(f"📊 Daily stats initialized - Balance: {start_balance:.4f} SOL")
        except Exception as e:
            logger.error(f"Failed to initialize daily stats: {e}")
    
    async def reset_daily_stats(self):
        """Reset daily stats for new trading day"""
        logger.info("📊 Resetting daily stats for new day")
        
        try:
            start_balance = await self.wallet.get_sol_balance()
        except:
            start_balance = self.daily_stats.get('current_balance', 0.0)
        
        self.daily_stats = {
            'date': date.today(),
            'start_balance': start_balance,
            'current_balance': start_balance,
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'trade_count': 0,
            'wins': 0,
            'losses': 0
        }
        
        logger.info(f"   New day starting balance: {start_balance:.4f} SOL")
    
    async def check_daily_limits(self) -> bool:
        """
        Check if daily trading limits allow further trading.
        
        Returns:
            True if trading allowed, False otherwise
        """
        # Reset stats if new day
        if date.today() != self.daily_stats['date']:
            await self.reset_daily_stats()
        
        # Check manual halt
        if self.trading_halted:
            logger.warning("🛑 Trading manually halted")
            return False
        
        # Check daily SOL loss limit
        if self.daily_stats['realized_pnl'] <= -self.max_daily_loss_sol:
            await self._trigger_halt(
                f"Daily Loss Limit Reached",
                f"Loss: {self.daily_stats['realized_pnl']:.4f} SOL\n"
                f"Limit: -{self.max_daily_loss_sol} SOL"
            )
            return False
        
        # Check daily percentage loss limit
        if self.daily_stats['start_balance'] > 0:
            loss_pct = (self.daily_stats['realized_pnl'] / self.daily_stats['start_balance']) * 100
            if loss_pct <= -self.max_daily_loss_pct:
                await self._trigger_halt(
                    f"Daily Loss % Reached",
                    f"Loss: {loss_pct:.2f}%\n"
                    f"Limit: -{self.max_daily_loss_pct}%"
                )
                return False
        
        # Check max daily trades
        if self.daily_stats['trade_count'] >= self.max_daily_trades:
            logger.warning(
                f"⚠️ MAX DAILY TRADES REACHED\n"
                f"   Trades: {self.daily_stats['trade_count']}/{self.max_daily_trades}"
            )
            return False
        
        # Check cooldown status
        if self.in_cooldown and time.time() < self.cooldown_until:
            remaining = int(self.cooldown_until - time.time())
            logger.info(f"⏸️ Cooldown active: {remaining}s remaining")
            return False
        elif self.in_cooldown:
            # Cooldown expired
            logger.info("✅ Cooldown period ended - resuming trading")
            self.in_cooldown = False
            self.consecutive_losses = 0
        
        return True
    
    async def can_trade(self, amount_sol: float, max_positions: int, current_positions: int) -> Tuple[bool, str]:
        """
        Validate if a trade can proceed.
        
        Args:
            amount_sol: Proposed trade amount in SOL
            max_positions: Maximum allowed positions
            current_positions: Current number of open positions
            
        Returns:
            (can_trade, reason): True if allowed with "OK", False with reason
        """
        # Get current balance
        try:
            current_balance = await self.wallet.get_sol_balance()
            self.daily_stats['current_balance'] = current_balance
        except Exception as e:
            error_msg = f"Failed to get balance: {e}"
            logger.error(f"❌ {error_msg}")
            return False, error_msg
        
        # Check minimum reserve
        remaining = current_balance - amount_sol
        if remaining < self.min_reserve_sol:
            reason = (
                f"Reserve protection triggered\n"
                f"   Current: {current_balance:.4f} SOL\n"
                f"   Trade: {amount_sol:.4f} SOL\n"
                f"   Would leave: {remaining:.4f} SOL\n"
                f"   Minimum reserve: {self.min_reserve_sol} SOL"
            )
            logger.error(f"❌ {reason}")
            return False, reason
        
        # Check per-trade percentage limit
        max_trade = current_balance * (self.max_trade_pct / 100)
        if amount_sol > max_trade:
            reason = (
                f"Trade size exceeds {self.max_trade_pct}% limit\n"
                f"   Requested: {amount_sol:.4f} SOL\n"
                f"   Max: {max_trade:.4f} SOL\n"
                f"   Balance: {current_balance:.4f} SOL"
            )
            logger.error(f"❌ {reason}")
            return False, reason
        
        # Check daily limits
        if not await self.check_daily_limits():
            return False, "Daily limits exceeded"
        
        # Check position limits
        if current_positions >= max_positions:
            reason = f"Max positions ({max_positions}) reached"
            logger.warning(f"⚠️ {reason}")
            return False, reason
        
        return True, "OK"
    
    def record_trade_result(self, profit_sol: float, close_reason: str):
        """
        Record trade result and update protection stats.
        
        Args:
            profit_sol: Realized profit/loss in SOL
            close_reason: Reason for closing position
        """
        # Update stats
        self.daily_stats['realized_pnl'] += profit_sol
        self.daily_stats['trade_count'] += 1
        
        if profit_sol >= 0:
            # Winning trade
            self.daily_stats['wins'] += 1
            self.consecutive_losses = 0  # Reset streak
            
            logger.info(
                f"✅ WIN #{self.daily_stats['wins']}\n"
                f"   Profit: +{profit_sol:.4f} SOL\n"
                f"   Reason: {close_reason}\n"
                f"   Daily PnL: {self.daily_stats['realized_pnl']:+.4f} SOL\n"
                f"   W/L: {self.daily_stats['wins']}/{self.daily_stats['losses']}"
            )
        else:
            # Losing trade
            self.daily_stats['losses'] += 1
            self.consecutive_losses += 1
            
            logger.warning(
                f"❌ LOSS #{self.daily_stats['losses']}\n"
                f"   Loss: {profit_sol:.4f} SOL\n"
                f"   Reason: {close_reason}\n"
                f"   Consecutive: {self.consecutive_losses}\n"
                f"   Daily PnL: {self.daily_stats['realized_pnl']:+.4f} SOL"
            )
            
            # Check daily loss limits (might trigger halt)
            if self.daily_stats['realized_pnl'] <= -self.max_daily_loss_sol:
                self.trading_halted = True
                logger.critical(
                    f"🛑 DAILY LOSS LIMIT REACHED!\n"
                    f"   Loss: {self.daily_stats['realized_pnl']:.4f} SOL\n"
                    f"   Limit: -{self.max_daily_loss_sol} SOL"
                )
                if self.telegram:
                    asyncio.create_task(self.telegram.send_alert(
                        f"🔴 TRADING HALTED - Daily Loss Limit\n\n"
                        f"Realized PnL: {self.daily_stats['realized_pnl']:.4f} SOL\n"
                        f"Limit: -{self.max_daily_loss_sol} SOL\n"
                        f"Trades: {self.daily_stats['trade_count']}\n"
                        f"W/L: {self.daily_stats['wins']}/{self.daily_stats['losses']}"
                    ))
            
            # Check percentage limit
            if self.daily_stats['start_balance'] > 0:
                loss_pct = (self.daily_stats['realized_pnl'] / self.daily_stats['start_balance']) * 100
                if loss_pct <= -self.max_daily_loss_pct:
                    self.trading_halted = True
                    logger.critical(
                        f"🛑 DAILY LOSS PCT REACHED!\n"
                        f"   Loss: {loss_pct:.2f}%\n"
                        f"   Limit: -{self.max_daily_loss_pct}%"
                    )
                    if self.telegram:
                        asyncio.create_task(self.telegram.send_alert(
                            f"🔴 TRADING HALTED - Daily Loss %\n\n"
                            f"Loss: {loss_pct:.2f}%\n"
                            f"Limit: -{self.max_daily_loss_pct}%\n"
                            f"PnL: {self.daily_stats['realized_pnl']:.4f} SOL"
                        ))
            
            # Check consecutive loss circuit breaker
            if self.consecutive_losses >= self.max_consecutive_losses:
                self._trigger_cooldown()
    
    def _trigger_cooldown(self):
        """Activate cooldown after consecutive losses"""
        logger.critical(
            f"🔴 COOLDOWN TRIGGERED\n"
            f"   Consecutive losses: {self.consecutive_losses}\n"
            f"   Duration: {self.cooldown_seconds}s"
        )
        
        self.in_cooldown = True
        self.cooldown_until = time.time() + self.cooldown_seconds
        
        # Send alert
        if self.telegram:
            asyncio.create_task(self.telegram.send_alert(
                f"🔴 COOLDOWN ACTIVATED\n\n"
                f"Consecutive losses: {self.consecutive_losses}\n"
                f"Cooldown: {self.cooldown_seconds}s\n"
                f"Daily PnL: {self.daily_stats['realized_pnl']:+.4f} SOL\n"
                f"W/L: {self.daily_stats['wins']}/{self.daily_stats['losses']}"
            ))
    
    async def _trigger_halt(self, title: str, details: str):
        """Halt trading and send alert"""
        logger.critical(f"🛑 {title.upper()}\n   {details}")
        
        self.trading_halted = True
        
        if self.telegram:
            asyncio.create_task(self.telegram.send_alert(
                f"🔴 TRADING HALTED\n\n"
                f"{title}\n"
                f"{details}\n\n"
                f"Trades: {self.daily_stats['trade_count']}\n"
                f"W/L: {self.daily_stats['wins']}/{self.daily_stats['losses']}"
            ))
    
    def get_stats(self) -> Dict:
        """Get current risk management stats"""
        return {
            **self.daily_stats,
            'consecutive_losses': self.consecutive_losses,
            'in_cooldown': self.in_cooldown,
            'trading_halted': self.trading_halted,
            'cooldown_remaining': max(0, int(self.cooldown_until - time.time())) if self.in_cooldown else 0
        }
    
    def resume_trading(self):
        """Manually resume trading (admin command)"""
        logger.info("✅ Trading manually resumed")
        self.trading_halted = False
        self.in_cooldown = False
        self.consecutive_losses = 0
