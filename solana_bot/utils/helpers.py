
import hashlib
from typing import Dict, Any
from ..constants import BASE_SLIPPAGE_BPS, MAX_SLIPPAGE_BPS

def anchor_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]

def sane_reserves(token_reserves: int, sol_reserves: int) -> bool:
    return 0 < token_reserves < 10**30 and 0 < sol_reserves < 10**20

def calculate_real_pnl(tokens: int, entry: float, value: float, decimals: int) -> Dict[str, float]:
    human = tokens / (10 ** decimals)
    pnl_pct = ((value - entry) / entry) * 100 if entry > 0 else 0
    return {"tokens_human": human, "pnl_pct": pnl_pct, "pnl_sol": value - entry}

def calculate_dynamic_slippage(sol_reserves: int, trade_sol: float) -> int:
    sol = sol_reserves / 1e9
    ratio = trade_sol / max(sol, 0.01)
    base = BASE_SLIPPAGE_BPS if sol >= 100 else 400 if sol >= 50 else 600 if sol >= 10 else 800 if sol >= 5 else 1000
    if ratio > 0.10:
        base = int(base * 1.5)
    elif ratio > 0.05:
        base = int(base * 1.2)
    return min(base, MAX_SLIPPAGE_BPS)

def clean_console(text: str) -> str:
    replacements = {
        "🚀": "[BUY]", "💰": "[WIN]", "✅": "[OK]", "❌": "[ERR]",
        "⚠️": "[WARN]", "🛡️": "[RISK]", "🤖": "[BOT]", "🔧": "[CFG]",
        "👀": "[WATCH]", "🚨": "[ALERT]", "⏳": "[WAIT]", "⏭️": "[SKIP]",
        "⛔": "[STOP]", "📍": "[POS]", "💸": "[SELL]", "🪐": "[JUP]",
        "📚": "[TEST]", "🛑": "[SL]", "💵": "[$]", "📊": "[SCR]",
        "💧": "[LIQ]", "🔗": "[LNK]", "🟢": "[+]", "🔴": "[-]",
        "🧠": "[SMART]", "🏎️": "[FAST]", "📈": "[UP]", "📉": "[DOWN]",
        "🧪": "[SIM]"
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text

def get_token_balance_from_list(balances: list, mint_addr: str, owner_addr: str) -> float:
    """Helper for calculate_transaction_flow"""
    for b in balances:
        # Handle object or dict if necessary, assuming object based on original code usage
        if getattr(b, "mint", None) == mint_addr and getattr(b, "owner", None) == owner_addr:
             return float(b.ui_token_amount.amount or 0)
    return 0.0

def calculate_transaction_flow(tx_value: Any, wallet: str, mint: str) -> Dict[str, float]:
    """
    Robustly calculate flow of SOL, WSOL, USDC, and Target Token for a wallet.
    Returns: {'native_sol': float, 'wsol': float, 'usdc': float, 'token': float}
    """
    flow = {"native_sol": 0.0, "wsol": 0.0, "usdc": 0.0, "token": 0.0}
    try:
        if not hasattr(tx_value, 'transaction') or not hasattr(tx_value.transaction, 'meta'):
             return flow
             
        meta = tx_value.transaction.meta
        if not meta:
            return flow
            
        message = tx_value.transaction.transaction.message
        
        # Helper to match wallet in account_keys
        def get_pk(k):
            if hasattr(k, "pubkey"): return str(k.pubkey)
            return str(k)
        
        account_keys = [get_pk(k) for k in message.account_keys]
        try:
            wallet_idx = account_keys.index(wallet)
        except ValueError:
            return flow
            
        # Calc Native SOL Delta
        pre_bal = meta.pre_balances[wallet_idx]
        post_bal = meta.post_balances[wallet_idx]
        flow["native_sol"] = (post_bal - pre_bal) / 1e9
        
        # Calc WSOL Delta (Mint: So111...)
        wsol_mint = "So11111111111111111111111111111111111111112"
        pre_wsol = get_token_balance_from_list(meta.pre_token_balances, wsol_mint, wallet)
        post_wsol = get_token_balance_from_list(meta.post_token_balances, wsol_mint, wallet)
        flow["wsol"] = (post_wsol - pre_wsol) / 1e9
        
        # Calc USDC Delta (Mint: EPjFW...)
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        pre_usdc = get_token_balance_from_list(meta.pre_token_balances, usdc_mint, wallet)
        post_usdc = get_token_balance_from_list(meta.post_token_balances, usdc_mint, wallet)
        flow["usdc"] = (post_usdc - pre_usdc) / 1e6
        
        # Calc Target Token Delta
        pre_token = get_token_balance_from_list(meta.pre_token_balances, mint, wallet)
        post_token = get_token_balance_from_list(meta.post_token_balances, mint, wallet)
        flow["token"] = (post_token - pre_token)
        
        return flow
        
    except Exception as e: 
        # print(f"Flow Calc Err: {e}") 
        return flow

def format_trade_alert(data: Dict[str, Any]) -> str:
    """Format a trade alert message for Telegram/Logging."""
    symbol = data.get("symbol", "???")
    action = data.get("action", "TRADE")
    amount = data.get("amount", 0.0)
    price = data.get("price", 0.0)
    pnl = data.get("pnl_pct", 0.0)
    
    emoji = "🚀" if action == "BUY" else "💸" if action == "SELL" else "📊"
    pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
    
    msg = f"{emoji} {action} {symbol}\n"
    msg += f"Amount: {amount:.4f} SOL\n"
    if price > 0:
        msg += f"Price: ${price:.8f}\n"
    if pnl != 0:
        msg += f"PnL: {pnl_emoji} {pnl:+.2f}%\n"
    
    return msg
