from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html import escape
from typing import Any

import httpx

from solana_bot.config import Settings
from solana_bot.core.models import BotStats, Position, RugcheckResult, TokenInfo


@dataclass(frozen=True)
class TelegramAction:
    kind: str
    mint: str | None = None
    user_id: int | None = None
    chat_id: int | str | None = None
    callback_id: str | None = None


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = settings.TELEGRAM_ENABLED and bool(self.token and self.chat_id)
        self.client = httpx.AsyncClient(timeout=settings.API_TIMEOUT_SEC)
        self.logger = logging.getLogger("solana_bot.telegram")
        self._last_update_id = 0
        self._last_poll_ts = 0.0

    async def close(self) -> None:
        await self.client.aclose()

    async def send_message(self, text: str, buttons: list[list[dict[str, Any]]] | None = None) -> None:
        if not self.enabled:
            return
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        await self._post("sendMessage", payload)

    async def send_trade_event(
        self,
        event: str,
        position: Position,
        rug: RugcheckResult | None = None,
        reason: str | None = None,
        pnl_pct: float | None = None,
        sol_price_eur: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        text = build_trade_message(event, position, rug, reason, pnl_pct, sol_price_eur)
        buttons = build_buttons(self.settings, position.token)
        await self.send_message(text, buttons)

    async def send_status(self, stats: BotStats, positions: dict[str, Position]) -> None:
        if not self.enabled:
            return
        text = build_status_message(stats, positions)
        await self.send_message(text)

    async def poll_actions(self, now: float) -> list[TelegramAction]:
        if not self.enabled or not self.settings.TELEGRAM_POLL_UPDATES:
            return []
        if now - self._last_poll_ts < self.settings.TELEGRAM_POLL_INTERVAL_SEC:
            return []
        self._last_poll_ts = now
        updates = await self._get_updates()
        actions: list[TelegramAction] = []
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int) and update_id >= self._last_update_id:
                self._last_update_id = update_id + 1
            if "callback_query" in update:
                action = self._handle_callback(update["callback_query"])
                if action:
                    actions.append(action)
            if "message" in update:
                action = self._handle_message(update["message"])
                if action:
                    actions.append(action)
        return actions

    def _handle_callback(self, payload: dict[str, Any]) -> TelegramAction | None:
        data = payload.get("data")
        callback_id = payload.get("id")
        message = payload.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        user_id = (payload.get("from") or {}).get("id")
        if not self._is_allowed_chat(chat_id):
            return None
        if isinstance(data, str) and data.startswith("force_sell:"):
            mint = data.split(":", 1)[1]
            if callback_id:
                asyncio.create_task(
                    self._post(
                        "answerCallbackQuery",
                        {"callback_query_id": callback_id, "text": "Force sell richiesto"},
                    )
                )
            return TelegramAction(
                kind="force_sell",
                mint=mint,
                user_id=user_id,
                chat_id=chat_id,
                callback_id=callback_id,
            )
        return None

    def _handle_message(self, payload: dict[str, Any]) -> TelegramAction | None:
        text = payload.get("text")
        if not isinstance(text, str):
            return None
        chat_id = (payload.get("chat") or {}).get("id")
        user_id = (payload.get("from") or {}).get("id")
        if not self._is_allowed_chat(chat_id):
            return None
        if text.startswith("/sell "):
            mint = text.split(" ", 1)[1].strip()
            if mint:
                return TelegramAction(kind="force_sell", mint=mint, user_id=user_id, chat_id=chat_id)
        if text.strip() == "/status":
            return TelegramAction(kind="status", user_id=user_id, chat_id=chat_id)
        if text.strip() == "/start_bot":
            return TelegramAction(kind="start_bot", user_id=user_id, chat_id=chat_id)
        if text.strip() == "/stop_bot":
            return TelegramAction(kind="stop_bot", user_id=user_id, chat_id=chat_id)
        if text.strip() == "/restart_bot":
            return TelegramAction(kind="restart_bot", user_id=user_id, chat_id=chat_id)
        return None

    def _is_allowed_chat(self, chat_id: int | str | None) -> bool:
        if chat_id is None:
            return False
        return str(chat_id) == str(self.chat_id)

    async def _get_updates(self) -> list[dict[str, Any]]:
        payload = {"timeout": 0, "offset": self._last_update_id}
        data = await self._post("getUpdates", payload, method_type="get")
        if isinstance(data, dict) and isinstance(data.get("result"), list):
            return data["result"]
        return []

    async def _post(self, method: str, payload: dict[str, Any], method_type: str = "post") -> Any:
        if not self.enabled:
            return {}
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            if method_type == "get":
                response = await self.client.get(url, params=payload)
            else:
                response = await self.client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            self.logger.warning("Telegram %s failed: %s", method, exc)
            return {}


def build_buttons(settings: Settings, token: TokenInfo) -> list[list[dict[str, Any]]]:
    mint = token.mint
    pair = token.metadata.get("pair_address") or mint
    buttons: list[list[dict[str, Any]]] = []

    def add_pair_row(left: tuple[str, str], right: tuple[str, str]) -> None:
        row = []
        if left[1]:
            row.append({"text": left[0], "url": left[1]})
        if right[1]:
            row.append({"text": right[0], "url": right[1]})
        if row:
            buttons.append(row)

    dex = settings.TELEGRAM_LINK_DEXSCREENER.format(mint=mint, pair_or_mint=pair)
    rug = settings.TELEGRAM_LINK_RUGCHECK.format(mint=mint)
    solscan = settings.TELEGRAM_LINK_SOLSCAN.format(mint=mint)
    photon = settings.TELEGRAM_LINK_PHOTON.format(mint=mint)
    bullx = settings.TELEGRAM_LINK_BULLX.format(mint=mint)
    gmgn = settings.TELEGRAM_LINK_GMGN.format(mint=mint)

    add_pair_row(("DexScreener", dex), ("RugCheck", rug))
    add_pair_row(("Photon", photon), ("BullX", bullx))
    add_pair_row(("GMGN", gmgn), ("Solscan", solscan))

    if settings.TELEGRAM_ENABLE_FORCE_SELL:
        buttons.append([{"text": "FORCE SELL", "callback_data": f"force_sell:{mint}"}])

    return buttons


def build_trade_message(
    event: str,
    position: Position,
    rug: RugcheckResult | None = None,
    reason: str | None = None,
    pnl_pct: float | None = None,
    sol_price_eur: float | None = None,
) -> str:
    token = position.token
    
    # --- HEADER ---
    header_map = {
        "SCOUT_OPEN": "🔭 <b>NUOVO SCOUT ENTRY</b>",
        "STATE_CHANGE": "🔄 <b>AGGIORNAMENTO STATO</b>",
        "EXIT": "🚪 <b>USCITA POSIZIONE</b>",
        "SCOUT_STOP": "🛑 <b>STOP LOSS (Scout)</b>",
        "TRAILING_STOP": "📉 <b>TRAILING STOP</b>",
        "TAKE_PROFIT": "💰 <b>TAKE PROFIT</b>",
        "COPY_TRAILING_ARMED": "🎯 <b>COPY TRAILING ATTIVATO</b>",
        "COPY_TRAILING_STOP": "📉 <b>COPY TRAILING STOP</b>",
        "COPY_EMERGENCY_STOP": "🛑 <b>COPY EMERGENCY STOP</b>",
    }
    header = header_map.get(event, f"🔔 <b>{event}</b>")
    
    # Check if this is a copy trade and get leader info
    is_copy_trade = token.metadata.get("is_copy_trade", False)
    copy_leader = token.metadata.get("copy_leader", "")
    
    # Add COPY indicator to header if it's a copy trade
    if is_copy_trade and event == "SCOUT_OPEN":
        header = f"📡 <b>COPY TRADE ENTRY</b>"

    # --- DATA PREP ---
    age_min = int(token.age_sec / 60) if token.age_sec else 0
    mcap = _format_usd(token.metadata.get("market_cap") or token.metadata.get("fdv"))
    liq = _format_usd(token.liquidity_usd)
    vol_5m = _format_usd(token.metadata.get("volume_m5", 0.0))
    
    phase = token.phase.value if token.phase else "UNKNOWN"
    symbol = escape(token.symbol or "UNKNOWN")
    mint = escape(token.mint or "")
    safe_mint = f"{mint[:4]}...{mint[-4:]}"

    entry = position.entry_price or 0.0
    last = position.last_price or 0.0
    
    if pnl_pct is None and entry:
        pnl_pct = (last / entry) - 1.0
    
    pnl_str = _format_pct(pnl_pct, scale=100.0)
    pnl_emoji = "🟢" if (pnl_pct or 0) >= 0 else "🔴"
    if (pnl_pct or 0) > 1.0: pnl_emoji = "🚀"
    
    # --- MESSAGE BODY ---
    lines = [
        f"{header}",
        f"💎 <b>{symbol}</b> | <code>{mint}</code>",
    ]
    
    # Add copy leader info if applicable
    if is_copy_trade and copy_leader:
        lines.append(f"👤 <b>Copiato da:</b> {escape(copy_leader)}")
    
    lines.extend([
        "",
        f"📊 <b>Posizione</b>",
        f"• Stato: <b>{position.state.value}</b> ({phase})",
        f"• Size: <b>{position.size_sol:.3f} SOL</b>",
        f"• Entry: {entry:.8f} SOL",
        f"• Last:  {last:.8f} SOL",
        f"• {pnl_emoji} <b>PnL: {pnl_str}</b>{_format_pnl_absolute(position.size_sol, pnl_pct, sol_price_eur)}",
        "",
        f"📉 <b>Market Data</b>",
        f"• MCap: {mcap} | Liq: {liq}",
        f"• Vol 5m: {vol_5m} | Age: {age_min}m",
    ])

    # Momentum
    price_change_5m = _format_pct(token.metadata.get("price_change_m5", 0.0), scale=1.0)
    buys_5m = int(token.metadata.get("txns_m5_buys", 0))
    sells_5m = int(token.metadata.get("txns_m5_sells", 0))
    
    lines.extend([
        "",
        f"⚡ <b>Momentum (5m)</b>",
        f"• Price: {price_change_5m}",
        f"• Txns: 🟩 {buys_5m} / 🟥 {sells_5m}"
    ])

    if rug:
        risk_score = rug.risk_score
        risk_emoji = "✅" if risk_score < 10 else "⚠️" if risk_score < 30 else "⛔"
        dev_holding = _format_pct(token.metadata.get("dev_holding"), scale=100.0)
        top10 = _format_pct(token.metadata.get("top10_holding"), scale=100.0)
        
        lines.extend([
            "",
            f"🛡️ <b>Sicurezza</b>",
            f"• Risk Score: {risk_score}/100 {risk_emoji}",
            f"• Dev: {dev_holding} | Top10: {top10}"
        ])

    if reason:
        lines.append("")
        lines.append(f"📝 <b>Note:</b> {escape(reason)}")

    return "\n".join(lines)


def build_status_message(stats: BotStats, positions: dict[str, Position]) -> str:
    closed = stats.trades_won + stats.trades_lost
    win_rate = (stats.trades_won / closed) * 100 if closed else 0.0
    
    pnl_total = stats.realized_pnl_sol
    pnl_emoji = "🟢" if pnl_total >= 0 else "🔴"
    if pnl_total > 1.0: pnl_emoji = "🚀"

    lines = [
        "🤖 <b>SOLANA BOT STATUS</b>",
        "",
        "📊 <b>Performance Sessione</b>",
        f"💰 Wallet: <b>{stats.cash_sol:.4f} SOL</b>",
        f"{pnl_emoji} PnL Tot: <b>{_format_sol(pnl_total)}</b>",
        f"🎯 Win Rate: <b>{win_rate:.1f}%</b> ({stats.trades_won}W / {stats.trades_lost}L)",
        f"📉 Trades Closed: {closed} (Ops: {stats.daily_trades})",
        "",
        f"📌 <b>Posizioni Aperte ({len(positions)})</b>",
    ]

    if positions:
        for position in list(positions.values())[:8]:
            entry = position.entry_price or 0.0
            last = position.last_price or 0.0
            pnl = (last / entry) - 1.0 if entry else 0.0
            symbol = escape(position.token.symbol or "UNKNOWN")
            
            pnl_emo = "🟢" if pnl >= 0 else "🔴"
            if pnl > 0.5: pnl_emo = "🚀"
            
            lines.append(
                f"{pnl_emo} <b>{symbol}</b> ({position.state.value})\n"
                f"   └ {_format_pct(pnl)} | {position.size_sol:.3f} SOL"
            )

    if len(positions) > 8:
        lines.append(f"<i>...e altre {len(positions)-8} posizioni</i>")

    return "\n".join(lines)


def _format_usd(value: object) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "$0"
    if val >= 1_000_000:
        return f"${val/1_000_000:.2f}M"
    if val >= 1_000:
        return f"${val/1_000:.1f}K"
    return f"${val:.0f}"


def _format_sol(value: object) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "0.0000 SOL"
    return f"{val:+.4f} SOL"


def _format_pct(value: object, scale: float = 100.0) -> str:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "0.0%"
    return f"{val * scale:+.1f}%"


def _format_pnl_absolute(size_sol: float, pnl_pct: float | None, sol_price_eur: float | None) -> str:
    """Format PnL in absolute SOL and EUR values, e.g., ' (+0.05 SOL / +80€)'"""
    if pnl_pct is None:
        return ""
    
    # Calculate profit in SOL
    pnl_sol = size_sol * pnl_pct
    
    # Default SOL price if not provided (approximate)
    if sol_price_eur is None:
        sol_price_eur = 200.0  # Fallback EUR price
    
    pnl_eur = pnl_sol * sol_price_eur
    
    return f" ({pnl_sol:+.3f} SOL / {pnl_eur:+.0f}€)"

