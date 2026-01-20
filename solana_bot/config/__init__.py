from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv is None:
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path)


_load_env()


def _env_str(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    return int(value) if value is not None and value != "" else default


def _env_float(key: str, default: float) -> float:
    value = os.getenv(key)
    return float(value) if value is not None and value != "" else default


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    # Core
    SOLANA_PRIVATE_KEY: str = _env_str("SOLANA_PRIVATE_KEY", "")
    RPC_URL: str = _env_str("RPC_URL", "")

    # Telegram
    TELEGRAM_BOT_TOKEN: str = _env_str("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = _env_str("TELEGRAM_CHAT_ID", "")
    TELEGRAM_ENABLED: bool = _env_bool("TELEGRAM_ENABLED", True)
    TELEGRAM_POLL_UPDATES: bool = _env_bool("TELEGRAM_POLL_UPDATES", True)
    TELEGRAM_POLL_INTERVAL_SEC: float = _env_float("TELEGRAM_POLL_INTERVAL_SEC", 3.0)
    TELEGRAM_ENABLE_FORCE_SELL: bool = _env_bool("TELEGRAM_ENABLE_FORCE_SELL", True)
    TELEGRAM_LINK_DEXSCREENER: str = _env_str(
        "TELEGRAM_LINK_DEXSCREENER", "https://dexscreener.com/solana/{pair_or_mint}"
    )
    
    # Jupiter API
    JUPITER_API_KEY: str = _env_str("JUPITER_API_KEY", "")
    TELEGRAM_LINK_RUGCHECK: str = _env_str(
        "TELEGRAM_LINK_RUGCHECK", "https://rugcheck.xyz/tokens/{mint}"
    )
    TELEGRAM_LINK_SOLSCAN: str = _env_str(
        "TELEGRAM_LINK_SOLSCAN", "https://solscan.io/token/{mint}"
    )
    TELEGRAM_LINK_PHOTON: str = _env_str("TELEGRAM_LINK_PHOTON", "")
    TELEGRAM_LINK_BULLX: str = _env_str("TELEGRAM_LINK_BULLX", "")
    TELEGRAM_LINK_GMGN: str = _env_str("TELEGRAM_LINK_GMGN", "")

    # Trading mode
    PAPER_TRADING_MODE: bool = _env_bool("PAPER_TRADING_MODE", True)
    CONVEX_MODE_ENABLED: bool = _env_bool("CONVEX_MODE_ENABLED", True)

    # Convex sizing
    CONVEX_SCOUT_SIZE_SOL: float = _env_float("CONVEX_SCOUT_SIZE_SOL", 0.01)
    CONVEX_CONFIRM_SIZE_SOL: float = _env_float("CONVEX_CONFIRM_SIZE_SOL", 0.04)
    CONVEX_MAX_TOTAL_SOL: float = _env_float("CONVEX_MAX_TOTAL_SOL", 0.15)

    # Convex thresholds
    CONVEX_SCOUT_TIMEOUT_SEC: int = _env_int("CONVEX_SCOUT_TIMEOUT_SEC", 180)
    CONVEX_SELECTION_THRESHOLD: int = _env_int("CONVEX_SELECTION_THRESHOLD", 2)
    CONFIRM_TO_CONVICTION_PNL_PCT: float = _env_float(
        "CONFIRM_TO_CONVICTION_PNL_PCT", 0.1
    )
    CONVICTION_SCORE_THRESHOLD: int = _env_int("CONVICTION_SCORE_THRESHOLD", 4)
    CONVICTION_CONSECUTIVE_WINDOWS: int = _env_int("CONVICTION_CONSECUTIVE_WINDOWS", 2)

    # Risk limits
    MAX_POSITIONS: int = _env_int("MAX_POSITIONS", 3)
    MAX_DAILY_LOSS_SOL: float = _env_float("MAX_DAILY_LOSS_SOL", 1.0)
    MAX_DAILY_TRADES: int = _env_int("MAX_DAILY_TRADES", 50)
    MIN_RESERVE_SOL: float = _env_float("MIN_RESERVE_SOL", 0.05)

    # Regime filter
    MAX_CONCURRENT_SCOUTS: int = _env_int("MAX_CONCURRENT_SCOUTS", 3)
    SCOUT_COOLDOWN_FAILURES: int = _env_int("SCOUT_COOLDOWN_FAILURES", 3)
    SCOUT_COOLDOWN_SEC: int = _env_int("SCOUT_COOLDOWN_SEC", 600)

    # Stop losses by phase (pct, positive numbers)
    SCOUT_STOP_LOSS_PCT: float = _env_float("SCOUT_STOP_LOSS_PCT", 0.25)  # Changed to 25% for bounce recovery
    CONFIRM_STOP_LOSS_PCT: float = _env_float("CONFIRM_STOP_LOSS_PCT", 0.15)
    CONVICTION_STOP_LOSS_PCT: float = _env_float("CONVICTION_STOP_LOSS_PCT", 0.12)
    
    # Bounce Recovery Settings
    BOUNCE_THRESHOLD_PCT: float = _env_float("BOUNCE_THRESHOLD_PCT", 0.15)  # Re-enter when +15% from bottom
    BOUNCE_MONITOR_DURATION_SEC: int = _env_int("BOUNCE_MONITOR_DURATION_SEC", 600)  # Monitor for 10 minutes
    BOUNCE_MAX_REENTRIES: int = _env_int("BOUNCE_MAX_REENTRIES", 2)  # Max 2 re-entry attempts
    BOUNCE_REENTRY_SIZE_MULTIPLIER: float = _env_float("BOUNCE_REENTRY_SIZE_MULTIPLIER", 0.5)  # 50% of original
    BOUNCE_MIN_VOLUME_SPIKE: float = _env_float("BOUNCE_MIN_VOLUME_SPIKE", 0.5)  # +50% volume required
    
    # Profit-Based Partial Exit Settings
    PARTIAL_EXIT_ENABLED: bool = _env_bool("PARTIAL_EXIT_ENABLED", True)  # Enable profit-based partial exits
    PARTIAL_EXIT_SIZE_50PCT: float = _env_float("PARTIAL_EXIT_SIZE_50PCT", 0.30)  # Sell 30% at +50% profit
    PARTIAL_EXIT_SIZE_100PCT: float = _env_float("PARTIAL_EXIT_SIZE_100PCT", 0.25)  # Sell 25% at +100% profit
    PARTIAL_EXIT_SIZE_150PCT: float = _env_float("PARTIAL_EXIT_SIZE_150PCT", 0.20)  # Sell 20% at +150% profit
    
    # Hybrid Strategy (Axiom)
    BREAK_EVEN_TRIGGER_PCT: float = _env_float("BREAK_EVEN_TRIGGER_PCT", 0.10)
    ANTI_PANIC_DURATION_SEC: int = _env_int("ANTI_PANIC_DURATION_SEC", 45)
    MOONBAG_SELL_TRIGGER_PCT: float = _env_float("MOONBAG_SELL_TRIGGER_PCT", 1.0)
    MOONBAG_SELL_PCT: float = _env_float("MOONBAG_SELL_PCT", 0.5)

    # Trailing
    BASE_TRAILING_PCT: float = _env_float("BASE_TRAILING_PCT", 0.08)
    MIN_TRAILING_PCT: float = _env_float("MIN_TRAILING_PCT", 0.05)
    MAX_TRAILING_PCT: float = _env_float("MAX_TRAILING_PCT", 0.25)

    # Safety
    ALLOW_FREEZE_ON_PUMPFUN: bool = _env_bool("ALLOW_FREEZE_ON_PUMPFUN", False)
    
    # Rugcheck Grace Period
    RUGCHECK_GRACE_PNL_HIGH: float = _env_float("RUGCHECK_GRACE_PNL_HIGH", 0.20)  # Ignore rugcheck if PnL > 20%
    RUGCHECK_GRACE_PNL_LOW: float = _env_float("RUGCHECK_GRACE_PNL_LOW", 0.10)   # Lenient rugcheck if PnL > 10%
    RUGCHECK_DISABLE_ON_CONVICTION: bool = _env_bool("RUGCHECK_DISABLE_ON_CONVICTION", False)  # Disable on CONVICTION
    RUGCHECK_DETAILED_LOGGING: bool = _env_bool("RUGCHECK_DETAILED_LOGGING", True)  # Log detailed check results
    RUGCHECK_API_ENABLED: bool = _env_bool("RUGCHECK_API_ENABLED", False)  # Enable public API check
    RUGCHECK_MAX_SCORE: int = _env_int("RUGCHECK_MAX_SCORE", 3000)  # Max allowed risk score (raw API value, 501=normal, >5000=suspicious)
    
    PUMPFUN_ONLY: bool = _env_bool("PUMPFUN_ONLY", True)  # Only trade Pump.fun tokens
    ENABLE_DEV_MONITOR: bool = _env_bool("ENABLE_DEV_MONITOR", False)
    ENABLE_CRIMINOLOGY: bool = _env_bool("ENABLE_CRIMINOLOGY", True) # Dev history check
    CRIMINOLOGY_MIN_WIN_RATE: float = _env_float("CRIMINOLOGY_MIN_WIN_RATE", 0.1)
    CRIMINOLOGY_MAX_SERIAL_RUGS: int = _env_int("CRIMINOLOGY_MAX_SERIAL_RUGS", 5)
    ENABLE_LP_MONITOR: bool = _env_bool("ENABLE_LP_MONITOR", False)
    ONCHAIN_HOLDER_STATS_IN_SCOUT: bool = _env_bool("ONCHAIN_HOLDER_STATS_IN_SCOUT", False)
    ONCHAIN_HOLDER_STATS_TTL_SEC: int = _env_int("ONCHAIN_HOLDER_STATS_TTL_SEC", 1800)
    ONCHAIN_MINT_INFO_TTL_SEC: int = _env_int("ONCHAIN_MINT_INFO_TTL_SEC", 24 * 60 * 60)

    # Simulation
    SIM_STARTING_BALANCE_SOL: float = _env_float("SIM_STARTING_BALANCE_SOL", 1.0)
    SIM_MAX_TICKS: int = _env_int("SIM_MAX_TICKS", 300)
    SIM_TICK_SEC: float = _env_float("SIM_TICK_SEC", 1.0)
    SIM_NEW_TOKEN_CHANCE: float = _env_float("SIM_NEW_TOKEN_CHANCE", 0.3)
    SIM_PRICE_VOLATILITY: float = _env_float("SIM_PRICE_VOLATILITY", 0.12)
    SIM_SLIPPAGE_PCT: float = _env_float("SIM_SLIPPAGE_PCT", 0.02)
    SIM_FEE_BPS: float = _env_float("SIM_FEE_BPS", 20.0)
    SIM_FIXED_FEE_SOL: float = _env_float("SIM_FIXED_FEE_SOL", 0.0001)
    # Event simulation probabilities (0 = disabled, use for testing exit logic)
    SIM_DEV_EVENT_PROBABILITY: float = _env_float("SIM_DEV_EVENT_PROBABILITY", 0.0)
    SIM_LP_EVENT_PROBABILITY: float = _env_float("SIM_LP_EVENT_PROBABILITY", 0.0)

    # API clients
    API_TIMEOUT_SEC: float = _env_float("API_TIMEOUT_SEC", 15.0)
    SCAN_INTERVAL_SEC: float = _env_float("SCAN_INTERVAL_SEC", 10.0)
    SCAN_LOG_EVERY_SEC: float = _env_float("SCAN_LOG_EVERY_SEC", 60.0)
    POSITION_METRICS_REFRESH_SEC: float = _env_float("POSITION_METRICS_REFRESH_SEC", 20.0)
    
    # Real-Time Price Feed Settings
    REALTIME_PRICE_ENABLED: bool = _env_bool("REALTIME_PRICE_ENABLED", True)
    BIRDEYE_POLL_SEC: float = _env_float("BIRDEYE_POLL_SEC", 3.0)
    REALTIME_JUPITER_POLL_SEC: float = _env_float("REALTIME_JUPITER_POLL_SEC", 3.0)
    REALTIME_STALE_THRESHOLD_SEC: float = _env_float("REALTIME_STALE_THRESHOLD_SEC", 2.0)
    REALTIME_RECONNECT_DELAY_SEC: float = _env_float("REALTIME_RECONNECT_DELAY_SEC", 60.0)

    # DexScreener
    USE_DEXSCREENER_DISCOVERY: bool = _env_bool("USE_DEXSCREENER_DISCOVERY", True)
    DEXSCREENER_API_BASE: str = _env_str("DEXSCREENER_API_BASE", "https://api.dexscreener.com")
    DEXSCREENER_CHAIN_ID: str = _env_str("DEXSCREENER_CHAIN_ID", "solana")
    DEXSCREENER_TOKEN_PROFILE_LIMIT: int = _env_int("DEXSCREENER_TOKEN_PROFILE_LIMIT", 30)
    DEXSCREENER_MAX_TOKENS_PER_SCAN: int = _env_int("DEXSCREENER_MAX_TOKENS_PER_SCAN", 12)
    DEXSCREENER_PROFILES_TTL_SEC: float = _env_float("DEXSCREENER_PROFILES_TTL_SEC", 30.0)
    DEXSCREENER_MAX_RETRIES: int = _env_int("DEXSCREENER_MAX_RETRIES", 3)
    DEXSCREENER_RETRY_BACKOFF_SEC: float = _env_float("DEXSCREENER_RETRY_BACKOFF_SEC", 1.5)
    USE_DEXSCREENER_SEARCH_FALLBACK: bool = _env_bool("USE_DEXSCREENER_SEARCH_FALLBACK", True)
    DEXSCREENER_SEARCH_QUERY: str = _env_str("DEXSCREENER_SEARCH_QUERY", "solana")
    DEXSCREENER_SEARCH_MAX_PAIRS: int = _env_int("DEXSCREENER_SEARCH_MAX_PAIRS", 20)
    DEXSCREENER_MIN_LIQUIDITY_USD: float = _env_float("DEXSCREENER_MIN_LIQUIDITY_USD", 100.0)
    DEXSCREENER_MAX_TOKEN_AGE_SEC: int = _env_int("DEXSCREENER_MAX_TOKEN_AGE_SEC", 2 * 60 * 60)
    DEXSCREENER_MIN_VOLUME_MCAP_RATIO: float = _env_float("DEXSCREENER_MIN_VOLUME_MCAP_RATIO", 0.01)
    DEXSCREENER_MIN_MCAP: float = _env_float("DEXSCREENER_MIN_MCAP", 10000.0)
    DEXSCREENER_MAX_MCAP: float = _env_float("DEXSCREENER_MAX_MCAP", 60000.0)
    DEXSCREENER_PRICE_CHANGE_5M_MIN: float = _env_float("DEXSCREENER_PRICE_CHANGE_5M_MIN", -50.0)
    DEXSCREENER_PRICE_CHANGE_5M_MAX: float = _env_float("DEXSCREENER_PRICE_CHANGE_5M_MAX", 500.0)
    SCAN_TOKEN_TTL_SEC: int = _env_int("SCAN_TOKEN_TTL_SEC", 300)
    
    # New Pairs Strategy (Fresh tokens < 3 min)
    NEW_PAIRS_DISCOVERY_ENABLED: bool = _env_bool("NEW_PAIRS_DISCOVERY_ENABLED", True)

    # FinalStretch filters (pre-migration tokens near bonding curve completion)
    FINALSTRETCH_ENABLED: bool = _env_bool("FINALSTRETCH_ENABLED", True)
    FINALSTRETCH_MAX_AGE_SEC: int = _env_int("FINALSTRETCH_MAX_AGE_SEC", 30 * 60)  # 30 min
    FINALSTRETCH_MIN_VOLUME_USD: float = _env_float("FINALSTRETCH_MIN_VOLUME_USD", 15000.0)
    FINALSTRETCH_MIN_MCAP_USD: float = _env_float("FINALSTRETCH_MIN_MCAP_USD", 12000.0)
    FINALSTRETCH_MIN_BONDING_PCT: float = _env_float("FINALSTRETCH_MIN_BONDING_PCT", 35.0)
    FINALSTRETCH_MAX_DEV_HOLDING: float = _env_float("FINALSTRETCH_MAX_DEV_HOLDING", 0.05)
    FINALSTRETCH_MAX_INSIDERS_PCT: float = _env_float("FINALSTRETCH_MAX_INSIDERS_PCT", 0.20)
    
    # Allow entry on low mcap if RugCheck passes
    ALLOW_LOW_MCAP_IF_RUGCHECK_PASS: bool = _env_bool("ALLOW_LOW_MCAP_IF_RUGCHECK_PASS", False)

    # PumpPortal (real-time Pump.fun token stream)
    USE_PUMPPORTAL_STREAM: bool = _env_bool("USE_PUMPPORTAL_STREAM", True)

    # Helius webhook
    USE_HELIUS_WEBHOOK: bool = _env_bool("USE_HELIUS_WEBHOOK", False)
    HELIUS_WEBHOOK_HOST: str = _env_str("HELIUS_WEBHOOK_HOST", "0.0.0.0")
    HELIUS_WEBHOOK_PORT: int = _env_int("HELIUS_WEBHOOK_PORT", 8080)
    HELIUS_WEBHOOK_PATH: str = _env_str("HELIUS_WEBHOOK_PATH", "/webhook")
    HELIUS_WEBHOOK_SECRET: str = _env_str("HELIUS_WEBHOOK_SECRET", "")
    HELIUS_WALLET_WEBHOOK_PORT: int = _env_int("HELIUS_WALLET_WEBHOOK_PORT", 8081)

    # Jupiter
    USE_JUPITER_QUOTES: bool = _env_bool("USE_JUPITER_QUOTES", True)
    JUPITER_PRICE_API_BASE: str = _env_str("JUPITER_PRICE_API_BASE", "https://price.jup.ag/v4")
    JUPITER_QUOTE_API_BASE: str = _env_str("JUPITER_QUOTE_API_BASE", "https://api.jup.ag/swap/v1")
    JUPITER_QUOTE_OUTPUT_MINT: str = _env_str(
        "JUPITER_QUOTE_OUTPUT_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    )
    QUOTE_CACHE_TTL_SEC: float = _env_float("QUOTE_CACHE_TTL_SEC", 2.0)

    # Live Trading & Jito (MEV Protection)
    JITO_ENABLED: bool = _env_bool("JITO_ENABLED", True)
    JITO_BLOCK_ENGINE_URL: str = _env_str("JITO_BLOCK_ENGINE_URL", "https://mainnet.block-engine.jito.wtf")
    LIVE_MAX_FEE_SOL: float = _env_float("LIVE_MAX_FEE_SOL", 0.1)
    LIVE_BUY_PRIORITY_FEE_SOL: float = _env_float("LIVE_BUY_PRIORITY_FEE_SOL", 0.001)  # Balanced: ~10% breakeven
    LIVE_BUY_JITO_TIP_SOL: float = _env_float("LIVE_BUY_JITO_TIP_SOL", 0.0005) # Reduced for profitability
    LIVE_BUY_SLIPPAGE_BPS: int = _env_int("LIVE_BUY_SLIPPAGE_BPS", 1500) # 15% slippage

    LIVE_SELL_PRIORITY_FEE_SOL: float = _env_float("LIVE_SELL_PRIORITY_FEE_SOL", 0.001)
    LIVE_SELL_JITO_TIP_SOL: float = _env_float("LIVE_SELL_JITO_TIP_SOL", 0.0005)
    LIVE_SELL_SLIPPAGE_BPS: int = _env_int("LIVE_SELL_SLIPPAGE_BPS", 3000)

    # Backtest
    BACKTEST_DATA_PATH: str = _env_str("BACKTEST_DATA_PATH", "backtest/data.jsonl")
    BACKTEST_INTERVAL: str = _env_str("BACKTEST_INTERVAL", "1m")
    BACKTEST_AVG_TRADE_USD: float = _env_float("BACKTEST_AVG_TRADE_USD", 50.0)
    BACKTEST_DEFAULT_LIQUIDITY_USD: float = _env_float(
        "BACKTEST_DEFAULT_LIQUIDITY_USD", 1000.0
    )

    # External APIs
    BIRDEYE_API_KEY: str = _env_str("BIRDEYE_API_KEY", "")
    INSIGHTX_API_KEY: str = _env_str("INSIGHTX_API_KEY", "")
    BIRDEYE_API_BASE: str = _env_str("BIRDEYE_API_BASE", "https://public-api.birdeye.so/defi")
    
    # CoinGecko API (Primary data source)
    COINGECKO_API_KEY: str = _env_str("COINGECKO_API_KEY", "")
    COINGECKO_API_BASE: str = _env_str("COINGECKO_API_BASE", "https://pro-api.coingecko.com/api/v3")
    COINGECKO_MAX_RETRIES: int = _env_int("COINGECKO_MAX_RETRIES", 3)
    COINGECKO_RETRY_BACKOFF_SEC: float = _env_float("COINGECKO_RETRY_BACKOFF_SEC", 1.0)
    COINGECKO_CACHE_TTL_SEC: float = _env_float("COINGECKO_CACHE_TTL_SEC", 30.0)
    USE_COINGECKO_PRIMARY: bool = _env_bool("USE_COINGECKO_PRIMARY", True)

    # Copy Trading
    COPY_TRADING_ENABLED: bool = _env_bool("COPY_TRADING_ENABLED", False)
    COPY_TRADING_LEADERS_FILE: str = _env_str("COPY_TRADING_LEADERS_FILE", "logs/leaders.json")
    COPY_DEFAULT_SIZE_SOL: float = _env_float("COPY_DEFAULT_SIZE_SOL", 0.03)
    COPY_MAX_POSITIONS: int = _env_int("COPY_MAX_POSITIONS", 3)
    COPY_MIN_LEADER_TRADE_SOL: float = _env_float("COPY_MIN_LEADER_TRADE_SOL", 0.1)
    COPY_DELAY_MS: int = _env_int("COPY_DELAY_MS", 0)  # 0 for max speed
    COPY_FOLLOW_SELLS: bool = _env_bool("COPY_FOLLOW_SELLS", True)
    COPY_FAST_MODE: bool = _env_bool("COPY_FAST_MODE", True)  # Skip price lookups, buy first
    HELIUS_WALLET_WEBHOOK_PATH: str = _env_str("HELIUS_WALLET_WEBHOOK_PATH", "/wallet-webhook")
    COPY_EMERGENCY_STOP_LOSS_PCT: float = _env_float("COPY_EMERGENCY_STOP_LOSS_PCT", 0.30)  # 30% stop loss for copy trades
    COPY_TRAILING_TRIGGER_PCT: float = _env_float("COPY_TRAILING_TRIGGER_PCT", 1.0)  # Activate trailing at +100% profit
    COPY_TRAILING_PCT: float = _env_float("COPY_TRAILING_PCT", 0.15)  # 15% trailing stop for copy trades
    COPY_SELL_ON_TRANSFER: bool = _env_bool("COPY_SELL_ON_TRANSFER", False)  # Disable sell on transfer by default

    # Logging
    LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO")
    LOG_DIR: str = _env_str("LOG_DIR", "logs")
    POSITION_LOG_EVERY_SEC: float = _env_float("POSITION_LOG_EVERY_SEC", 30.0)
    POSITION_SNAPSHOT_PATH: str = _env_str("POSITION_SNAPSHOT_PATH", "logs/positions.json")
    DASHBOARD_PASSWORD: str = _env_str("DASHBOARD_PASSWORD", "antigravity123")


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings()
    return _SETTINGS
