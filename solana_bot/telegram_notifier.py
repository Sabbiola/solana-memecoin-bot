"""
Telegram integration for bot notifications and controls.

Provides:
- Trade notifications with embedded buttons
- Bot status and control commands
- Position monitoring alerts
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import aiohttp

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Handles Telegram notifications and command processing.
    
    Features:
    - Send trade alerts with inline buttons
    - Process commands (/status, /stop, /resume, /positions)
    - Handle button callbacks
    """
    
    def __init__(
        self,
        bot_token: Optional[str],
        chat_id: Optional[str],
        admin_id: Optional[str] = None
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.admin_id = admin_id
        self.enabled = bool(bot_token and chat_id)
        self.base_url = f"https://api.telegram.org/bot{bot_token}" if bot_token else None
        self.last_update_id = 0
        
        if not self.enabled:
            logger.warning("Telegram disabled - bot_token or chat_id missing")
        else:
            logger.info(f"Telegram notifier initialized for chat {admin_id or chat_id}")
    
    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict] = None,
        disable_notification: bool = False
    ) -> bool:
        """
        Send a text message to Telegram admin privately.
        
        Args:
            text: Message text (supports HTML formatting)
            parse_mode: "HTML" or "Markdown"
            reply_markup: Inline keyboard markup
            disable_notification: Silent notification
        
        Returns:
            True if sent successfully
        """
        if not self.enabled:
            logger.debug("Telegram disabled, skipping message")
            return False
        
        # Always send to admin privately (REQUIRED)
        if not self.admin_id:
            logger.error("ADMIN_ID not set - cannot send private messages!")
            return False
        
        target_chat = self.admin_id
        
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": target_chat,  # Now uses admin_id
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": disable_notification
            }
            
            if reply_markup:
                payload["reply_markup"] = reply_markup
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    if resp.status == 200:
                        logger.debug(f"Telegram message sent to admin privately")
                        return True
                    else:
                        result = await resp.text()
                        logger.error(f"Telegram send failed: status={resp.status}, response={result}")
                        return False
        
        except Exception as e:
            logger.error(f"Telegram send exception: {e}")
            return False
    
    async def send_trade_alert(
        self,
        action: str,
        mint: str,
        amount_sol: float,
        phase: str,
        wallet_name: str = "Unknown",
        tx_signature: Optional[str] = None,
        token_name: Optional[str] = None,
        token_symbol: Optional[str] = None,
        mcap: Optional[float] = None,
        liquidity: Optional[float] = None,
        risk_score: Optional[int] = None,
        risk_level: Optional[str] = None,
        profile_name: Optional[str] = None,
        trailing_pct: Optional[float] = None,
        holders_count: Optional[int] = None,
        top_10_pct: Optional[float] = None,
        dev_pct: Optional[float] = None,
        is_lp_locked: Optional[bool] = None,
        mint_revoked: Optional[bool] = None,
        freeze_revoked: Optional[bool] = None,
        age_hours: Optional[float] = None,
        volume_24h: Optional[float] = None
    ) -> bool:
        """
        Send a rich trade notification with action buttons.
        """
        if action == "BUY":
            emoji = "🚀"
            header = "━━━━━━━━━━━━━━━━━━━━━━\n🚀 <b>NEW BUY EXECUTED</b> 🚀\n━━━━━━━━━━━━━━━━━━━━━━"
        else:
            emoji = "💸"
            header = "━━━━━━━━━━━━━━━━━━━━━━\n💸 <b>SELL EXECUTED</b> 💸\n━━━━━━━━━━━━━━━━━━━━━━"
        
        # Token info section
        token_display = token_name or mint[:12]
        symbol_display = f"${token_symbol}" if token_symbol else ""
        
        # 1. 🚀 HEADER SECTION
        text = f"{header}\n\n"
        text += f"💎 <b>Token:</b> {token_display} ({symbol_display})\n"
        text += f"📊 <b>Score:</b> <code>{risk_score or 0}/100</code> | 🛡️ <b>Level:</b> {risk_level or 'UNKNOWN'}\n"
        text += f"<code>{mint}</code>\n\n"
        
        # 2. 📈 TRADE SECTION
        text += f"📈 <b>Trade Details</b>\n"
        text += f"├ Amount: <b>{amount_sol:.4f} SOL</b>\n"
        text += f"├ Phase: <code>{phase}</code>\n"
        if volume_24h:
            text += f"├ Volume: <b>${volume_24h:,.0f}</b>\n"
        if mcap:
            text += f"└ MCap: <b>${mcap:,.0f}</b>\n\n"
        else:
            text += "└ Source: <code>Whale Action</code>\n\n"

        # 3. 👥 HOLDERS SECTION
        text += f"👥 <b>Holders Analysis</b>\n"
        dev_val = f"{dev_pct:.1f}%" if dev_pct is not None else "??%"
        top10_val = f"{top_10_pct:.1f}%" if top_10_pct is not None else "??%"
        text += f"├ Dev Hold: <b>{dev_val}</b>\n"
        text += f"├ Top 10: <b>{top10_val}</b>\n"
        if holders_count:
            text += f"└ Count: <b>{holders_count:,}</b>\n\n"
        else:
            text += "└ Status: <b>Early Discovery</b>\n\n"

        # 4. 🛡️ SECURITY SECTION
        text += f"🛡️ <b>Security Check</b>\n"
        lp_status = "✅ LOCKED" if is_lp_locked else "❌ UNLOCKED"
        mint_status = "✅ REVOKED" if mint_revoked else "❌ ACTIVE"
        freeze_status = "✅ REVOKED" if freeze_revoked else "❌ ACTIVE"
        text += f"├ LP Status: <b>{lp_status}</b>\n"
        text += f"├ Mint Auth: <b>{mint_status}</b>\n"
        text += f"└ Freeze Auth: <b>{freeze_status}</b>\n\n"

        # 5. 💧 LP & PERFORMANCE SECTION
        text += f"🧪 <b>LP & Market</b>\n"
        if liquidity:
            text += f"├ Liquidity: <b>${liquidity:,.0f}</b>\n"
        if age_hours is not None:
            age_str = f"{age_hours:.1f}h" if age_hours >= 1 else f"{int(age_hours * 60)}m"
            text += f"├ Token Age: <b>{age_str}</b>\n"
        text += f"└ Profile: <b>{profile_name or 'STANDARD'}</b>\n"
        
        if trailing_pct:
            text += f"\n⚙️ <b>Strategy:</b> <code>Trailing {trailing_pct}%</code>\n"

        # TX link
        if tx_signature and tx_signature != "SIMULATION" and tx_signature != "legacy":
            text += f"\n🔗 <a href='https://solscan.io/tx/{tx_signature}'>View on Solscan</a>"
        
        text += "\n━━━━━━━━━━━━━━━━━━━━━━"
        
        # Rich inline keyboard
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "📊 DexScreener", "url": f"https://dexscreener.com/solana/{mint}"},
                    {"text": "🛡️ Rugcheck", "url": f"https://rugcheck.xyz/tokens/{mint}"}
                ],
                [
                    {"text": "📡 Photon", "url": f"https://photon-sol.tinyastro.io/en/lp/{mint}"},
                    {"text": "🔥 BullX", "url": f"https://bullx.io/terminal?chainId=1399811149&address={mint}"}
                ],
                [
                    {"text": "🛑 FORCE SELL", "callback_data": f"sell_{mint}"}
                ]
            ]
        }
        
        return await self.send_message(text, reply_markup=keyboard)
    
    async def send_position_update(
        self,
        mint: str,
        entry_sol: float,
        current_value: float,
        pnl_pct: float,
        highest_value: float,
        trailing_stop_pct: float,
        token_name: Optional[str] = None,
        token_symbol: Optional[str] = None,
        time_held: Optional[str] = None,
        break_even_sold: bool = False
    ) -> bool:
        """Send rich position monitoring update."""
        
        # PnL visual
        if pnl_pct >= 50:
            pnl_emoji = "🚀"
        elif pnl_pct >= 20:
            pnl_emoji = "📈"
        elif pnl_pct >= 0:
            pnl_emoji = "🟢"
        elif pnl_pct >= -5:
            pnl_emoji = "🟡"
        else:
            pnl_emoji = "🔴"
        
        # Progress bar
        pnl_capped = max(-20, min(100, pnl_pct))
        progress = int((pnl_capped + 20) / 120 * 10)
        bar = "█" * progress + "░" * (10 - progress)
        
        token_display = token_name or mint[:12]
        symbol_display = f"${token_symbol}" if token_symbol else ""
        
        text = f"━━━━━━━━━━━━━━━━━━━━━━\n📍 <b>POSITION UPDATE</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"🪙 <b>{token_display}</b> {symbol_display}\n\n"
        
        text += f"💰 <b>Value</b>\n"
        text += f"├ Entry: <code>{entry_sol:.4f}</code> SOL\n"
        text += f"├ Current: <code>{current_value:.4f}</code> SOL\n"
        text += f"└ Peak: <code>{highest_value:.4f}</code> SOL\n\n"
        
        text += f"{pnl_emoji} <b>PnL: {pnl_pct:+.2f}%</b>\n"
        text += f"<code>[{bar}]</code>\n\n"
        
        if break_even_sold:
            text += f"✅ <b>Break-even sold!</b> Playing with house money\n\n"
        
        text += f"🛡️ <b>Protection</b>\n"
        text += f"└ Trailing: <b>{trailing_stop_pct}%</b> from peak\n"
        
        if time_held:
            text += f"\n⏱ Held: {time_held}\n"
        
        text += "\n━━━━━━━━━━━━━━━━━━━━━━"
        
        keyboard = {
            "inline_keyboard": [[
                {"text": "📊 DexScreener", "url": f"https://dexscreener.com/solana/{mint}"},
                {"text": "🛡️ Rugcheck", "url": f"https://rugcheck.xyz/tokens/{mint}"}
            ], [
                {"text": "📡 Photon", "url": f"https://photon-sol.tinyastro.io/en/lp/{mint}"},
                {"text": "🔥 BullX", "url": f"https://bullx.io/terminal?chainId=1399811149&address={mint}"}
            ], [
                {"text": "🛑 FORCE SELL", "callback_data": f"sell_{mint}"}
            ]]
        }
        
        return await self.send_message(text, reply_markup=keyboard, disable_notification=True)
    
    async def send_sell_alert(
        self,
        mint: str,
        reason: str,
        entry_sol: float,
        exit_sol: float,
        pnl_pct: float,
        hold_time: str,
        token_name: Optional[str] = None,
        token_symbol: Optional[str] = None,
        tx_signature: Optional[str] = None
    ) -> bool:
        """Send rich sell notification."""
        
        profit_sol = exit_sol - entry_sol
        
        if pnl_pct >= 50:
            emoji = "🎉"
            header = "━━━━━━━━━━━━━━━━━━━━━━\n🎉 <b>BIG WIN!</b> 🎉\n━━━━━━━━━━━━━━━━━━━━━━"
        elif pnl_pct >= 0:
            emoji = "✅"
            header = "━━━━━━━━━━━━━━━━━━━━━━\n✅ <b>PROFIT SECURED</b> ✅\n━━━━━━━━━━━━━━━━━━━━━━"
        else:
            emoji = "⚠️"
            header = "━━━━━━━━━━━━━━━━━━━━━━\n⚠️ <b>LOSS TAKEN</b> ⚠️\n━━━━━━━━━━━━━━━━━━━━━━"
        
        token_display = token_name or mint[:12]
        symbol_display = f"${token_symbol}" if token_symbol else ""
        
        text = f"{header}\n\n"
        text += f"🪙 <b>{token_display}</b> {symbol_display}\n\n"
        
        text += f"📊 <b>Trade Summary</b>\n"
        text += f"├ Entry: <code>{entry_sol:.4f}</code> SOL\n"
        text += f"├ Exit: <code>{exit_sol:.4f}</code> SOL\n"
        text += f"└ Reason: <code>{reason}</code>\n\n"
        
        text += f"💰 <b>Result</b>\n"
        text += f"├ PnL: <b>{pnl_pct:+.2f}%</b>\n"
        
        # Smart formatting for small numbers
        if abs(profit_sol) < 0.0001 and profit_sol != 0:
            text += f"└ Profit: <b>{profit_sol:+.8f} SOL</b>\n\n"
        else:
            text += f"└ Profit: <b>{profit_sol:+.4f} SOL</b>\n\n"
        
        text += f"⏱ Hold Time: {hold_time}\n"
        
        if tx_signature:
            text += f"\n🔗 <a href='https://solscan.io/tx/{tx_signature}'>View Transaction</a>\n"
        
        text += "\n━━━━━━━━━━━━━━━━━━━━━━"
        
        return await self.send_message(text)
    
    async def send_error_alert(self, error_type: str, details: str) -> bool:
        """Send error notification."""
        text = f"🚨 <b>ERROR</b>\n\n"
        text += f"<b>Type:</b> {error_type}\n"
        text += f"<b>Details:</b> {details}\n"
        
        return await self.send_message(text)
    
    async def check_commands(self) -> Optional[Dict[str, Any]]:
        """
        Poll for new commands from Telegram.
        
        Returns:
            Dict with command info if new command received, else None
        """
        if not self.enabled:
            return None
        
        try:
            url = f"{self.base_url}/getUpdates"
            params = {
                "offset": self.last_update_id + 1,
                "timeout": 5,
                "allowed_updates": ["message", "callback_query"]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
                    
                    if not data.get("ok") or not data.get("result"):
                        return None
                    
                    updates = data["result"]
                    if not updates:
                        return None
                    
                    # Process first update
                    update = updates[0]
                    self.last_update_id = update.get("update_id", self.last_update_id)
                    
                    # Handle text commands
                    if "message" in update:
                        message = update["message"]
                        user_id = str(message.get("from", {}).get("id", ""))
                        text = message.get("text", "")
                        
                        if text.startswith("/"):
                            command = text.split()[0][1:]  # Remove '/'
                            args = text.split()[1:] if len(text.split()) > 1 else []
                            
                            # Define admin-only commands
                            admin_commands = ["stop", "resume"]
                            is_admin_command = command in admin_commands
                            
                            # Check permissions
                            is_admin = self.admin_id and user_id == str(self.admin_id)
                            
                            # Import viewer IDs
                            try:
                                from .config import VIEWER_IDS
                            except ImportError:
                                try:
                                    from solana_bot.config import VIEWER_IDS
                                except ImportError:
                                    VIEWER_IDS = []
                            
                            is_viewer = user_id in VIEWER_IDS
                            
                            # Admin commands require admin
                            if is_admin_command and not is_admin:
                                logger.warning(f"Admin command from non-admin: user_id={user_id}, command={command}")
                                return None
                            
                            # Non-admin commands allowed for admin and viewers
                            if not is_admin and not is_viewer:
                                logger.warning(f"Command from unauthorized user: user_id={user_id}")
                                return None
                            
                            return {
                                "type": "command",
                                "command": command,
                                "args": args,
                                "chat_id": message["chat"]["id"],
                                "user_id": user_id,
                                "is_admin": is_admin
                            }
                    
                    # Handle button callbacks
                    elif "callback_query" in update:
                        callback = update["callback_query"]
                        
                        # Acknowledge callback
                        await self._answer_callback(callback["id"])
                        
                        return {
                            "type": "callback",
                            "data": callback.get("data", ""),
                            "chat_id": callback["message"]["chat"]["id"]
                        }
        
        except Exception as e:
            logger.error(f"Error checking Telegram commands: {e}")
            return None
    
    async def _answer_callback(self, callback_id: str, text: str = "Processing...") -> bool:
        """Acknowledge a callback query."""
        try:
            url = f"{self.base_url}/answerCallbackQuery"
            payload = {"callback_query_id": callback_id, "text": text}
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as resp:
                    return resp.status == 200
        except:
            return False
    
    async def send_status_summary(
        self,
        bot_version: str,
        active_positions: int,
        total_value_sol: float,
        uptime: str,
        learning_mode: bool,
        wallet_balance: float = 0.0,
        total_pnl_sol: float = 0.0,
        trades_today: int = 0,
        win_rate: float = 0.0
    ) -> bool:
        """Send rich bot status summary."""
        
        mode_emoji = "🧪" if learning_mode else "⚡"
        mode_text = "LEARNING" if learning_mode else "LIVE"
        
        # PnL color
        pnl_emoji = "🟢" if total_pnl_sol >= 0 else "🔴"
        
        text = f"━━━━━━━━━━━━━━━━━━━━━━\n🤖 <b>BOT STATUS</b>\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        text += f"⚙️ <b>System</b>\n"
        text += f"├ Version: <code>{bot_version}</code>\n"
        text += f"├ Mode: {mode_emoji} <b>{mode_text}</b>\n"
        text += f"└ Uptime: {uptime}\n\n"
        
        text += f"💰 <b>Wallet</b>\n"
        text += f"├ Balance: <b>{wallet_balance:.4f} SOL</b>\n"
        text += f"└ Positions: <b>{total_value_sol:.4f} SOL</b>\n\n"
        
        text += f"📊 <b>Trading</b>\n"
        text += f"├ Active Positions: <b>{active_positions}</b>\n"
        text += f"├ Trades Today: <b>{trades_today}</b>\n"
        text += f"├ Win Rate: <b>{win_rate:.1f}%</b>\n"
        text += f"└ {pnl_emoji} Total PnL: <b>{total_pnl_sol:+.4f} SOL</b>\n\n"
        
        text += "━━━━━━━━━━━━━━━━━━━━━━"
        
        return await self.send_message(text)

