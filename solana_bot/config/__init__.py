"""Config package"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from .risk_config import (
    RiskConfig,
    RiskConfigManager,
    get_risk_config,
    get_config_manager,
    PositionLimits,
    StopLossConfig,
    TakeProfitConfig,
    TokenFilters,
    JitoConfig,
    TelegramConfig
)

# ============================================
# CREDENTIALS & ENDPOINTS
# ============================================
PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")
RPC_URL = os.getenv("RPC_URL")
WSS_URL = RPC_URL.replace("https", "wss") if RPC_URL else None
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ADMIN_ID = os.getenv("ADMIN_ID")

# Birdeye API for historical data  
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
BIRDEYE_API_URL = "https://public-api.birdeye.so"

# Viewer IDs - Can execute read-only commands like /status, /positions
# but NOT admin commands like /stop, /resume
VIEWER_IDS = ["1768337867", "359521680"]

# Import constants from constants.py
# Note: Using ..constants because this file is in solana_bot/config/ package
from ..constants import (
    PUMP_PROGRAM, PUMP_FEE, PUMP_GLOBAL, EVENT_AUTH,
    PUMP_AMM_PROGRAM, PUMP_AMM_FEE, RAYDIUM_V4_PROGRAM, OPENBOOK_PROGRAM,
    SYSTEM_PROGRAM, TOKEN_PROGRAM, TOKEN_2022_PROGRAM, RENT_PROGRAM,
    ASSOC_TOKEN_ACC_PROG, WSOL_MINT, JITO_TIPS, JITO_URL,
    JUPITER_QUOTE_API, JUPITER_SWAP_API
)

# ============================================
# STRATEGY PARAMETERS
# ============================================

# 🎯 OPTIMIZATION FLAGS
ENABLE_AGGRESSIVE_SELL_RETRY = os.getenv("ENABLE_AGGRESSIVE_SELL_RETRY", "False").lower() == "true"
# ⚠️ If True, enables old multi-DEX retry logic (slower, more expensive, but safer if Jupiter is down)
# Default: False (uses smart mode: native DEX → Jupiter fallback)

# ============================================
# RISK MANAGEMENT (GLOBAL)
# ============================================
MAX_POSITIONS = 3           # Max concurrent positions
STOP_LOSS_PCT = 15.0        # Hard stop loss %
TAKE_PROFIT_PCT = 50.0      # Take profit target %
TRAILING_STOP_PCT = 1.6     # Trailing stop trigger %

MAX_CONSECUTIVE_LOSSES = 2  # Cooldown after 2 losses
COOLDOWN_SECONDS = 300      # 5 min cooldown

# NEW: Risk limits
MAX_DAILY_LOSS_SOL = 1.0    # Max daily loss in SOL
MAX_DAILY_LOSS_PCT = 10.0   # Max daily loss % of balance
MAX_DAILY_TRADES = 50       # Max trades per day
MIN_RESERVE_SOL = 0.05      # Min SOL to keep for fees
MAX_TRADE_PCT_OF_BALANCE = 20.0 # Max 20% of balance per trade

# Trading Features
ONE_SHOT_MODE = False       # If True, stops after 1 trade (good for testing)

# ============================================
# TRAILING STOP CONFIG
# ============================================
TRAILING_STOP_PCT = 1.6  # 1.6% trailing stop

# ============================================
# SCALPING STRATEGY CONFIG
# ============================================
SCALPING_ENABLED = True
SCALPING_RSI_OVERSOLD = 30
SCALPING_RSI_OVERBOUGHT = 70
SCALPING_VOLUME_MULTIPLIER = 2.0
SCALPING_MIN_LIQUIDITY = 100    # Lowered to find more tokens
SCALPING_MIN_VOLUME = 100       # Lowered to find more tokens

# ============================================
# PAPER TRADING CONFIG
# ============================================
# Default to True for safety if env var missing
PAPER_TRADING_MODE = os.getenv("PAPER_TRADING_MODE", "True").lower() == "true"
PAPER_INITIAL_BALANCE = 10.0 # SOL

# Anti-Rug Filters
MIN_LIQUIDITY_SOL = 5           # Lowered: Min 5 SOL liquidity
MAX_TOP_10_HOLDERS_PCT = 30 # Max % held by top 10 (excluding bonding curve)
MIN_MINT_AGE_HOURS = 0      # Allow fresh tokens (0 hours)
BURN_AUTHORITY_CHECK = False # Optional check
MINT_AUTHORITY_CHECK = True  # Reject if mint authority enabled

# ============================================
# JITO MEV PROTECTION
# ============================================
JITO_REGION = "frankfurt"   # amsterdam, frankfurt, ny, tokyo
USE_JITO = True             # Enable Jito bundles
JITO_TIP_SOL = 0.001        # Tip amount

# ============================================
# QUICK SCALP STRATEGY (Super Early Tokens)
# ============================================
# 🎯 Target: Very fresh tokens for 50-100% pumps in minutes

# BUY SETTINGS
EARLY_TOKEN_BUY_AMOUNT = 0.05       # Position size (Updated to match EARLY_STRATEGY)
EARLY_TOKEN_MIN_LIQUIDITY_SOL = 5   # Min 5 SOL liquidity
EARLY_TOKEN_MAX_LIQUIDITY_SOL = 100 # Max 100 SOL

# AGE FILTERS (in minutes)
# Webhook tokens: 1-5 min (super fresh from pump.fun)
# DexScreener tokens: use their own age data
EARLY_TOKEN_MIN_AGE_MINUTES = 1     # Min 1 minute (basic safety)
EARLY_TOKEN_MAX_AGE_MINUTES = 30    # Max 30 minutes (for DexScreener tokens)

# DYNAMIC BREAK-EVEN (based on fees, not fixed %)
# BE = entry_sol + estimated_fees
ESTIMATED_SLIPPAGE_PCT = 2.0        # ~2% total slippage (entry + exit)
JITO_TIP_PER_TX = 0.001             # Jito tip per transaction
URGENT_JITO_TIP = 0.003             # Higher tip for urgent/emergency sells (3x normal)
LIQUIDITY_DROP_THRESHOLD_PCT = 20.0 # Emergency sell if LP drops >20%
# Calculated: BE triggers when value >= entry * (1 + slippage%) + 2*tips

# TRAILING STOP (after BE)
TRAILING_STOP_AFTER_BE_PCT = 30.0   # 30% trailing - more room for volatility
HARD_STOP_LOSS_PCT = -50.0          # -50% stop (tighter than before)

# TAKE PROFIT TIERS (Partial sells to lock in gains)
# Format: [(pnl_threshold_pct, sell_pct), ...]
TAKE_PROFIT_TIERS = [
    (50.0, 30.0),   # Sell 30% at +50%
    (100.0, 30.0),  # Sell 30% at +100%
    (200.0, 20.0),  # Sell 20% at +200%
    # Remaining 20% = moonbag with trailing
]

# TIME LIMITS
MAX_HOLD_TIME_MINUTES = 10          # Sell after 10 min regardless
NO_PUMP_TIMEOUT_MINUTES = 2         # If no gain in 2 min, sell EARLY tokens quickly
NO_PUMP_MIN_GAIN_PCT = 5.0          # Minimum gain required within timeout

# ANTI-RUG FILTERS
EARLY_MIN_HOLDERS = 10              # Reduced for fresh tokens
EARLY_MAX_DEV_HOLDINGS_PCT = 15     # Slightly relaxed
EARLY_MIN_VOLUME_5M = 100           # Lower for fresh tokens
MIN_BONDING_CURVE_PROGRESS = 1.5    # Min % progress to avoid stagnant tokens (User requested)

# PHASE TARGETING - Include Jupiter for STABLE strategy tokens
TARGET_PHASES = ["BONDING_CURVE", "PUMPSWAP", "RAYDIUM", "JUPITER"]

# ============================================
# RUGCHECK SAFETY THRESHOLDS (Configurable)
# ============================================
# These thresholds control when tokens are rejected by rugcheck.
# Override via environment variables: BOT_MAX_TOP_HOLDER_PCT, BOT_MIN_SAFETY_SCORE, etc.

# EARLY mode thresholds (fresh pump.fun tokens on bonding curve)
EARLY_MAX_TOP_HOLDER_PCT = float(os.getenv("BOT_EARLY_MAX_TOP_HOLDER_PCT", "20.0"))
EARLY_MAX_TOP3_HOLDERS_PCT = float(os.getenv("BOT_EARLY_MAX_TOP3_HOLDERS_PCT", "50.0"))
EARLY_MAX_DEV_PCT = float(os.getenv("BOT_EARLY_MAX_DEV_PCT", "15.0"))
EARLY_MIN_LIQUIDITY_USD = float(os.getenv("BOT_EARLY_MIN_LIQUIDITY_USD", "1000"))
EARLY_MAX_SAFE_SCORE = int(os.getenv("BOT_EARLY_MAX_SAFE_SCORE", "49"))

# STABLE mode thresholds (mature Jupiter tokens)
STABLE_MAX_TOP_HOLDER_PCT = float(os.getenv("BOT_STABLE_MAX_TOP_HOLDER_PCT", "10.0"))
STABLE_MAX_TOP3_HOLDERS_PCT = float(os.getenv("BOT_STABLE_MAX_TOP3_HOLDERS_PCT", "30.0"))
STABLE_MAX_DEV_PCT = float(os.getenv("BOT_STABLE_MAX_DEV_PCT", "5.0"))
STABLE_MIN_LIQUIDITY_USD = float(os.getenv("BOT_STABLE_MIN_LIQUIDITY_USD", "5000"))
STABLE_MAX_SAFE_SCORE = int(os.getenv("BOT_STABLE_MAX_SAFE_SCORE", "39"))

# Uncomment for all phases:

# Legacy config for backward compatibility
FRESH_TOKEN_MAX_AGE_HOURS = 24
FRESH_TOKEN_MAX_MC = 500000
FRESH_BUY_AMOUNT = EARLY_TOKEN_BUY_AMOUNT
FRESH_TRAILING_STOP = TRAILING_STOP_AFTER_BE_PCT
MATURE_BUY_AMOUNT = 1.0
MATURE_TRAILING_STOP = 2.5

# ============================================
# DUAL STRATEGY CONFIG
# ============================================
# EARLY: Fresh tokens from webhook (high volatility, moonbag potential)
EARLY_STRATEGY = {
    "trailing_stop_pct": 10.0,
    "hard_stop_pct": -25.0,         # FIXED: -60% was incoherent with asymmetry entry
    "max_hold_minutes": 0,          # DISABLED - No time limit for moonbag potential
    "buy_amount_sol": 0.05,         # Smaller position (risk reduction)
    "take_profit_pct": 50.0,
    "no_pump_timeout_min": 2,       # Exit if no pump in 2 min (User requested strict 2m)
}

# STABLE: Mature tokens (low volatility, small gains, higher entry)
STABLE_STRATEGY = {
    "trailing_stop_pct": 3.0,
    "hard_stop_pct": -10.0,         # FIXED: Tighter for stable tokens
    "max_hold_minutes": 15,         # Shorter hold for stable too
    "buy_amount_sol": 0.3,          # Bigger position (lower risk tokens)
    "take_profit_pct": 15.0,
    "no_pump_timeout_min": 5,       # Exit if no +5% in 5 min
}

# Stability detection thresholds
STABLE_MIN_MCAP_USD = 100_000      # MCap >= $100k
STABLE_MIN_LIQUIDITY_USD = 50_000  # Liquidity >= $50k
STABLE_MIN_AGE_HOURS = 1.0         # Age >= 1 hour

# WHALE WALLETS - Follow these addresses for copy-trading
# Add wallet addresses to this list (e.g. profitable whales or leader wallets)
WHALE_WALLETS = [
    "5C5RjP6bQWy2KkEJ9YCZcf64aXmSMX42tX1tR1Uj7wP2",
    "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
    "GijFWw4oNyh9ko3FaZforNsi3jk6wDovARpkKahPD4o5",
    "7BNaxx6KdUYrjACNQZ9He26NBFoFxujQMAfNLnArLGH5",
    "5aLY85pyxiuX3fd4RgM3Yc1e3MAL6b7UgaZz6MS3JUfG",
    "Ez2jp3rwXUbaTx7XwiHGaWVgTPFdzJoSg8TopqbxfaJN",
    "EfoagTdRY1TJd5qBLt8zkDpyx2oCgKXgUWX5CDMzW5e",
    "Be24Gbf5KisDk1LcWWZsBn8dvB816By7YzYF5zWZnRR6",
    "9wRuFPJZFviuHv9q4hsaxUUADDphX6oSjcMA3RuxTFRG",
    "EcFpwMCyrdNTe8dotWwQqfNrQaXQSGMPFQkH3n3gdoPv"
]

# MINIMUM SOL amount for a whale trade to be considered "significant"
WHALE_MIN_BUY_SOL = 0.5 

# ============================================
# CONVEX STRATEGY CONFIG (SCOUT → CONFIRM → CONVICTION)
# ============================================
# Philosophy: Pay small "scouting fees" to discover living tokens,
# then add size only when Selection proves the token is alive.

# Entry sizes per phase
CONVEX_SCOUT_SIZE_SOL = float(os.getenv("CONVEX_SCOUT_SIZE_SOL", "0.01"))       # Micro-entry for discovery
CONVEX_CONFIRM_SIZE_SOL = float(os.getenv("CONVEX_CONFIRM_SIZE_SOL", "0.04"))  # Add on selection
CONVEX_MAX_TOTAL_SOL = float(os.getenv("CONVEX_MAX_TOTAL_SOL", "0.15"))        # Max total commitment

# Scout evaluation settings
CONVEX_SCOUT_TIMEOUT_SEC = int(os.getenv("CONVEX_SCOUT_TIMEOUT_SEC", "180"))   # 3 min default
CONVEX_BASELINE_CAPTURE_SEC = int(os.getenv("CONVEX_BASELINE_CAPTURE_SEC", "45"))  # Baseline window
CONVEX_EVAL_WINDOW_SEC = int(os.getenv("CONVEX_EVAL_WINDOW_SEC", "30"))        # Selection eval window

# Selection thresholds
CONVEX_SELECTION_THRESHOLD = int(os.getenv("CONVEX_SELECTION_THRESHOLD", "2"))     # Min signals (0-5)
CONVEX_SELECTION_WINDOWS = int(os.getenv("CONVEX_SELECTION_WINDOWS", "2"))         # Consecutive windows needed

# Enable/disable convex mode
CONVEX_MODE_ENABLED = os.getenv("CONVEX_MODE_ENABLED", "True").lower() == "true"
