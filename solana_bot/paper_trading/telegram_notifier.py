"""
Enhanced Telegram Notifier for Paper Trading

Adds rich notifications specifically for paper trading mode with:
- Clear PAPER TRADE badges
- DexScreener links
- Performance summaries
- Emoji-rich formatting
"""

import logging
from typing import Optional
from ..telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class PaperTradingNotifier:
    """
    Enhanced notifier for paper trading mode.
    
    Wraps TelegramNotifier with paper-trading-specific messages.
    """
    
    def __init__(self, telegram_notifier: TelegramNotifier):
        self.notifier = telegram_notifier
    
    async def send_paper_buy_alert(
        self,
        symbol: str,
        mint: str,
        amount_sol: float,
        price: float,
        token_amount: float,
        balance_after: float,
        slippage_pct: float = 0.5,
        phase: str = "UNKNOWN"
    ):
        """Send paper trading buy notification."""
        
        # DexScreener link
        dex_link = f"https://dexscreener.com/solana/{mint}"
        
        header = "━━━━━━━━━━━━━━━━━━━━━━\n🧪 <b>PAPER TRADE - BUY</b> 🧪\n━━━━━━━━━━━━━━━━━━━━━━"
        
        message = f"""
{header}

💎 <b>Token:</b> {symbol}
📊 <b>Price:</b> <code>{price:.8f}</code> SOL
🪙 <b>Tokens:</b> <code>{token_amount:,.0f}</code>

📈 <b>Trade Details</b>
├ Amount: <b>{amount_sol:.4f} SOL</b>
├ Phase: <code>{phase}</code>
└ Slippage: <b>{slippage_pct}%</b> (sim)

💵 <b>Portfolio Status</b>
├ Balance: <b>{balance_after:.4f} SOL</b>
└ Status: 🧪 <i>Simulation Only</i>

🔗 <a href="{dex_link}">View on DexScreener</a>
<code>{mint}</code>
"""
        
        # Inline keyboard for consistency
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "📊 DexScreener", "url": f"https://dexscreener.com/solana/{mint}"},
                    {"text": "🛡️ Rugcheck", "url": f"https://rugcheck.xyz/tokens/{mint}"}
                ],
                [
                    {"text": "🛑 FORCE SELL (SIM)", "callback_data": f"sell_{mint}"}
                ]
            ]
        }
        
        await self.notifier.send_message(message.strip(), reply_markup=keyboard)
    
    async def send_paper_sell_alert(
        self,
        symbol: str,
        mint: str,
        entry_sol: float,
        exit_sol: float,
        pnl_sol: float,
        pnl_pct: float,
        balance_after: float,
        reason: str,
        hold_time: Optional[str] = None
    ):
        """Send paper trading sell notification."""
        
        # Emoji based on P&L
        if pnl_pct > 10:
            emoji = "🚀🎉"
        elif pnl_pct > 0:
            emoji = "✅"
        elif pnl_pct > -10:
            emoji = "⚠️"
        else:
            emoji = "❌"
        
        dex_link = f"https://dexscreener.com/solana/{mint}"
        
        message = f"""
{emoji} <b>PAPER TRADE - SELL</b> {emoji}

💎 <b>Token:</b> {symbol}
📥 <b>Entry:</b> {entry_sol:.4f} SOL
📤 <b>Exit:</b> {exit_sol:.4f} SOL

💵 <b>P&L:</b> {pnl_sol:+.4f} SOL (<b>{pnl_pct:+.1f}%</b>)
🔄 <b>Reason:</b> {reason}
"""
        
        if hold_time:
            message += f"⏱️ <b>Hold Time:</b> {hold_time}\n"
        
        message += f"""
💰 <b>Balance After:</b> {balance_after:.4f} SOL

🔗 <a href="{dex_link}">View on DexScreener</a>

⚠️ <i>Virtual trade - no real transaction</i>
"""
        
        await self.notifier.send_message(message.strip())
    
    async def send_paper_session_summary(
        self,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        win_rate: float,
        total_pnl: float,
        total_pnl_pct: float,
        initial_balance: float,
        final_balance: float,
        open_positions: int
    ):
        """Send paper trading session summary."""
        
        # Performance emoji
        if total_pnl_pct > 5:
            perf_emoji = "🚀"
        elif total_pnl_pct > 0:
            perf_emoji = "✅"
        elif total_pnl_pct > -5:
            perf_emoji = "⚠️"
        else:
            perf_emoji = "❌"
        
        message = f"""
📊 <b>PAPER TRADING SUMMARY</b> {perf_emoji}

<b>═══ PERFORMANCE ═══</b>
💰 Initial: {initial_balance:.4f} SOL
💵 Current: {final_balance:.4f} SOL
📈 P&L: <b>{total_pnl:+.4f} SOL ({total_pnl_pct:+.2f}%)</b>

<b>═══ STATISTICS ═══</b>
📊 Total Trades: {total_trades}
✅ Wins: {winning_trades}
❌ Losses: {losing_trades}
🎯 Win Rate: <b>{win_rate:.1f}%</b>

<b>═══ CURRENT STATUS ═══</b>
📦 Open Positions: {open_positions}

⚠️ <i>Paper trading mode - simulate only</i>
"""
        
        await self.notifier.send_message(message.strip())
    
    async def send_startup_message(
        self,
        mode: str,
        initial_balance: float,
        slippage_pct: float
    ):
        """Send bot startup notification."""
        
        message = f"""
🤖 <b>BOT STARTED - PAPER TRADING MODE</b> 🧪

<b>═══ CONFIGURATION ═══</b>
🧪 Mode: <b>{mode}</b>
💰 Initial Balance: {initial_balance:.4f} SOL
📉 Slippage: {slippage_pct}%

⚠️ <b>NO REAL TRANSACTIONS!</b>
All trades are simulated for testing purposes.

Monitor your virtual performance here! 📊
"""
        
        await self.notifier.send_message(message.strip())
