
import asyncio
import json
import os
import sys
import time
import traceback
import signal
import logging
from typing import Dict, Optional, List, Any
import aiohttp
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey # type: ignore
from solders.signature import Signature # type: ignore

# Add parent directory to path to import sibling modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import new structured components
from ..logger import CorrelationLogger, setup_logging
from ..exceptions import BotException, StateException, NetworkException
from ..core.integrations import SafeRiskManager, SafeDataCollector

# Setup structured logging
setup_logging(level="INFO")
logger = CorrelationLogger(__name__)

# Try to import external modules
try:
    from risk_manager import RiskManager
    logger.info("RiskManager module loaded")
except ImportError:
    logger.warning("RiskManager not found, using safe defaults")
    RiskManager = None

try:
    import data_collector
    logger.info("DataCollector module loaded")
except ImportError:
    logger.warning("DataCollector not found, data collection disabled")
    data_collector = None

from ..config import (
    RPC_URL, ONE_SHOT_MODE,
    MAX_POSITIONS, STOP_LOSS_PCT, TAKE_PROFIT_PCT, TRAILING_STOP_PCT,  # LEGACY: Not used in Convex mode
    MAX_CONSECUTIVE_LOSSES, COOLDOWN_SECONDS, TG_TOKEN, TG_CHAT_ID, ADMIN_ID,
    JITO_REGION,
    MAX_DAILY_LOSS_SOL, MAX_DAILY_LOSS_PCT, MAX_DAILY_TRADES,
    MIN_RESERVE_SOL, MAX_TRADE_PCT_OF_BALANCE,
    # Dual Strategy Config
    EARLY_STRATEGY, STABLE_STRATEGY,
    STABLE_MIN_MCAP_USD, STABLE_MIN_LIQUIDITY_USD, STABLE_MIN_AGE_HOURS
)
from ..constants import SUPPORTED_DEX_PROGRAMS, WSOL_MINT
from ..utils.helpers import calculate_transaction_flow, calculate_real_pnl, clean_console, format_trade_alert
from ..telegram_notifier import TelegramNotifier
from .wallet import WalletManager
from .validator import Validator
from .strategy import StrategyManager
from .trader import Trader

# NEW: Scalping specific components
from .token_scanner import TokenScanner
from .scalping_strategy import ScalpingStrategy, TokenData

# NEW: Modern components
from .transaction_parser import TransactionParser
from .jupiter_client import JupiterClient
from .price_feed import PriceFeed
from ..db.database import DatabaseManager

# PHASE 2: Advanced components
from .rpc_client import RPCClientWithFallback, create_rpc_client_from_config
from .tx_confirmer import TransactionConfirmer
from .jito_client import JitoClient, MEVProtector, JitoRegion
from ..config.risk_config import RiskConfigManager, get_risk_config
from ..utils.trade_export import TradeExporter
from .command_processor import CommandProcessor

# NEW: Helius Webhook for real-time token detection
from .helius_webhook import HeliusWebhookServer, NewToken

# NEW: Rugcheck for token safety validation
from .rugcheck import Rugchecker

# v12.3 SHARP EDGE COMPONENTS
from .event_bus import EventBus, EventType
from .partial_exit_manager import PartialExitManager
from .narrative_analyzer import NarrativeAnalyzer
from .dynamic_eas_tracker import DynamicEASTracker
from .trade_performance_tracker import TradePerformanceTracker
from .runner_protection import RunnerProtectionLayer

# CONVEX STRATEGY: Phased entry (SCOUT → CONFIRM → CONVICTION → MOONBAG)
from .convex_state_machine import ConvexStateMachine, ConvexState, ConvexPosition
from .trade_metrics_logger import TradeMetricsLogger, get_metrics_logger

class UniversalCopyTradeV7:
    """
    Autonomous Solana Scalping Bot V9 (Formerly CopyTradeV7)
    
    Architecture:
    - TokenScanner: Finds opportunities (DexScreener + Filters)
    - ScalpingStrategy: Decides entry/exit (RSI, Volume, Trailing Stop)
    - Trader: Executes trades (Jupiter/Raydium/PumpFun)
    - RiskManager: Protects capital
    """
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.client: Optional[AsyncClient] = None
        self.is_running = True
        
        # Components (initialized in start)
        self.wallet: Optional[WalletManager] = None
        self.validator: Optional[Validator] = None
        self.trader: Optional[Trader] = None
        self.strategy_manager: Optional[StrategyManager] = None
        
        # Scalping Components
        self.scanner: Optional[TokenScanner] = None
        self.scalping_strategy: Optional[ScalpingStrategy] = None
        
        # NEW: Modern components
        self.parser: Optional[TransactionParser] = None
        self.jupiter: Optional[JupiterClient] = None
        self.db: Optional[DatabaseManager] = None
        self.price_feed: Optional[PriceFeed] = None
        self.trading_manager = None  # Paper/Live mode manager
        
        # PHASE 2: Advanced components
        self.rpc_client_with_fallback: Optional[RPCClientWithFallback] = None
        self.tx_confirmer: Optional[TransactionConfirmer] = None
        self.jito_client: Optional[JitoClient] = None
        self.mev_protector: Optional[MEVProtector] = None
        self.risk_config_manager: Optional[RiskConfigManager] = None
        self.trade_exporter: Optional[TradeExporter] = None
        
        # Monitoring components
        self.command_processor = None
        
        # NEW: Helius Webhook Server for real-time token detection
        self.webhook_server: Optional[HeliusWebhookServer] = None
        
        # ✅ Initialize external integrations with safe wrappers
        risk_manager_instance = None
        if RiskManager:
            try:
                # Create callback functions for RiskManager
                def get_sol_balance_sync():
                    try:
                        if self.wallet:
                            return asyncio.create_task(self.wallet.get_sol_balance())
                        return 0.0
                    except Exception as e:
                        logger.error(f"Error in get_sol_balance_sync: {e}")
                        return 0.0
                
                def get_positions_value_sync():
                    try:
                        total = 0.0
                        for mint, pos in self.active_positions.items():
                            total += pos.get("entry_sol", 0.0)
                        return total
                    except Exception as e:
                        logger.error(f"Error in get_positions_value_sync: {e}")
                        return 0.0
                
                # Initialize RiskManager
                risk_manager_instance = RiskManager(
                    get_onchain_sol_balance=get_sol_balance_sync,
                    get_open_positions_value=get_positions_value_sync,
                    learning_mode=False,  # LIVE MODE - bot will execute trades
                    max_daily_loss_pct=10.0,
                    max_total_exposure_pct=50.0
                )
                logger.info("RiskManager initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize RiskManager: {e}")
                risk_manager_instance = None
        
        # Wrap in SafeRiskManager (never crashes)
        self.risk_manager = SafeRiskManager(risk_manager_instance)
        
        # Wrap DataCollector safely
        self.data_collector = SafeDataCollector(data_collector)
        if data_collector:
            self.data_collector.init_db()

        # 🛡️ ACCOUNT PROTECTOR: Initialize without telegram (will be set later)
        # Deferred to avoid AttributeError since telegram might not exist yet
        self.account_protector = None
        self._account_protector_initialized = False
        
        # State
        self.active_positions: Dict[str, Dict] = {}
        self.processing_tokens: set = set()  # Tokens currently being processed (for race condition prevention)
        self.positions_lock = asyncio.Lock()
        self.state_file = "bot_state.json"
        
        # ✅ Initialize Telegram notifier
        self.telegram = TelegramNotifier(
            bot_token=TG_TOKEN,
            chat_id=TG_CHAT_ID,
            admin_id=ADMIN_ID
        )
        
        # Cache
        self.last_sol_price_update = 0
        self.sol_price_eur = 0.0
        self.start_time = time.time()
        
        # 📊 Session Stats for /status command
        self.session_stats = {
            "wins": 0,
            "losses": 0,
            "pnl_positive": 0.0,
            "pnl_negative": 0.0,
            "start_time": time.time()
        }
        
        # 🚫 Cooldown: Recently traded tokens (prevent re-buying within 1h)
        self.traded_tokens: Dict[str, float] = {}  # mint -> timestamp
        self.token_cooldown_hours = 1.0  # Don't re-buy within 1 hour
        
        # Load State
        self.load_state()
        
        # v12.3 SHARP EDGE: Global components
        self.trade_tracker = TradePerformanceTracker(log_file="logs/trade_metrics.json")
        self.event_bus = EventBus(lookback_seconds=30.0)
        
        # CONVEX STRATEGY: State machine for phased entries
        self.convex_machine = ConvexStateMachine()
        self.convex_metrics_logger = get_metrics_logger()
        logger.info("✅ ConvexStateMachine initialized")
    
    async def _update_session_stats(self, pnl_sol: float, is_full_exit: bool = True):
        """
        Update session stats after a sell.
        
        Args:
            pnl_sol: Profit/loss in SOL (positive = profit, negative = loss)
            is_full_exit: Whether this was a full position exit (affects win/loss counter)
        """
        if is_full_exit:
            if pnl_sol >= 0:
                self.session_stats["wins"] += 1
            else:
                self.session_stats["losses"] += 1
        
        if pnl_sol >= 0:
            self.session_stats["pnl_positive"] += pnl_sol
        else:
            self.session_stats["pnl_negative"] += abs(pnl_sol)
        
        # Save state to persist
        await self.save_state()
        logger.debug(f"📊 Stats updated: W={self.session_stats['wins']} L={self.session_stats['losses']} PnL={pnl_sol:+.4f}")
        
    async def _ensure_account_protector_ready(self):
        """Ensure AccountProtector is initialized (call once on first use)"""
        if not self._account_protector_initialized:
            from .account_protector import AccountProtector
            
            # Get telegram if it exists
            telegram = getattr(self, 'telegram', None)
            
            self.account_protector = AccountProtector(
                wallet_manager=self.wallet,
                telegram_notifier=telegram,
                max_daily_loss_sol=MAX_DAILY_LOSS_SOL,
                max_daily_loss_pct=MAX_DAILY_LOSS_PCT,
                max_daily_trades=MAX_DAILY_TRADES,
                min_reserve_sol=MIN_RESERVE_SOL,
                max_trade_pct=MAX_TRADE_PCT_OF_BALANCE,
                max_consecutive_losses=MAX_CONSECUTIVE_LOSSES,
                cooldown_seconds=COOLDOWN_SECONDS
            )
            await self.account_protector.initialize()
            self._account_protector_initialized = True
            logger.info("✅ AccountProtector initialized")
    
    def load_state(self):
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    # Restore positions (careful with task reconstruction)
                    saved_positions = data.get("active_positions", {})
                    # We can't easily restore running tasks, so we might just load data
                    # and let manage_orphan logic handle restarting monitors?
                    # For now just load dict, but tasks are missing.
                    for mint, pos in saved_positions.items():
                         pos["task"] = None # Will need to restart
                         self.active_positions[mint] = pos
                    
                    # Restore cooldown data (persisted traded_tokens)
                    self.traded_tokens = data.get("traded_tokens", {})
                    if self.traded_tokens:
                        logger.info(f"📋 Restored {len(self.traded_tokens)} cooldown entries")
                    
                    # Restore session stats
                    saved_stats = data.get("session_stats", {})
                    if saved_stats:
                        self.session_stats.update(saved_stats)
                        logger.info(f"📊 Restored stats: W={self.session_stats['wins']} L={self.session_stats['losses']}")
        except Exception as e:
            print(f"[WARN] Load state failed: {e}")

    async def save_state(self):
        try:
            data = {
                "active_positions": {k: {x: y for x, y in v.items() if x != "task"} for k, v in self.active_positions.items()},
                "traded_tokens": self.traded_tokens,  # Persist cooldown data
                "session_stats": self.session_stats   # Persist wins/losses/PnL
            }
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"[ERR] Save state failed: {e}")
    
    def _get_strategy_profile(self, opp) -> str:
        """
        Determine strategy profile based on token stability.
        
        STABLE: High mcap ($100k+), high liquidity ($50k+), old (1h+)
        EARLY: Everything else (fresh tokens, low mcap)
        """
        mcap = getattr(opp, 'market_cap', 0) or 0
        liquidity = getattr(opp, 'liquidity_usd', 0) or 0
        age_hours = getattr(opp, 'age_hours', 0) or 0
        
        is_stable = (
            mcap >= STABLE_MIN_MCAP_USD and
            liquidity >= STABLE_MIN_LIQUIDITY_USD and
            age_hours >= STABLE_MIN_AGE_HOURS
        )
        
        profile = "STABLE" if is_stable else "EARLY"
        logger.info(f"   🎯 Strategy: {profile} (mcap=${mcap:,.0f}, liq=${liquidity:,.0f}, age={age_hours:.1f}h)")
        return profile
    
    def _get_uptime(self) -> str:
        """Get formatted uptime string."""
        elapsed = time.time() - self.start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        return f"{hours}h {minutes}m"
    
    async def _handle_telegram_command(self, cmd: dict):
        """Handle Telegram commands."""
        command = cmd.get("command", "")
        args = cmd.get("args", [])
        is_admin = cmd.get("is_admin", False)
        
        # ================== INFO COMMANDS ==================
        if command == "status":
            wins = self.session_stats["wins"]
            losses = self.session_stats["losses"]
            total = wins + losses
            wr = (wins / total * 100) if total > 0 else 0
            pnl = self.session_stats["pnl_positive"] - self.session_stats["pnl_negative"]
            
            balance = 0.0
            if self.wallet:
                try:
                    balance = await self.wallet.get_sol_balance()
                except:
                    pass
            
            # Calculate active position value using CURRENT value (real-time)
            position_value = sum(p.get("current_value", p.get("entry_sol", 0)) for p in self.active_positions.values())
            
            from ..config import PAPER_TRADING_MODE
            mode = "📝 PAPER" if PAPER_TRADING_MODE else "💰 LIVE"
            
            await self.telegram.send_status_summary(
                bot_version="V9.0-DualStrategy",
                active_positions=len(self.active_positions),
                total_value_sol=position_value,
                uptime=self._get_uptime(),
                learning_mode=PAPER_TRADING_MODE,
                wallet_balance=balance,
                total_pnl_sol=pnl,
                trades_today=total,
                win_rate=wr
            )
            logger.info(f"📊 Status sent: WR={wr:.1f}% W={wins} L={losses} PnL={pnl:+.4f} SOL")
        
        elif command == "position" or command == "pos":
            if not self.active_positions:
                await self.telegram.send_message("📭 <b>No active positions</b>")
            else:
                for mint, pos in self.active_positions.items():
                    symbol = pos.get("symbol", "???")
                    entry = pos.get("entry_sol", 0)
                    current = pos.get("current_value", entry)
                    peak = pos.get("peak_value", current)
                    pnl_pct = ((current - entry) / entry * 100) if entry > 0 else 0
                    pnl_sol = current - entry
                    profile = pos.get("strategy_profile", "UNKNOWN")
                    stop = pos.get("stop_loss_value", 0)
                    be_active = pos.get("break_even_activated", False)
                    
                    status = "🔒 BE" if be_active else "🎯"
                    pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
                    
                    msg = f"""📍 <b>{symbol}</b> ({profile})

{pnl_emoji} <b>PnL:</b> {pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL)
💰 <b>Entry:</b> {entry:.4f} SOL
📈 <b>Current:</b> {current:.4f} SOL
🏔️ <b>Peak:</b> {peak:.4f} SOL
🛑 <b>Stop:</b> {stop:.4f} SOL {status}

<code>{mint}</code>"""
                    await self.telegram.send_message(msg)
            logger.info("📍 Position info sent")
        
        elif command == "balance" or command == "bal":
            try:
                balance = await self.wallet.get_sol_balance()
                eur_rate = self.sol_price_eur or 0
                eur_value = balance * eur_rate if eur_rate else 0
                
                msg = f"""💰 <b>Wallet Balance</b>

<b>{balance:.4f} SOL</b>"""
                if eur_value:
                    msg += f"\n≈ €{eur_value:.2f}"
                
                await self.telegram.send_message(msg)
                logger.info(f"💰 Balance sent: {balance:.4f} SOL")
            except Exception as e:
                await self.telegram.send_message(f"❌ Error: {e}")
        
        elif command == "config" or command == "cfg":
            from ..config import (
                PAPER_TRADING_MODE, 
                STABLE_BUY_AMOUNT, EARLY_BUY_AMOUNT,
                STABLE_TRAILING_STOP_PCT, EARLY_TRAILING_STOP_PCT,
                STABLE_HARD_STOP_LOSS_PCT, EARLY_HARD_STOP_LOSS_PCT,
                MAX_HOLD_TIME_MINUTES, EARLY_MAX_HOLD_TIME_MINUTES
            )
            
            mode = "📝 PAPER" if PAPER_TRADING_MODE else "💰 LIVE"
            
            msg = f"""⚙️ <b>Bot Configuration</b>

<b>Mode:</b> {mode}

<b>STABLE Strategy:</b>
• Entry: {STABLE_BUY_AMOUNT} SOL
• Trailing: {STABLE_TRAILING_STOP_PCT}%
• Stop Loss: {STABLE_HARD_STOP_LOSS_PCT}%
• Max Hold: {MAX_HOLD_TIME_MINUTES} min

<b>EARLY Strategy:</b>
• Entry: {EARLY_BUY_AMOUNT} SOL
• Trailing: {EARLY_TRAILING_STOP_PCT}%
• Stop Loss: {EARLY_HARD_STOP_LOSS_PCT}%
• Max Hold: {EARLY_MAX_HOLD_TIME_MINUTES} min"""
            await self.telegram.send_message(msg)
            logger.info("⚙️ Config sent")
        
        elif command == "ping":
            await self.telegram.send_message("🏓 <b>Pong!</b> Bot is alive")
            logger.info("🏓 Ping/Pong")
        
        elif command == "help":
            help_text = """🤖 <b>Solana Trading Bot</b>

📊 <b>Info:</b>
/status - Bot stats, PnL, balance
/position - Active position details
/balance - Wallet balance
/config - Current settings
/ping - Check if bot alive

⚙️ <b>Admin:</b>
/sell - Sell active position
/stop - Stop the bot
/resume - Resume the bot

<i>Shortcuts: /pos, /bal, /cfg</i>"""
            await self.telegram.send_message(help_text)
            logger.info("📋 Help sent")
        
        # ================== ADMIN COMMANDS ==================
        elif command == "stop":
            if not is_admin:
                await self.telegram.send_message("❌ Admin only")
                return
            logger.warning("🛑 Stop command received")
            self.is_running = False
            await self.telegram.send_message("⏹️ Bot stopping...")
        
        elif command == "resume":
            if not is_admin:
                await self.telegram.send_message("❌ Admin only")
                return
            logger.info("▶️ Resume command received")
            self.is_running = True
            await self.telegram.send_message("▶️ Bot resumed!")
        
        elif command == "sell":
            if not is_admin:
                await self.telegram.send_message("❌ Admin only")
                return
            
            if not self.active_positions:
                await self.telegram.send_message("📭 No position to sell")
                return
            
            # Sell first/only active position
            mint = list(self.active_positions.keys())[0]
            pos = self.active_positions[mint]
            symbol = pos.get("symbol", "???")
            
            await self.telegram.send_message(f"🔴 Selling {symbol}...")
            
            try:
                success, sig = await self.trading_manager.execute_sell(
                    mint=mint,
                    symbol=symbol,
                    sell_pct=100.0
                )
                if success:
                    # Update stats
                    entry_sol = pos.get("entry_sol", 0.0)
                    current_val = pos.get("current_value", entry_sol) # Best effort value
                    pnl_sol = current_val - entry_sol
                    
                    await self._update_session_stats(pnl_sol=pnl_sol, is_full_exit=True)
                    
                    # Remove position
                    if mint in self.active_positions:
                        del self.active_positions[mint]
                    
                    await self.telegram.send_message(f"✅ Sold {symbol}!\nTX: <code>{sig[:20]}...</code>")
                    logger.info(f"🔴 Manual sell executed: {symbol}")
                else:
                    await self.telegram.send_message(f"❌ Sell failed for {symbol}")
            except Exception as e:
                await self.telegram.send_message(f"❌ Sell error: {e}")
                logger.error(f"Manual sell error: {e}")
        
        else:
            # Unknown command
            await self.telegram.send_message(f"❓ Unknown command: /{command}\nUse /help")
    
    # =============================================================================
    # 🛡️ RISK PROTECTION - Using dedicated RiskManager module
    # =============================================================================
    # Daily stats and protection handled by self.risk_manager
    # Access via: self.risk_manager.can_trade(), record_trade_result(), etc.

    async def _perform_health_check(self):
        """Verify critical components are operational."""
        logger.info("🏥 Performing startup health check...")
        
        # 1. Check RPC
        try:
            await self.client.get_slot()  # Use get_slot() instead of get_health()
            logger.info("✅ RPC Health: OK")
        except Exception as e:
            logger.error(f"❌ RPC Health: FAILED ({e})")
            
        # 2. Check Database
        if self.db:
            try:
                self.db.get_stats(days=1)
                logger.info("✅ Database Health: OK")
            except Exception as e:
                logger.error(f"❌ Database Health: FAILED ({e})")

        # 3. Check Wallet
        try:
            bal = await self.wallet.get_sol_balance()
            logger.info(f"✅ Wallet Health: OK (Balance: {bal:.4f} SOL)")
        except Exception as e:
            logger.error(f"❌ Wallet Health: FAILED ({e})")

    async def start(self):
        print("🤖 Scalping Bot V9 Starting...")
        
        # Setup Async Components
        self.client = AsyncClient(RPC_URL)
        from aiohttp.resolver import ThreadedResolver
        resolver = ThreadedResolver()
        connector = aiohttp.TCPConnector(resolver=resolver)
        self.session = aiohttp.ClientSession(connector=connector)
        
        # Init Core
        self.wallet = WalletManager(self.client)
        self.validator = Validator(self.session, self.client)
        self.strategy_manager = StrategyManager(self.validator)
        
        # NEW: Initialize JupiterClient FIRST (needed by Trader for TX parsing)
        self.jupiter = JupiterClient(self.session, self.client, self.wallet.payer)
        
        # Pass jupiter_client to Trader for TX parsing capability
        self.trader = Trader(self.session, self.client, self.wallet, self.validator, self.risk_manager, jupiter_client=self.jupiter)
        
        # NEW: Initialize other modern components
        self.db = DatabaseManager("bot_data.db")
        self.parser = TransactionParser()
        self.price_feed = PriceFeed(self.session, validator=self.validator)
        
        # 🚀 SCALPING COMPONENTS
        self.scanner = TokenScanner(self.session, self.validator)
        self.scalping_strategy = ScalpingStrategy()
        
        # 🛡️ RUGCHECK: Token safety validation
        self.rugchecker = Rugchecker(self.session, self.client)
        logger.info("✅ Rugchecker initialized")
        
        # 🧪 PAPER TRADING: Initialize mode manager
        from .trading_mode_manager import TradingModeManager
        self.trading_manager = TradingModeManager(
            real_trader=self.trader,
            price_feed=self.price_feed,
            telegram_notifier=self.telegram  # Pass telegram for notifications
        )
        
        logger.info("✅ Modern components initialized (Parser, Jupiter, Database, PriceFeed, Scanner)")
        
        # Log trading mode clearly
        from ..config import PAPER_TRADING_MODE
        if PAPER_TRADING_MODE:
            logger.warning("=" * 80)
            logger.warning("🧪 PAPER TRADING MODE - NO REAL TRANSACTIONS!")  
            logger.warning("=" * 80)
            
            # Send startup notification
            if self.telegram:
                try:
                    from ..paper_trading.telegram_notifier import PaperTradingNotifier
                    paper_notifier = PaperTradingNotifier(self.telegram)
                    await paper_notifier.send_startup_message(
                        mode="PAPER TRADING",
                        initial_balance=10.0,
                        slippage_pct=0.5
                    )
                except Exception as e:
                    import traceback
                    logger.warning(f"Startup notification failed: {str(e)}")
                    logger.debug(traceback.format_exc())
        else:
            logger.info("=" * 80)
            logger.info("💰 LIVE TRADING MODE - Real blockchain transactions enabled")
            logger.info("=" * 80)
        
        # PHASE 2: Initialize advanced components
        try:
            # 1. Risk Config Manager (hot-reload config)
            self.risk_config_manager = RiskConfigManager("config/risk_config.yaml")
            logger.info("✅ RiskConfigManager initialized (hot-reload enabled)")
            
            # 2. RPC Client with Fallback (replaces AsyncClient for critical operations)
            self.rpc_client_with_fallback = create_rpc_client_from_config()
            await self.rpc_client_with_fallback.initialize()
            logger.info("✅ RPC Fallback initialized (auto-failover enabled)")
            
            # 3. Transaction Confirmer (robust confirmation logic)
            self.tx_confirmer = TransactionConfirmer(self.rpc_client_with_fallback)
            logger.info("✅ TransactionConfirmer initialized")
            
            # 4. Jito Client (MEV protection)
            try:
                region_enum = JitoRegion(JITO_REGION.lower())
            except ValueError:
                logger.warning(f"Invalid Jito region '{JITO_REGION}', defaulting to FRANKFURT")
                region_enum = JitoRegion.FRANKFURT

            self.jito_client = JitoClient(
                session=self.session,
                payer=self.wallet.payer,
                region=region_enum,
                rpc_client=self.client
            )
            
            # 5. MEV Protector (smart routing)
            self.mev_protector = MEVProtector(
                jito_client=self.jito_client,
                rpc_client=self.client  # Fallback to regular RPC
            )
            logger.info(f"✅ Jito/MEV protection initialized (Region: {region_enum.value})")
            
            # 6. Trade Export (optional)
            self.trade_exporter = TradeExporter(self.db)
            logger.info("✅ TradeExporter initialized")
            
            logger.info("🚀 ALL PHASE 2 COMPONENTS INITIALIZED!")
            
            # Perform initial health check
            await self._perform_health_check()
            
        except Exception as e:
            logger.error(f"⚠️  Phase 2 initialization error: {e}")
            logger.warning("Continuing with basic components only")
        
        # Define wrappers for CommandProcessor
        async def manual_sell_wrapper(mint: str, reason: str, pct: float) -> Optional[str]:
            symbol = self.active_positions.get(mint, {}).get("symbol", "UNKNOWN")
            success, sig = await self.trading_manager.execute_sell(
                mint=mint, 
                symbol=symbol, 
                sell_pct=pct
            )
            return sig if success else None

        async def stop_wrapper():
            self.stop()

        # Initialize Command Processor
        self.command_processor = CommandProcessor(
            on_sell=manual_sell_wrapper,
            on_stop=stop_wrapper,
            get_positions=lambda: self.active_positions
        )
        logger.info("✅ CommandProcessor started")
        
        # Start Telegram Command Listener 
        # NOTE: Telegram polling disabled for now - commands via CommandProcessor file
        # if self.telegram:
        #     asyncio.create_task(self.telegram.start_polling(self.command_processor.handle_command))
        #     logger.info("✅ Telegram command listener started")

        # NEW: Start Helius Webhook Server for real-time token detection
        try:
            # Extract API key from RPC URL
            import re
            helius_key_match = re.search(r'api-key=([a-f0-9-]+)', RPC_URL)
            helius_api_key = helius_key_match.group(1) if helius_key_match else None
            
            self.webhook_server = HeliusWebhookServer(
                port=8765,
                webhook_path="/webhook",
                helius_api_key=helius_api_key,
                session=self.session
            )
            await self.webhook_server.start()
            logger.info("🔔 Helius Webhook Server started on port 8765")
            logger.info("   Configure webhook at dashboard.helius.dev → http://YOUR_VPS_IP:8765/webhook")
        except Exception as e:
            logger.warning(f"⚠️ Webhook server failed to start: {e}")
            logger.warning("   Continuing with DexScreener scanning only")

        # NEW: Blacklist Manager
        from .blacklist import BlacklistManager
        self.blacklist = BlacklistManager()
        logger.info("✅ BlacklistManager initialized")

        # Start Bot Loop
        logger.info("🚀 Bot initialization complete.")
        
        # 🧹 CLEANUP: Check for stale positions from previous runs
        await self._cleanup_stale_positions()
        
        logger.info("Starting scalping loop...")
        await self.run_scalping_loop()

    async def _cleanup_stale_positions(self):
        """Check for and sell outdated positions on startup."""
        if not self.active_positions:
            return

        logger.info(f"🧹 Checking {len(self.active_positions)} active positions for staleness...")
        to_sell = []
        
        # Default timeout if not in strategy
        timeout_sec = 15 * 60  # 15 mins default
        if self.scalping_strategy:
            timeout_sec = self.scalping_strategy.max_hold_time_minutes * 60
            
        now = time.time()
        
        for mint, pos in self.active_positions.items():
            entry_time = pos.get("entry_time", 0) or pos.get("timestamp", 0)
            duration = now - entry_time
            
            if duration > timeout_sec:
                symbol = pos.get("symbol", "???")
                logger.warning(f"⚠️ Found STALE position: {symbol} (Duration: {duration/60:.1f}m > {timeout_sec/60:.1f}m)")
                to_sell.append((mint, symbol))
        
        for mint, symbol in to_sell:
            logger.info(f"🛑 Force selling stale position: {symbol}")
            try:
                success, sig = await self.trading_manager.execute_sell(
                    mint=mint,
                    symbol=symbol,
                    sell_pct=100.0,
                    reason="STALE_ON_STARTUP"
                )
                if success:
                    logger.info(f"✅ Stale position cleaned up: {symbol}")
                    
                    # Update stats (Best effort PnL)
                    pos = self.active_positions.get(mint, {})
                    pnl_sol = pos.get("current_value", 0) - pos.get("entry_sol", 0)
                    await self._update_session_stats(pnl_sol=pnl_sol, is_full_exit=True)
                    
                    # Remove position
                    if mint in self.active_positions:
                        del self.active_positions[mint]
                        
                    # Add to blacklist
                    self.blacklist.add_timeout(mint, duration_minutes=60)
            except Exception as e:
                logger.error(f"❌ Failed to cleanup stale position {symbol}: {e}")

    async def run_scalping_loop(self):
        """Main autonomous scalping loop - ONE POSITION AT A TIME"""
        
        # If no scanner, can't loop
        if not self.scanner:
            logger.error("❌ TokenScanner not initialized. Exiting loop.")
            return

        logger.info("🔄 Starting autonomous market scanning (SINGLE POSITION MODE)...")
        
        while self.is_running:
            try:
                # 📏 Check for Telegram commands FIRST (always responsive!)
                if self.telegram:
                    try:
                        cmd = await self.telegram.check_commands()
                        if cmd:
                            if cmd.get("type") == "command":
                                await self._handle_telegram_command(cmd)
                            elif cmd.get("type") == "callback":
                                # Handle button callbacks (Force Sell, etc.)
                                callback_data = cmd.get("data", "")
                                if callback_data.startswith("sell_"):
                                    mint = callback_data[5:]  # Remove "sell_" prefix
                                    logger.info(f"🛑 FORCE SELL triggered via Telegram button for {mint[:8]}...")
                                    await self.telegram.send_message(f"🛑 <b>FORCE SELL</b> triggered for <code>{mint[:8]}...</code>\n\nExecuting sell...")
                                    
                                    # Execute the sell
                                    if mint in self.active_positions:
                                        position = self.active_positions[mint]
                                        symbol = position.get("symbol", "UNKNOWN")
                                        success, sig = await self.trading_manager.execute_sell(
                                            mint=mint,
                                            symbol=symbol,
                                            sell_pct=100.0
                                        )
                                        if success:
                                            # Update stats
                                            entry_sol = position.get("entry_sol", 0.0)
                                            current_val = position.get("current_value", entry_sol)
                                            pnl_sol = current_val - entry_sol
                                            
                                            await self._update_session_stats(pnl_sol=pnl_sol, is_full_exit=True)
                                            
                                            # Remove position
                                            if mint in self.active_positions:
                                                del self.active_positions[mint]
                                            
                                            await self.telegram.send_message(f"✅ <b>SOLD</b> {symbol}\n\nTX: <code>{sig}</code>")
                                            logger.info(f"✅ Force sell successful for {symbol}: {sig}")
                                        else:
                                            await self.telegram.send_message(f"❌ <b>SELL FAILED</b> for {symbol}")
                                            logger.error(f"❌ Force sell failed for {symbol}")
                                    else:
                                        await self.telegram.send_message(f"⚠️ No active position for <code>{mint[:8]}...</code>")
                    except Exception as e:
                        logger.debug(f"Telegram command check error: {e}")
                
                # ⚠️ SINGLE POSITION MODE: Skip if we already have an active position
                if self.active_positions:
                    active_mint = list(self.active_positions.keys())[0]
                    position = self.active_positions[active_mint]
                    active_symbol = position.get("symbol", "???")
                    
                    # 🔄 ORPHAN RECOVERY: Restart monitor if position has no task
                    task = position.get("task")
                    if task is None or (hasattr(task, 'done') and task.done()):
                        logger.info(f"🔄 Restarting orphan monitor for {active_symbol}...")
                        monitor_task = asyncio.create_task(
                            self._monitor_position(active_mint, active_symbol)
                        )
                        self.active_positions[active_mint]["task"] = monitor_task
                        logger.info(f"✅ Monitor restarted for {active_symbol}")
                    else:
                        logger.info(f"⏳ Waiting for {active_symbol} to be sold before scanning for new opportunities...")
                    
                    await asyncio.sleep(5)
                    continue
                
                # Also skip if currently processing a buy
                if self.processing_tokens:
                    logger.debug("Processing tokens, waiting...")
                    await asyncio.sleep(2)
                    continue
                
                # ========== SOURCE 1: HELIUS WEBHOOK (Real-time) ==========
                # Check for new tokens from webhook first (priority!)
                webhook_opportunities = []
                if self.webhook_server and self.webhook_server.is_running:
                    webhook_tokens = self.webhook_server.get_pending_tokens()
                    if webhook_tokens:
                        logger.info(f"🔔 Got {len(webhook_tokens)} tokens from Helius webhook!")
                        
                        for wt in webhook_tokens:
                            # AGE FILTER - skip tokens that are TOO OLD
                            # Note: age=0 is OK for webhook (super fresh tokens!)
                            from ..config import EARLY_TOKEN_MAX_AGE_MINUTES
                            if wt.age_minutes > EARLY_TOKEN_MAX_AGE_MINUTES:
                                logger.debug(f"   ⏳ [{wt.symbol}] too old ({wt.age_minutes:.1f}m > {EARLY_TOKEN_MAX_AGE_MINUTES}m), skipping")
                                continue
                            
                            # Fetch metadata if symbol is unknown
                            symbol = wt.symbol
                            name = wt.name
                            if symbol == "???" or symbol == "NEW" or not symbol:
                                metadata = await self.webhook_server.fetch_token_metadata(wt.mint)
                                if metadata:
                                    symbol = metadata.get("symbol") or wt.mint[:6]
                                    name = metadata.get("name") or ""
                                    logger.info(f"   📛 Fetched metadata: [{symbol}] {name}")
                            
                            # Convert webhook token to TokenOpportunity
                            from .token_scanner import TokenOpportunity
                            
                            # 🎯 Dynamic Phase Detection for Whale buys
                            # If it came from a whale swap, we need to check if it's already on Raydium/Jupiter
                            detected_phase = "BONDING_CURVE" # Default for fresh pump tokens
                            if wt.whale_address:
                                logger.info(f"🔍 Whale detected! Checking phase for {wt.mint[:8]}...")
                                detected_phase = await self.validator.detect_token_phase(wt.mint)
                                logger.info(f"📍 {wt.symbol} Phase detected: {detected_phase}")

                            opp = TokenOpportunity(
                                mint=wt.mint,
                                symbol=symbol,
                                name=name,
                                price_usd=0,
                                volume_24h=0,
                                liquidity_usd=0,
                                price_change_24h=0,
                                market_cap=0,
                                safety_score=0,  # Mark as needing extra validation
                                phase=detected_phase,
                                whale_address=wt.whale_address
                            )
                            webhook_opportunities.append(opp)
                            logger.info(f"   🆕 [{symbol}] from webhook (age: {wt.age_minutes:.1f}m) ✅")
                
                dexscreener_opportunities = await self.scanner.scan_opportunities()
                
                # COMBINED MODE: Webhook (1-5 min) + DexScreener (5-30 min)
                # Webhook tokens get priority (listed first)
                combined_opportunities = webhook_opportunities + (dexscreener_opportunities or [])
                
                # 🔄 DEDUPLICATION: Remove duplicate tokens (same token from webhook + dexscreener)
                seen_mints = set()
                opportunities = []
                for opp in combined_opportunities:
                    if opp.mint not in seen_mints:
                        seen_mints.add(opp.mint)
                        opportunities.append(opp)
                    else:
                        logger.debug(f"   🔄 Deduplicated {opp.symbol} (already in list)")
                
                logger.info(f"📋 Total opportunities: {len(opportunities)} (webhook={len(webhook_opportunities)}, dex={len(dexscreener_opportunities or [])})")
                
                if not opportunities:
                    # No tokens from either source
                    await asyncio.sleep(2.0)
                    continue
                
                # 2. Evaluate ONLY FIRST valid opportunity (single position mode)
                bought = False
                for opp in opportunities:
                    # 🚦 TRACE: Log every opportunity entering the loop
                    # logger.info(f"🚦 Processing {opp.symbol} ({opp.mint[:6]})...")
                    
                    if not self.is_running or bought:
                        break
                    
                    # 🐋 WHALE ALERT: Log if this is a whale buy
                    if opp.whale_address:
                        logger.info(f"🐋 WHALE ALERT! Followed wallet {opp.whale_address[:8]}... is buying {opp.symbol}!")
                        # Optionally: increase safety score or bypass some filters
                        opp.safety_score = 100 # High priority for whales
                    
                    if opp.mint in self.active_positions:
                        logger.warning(f"   ⏭️ [{opp.symbol}] Already in active_positions, skipping")
                        continue
                        
                    # 🚫 BLACKLIST CHECK: Skip banned tokens
                    if self.blacklist.is_blocked(opp.mint):
                        logger.info(f"   ⛔ [{opp.symbol}] is currently BLACKLISTED, skipping")
                        continue
                    
                    # ⏳ COOLDOWN CHECK: Don't re-buy recently traded tokens
                    from ..config import COOLDOWN_SECONDS
                    if opp.mint in self.traded_tokens:
                        last_trade_time = self.traded_tokens[opp.mint]
                        elapsed_seconds = time.time() - last_trade_time
                        if elapsed_seconds < COOLDOWN_SECONDS:
                            cooldown_remaining = int((COOLDOWN_SECONDS - elapsed_seconds) / 60)
                            logger.info(
                                f"   ⏳ [{opp.symbol}] In COOLDOWN ({cooldown_remaining}m remaining of {COOLDOWN_SECONDS//60}m), skipping"
                            )
                            continue
                        else:
                            # Cooldown expired, remove from dict to save memory
                            del self.traded_tokens[opp.mint]
                            logger.debug(f"   ✅ [{opp.symbol}] Cooldown expired, can trade again")
                        
                    if opp.mint in self.processing_tokens:
                        logger.warning(f"   ⏭️ [{opp.symbol}] Already in processing_tokens, skipping")
                        continue
                        
                    logger.info(f"🔎 Analyzing {opp.symbol} ({opp.mint[:6]}...)...")
                    
                    # Fetch detailed data for strategy
                    token_data = TokenData(
                        mint=opp.mint,
                        price=opp.price_usd,
                        volume_24h=opp.volume_24h,
                        liquidity_usd=opp.liquidity_usd,
                        price_change_24h=opp.price_change_24h,
                        rsi=25.0,  # Placeholder
                        avg_volume=opp.volume_24h / 2.0
                    )
                    
                    # 3. Strategy Check
                    should_buy = await self.scalping_strategy.should_enter(token_data, self.validator)
                    
                    # 4. EXTRA SAFETY CHECK for webhook tokens (may lack data)
                    if should_buy and opp.safety_score == 0:
                        # Webhook tokens need extra validation
                        logger.info(f"   ⚠️ Safety=0, running extra validation...")
                        
                        # Check liquidity via pool_quality_filter + market context
                        try:
                            # Get liquidity from pool quality - USE ACTUAL PHASE!
                            pool_check = await self.validator.pool_quality_filter(opp.phase, opp.mint)
                            liquidity = pool_check.get("liquidity_sol", 0)
                            
                            # Get volume from market context
                            market_ctx = await self.validator.get_market_context(opp.mint)
                            volume = market_ctx.get("vol_h1", 0)
                            
                            # Check minimum liquidity and progress (Momentum Filter)
                            progress = pool_check.get("progress", 0)
                            from ..config import MIN_BONDING_CURVE_PROGRESS
                            
                            # 🔄 PHASE-SPECIFIC VALIDATION
                            if opp.phase == "BONDING_CURVE":
                                # Strict checks for bonding curve tokens
                                if liquidity < 5:  # Min 5 SOL liquidity (from config)
                                    logger.warning(f"   ❌ Low liquidity ({liquidity:.1f} SOL), SKIPPING")
                                    should_buy = False
                                elif progress < MIN_BONDING_CURVE_PROGRESS:
                                    logger.warning(f"   ❌ Stagnant token (Progress={progress:.1f}% < {MIN_BONDING_CURVE_PROGRESS}%), SKIPPING")
                                    should_buy = False
                                else:
                                    # ✅ Update opportunity with fetched data
                                    opp.liquidity_usd = liquidity * 150 # Est USD if SOL=150
                                    opp.volume_24h = volume
                                    # Try to get mcap too
                                    if market_ctx.get("fdv"):
                                        opp.market_cap = market_ctx.get("fdv")
                                    else:
                                        # Fallback: estimate mcap from liquidity for BONDING_CURVE
                                        # Typical pump.fun: 85 SOL = 100% curve = ~$60k-80k mcap range
                                        # Simple linear estimate:
                                        opp.market_cap = (liquidity / 85.0) * 80000 
                                        
                                    logger.info(f"   ✅ Validation passed: Progress={progress:.1f}%, Liq={liquidity:.1f} SOL, Vol=${volume:.0f}")
                            else:
                                # For JUPITER/RAYDIUM: trust pool_quality_filter result
                                # If it passed pool_quality_filter, we trust it (even with liq=0)
                                if pool_check.get("passed"):
                                    logger.info(f"   ✅ {opp.phase} token passed pool_quality_filter (reason: {pool_check.get('reason')})")
                                    # Update opportunity with whatever data we have
                                    if liquidity > 0:
                                        opp.liquidity_usd = liquidity * 150
                                    if volume > 0:
                                        opp.volume_24h = volume
                                    if market_ctx.get("fdv"):
                                        opp.market_cap = market_ctx.get("fdv")
                                else:
                                    logger.warning(f"   ❌ {opp.phase} token failed pool_quality_filter: {pool_check.get('reason')}")
                                    should_buy = False
                        except Exception as e:
                            logger.warning(f"   ❌ Validation error: {e}, SKIPPING")
                            should_buy = False
                    
                    # 5. 🛡️ RUGCHECK: Final safety validation before buy
                    if should_buy:
                        # Use EARLY mode rugcheck for fresh pump.fun tokens
                        is_early_token = opp.phase in ["BONDING_CURVE", "PUMPSWAP"]
                        mode_label = "EARLY" if is_early_token else "STABLE"
                        logger.info(f"   🔍 Running RUGCHECK ({mode_label} mode) for {opp.symbol}...")
                        try:
                            rugcheck_result = await self.rugchecker.check(opp.mint, early_mode=is_early_token)
                            logger.info(f"   📊 Rugcheck result: score={rugcheck_result.risk_score}, level={rugcheck_result.risk_level}, safe={rugcheck_result.is_safe}")
                            if not rugcheck_result.is_safe:
                                logger.warning(f"   ⛔ RUGCHECK FAILED: {rugcheck_result.risk_level} ({rugcheck_result.risk_score}/100)")
                                for warning in rugcheck_result.warnings[:3]:
                                    logger.warning(f"      ⚠️ {warning}")
                                should_buy = False
                            else:
                                logger.info(f"   ✅ Rugcheck passed: {rugcheck_result.risk_level} ({rugcheck_result.risk_score}/100)")
                        except Exception as e:
                            logger.warning(f"   ⚠️ Rugcheck error: {e}, proceeding with caution")
                            # Don't block on rugcheck errors - it's an extra safety layer
                    
                    # 6. 📈 MOMENTUM FILTER: Don't buy tokens that are dumping OR DEAD
                    if should_buy:
                        # v12.3.1: Require POSITIVE momentum for EARLY tokens
                        # Dead/flat tokens won't pump - we need active momentum
                        min_momentum = 5.0  # Require at least +5% momentum
                        
                        if opp.price_change_24h < -10:
                            logger.warning(f"   📉 MOMENTUM FILTER: Token dumping ({opp.price_change_24h:+.1f}%), SKIPPING")
                            should_buy = False
                        elif opp.price_change_24h < min_momentum:
                            # Token is flat or slightly negative - likely dead
                            logger.warning(f"   📊 MOMENTUM FILTER: Token stagnant ({opp.price_change_24h:+.1f}% < +{min_momentum}%), SKIPPING")
                            should_buy = False
                        else:
                            logger.info(f"   ✅ Momentum OK: {opp.price_change_24h:+.1f}% (min {min_momentum}%)")
                    
                    # 7. 📊 SCORING SYSTEM: Probabilistic quality check
                    if should_buy:
                        from .entry_scorer import EntryScorer
                        
                        scorer = EntryScorer(threshold=50.0)  # LOWERED from 70 to 50
                        
                        # Get pool_check and volume_metrics if available
                        pool_check = locals().get('pool_check')
                        
                        # Calculate comprehensive score
                        token_score = await scorer.score_token(
                            opportunity=opp,
                            rugcheck_result=locals().get('rugcheck_result'),
                            volume_metrics=None,  # Would need to pass from scanner
                            pool_check=pool_check
                        )
                        
                        if not token_score.passed_threshold:
                            logger.warning(
                                f"   ⛔ SCORE TOO LOW: {token_score.total:.0f}/100 < 50 "
                                f"(threshold) | {token_score.reason}"
                            )
                            should_buy = False
                        else:
                            logger.info(
                                f"   ✅ SCORE PASSED: {token_score.total:.0f}/100 >= 50 | "
                                f"{token_score.reason}"
                            )
                    
                    if should_buy:
                        logger.info(f"   🚀 EXECUTING ENTRY for {opp.symbol}...")
                        # Pass rugcheck_result to execute_entry if available
                        rc_res = locals().get('rugcheck_result')
                        success = await self.execute_entry(opp, rugcheck_result=rc_res)
                        if success:
                            bought = True  # Stop looking at more opportunities
                            logger.info(f"🎯 Bought {opp.symbol} - Now monitoring until sell...")
                        else:
                            logger.warning(f"   ❌ execute_entry failed for {opp.symbol}")
                        
                        if ONE_SHOT_MODE:
                            logger.info("🛑 One Shot Mode enabled. Stopping.")
                            self.is_running = False
                            break
                    else:
                        logger.info(f"   ❌ {opp.symbol} did NOT pass all checks (should_buy=False)")
                # Sleep between scans
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(5)
    
    async def execute_entry(self, opp, rugcheck_result=None) -> bool:
        """Execute buy for an opportunity. Returns True if bought successfully."""
        
        # 🚫 COOLDOWN CHECK: Don't re-buy recently traded tokens
        if self.blacklist.is_blocked(opp.mint):
            logger.info(f"⏳ [{opp.symbol}] Blacklisted/Cooldown active, skipping")
            return False
        
        async with self.positions_lock:
            if opp.mint in self.active_positions or opp.mint in self.processing_tokens:
                return False
            self.processing_tokens.add(opp.mint)
            
        try:
            logger.info(f"🚀 EXECUTING ENTRY on {opp.symbol}...")
            
            # 🎯 Check CONVEX MODE first
            from ..config import CONVEX_MODE_ENABLED, CONVEX_SCOUT_SIZE_SOL
            
            use_convex = CONVEX_MODE_ENABLED
            
            if use_convex:
                # CONVEX STRATEGY: Use scout size and create state machine position
                buy_amount = CONVEX_SCOUT_SIZE_SOL
                strategy_profile = "CONVEX_SCOUT"
                
                # Create convex position
                convex_pos = self.convex_machine.create_position(opp.mint, opp.symbol)
                
                logger.info(
                    f"🔍 [{opp.symbol}] CONVEX MODE: Scout entry with {buy_amount} SOL | "
                    f"Selection will determine if we add size"
                )
            else:
                # LEGACY: Use EARLY/STABLE strategy profile
                strategy_profile = self._get_strategy_profile(opp)
                strategy_config = STABLE_STRATEGY if strategy_profile == "STABLE" else EARLY_STRATEGY
                buy_amount = strategy_config["buy_amount_sol"]
            
            # 🛡️ ENFORCE RISK LIMITS via RiskConfigManager
            if self.risk_config_manager:
                try:
                    risk_cfg = self.risk_config_manager.get_config()
                    max_pos = risk_cfg.position_limits.max_position_sol
                    if buy_amount > max_pos:
                        logger.warning(f"⚠️ Capping buy amount {buy_amount:.4f} SOL to risk limit {max_pos:.4f} SOL")
                        buy_amount = max_pos
                except Exception as e:
                    logger.warning(f"Failed to check risk config: {e}")
            
            # Use Trading Manager to route (Paper/Live)
            result = await self.trading_manager.execute_buy(
                mint=opp.mint,
                symbol=opp.symbol,
                amount_sol=buy_amount,
                phase=opp.phase
            )
            
            if result:
                success, signature = result
                if success:
                    logger.info(f"✅ BOUGHT {opp.symbol} - TX: {signature}")
                    
                    # 🚫 Set cooldown IMMEDIATELY after buy (prevents re-buying within 1h)
                    self.traded_tokens[opp.mint] = time.time()
                    logger.info(f"⏳ Cooldown activated for {opp.symbol} (1h from now)")
                    
                    # 🚨 SEND RICH TELEGRAM ALERT (Premium format)
                    if self.telegram:
                        try:
                            # Extract extra fields from rugcheck_result if available
                            extra_fields = {}
                            if rugcheck_result:
                                extra_fields = {
                                    "risk_score": rugcheck_result.risk_score,
                                    "risk_level": rugcheck_result.risk_level,
                                    "top_10_pct": rugcheck_result.top_10_holders_pct,
                                    "dev_pct": rugcheck_result.dev_holding_pct,
                                    "is_lp_locked": rugcheck_result.liquidity_locked,
                                    "mint_revoked": rugcheck_result.mint_authority_revoked,
                                    "freeze_revoked": rugcheck_result.freeze_authority_revoked,
                                    "age_hours": rugcheck_result.token_age_hours
                                }
                            
                            await self.telegram.send_trade_alert(
                                action="BUY",
                                mint=opp.mint,
                                amount_sol=buy_amount,
                                phase=opp.phase,
                                tx_signature=signature,
                                token_name=opp.name,
                                token_symbol=opp.symbol,
                                mcap=opp.market_cap,
                                liquidity=opp.liquidity_usd,
                                profile_name=strategy_profile,
                                trailing_pct=strategy_config.get("trailing_stop_pct", 10.0),
                                volume_24h=opp.volume_24h,
                                wallet_name="Bot Strategy",
                                **extra_fields
                            )
                            logger.info(f"📤 Premium Telegram buy alert sent for {opp.symbol}")
                        except Exception as e:
                            logger.error(f"❌ Telegram premium alert failed: {e}")
                    
                    # Get current price for position tracking
                    price_data = await self.price_feed.get_price(opp.mint)
                    entry_price = price_data.price_sol if price_data else opp.price_usd
                    
                    # Register position in active_positions
                    # Get tokens from TX parsing (bypasses RPC delay)
                    tokens_from_tx = getattr(self.trading_manager, 'last_tokens_received', 0) or 0
                    
                    # 🛡️ Initialize LP Monitor and Dev Tracker
                    from .lp_monitor import LPMonitor
                    from .dev_tracker import DevTracker
                    
                    lp_monitor = LPMonitor(
                        mint=opp.mint,
                        phase=opp.phase,
                        validator=self.validator,
                        check_interval=5.0,
                        alert_threshold_pct=5.0
                    )
                    await lp_monitor.start()
                    
                    dev_tracker = None
                    # Note: RugcheckResult has dev_holding_pct but not dev_wallet address
                    # DevTracker would need the actual wallet address from holder analysis
                    # For now, skip DevTracker if no dev_wallet available
                    dev_wallet_addr = getattr(rugcheck_result, 'dev_wallet', None) if rugcheck_result else None
                    if dev_wallet_addr:
                        dev_tracker = DevTracker(
                            dev_wallet=dev_wallet_addr,
                            mint=opp.mint,
                            rpc_client=self.client,
                            rugcheck_result=rugcheck_result
                        )
                        await dev_tracker.start()
                    
                    # 🎯 Initialize Retail Trap Detector
                    from .retail_trap_detector import RetailTrapDetector
                    retail_trap = RetailTrapDetector()
                    
                    # v12.3 SHARP EDGE: Per-position components
                    eas_tracker = DynamicEASTracker(
                        initial_eas=1.5,  # Default, will be recalculated
                        mint=opp.mint,
                        position_size_sol=buy_amount
                    )
                    partial_manager = PartialExitManager()
                    narrative_analyzer = NarrativeAnalyzer()
                    runner_protection = RunnerProtectionLayer()
                    
                    # Start trade tracking
                    self.trade_tracker.start_trade(
                        mint=opp.mint,
                        symbol=opp.symbol,
                        entry_sol=buy_amount,
                        entry_price=entry_price,
                        eas_score=1.5,  # Default
                        total_score=75,  # Default
                        liquidity=opp.liquidity_usd,
                        strategy_profile=strategy_profile
                    )
                    
                    async with self.positions_lock:
                        self.active_positions[opp.mint] = {
                            "symbol": opp.symbol,
                            "entry_sol": buy_amount,
                            "entry_price": entry_price,
                            "entry_time": asyncio.get_event_loop().time(),
                            "phase": opp.phase,
                            "highest_value": buy_amount,
                            "tx_signature": signature,
                            "tokens_from_tx": tokens_from_tx,
                            "strategy_profile": strategy_profile,
                            "initial_liquidity": opp.liquidity_usd,
                            "initial_mcap": opp.market_cap,
                            "rugcheck_result": rugcheck_result,
                            "lp_monitor": lp_monitor,
                            "dev_tracker": dev_tracker,
                            "retail_trap": retail_trap,
                            # v12.3 Components
                            "eas_tracker": eas_tracker,
                            "partial_manager": partial_manager,
                            "narrative_analyzer": narrative_analyzer,
                            "runner_protection": runner_protection,
                            "prev_risk_level": "LOW",
                            "task": None
                        }

                    
                    # Start position monitoring in background
                    monitor_task = asyncio.create_task(
                        self._monitor_position(opp.mint, opp.symbol)
                    )
                    self.active_positions[opp.mint]["task"] = monitor_task
                    
                    # CONVEX: Also start scout selection monitor
                    if use_convex and 'convex_pos' in locals():
                        # Store convex state in position
                        self.active_positions[opp.mint]["convex_state"] = "SCOUT_EVAL"
                        self.active_positions[opp.mint]["convex_position"] = convex_pos
                        
                        # Execute scout entry in state machine
                        convex_pos.scout_entry_sol = buy_amount
                        convex_pos.scout_entry_time = time.time()
                        convex_pos.total_entry_sol = buy_amount
                        convex_pos.scout_tokens = tokens_from_tx
                        convex_pos.total_tokens = tokens_from_tx
                        
                        convex_pos.transition_to(
                            ConvexState.SCOUT_EVAL,
                            "Scout entry filled",
                            {"entry_sol": buy_amount, "signature": signature}
                        )
                        
                        # Start selection monitor
                        selection_task = asyncio.create_task(
                            self._monitor_scout_selection(convex_pos, opp.phase)
                        )
                        self.active_positions[opp.mint]["selection_task"] = selection_task
                        
                        logger.info(f"🔍 [{opp.symbol}] Scout selection monitor started")
                    
                    # Pause webhook while monitoring (no new tokens needed)
                    if self.webhook_server:
                        self.webhook_server.paused = True
                        logger.info("⏸️ Webhook paused while monitoring position")
                    
                    await self.save_state()
                    return True
                else:
                    logger.warning(f"Buy failed for {opp.symbol}: {signature}")
                    return False
            else:
                logger.warning(f"No result from execute_buy for {opp.symbol}")
                return False
                
        except Exception as e:
            logger.error(f"Buy failed for {opp.mint}: {e}")
            return False
        finally:
            async with self.positions_lock:
                self.processing_tokens.discard(opp.mint)

    # =========================================================================
    # CONVEX STRATEGY: Scout Selection Monitor
    # =========================================================================
    
    async def _monitor_scout_selection(self, convex_pos: ConvexPosition, phase: str = "BONDING_CURVE"):
        """
        Monitor a SCOUT_EVAL position for selection signals.
        
        This runs in parallel with _monitor_position but focuses on:
        1. Capturing baseline metrics (first 45s)
        2. Evaluating selection signals (tx accel, wallet influx, HH, etc.)
        3. Triggering CONFIRM_ADD if selection score >= 2 for 2 consecutive windows
        4. Timeout exit if no selection after 3 minutes
        
        If selection occurs, adds size and transitions to CONVICTION.
        """
        mint = convex_pos.mint
        symbol = convex_pos.symbol
        
        logger.info(f"🔍 [{symbol}] Starting SCOUT selection monitor...")
        
        # Import config
        from ..config import (
            CONVEX_BASELINE_CAPTURE_SEC,
            CONVEX_EVAL_WINDOW_SEC,
            CONVEX_SCOUT_TIMEOUT_SEC
        )
        
        baseline_capture_sec = CONVEX_BASELINE_CAPTURE_SEC
        eval_window_sec = CONVEX_EVAL_WINDOW_SEC
        timeout_sec = CONVEX_SCOUT_TIMEOUT_SEC
        
        # State
        last_eval_time = 0
        baseline_complete = False
        
        # For tracking signals
        last_tx_count = 0
        last_wallet_count = 0
        last_price = 0
        last_curve_progress = 0
        
        try:
            while self.is_running and mint in self.active_positions:
                await asyncio.sleep(2)  # Check every 2s
                
                # Check if still in SCOUT_EVAL
                convex_pos = self.convex_machine.get_position(mint)
                if not convex_pos or convex_pos.state != ConvexState.SCOUT_EVAL:
                    logger.info(f"🔄 [{symbol}] No longer in SCOUT_EVAL, stopping selection monitor")
                    break
                
                now = time.time()
                time_in_eval = convex_pos.get_time_in_state()
                
                # Get current metrics from price feed / validator
                try:
                    # Get current data
                    price_data = await self.price_feed.get_price(mint)
                    current_price = price_data.price if price_data else 0
                    
                    # Get transaction metrics from validator
                    pool_data = await self.validator.pool_quality_filter(phase, mint)
                    current_tx_count = pool_data.get("txns_h1", 0) or 0
                    curve_progress = pool_data.get("progress", 0) or 0
                    
                    # Estimate buyers (simplified - would need more data for real count)
                    # For now use transaction delta as proxy
                    tx_delta = current_tx_count - last_tx_count if last_tx_count > 0 else 0
                    tx_per_sec = tx_delta / 2.0 if tx_delta > 0 else 0.1  # 2s window
                    
                    # Curve slope calculation
                    curve_delta = curve_progress - last_curve_progress if last_curve_progress > 0 else 0
                    curve_slope_per_min = (curve_delta / 2.0) * 60  # Convert to per minute
                    
                    # Higher high check
                    had_hh = current_price > last_price if last_price > 0 else False
                    
                except Exception as e:
                    logger.debug(f"Error fetching metrics for {symbol}: {e}")
                    await asyncio.sleep(2)
                    continue
                
                # Phase 1: Capture baseline (first 45s)
                if time_in_eval < baseline_capture_sec:
                    self.convex_machine.update_baseline(
                        convex_pos,
                        tx_per_second=tx_per_sec,
                        new_buyers_per_min=tx_delta * 30,  # Rough estimate
                        curve_progress_per_min=curve_slope_per_min,
                        current_price=current_price
                    )
                    logger.debug(
                        f"📊 [{symbol}] Baseline capture: {time_in_eval:.0f}s | "
                        f"txps={tx_per_sec:.2f}, curve={curve_slope_per_min:.2f}%/min"
                    )
                    
                    # Update tracking vars
                    last_tx_count = current_tx_count
                    last_wallet_count = 0
                    last_price = current_price
                    last_curve_progress = curve_progress
                    continue
                
                # Mark baseline complete
                if not baseline_complete:
                    baseline_complete = True
                    logger.info(
                        f"📊 [{symbol}] Baseline captured: "
                        f"txps={convex_pos.baseline.tx_per_second:.2f}, "
                        f"curve={convex_pos.baseline.curve_progress_per_min:.2f}%/min, "
                        f"samples={convex_pos.baseline.samples}"
                    )
                
                # Phase 2: Evaluate selection every EVAL_WINDOW_SEC
                if now - last_eval_time >= eval_window_sec:
                    last_eval_time = now
                    
                    # Simplified sell absorption (would need candle data)
                    # For now, check if price recovered after dip
                    had_absorption = current_price >= last_price * 0.98  # Recovered from 2% dip
                    
                    should_confirm, signals, score = self.convex_machine.evaluate_selection(
                        position=convex_pos,
                        current_txps=tx_per_sec,
                        current_new_buyers=tx_delta * 30,
                        current_curve_slope=curve_slope_per_min,
                        current_price=current_price,
                        had_red_candle_bought=had_absorption
                    )
                    
                    if should_confirm:
                        logger.info(f"🚀 [{symbol}] SELECTION CONFIRMED! Score={score}/5 - Checking quality gate...")
                        
                        # ========================================
                        # 🛡️ ANTI-FAKE QUALITY FILTER
                        # ========================================
                        # Reject wash trading / tx spam / micro-buy recycling
                        # Require REAL wallet activity before CONFIRM
                        
                        quality_passed = True
                        quality_reason = ""

                        activity = await self._get_recent_buy_activity(mint=mint, window_sec=60)
                        unique_wallets = activity["unique_wallets"]
                        avg_buy = activity["avg_buy_sol"]
                        max_buy = activity["max_buy_sol"]
                        recycled_wallets = activity["recycled_wallets"]
                        wallets_min_buy = activity["wallets_min_buy"]
                        buy_events = activity["buy_events"]

                        # Real quality check: require ≥5 unique wallets >0.1 SOL OR ≥1 buy >0.5 SOL
                        if buy_events == 0:
                            quality_passed = False
                            quality_reason = "No recent buys detected in last 60s"
                        elif wallets_min_buy < 5 and max_buy < 0.5:
                            quality_passed = False
                            quality_reason = "Insufficient real buyers"

                        metrics_summary = (
                            f"unique_wallets={unique_wallets}, "
                            f"wallets>=0.1SOL={wallets_min_buy}, "
                            f"avg_buy={avg_buy:.3f} SOL, "
                            f"max_buy={max_buy:.3f} SOL, "
                            f"recycled_wallets={recycled_wallets}, "
                            f"buy_events={buy_events}"
                        )
                        if quality_reason:
                            quality_reason = f"{quality_reason} | {metrics_summary}"
                        else:
                            quality_reason = metrics_summary
                        
                        if not quality_passed:
                            logger.warning(
                                f"⛔ [{symbol}] QUALITY FILTER FAILED: {quality_reason} - "
                                f"Exiting scout (likely fake pump)"
                            )
                            
                            # Exit without CONFIRM
                            self.convex_machine.exit_position(convex_pos, f"QUALITY_FAIL: {quality_reason}")
                            if mint in self.active_positions:
                                self.active_positions[mint]["convex_state"] = "EXITED"
                            
                            # Sell scout position
                            await self.trading_manager.execute_sell(
                                mint=mint,
                                symbol=symbol,
                                sell_pct=100.0
                            )
                            break
                        
                        logger.info(
                            f"✅ [{symbol}] Quality gate passed - Proceeding to CONFIRM add | {quality_reason}"
                        )
                        
                        # Execute CONFIRM add
                        success, sig = await self.convex_machine.execute_confirm_add(
                            position=convex_pos,
                            trading_manager=self.trading_manager,
                            rugchecker=self.rugchecker,
                            phase=phase
                        )
                        
                        if success:
                            logger.info(f"✅ [{symbol}] CONFIRM add successful - Now in CONVICTION phase")
                            
                            # Update active_positions with new total
                            if mint in self.active_positions:
                                self.active_positions[mint]["entry_sol"] = convex_pos.total_entry_sol
                                self.active_positions[mint]["convex_state"] = "CONVICTION"
                            
                            # Send Telegram notification
                            if self.telegram:
                                await self.telegram.send_message(
                                    f"🚀 <b>SELECTION CONFIRMED</b>\n\n"
                                    f"🪙 {symbol}\n"
                                    f"📊 Score: {score}/5\n"
                                    f"💰 Total: {convex_pos.total_entry_sol:.4f} SOL\n"
                                    f"🎯 Now in CONVICTION phase"
                                )
                        else:
                            logger.warning(f"❌ [{symbol}] CONFIRM add failed, staying in scout")

                        break  # Exit monitor either way
                
                # Update tracking vars
                last_tx_count = current_tx_count
                last_price = current_price
                last_curve_progress = curve_progress
                
                # Phase 3: Timeout check
                if self.convex_machine.should_scout_timeout(convex_pos):
                    logger.warning(
                        f"⏰ [{symbol}] SCOUT TIMEOUT after {timeout_sec}s | "
                        f"Final score: {convex_pos.selection_score}/5 - Token is DEAD"
                    )
                    
                    # Exit scout position
                    self.convex_machine.exit_position(convex_pos, "SCOUT_TIMEOUT")
                    
                    # Update active position state
                    if mint in self.active_positions:
                        self.active_positions[mint]["convex_state"] = "EXITED"
                        self.active_positions[mint]["exit_reason"] = "SCOUT_TIMEOUT"
                    
                    # Send timeout notification
                    if self.telegram:
                        await self.telegram.send_message(
                            f"⏰ <b>SCOUT TIMEOUT</b>\n\n"
                            f"🪙 {symbol}\n"
                            f"📊 Final score: {convex_pos.selection_score}/5\n"
                            f"❌ Token is DEAD - exiting scout"
                        )
                    
                    break
                    
        except Exception as e:
            logger.error(f"Scout selection monitor error for {symbol}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        
        logger.info(f"🔍 [{symbol}] Scout selection monitor ended")

    async def _get_recent_buy_activity(self, mint: str, window_sec: int = 60) -> Dict[str, Any]:
        """Fetch recent buy activity for a mint within a time window."""
        summary = {
            "unique_wallets": 0,
            "avg_buy_sol": 0.0,
            "max_buy_sol": 0.0,
            "recycled_wallets": 0,
            "wallets_min_buy": 0,
            "buy_events": 0,
        }

        if not self.client:
            return summary

        def _extract_value(response: Any) -> Optional[Any]:
            if response is None:
                return None
            if hasattr(response, "value"):
                return response.value
            if isinstance(response, dict):
                return response.get("result") or response.get("value")
            return None

        now = int(time.time())
        try:
            signatures_resp = await self.client.get_signatures_for_address(
                Pubkey.from_string(mint),
                limit=50
            )
            signatures_value = _extract_value(signatures_resp) or []
        except Exception as exc:
            logger.warning(f"⚠️ Failed to fetch signatures for {mint}: {exc}")
            return summary

        recent_signatures = [
            entry for entry in signatures_value
            if entry and entry.get("blockTime") and entry["blockTime"] >= now - window_sec
        ]

        if not recent_signatures:
            return summary

        buy_sizes: List[float] = []
        wallet_buy_counts: Dict[str, int] = {}
        wallets_min_buy: set = set()

        for entry in recent_signatures:
            signature = entry.get("signature")
            if not signature:
                continue

            try:
                tx_resp = await self.client.get_transaction(
                    Signature.from_string(signature),
                    encoding="jsonParsed",
                    max_supported_transaction_version=0
                )
                tx_value = _extract_value(tx_resp)
            except Exception as exc:
                logger.debug(f"Failed to fetch transaction {signature}: {exc}")
                continue

            if not tx_value:
                continue

            meta = tx_value.get("meta") or {}
            message = (tx_value.get("transaction") or {}).get("message") or {}
            account_keys = message.get("accountKeys") or []

            key_list: List[str] = []
            signers: set = set()
            for key in account_keys:
                if isinstance(key, dict):
                    pubkey = key.get("pubkey")
                    if pubkey:
                        key_list.append(pubkey)
                        if key.get("signer"):
                            signers.add(pubkey)
                elif isinstance(key, str):
                    key_list.append(key)

            pre_balances = meta.get("preBalances") or []
            post_balances = meta.get("postBalances") or []
            sol_deltas: Dict[str, float] = {}
            for idx, pubkey in enumerate(key_list):
                if idx < len(pre_balances) and idx < len(post_balances):
                    sol_deltas[pubkey] = (pre_balances[idx] - post_balances[idx]) / 1e9

            def token_amount(balance: Dict[str, Any]) -> float:
                ui_amount = (balance.get("uiTokenAmount") or {}).get("uiAmount")
                if ui_amount is not None:
                    return float(ui_amount)
                amount = (balance.get("uiTokenAmount") or {}).get("amount")
                decimals = (balance.get("uiTokenAmount") or {}).get("decimals", 0)
                if amount is not None:
                    try:
                        return int(amount) / (10 ** decimals)
                    except (ValueError, TypeError):
                        return 0.0
                return 0.0

            pre_token_balances = meta.get("preTokenBalances") or []
            post_token_balances = meta.get("postTokenBalances") or []
            pre_by_owner: Dict[str, float] = {}
            post_by_owner: Dict[str, float] = {}

            for bal in pre_token_balances:
                if bal.get("mint") != mint:
                    continue
                owner = bal.get("owner")
                if not owner:
                    continue
                pre_by_owner[owner] = token_amount(bal)

            for bal in post_token_balances:
                if bal.get("mint") != mint:
                    continue
                owner = bal.get("owner")
                if not owner:
                    continue
                post_by_owner[owner] = token_amount(bal)

            owners = set(pre_by_owner.keys()) | set(post_by_owner.keys())
            if not owners and signers:
                owners = set(signers)

            for owner in owners:
                delta = post_by_owner.get(owner, 0.0) - pre_by_owner.get(owner, 0.0)
                if delta <= 0:
                    continue

                sol_spent = sol_deltas.get(owner)
                if sol_spent is None and signers:
                    if len(signers) == 1:
                        sol_spent = sol_deltas.get(next(iter(signers)), 0.0)
                    else:
                        sol_spent = 0.0
                if sol_spent is None:
                    sol_spent = 0.0

                sol_spent = max(sol_spent, 0.0)
                buy_sizes.append(sol_spent)
                wallet_buy_counts[owner] = wallet_buy_counts.get(owner, 0) + 1
                if sol_spent >= 0.1:
                    wallets_min_buy.add(owner)

        if not buy_sizes:
            return summary

        summary["buy_events"] = len(buy_sizes)
        summary["unique_wallets"] = len(wallet_buy_counts)
        summary["avg_buy_sol"] = sum(buy_sizes) / len(buy_sizes)
        summary["max_buy_sol"] = max(buy_sizes)
        summary["recycled_wallets"] = len([w for w, c in wallet_buy_counts.items() if c > 1])
        summary["wallets_min_buy"] = len(wallets_min_buy)
        return summary

    async def _monitor_position(self, mint: str, symbol: str):
        """
        Monitor a position and sell when stop is triggered.
        
        QUICK SCALP STRATEGY:
        1. Time limit: Sell after MAX_HOLD_TIME_MINUTES regardless
        2. Dynamic BE: When value >= entry + fees, activate trailing
        3. After BE: Trailing stop from peak
        4. Hard stop: Exit at -50% if drops before BE
        """
        # Get strategy profile early for logging
        position = self.active_positions.get(mint, {})
        strategy_profile = position.get("strategy_profile", "EARLY")
        
        logger.info(f"📊 Starting position monitor for {symbol} ({strategy_profile})...")
        
        # 🛡️ RPC PROPAGATION DELAY FIX: Grace period for RPC to sync
        # This prevents false "zero tokens" detection right after buy
        await asyncio.sleep(3)
        logger.debug(f"   ⏳ Grace period complete, starting price monitoring...")
        
        # Import config values
        from ..config import (
            ESTIMATED_SLIPPAGE_PCT,
            JITO_TIP_PER_TX,
            NO_PUMP_TIMEOUT_MINUTES,
            NO_PUMP_MIN_GAIN_PCT,
            LIQUIDITY_DROP_THRESHOLD_PCT
        )
        
        # Get entry info and strategy profile
        position = self.active_positions.get(mint, {})
        entry_sol = position.get("entry_sol", 0.2)
        entry_time = position.get("entry_time", asyncio.get_event_loop().time())
        
        # 🎯 Get strategy-specific parameters
        strategy_profile = position.get("strategy_profile", "EARLY")
        strategy_config = STABLE_STRATEGY if strategy_profile == "STABLE" else EARLY_STRATEGY
        
        trailing_pct = strategy_config["trailing_stop_pct"]
        hard_stop_pct = strategy_config["hard_stop_pct"]
        max_hold_seconds = strategy_config["max_hold_minutes"] * 60
        
        logger.info(f"   📋 Profile: {strategy_profile} | Trailing: {trailing_pct}% | SL: {hard_stop_pct}% | Max: {max_hold_seconds//60}min")
        
        # Calculate dynamic break-even value (entry + all fees)
        # 🔧 CONSERVATIVE ESTIMATE: Don't assume 2x Jito tips since we use Helius Smart Routing
        # which sometimes doesn't use Jito. Use 0.5% buffer for safety instead.
        total_fees = entry_sol * (ESTIMATED_SLIPPAGE_PCT / 100)  # 2% slippage
        # Don't multiply tips by 2 - Helius may not use Jito for both txs
        total_tips = JITO_TIP_PER_TX * 0.5  # Conservative estimate (not always 2x)
        buffer = entry_sol * 0.005  # Extra 0.5% buffer for safety
        break_even_value = entry_sol + total_fees + total_tips + buffer
        
        logger.info(f"   💰 Entry: {entry_sol:.4f} SOL | BE Value: {break_even_value:.4f} SOL (fees: {total_fees + total_tips + buffer:.4f})")
        
        # State tracking (params already set by strategy profile above)
        break_even_activated = False
        highest_value = entry_sol
        last_value = entry_sol  # Track previous value for crash detection
        current_value = entry_sol # Initial value is entry
        pnl_pct = 0.0             # Start at 0%
        last_telegram_update = 0
        telegram_update_interval = 15  # More frequent updates for quick scalp
        
        # Anti-rug config
        CRASH_THRESHOLD_PCT = 20.0  # Emergency sell if drops >20% in single check
        POST_BE_TRAILING_PCT = 8.0  # Tighter trailing after break-even
        
        # v12.3.1: WARMUP PERIOD - Let price data stabilize before reacting
        # During warmup: collect data, update trackers, but NO kill-switches or exits
        WARMUP_SECONDS = 10.0  # 10 seconds to let Jupiter quotes stabilize
        
        # Take profit tiers tracking
        from ..config import TAKE_PROFIT_TIERS
        executed_tiers = set()  # Track which tiers have been executed
        remaining_pct = 100.0   # Track remaining position percentage
        
        # 📊 Track entry liquidity for LP drop detection
        entry_liquidity = position.get("initial_liquidity", 0.0)
        entry_mcap = position.get("initial_mcap", 0.0)
        
        try:
            price_data = await self.price_feed.get_price(mint)
            if price_data and price_data.liquidity_usd > 0:
                entry_liquidity = price_data.liquidity_usd
            
            logger.info(f"   💧 Entry Liquidity: ${entry_liquidity:,.0f}")
            if entry_mcap > 0:
                logger.info(f"   📈 Entry MCap: ${entry_mcap:,.0f}")
        except:
            pass
        
        try:
            while self.is_running and mint in self.active_positions:
                # 🔧 OPTIMIZED: State-based polling intervals to save RPC credits
                # SCOUT: 5s (we don't need fast updates for micro-position)
                # CONFIRM: 2s 
                # CONVICTION: 1s (need fast reaction for larger position)
                position = self.active_positions.get(mint, {})
                convex_state = position.get("convex_state", "CONVICTION")  # Default to fast
                
                poll_intervals = {
                    "SCOUT_EVAL": 5.0,
                    "CONFIRM_ADD": 2.0,
                    "CONVICTION": 1.0,
                    "MOONBAG": 2.0,
                }
                poll_interval = poll_intervals.get(convex_state, 0.5)
                
                await asyncio.sleep(poll_interval)
                
                if mint not in self.active_positions:
                    break
                position = self.active_positions.get(mint)
                if not position:
                    break
                
                # 🚀 REAL-TIME PRICING: Use Jupiter Quote instead of slow APIs
                # Get token amount from position (saved at buy time)
                token_amount = position.get("tokens_from_tx", 0)
                
                if token_amount > 0:
                    # Use Jupiter Quote for instant price (not cached DexScreener)
                    current_value = await self.price_feed.get_token_value_sol(
                        mint=mint,
                        token_amount=int(token_amount),
                        decimals=6  # Standard pump.fun decimals
                    )
                    
                    if current_value > 0:
                        # Calculate PnL from real-time value
                        pnl_pct = ((current_value - entry_sol) / entry_sol) * 100
                    else:
                        # Jupiter failed, use last known value or entry
                        current_value = last_value if last_value > 0 else entry_sol
                        pnl_pct = ((current_value - entry_sol) / entry_sol) * 100
                        logger.debug(f"⚠️ Jupiter Quote failed, using last value: {current_value:.4f}")
                else:
                    # Fallback: use old method if no token_amount
                    price_data = await self.price_feed.get_price(mint)
                    if not price_data:
                        continue
                    
                    current_price = price_data.price_sol
                    self.trading_manager.update_position_price(mint, current_price)
                    
                    paper_pos = self.trading_manager.get_position_value(mint)
                    if paper_pos:
                        current_value = paper_pos.get("current_value", entry_sol)
                        pnl_pct = paper_pos.get("pnl_pct", 0.0)
                    else:
                        entry_price = position.get("entry_price", current_price)
                        price_change = (current_price - entry_price) / entry_price if entry_price > 0 else 0
                        current_value = entry_sol * (1 + price_change)
                        pnl_pct = price_change * 100
                
                # Check time limit FIRST
                current_loop_time = asyncio.get_event_loop().time()
                hold_time_seconds = current_loop_time - entry_time
                hold_time_minutes = hold_time_seconds / 60
                
                # ========================================
                # L0 KILL-SWITCHES (HIGHEST PRIORITY)
                # ========================================
                
                # CHECK 1: LP Integrity Monitor
                lp_monitor = position.get("lp_monitor")
                if lp_monitor:
                    if not await lp_monitor.check_integrity():
                        # v12.3: Publish to EventBus instead of hard exit
                        self.event_bus.publish(
                            EventType.LP_DROP_SEVERE, 
                            {"reason": lp_monitor.last_alert_reason}
                        )
                        logger.warning(f"⚠️ LP INTEGRITY ALERT for {symbol}: {lp_monitor.last_alert_reason}")
                
                # CHECK 2: Dev Activity Tracker
                dev_tracker = position.get("dev_tracker")
                if dev_tracker:
                    if await dev_tracker.check_dev_activity():
                        # v12.3: Publish to EventBus
                        self.event_bus.publish(
                            EventType.DEV_SELL_CONFIRMED, 
                            {"mint": mint}
                        )
                        logger.warning(f"⚠️ DEV ACTIVITY DETECTED for {symbol}")
                
                # CHECK 3: Retail Trap Detector
                retail_trap = position.get("retail_trap")
                if retail_trap:
                    # Feed datapoint (price + holder estimate + avg size)
                    estimated_holders = int(current_value * 100)  # Rough estimate
                    avg_size_estimate = current_value / max(estimated_holders, 1)
                    
                    retail_trap.add_datapoint(
                        price=current_value,
                        holder_count=estimated_holders,
                        avg_trade_size=avg_size_estimate
                    )
                    
                    # Check for trap pattern
                    trap_signal = retail_trap.detect()
                    
                    if trap_signal.detected:
                        # v12.3: Publish to EventBus as MAJOR event (need another cause for exit)
                        self.event_bus.publish(
                            EventType.PRICE_IMPACT_EXTREME if trap_signal.confidence > 0.8 else EventType.SLIPPAGE_SPIKE,
                            {"signal": trap_signal.reason, "conf": trap_signal.confidence}
                        )
                        logger.info(f"ℹ️ Retail distribution signal for {symbol}: {trap_signal.reason} (conf: {trap_signal.confidence:.2f})")
                
                
                # Update highest value for trailing stop
                if current_value > highest_value:
                    highest_value = current_value
                    self.active_positions[mint]["highest_value"] = highest_value
                
                # ========================================
                # 🚨 SCOUT BYPASS: NO PROFIT MANAGEMENT
                # ========================================
                # CRITICAL: SCOUT positions are NEVER managed for profit
                # Only check: timeout + critical events
                # NO: trailing, partials, runner, narrative, take-profit
                
                if convex_state == "SCOUT_EVAL":
                    # Get convex position for timeout check
                    convex_pos = position.get("convex_position")
                    
                    if convex_pos and self.convex_machine.should_scout_timeout(convex_pos):
                        logger.warning(
                            f"⏱️ [{symbol}] SCOUT TIMEOUT ({convex_pos.get_time_in_state():.0f}s) - "
                            f"No selection confirmation, exiting scout"
                        )
                        
                        # Exit position
                        success, sig = await self.trading_manager.execute_sell(
                            mint=mint,
                            symbol=symbol,
                            sell_pct=100.0
                        )
                        
                        if success:
                            self.convex_machine.exit_position(convex_pos, "SCOUT_TIMEOUT")
                            self.active_positions[mint]["convex_state"] = "EXITED"
                            
                            await self._cleanup_position(
                                mint, symbol, "SCOUT_TIMEOUT", 
                                current_value, entry_sol, sig
                            )
                        
                        break
                    
                    # SCOUT: Skip all profit management below, only continue monitoring
                    continue
                
                # ========================================
                # v12.3 SHARP EDGE UPDATES (CONVICTION+)
                # ========================================
                
                # Get v12.3 components
                eas_tracker = position.get("eas_tracker")
                partial_manager = position.get("partial_manager")
                narrative_analyzer = position.get("narrative_analyzer")
                runner_protection = position.get("runner_protection")
                prev_risk_level = position.get("prev_risk_level", "LOW")
                
                # 1. Update EAS tracker (with hysteresis)
                current_risk = "LOW"
                if eas_tracker:
                    try:
                        current_liq_data = await self.price_feed.get_price(mint)
                        liq_usd = current_liq_data.liquidity_usd if current_liq_data else 0
                        
                        eas_tracker.calculate_current_eas(
                            current_price=current_value,
                            liquidity_usd=liq_usd,
                            price_momentum=pnl_pct
                        )
                        current_risk = eas_tracker.get_risk_level()
                        self.active_positions[mint]["prev_risk_level"] = current_risk
                    except Exception as e:
                        logger.debug(f"EAS tracker update failed: {e}")
                
                # 2. Update runner state (gradual)
                if runner_protection:
                    runner_protection.update(pnl_pct)
                    runner_status = runner_protection.get_status()
                    
                    if runner_status.get("state") not in ["NORMAL", None]:
                        logger.info(f"🏃 {symbol} State={runner_status.get('state')} PnL={pnl_pct:.1f}%")
                
                # 3. Check event bus for critical events (SKIP DURING WARMUP)
                should_emergency_exit, event_reason = self.event_bus.should_emergency_exit()
                if should_emergency_exit and hold_time_seconds >= WARMUP_SECONDS:
                    logger.error(f"🚨 EVENT BUS TRIGGERED: {event_reason} for {symbol}")
                    
                    success, sig = await self.trading_manager.execute_sell(
                        mint=mint,
                        symbol=symbol,
                        sell_pct=100.0
                    )
                    
                    if success:
                        logger.info(f"⚡ EVENT EXIT {symbol} - {event_reason} - TX: {sig}")
                        self.trade_tracker.end_trade(
                            mint=mint,
                            exit_price=current_value,
                            exit_reason=event_reason,
                            realized_pnl_sol=current_value - entry_sol,
                            realized_pnl_pct=pnl_pct
                        )
                        
                        # Update session stats
                        await self._update_session_stats(
                            pnl_sol=current_value - entry_sol,
                            is_full_exit=True
                        )
                        
                    if mint in self.active_positions:
                        del self.active_positions[mint]
                    self.event_bus.reset()
                    break
                elif should_emergency_exit:
                    logger.info(f"⏳ [WARMUP] Event detected but waiting {WARMUP_SECONDS - hold_time_seconds:.1f}s...")
                
                # 4. Update MFE/MAE tracking
                self.trade_tracker.update_excursion(mint, pnl_pct)
                
                # 5. Check partial exits on risk transitions (SKIP DURING WARMUP)
                if partial_manager and runner_protection and hold_time_seconds >= WARMUP_SECONDS:
                    runner_state = runner_protection.get_status().get("state", "NORMAL")
                    
                    should_partial, partial_pct, partial_reason = partial_manager.should_partial_exit(
                        risk_level=current_risk,
                        runner_state=runner_state,
                        previous_risk=prev_risk_level
                    )
                    
                    if should_partial:
                        logger.warning(f"📊 PARTIAL EXIT: {partial_pct}% of {symbol} - {partial_reason}")
                        
                        success, sig = await self.trading_manager.execute_sell(
                            mint=mint,
                            symbol=symbol,
                            sell_pct=partial_pct
                        )
                        
                        if success:
                            # Calculate partial PnL
                            portion = partial_pct / 100.0
                            partial_pnl_sol = (current_value - entry_sol) * portion
                            await self._update_session_stats(
                                pnl_sol=partial_pnl_sol,
                                is_full_exit=False
                            )
                            logger.info(f"✅ Partial sold {partial_pct}% of {symbol} at {pnl_pct:.1f}% PnL - TX: {sig}")
                            if self.telegram:
                                try:
                                    await self.telegram.send_message(
                                        f"📊 <b>PARTIAL EXIT - {partial_reason}</b>\n\n"
                                        f"Token: <b>{symbol}</b>\n"
                                        f"Sold: {partial_pct}%\n"
                                        f"PnL: {pnl_pct:+.1f}%\n"
                                        f"Remaining: {partial_manager.get_remaining_pct():.0f}%"
                                    )
                                except:
                                    pass
                
                # 6. Update narrative phase
                narrative_multi = 1.0
                if narrative_analyzer:
                    try:
                        # Estimate buy/sell counts from price movement
                        buy_count = int(max(0, pnl_pct) * 2) + 5
                        sell_count = int(max(0, -pnl_pct) * 2) + 3
                        
                        await narrative_analyzer.calculate_metrics(
                            unique_wallets=int(hold_time_seconds * 0.5),
                            buy_count=buy_count,
                            sell_count=sell_count
                        )
                        narrative_multi = narrative_analyzer.get_trailing_multiplier()
                    except Exception as e:
                        logger.debug(f"Narrative update failed: {e}")
                
                # 7. Calculate dynamic trailing with all multipliers
                base_trailing = strategy_config.get("trailing_stop_pct", 10.0)
                
                # State multiplier from runner protection
                state_multi = 1.0
                if runner_protection:
                    state_multi = runner_protection.get_dynamic_trailing(1.0)
                
                # EAS risk multiplier
                eas_multi = 1.0
                if eas_tracker:
                    eas_multi = eas_tracker.get_trailing_adjustment(1.0, current_risk)
                
                # Final dynamic trailing with INTELLIGENT FLOOR
                dynamic_trailing_raw = base_trailing * state_multi * eas_multi * narrative_multi
                
                # 🛡️ INTELLIGENT FLOOR: Never allow trailing to go below 5% after break-even
                # This prevents multipliers from accidentally disabling protection
                MIN_TRAILING_PCT = 5.0 if break_even_activated else base_trailing
                dynamic_trailing = max(MIN_TRAILING_PCT, dynamic_trailing_raw)
                
                # Log if multipliers are affecting the trailing significantly
                if abs(dynamic_trailing - base_trailing) > 1.0:
                    logger.debug(
                        f"🎚️ Dynamic trailing: {base_trailing:.1f}% → {dynamic_trailing:.1f}% "
                        f"(state={state_multi:.2f} eas={eas_multi:.2f} narr={narrative_multi:.2f})"
                    )
                
                # Alert if floor was applied (means multipliers tried to zero it out)
                if dynamic_trailing_raw < MIN_TRAILING_PCT and break_even_activated:
                    logger.warning(
                        f"⚠️ Trailing floor applied! Raw={dynamic_trailing_raw:.1f}% → Floor={MIN_TRAILING_PCT:.1f}% "
                        f"| Multipliers: state={state_multi:.2f} eas={eas_multi:.2f} narr={narrative_multi:.2f}"
                    )
                
                # ========================================
                # END v12.3 UPDATES
                # ========================================
                
                # 🚨 EMERGENCY SELL: Crash detection (rug pull protection)
                # v12.3.1: SKIP DURING WARMUP - need stable data before detecting "crashes"
                if hold_time_seconds >= WARMUP_SECONDS and last_value > 0 and current_value < last_value * (1 - CRASH_THRESHOLD_PCT / 100):
                    crash_pct = (1 - current_value / last_value) * 100
                    logger.warning(f"🚨 CRASH DETECTED! {symbol} dropped {crash_pct:.1f}% in single check - EMERGENCY SELL!")
                    
                    # Immediate sell - don't wait for normal stop logic
                    success, sig = await self.trading_manager.execute_sell(
                        mint=mint,
                        symbol=symbol,
                        sell_pct=100.0
                    )
                    
                    if success:
                        logger.info(f"⚡ EMERGENCY SOLD {symbol} - Crash protection triggered - TX: {sig}")
                        
                        # Calculate hold time
                        hold_time_str = f"{int(hold_time_seconds // 60)}m {int(hold_time_seconds % 60)}s"
                        
                        # Send complete sell alert
                        if self.telegram:
                            try:
                                await self.telegram.send_sell_alert(
                                    mint=mint,
                                    reason=f"CRASH (-{crash_pct:.0f}%)",
                                    entry_sol=entry_sol,
                                    exit_sol=current_value,
                                    pnl_pct=pnl_pct,
                                    hold_time=hold_time_str,
                                    token_symbol=symbol,
                                    tx_signature=sig if sig != "LIVE_TX" else None
                                )
                            except Exception as tg_err:
                                logger.debug(f"Telegram sell alert failed: {tg_err}")
                        
                        # Remove position and exit loop
                        if mint in self.active_positions:
                            del self.active_positions[mint]
                        
                        # Update session stats for /status
                        pnl_sol = current_value - entry_sol
                        if pnl_sol >= 0:
                            self.session_stats["wins"] += 1
                            self.session_stats["pnl_positive"] += pnl_sol
                        else:
                            self.session_stats["losses"] += 1
                            self.session_stats["pnl_negative"] += abs(pnl_sol)
                        
                        # Save state to persist stats
                        await self.save_state()
                            
                        # Add to blacklist (Crash = dangerous)
                        self.blacklist.add_timeout(mint, duration_minutes=120)  # 2h ban for crashers
                        break
                    else:
                        # Sell failed - log error but still remove position to avoid stuck state
                        logger.error(f"❌ EMERGENCY SELL FAILED for {symbol} - position stuck!")
                        if self.telegram:
                            try:
                                await self.telegram.send_message(
                                    f"⚠️ <b>EMERGENCY SELL FAILED</b>\n\n"
                                    f"Token: <b>{symbol}</b>\n"
                                    f"Reason: CRASH detected but sell failed\n"
                                    f"⚠️ Manual intervention may be needed!"
                                )
                            except:
                                pass
                        # Still remove from active positions to prevent infinite loop
                        if mint in self.active_positions:
                            del self.active_positions[mint]
                        break
                
                # 💧 LIQUIDITY DROP DETECTION: Emergency sell if LP removed
                if entry_liquidity > 0:
                    # GRACE PERIOD: Don't check LP drop in the first 60s (prevents false indexing 0-liq alerts)
                    if hold_time_seconds > 60:
                        try:
                            current_liq_data = await self.price_feed.get_price(mint)
                            if current_liq_data:
                                current_liquidity = current_liq_data.liquidity_usd
                                
                                # 🛡️ API LAG PROTECTION: If DexScreener says 0, but it's a fresh token, 
                                # it's almost certainly an indexing delay, not a 100% rug.
                                # A real rug on a token we just bought would be suspicious if it hits 0 exactly.
                                if current_liquidity == 0 and hold_time_seconds < 300: # 5 min grace for 0-liq specifically
                                    # Skip for now, don't trigger emergency sell on potential API lag
                                    pass
                                else:
                                    liq_drop_pct = ((entry_liquidity - current_liquidity) / entry_liquidity) * 100
                                    
                                    if liq_drop_pct >= LIQUIDITY_DROP_THRESHOLD_PCT:
                                        logger.warning(f"💧 LIQUIDITY DROP! {symbol} LP dropped {liq_drop_pct:.1f}% - EMERGENCY SELL!")
                                        
                                        success, sig = await self.trading_manager.execute_sell(
                                            mint=mint,
                                            symbol=symbol,
                                            sell_pct=100.0
                                        )
                                        
                                        if success:
                                            logger.info(f"⚡ SOLD {symbol} - LP protection triggered - TX: {sig}")
                                            
                                            # Calculate hold time
                                            hold_time_str = f"{int(hold_time_seconds // 60)}m {int(hold_time_seconds % 60)}s"
                                            
                                            # Send complete sell alert
                                            if self.telegram:
                                                try:
                                                    await self.telegram.send_sell_alert(
                                                        mint=mint,
                                                        reason=f"LP_DROP (-{liq_drop_pct:.0f}%)",
                                                        entry_sol=entry_sol,
                                                        exit_sol=current_value,
                                                        pnl_pct=pnl_pct,
                                                        hold_time=hold_time_str,
                                                        token_symbol=symbol,
                                                        tx_signature=sig if sig != "LIVE_TX" else None
                                                    )
                                                except Exception as tg_err:
                                                    logger.debug(f"Telegram sell alert failed: {tg_err}")
                                            
                                            if mint in self.active_positions:
                                                del self.active_positions[mint]
                                                
                                            # Update stats
                                            pnl_sol = current_value - entry_sol
                                            await self._update_session_stats(pnl_sol=pnl_sol, is_full_exit=True)
                                            
                                            # Add to blacklist (LP Pull = dangerous)
                                            self.blacklist.add_timeout(mint, duration_minutes=240)  # 4h ban for ruggers
                                            break
                                        else:
                                            # Sell failed - log error but still remove position
                                            logger.error(f"❌ LP DROP SELL FAILED for {symbol}")
                                            if self.telegram:
                                                try:
                                                    await self.telegram.send_message(
                                                        f"⚠️ <b>LP DROP SELL FAILED</b>\n\n"
                                                        f"Token: <b>{symbol}</b>\n"
                                                        f"LP Drop: -{liq_drop_pct:.1f}%\n"
                                                        f"⚠️ Manual intervention may be needed!"
                                                    )
                                                except:
                                                    pass
                                            if mint in self.active_positions:
                                                del self.active_positions[mint]
                                            break
                        except Exception as liq_err:
                            logger.error(f"Error checking liquidity drop: {liq_err}")
                
                # Update last_value for next crash check
                last_value = current_value
                
                # 💰 TAKE PROFIT TIERS: Partial sells to lock in gains
                for tier_pnl, tier_sell_pct in TAKE_PROFIT_TIERS:
                    if tier_pnl not in executed_tiers and pnl_pct >= tier_pnl:
                        # Calculate actual sell percentage based on remaining position
                        actual_sell_pct = min(tier_sell_pct, remaining_pct)
                        
                        if actual_sell_pct > 0:
                            logger.info(f"💰 TAKE PROFIT! {symbol} hit +{tier_pnl}% - Selling {actual_sell_pct:.0f}%...")
                            
                            success, sig = await self.trading_manager.execute_sell(
                                mint=mint,
                                symbol=symbol,
                                sell_pct=actual_sell_pct
                            )
                            
                            if success:
                                executed_tiers.add(tier_pnl)
                                remaining_pct -= actual_sell_pct
                                
                                # Update session stats for partial sell
                                # Calculate partial PnL: (Total Current Value - Total Entry) * (sold_pct / 100)
                                # BUT current_value is total position value.
                                # entry_sol is total entry.
                                # sold_portion = actual_sell_pct / 100.0
                                tp_pnl_sol = (current_value - entry_sol) * (actual_sell_pct / 100.0)
                                await self._update_session_stats(pnl_sol=tp_pnl_sol, is_full_exit=False)
                                
                                logger.info(f"✅ Sold {actual_sell_pct:.0f}% at +{pnl_pct:.1f}% | Remaining: {remaining_pct:.0f}%")
                                
                                if self.telegram:
                                    try:
                                        await self.telegram.send_message(
                                            f"💰 <b>TAKE PROFIT</b>\n\n"
                                            f"Token: <b>{symbol}</b>\n"
                                            f"Tier: +{tier_pnl:.0f}%\n"
                                            f"Sold: {actual_sell_pct:.0f}%\n"
                                            f"Remaining: {remaining_pct:.0f}%\n"
                                            f"🚀 Moonbag continues!"
                                        )
                                    except:
                                        pass
                                
                                # Exit loop if no position left
                                if remaining_pct <= 0:
                                    if mint in self.active_positions:
                                        del self.active_positions[mint]
                                    break
                
                # Check if position was fully sold via TP
                if remaining_pct <= 0:
                    break
                
                # Check if DYNAMIC break-even should be activated
                # BE = when current_value >= break_even_value (entry + fees)
                if not break_even_activated and current_value >= break_even_value:
                    break_even_activated = True
                    self.active_positions[mint]["break_even_activated"] = True
                    profit_sol = current_value - entry_sol
                    logger.info(f"🔒 {symbol}: BREAK-EVEN ACTIVATED! Value={current_value:.4f} >= BE={break_even_value:.4f} (+{profit_sol:.4f} SOL)")
                    
                    # Telegram notification for break-even
                    if self.telegram:
                        try:
                            await self.telegram.send_message(
                                f"🔒 <b>BREAK-EVEN ACTIVATED</b>\n\n"
                                f"Token: <b>{symbol}</b>\n"
                                f"Value: {current_value:.4f} SOL\n"
                                f"Profit: +{profit_sol:.4f} SOL\n\n"
                                f"✅ Fees covered - profit locked!\n"
                                f"🚀 Trailing {trailing_pct}% from peak"
                            )
                        except:
                            pass
                
                # Determine stop level based on state
                if break_even_activated:
                    # v12.3 SHARP EDGE: Use calculated dynamic trailing stop
                    trailing_stop_value = highest_value * (1 - dynamic_trailing / 100)
                    stop_value = max(break_even_value, trailing_stop_value)  # Never below BE!
                    # Show which protection is active (USE ACTUAL DYNAMIC VALUE)
                    if trailing_stop_value >= break_even_value:
                        stop_type = f"TRAILING {dynamic_trailing:.1f}%"  # Show actual dynamic trailing
                    else:
                        stop_type = f"BE_FLOOR"  # BE is protecting, not pure trailing
                else:
                    # Before break-even: only hard stop loss
                    stop_value = entry_sol * (1 + hard_stop_pct / 100)
                    stop_type = f"HARD SL {hard_stop_pct}%"
                
                # 📊 Update active_positions with LIVE data for /position command
                self.active_positions[mint]["current_value"] = current_value
                self.active_positions[mint]["pnl_pct"] = pnl_pct
                self.active_positions[mint]["peak_value"] = highest_value
                self.active_positions[mint]["stop_loss_value"] = stop_value
                
                # 🔍 DEBUG: Log BE status every loop for debugging
                if not break_even_activated:
                    be_gap = break_even_value - current_value
                    logger.debug(
                        f"   💭 BE Check: Current={current_value:.4f} vs BE={break_even_value:.4f} "
                        f"| Gap={be_gap:.4f} SOL ({(be_gap/entry_sol)*100:.1f}%)"
                    )
                
                # Log position status
                be_status = "🔒BE" if break_even_activated else "⏳"
                logger.info(
                    f"📈 {symbol}: PnL={pnl_pct:+.1f}% | "
                    f"Val={current_value:.4f} | Peak={highest_value:.4f} | "
                    f"Stop={stop_value:.4f} ({stop_type}) {be_status}"
                )
                
                # Send Telegram update every 30 seconds
                import time as time_module
                current_time = time_module.time()
                if current_time - last_telegram_update >= telegram_update_interval:
                    last_telegram_update = current_time
                    if self.telegram:
                        try:
                            await self.telegram.send_position_update(
                                mint=mint,
                                entry_sol=entry_sol,
                                current_value=current_value,
                                pnl_pct=pnl_pct,
                                highest_value=highest_value,
                                trailing_stop_pct=trailing_pct if break_even_activated else 0,
                                token_symbol=symbol,
                                break_even_sold=break_even_activated
                            )
                        except Exception as e:
                            logger.debug(f"Telegram position update failed: {e}")
                
                # Check if stop triggered
                should_sell = False
                sell_reason = ""
                
                # Use per-strategy no_pump_timeout (fallback to global)
                no_pump_timeout_min = strategy_config.get("no_pump_timeout_min", NO_PUMP_TIMEOUT_MINUTES)
                no_pump_timeout_seconds = no_pump_timeout_min * 60
                
                # 1. TIME LIMIT CHECK (skip if max_hold = 0, means disabled for moonbag)
                if max_hold_seconds > 0 and hold_time_seconds >= max_hold_seconds:
                    should_sell = True
                    sell_reason = f"TIME_LIMIT ({max_hold_seconds//60}min)"
                    logger.info(f"⏰ {symbol}: Time limit reached ({hold_time_minutes:.1f}m)")
                
                # 2. NO PUMP TIMEOUT - exit if no growth within X minutes
                # For STABLE: require +5% to be considered "growing" (not just >0%)
                elif not break_even_activated and hold_time_seconds >= no_pump_timeout_seconds:
                    # STABLE strategy: need at least +5% to bypass timeout
                    # EARLY strategy: now ALSO need +5% (User requested strict check)
                    min_growth_pct = NO_PUMP_MIN_GAIN_PCT
                    is_growing = pnl_pct > min_growth_pct
                    
                    if pnl_pct < NO_PUMP_MIN_GAIN_PCT and not is_growing:
                        should_sell = True
                        sell_reason = f"NO_PUMP_TIMEOUT ({no_pump_timeout_min}min, need +{NO_PUMP_MIN_GAIN_PCT}%)"
                        logger.info(f"📉 {symbol}: No pump in {no_pump_timeout_min}min (PnL={pnl_pct:+.1f}%), selling")
                    elif is_growing:
                        logger.info(f"🚀 {symbol}: Token is growing ({pnl_pct:+.1f}% > {min_growth_pct}%), holding...")
                
                # 3. After break-even: check trailing stop
                elif break_even_activated:
                    if current_value <= stop_value:
                        should_sell = True
                        sell_reason = f"TRAILING_STOP ({trailing_pct}% from peak)"
                
                # 4. Before break-even: only hard stop loss
                else:
                    if pnl_pct <= hard_stop_pct:
                        should_sell = True
                        sell_reason = f"HARD_STOP ({hard_stop_pct}%)"
                
                if should_sell:
                    logger.info(f"🛑 SELLING {symbol} - Reason: {sell_reason}")
                    
                    # Execute sell
                    success, sig = await self.trading_manager.execute_sell(
                        mint=mint,
                        symbol=symbol,
                        sell_pct=100.0
                    )
                    
                    if success:
                        profit_status = "PROFIT" if pnl_pct > 0 else "LOSS"
                        logger.info(f"✅ SOLD {symbol} - {profit_status} {pnl_pct:+.1f}% - TX: {sig}")
                        
                        # v12.3: End trade tracking
                        self.trade_tracker.end_trade(
                            mint=mint,
                            exit_price=current_value,
                            exit_reason=sell_reason,
                            realized_pnl_sol=current_value - entry_sol,
                            realized_pnl_pct=pnl_pct
                        )
                        
                        # 📊 Update session stats
                        pnl_sol = current_value - entry_sol
                        await self._update_session_stats(
                            pnl_sol=pnl_sol,
                            is_full_exit=True
                        )
                        
                        # 🚫 Add to blacklist (Standard 1h cooldown)
                        self.blacklist.add_timeout(mint, duration_minutes=60)
                        
                        # Send Telegram notification
                        if self.telegram:
                            try:
                                await self.telegram.send_sell_alert(
                                    mint=mint,
                                    reason=sell_reason,
                                    entry_sol=entry_sol,
                                    exit_sol=current_value,
                                    pnl_pct=pnl_pct,
                                    hold_time=f"{hold_time_minutes:.0f}m",
                                    token_symbol=symbol,
                                    tx_signature=sig
                                )
                                logger.info(f"📤 Telegram sell alert sent for {symbol}")
                            except Exception as e:
                                logger.error(f"❌ Telegram sell alert failed: {e}")
                    else:
                        logger.error(f"❌ Failed to sell {symbol}: {sig}")
                    
                    # Remove position regardless of sell success
                    async with self.positions_lock:
                        if mint in self.active_positions:
                            del self.active_positions[mint]
                    
                    await self.save_state()
                    break
                    
        except asyncio.CancelledError:
            logger.info(f"Monitor cancelled for {symbol}")
        except Exception as e:
            logger.error(f"Monitor error for {symbol}: {e}")
        finally:
            # Cleanup: ensure position is removed if monitor exits unexpectedly
            if mint in self.active_positions:
                logger.warning(f"Monitor exited but position still exists for {symbol}")
            
            # Resume webhook to receive new tokens
            if self.webhook_server:
                self.webhook_server.paused = False
                # Clear old tokens from queue
                self.webhook_server.token_queue = asyncio.Queue(maxsize=500)
                logger.info("▶️ Webhook resumed - ready for new tokens")

    async def _cleanup(self):
        """Cleanup resources on shutdown."""
        if self.session:
            await self.session.close()
        if self.db:
            self.db.close()
        logger.info("Cleanup complete.")

    def stop(self):
        self.is_running = False
