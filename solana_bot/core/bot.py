from __future__ import annotations

import asyncio
import logging
import json
import os
from pathlib import Path

from solana_bot.config import Settings
from solana_bot.core.bounce_recovery import BounceRecoveryManager
from solana_bot.core.convex_state_machine import ConvexStateMachine
from solana_bot.core.dev_tracker import DevTracker
from solana_bot.core.dynamic_eas_tracker import EASTracker
from solana_bot.core.dynamic_trailing import TrailingCalculator
from solana_bot.core.entry_scorer import EntryScorer
from solana_bot.core.entry_scorer import EntryScorer
from solana_bot.core.event_bus import EventBus
from solana_bot.core.insightx_client import InsightXClient
from solana_bot.core.lp_monitor import LPMonitor
from solana_bot.core.models import (
    BotStats,
    Position,
    PositionState,
    TokenInfo,
    Phase,
)
from solana_bot.core.narrative_analyzer import NarrativeAnalyzer
from solana_bot.core.partial_exit_manager import PartialExitManager
from solana_bot.core.position_monitor import PositionMonitor
from solana_bot.core.birdeye_price_client import BirdeyePriceClient
from solana_bot.core.realtime_price_feed import RealTimePriceFeed
from solana_bot.core.position_price_monitor import PositionPriceMonitor
from solana_bot.core.price_feed import PriceFeed
from solana_bot.core.rugcheck import Rugchecker
from solana_bot.core.runner_protection import RunnerProtection
from solana_bot.core.runtime_supervisor import RuntimeSupervisor
from solana_bot.core.telegram_notifier import TelegramAction, TelegramNotifier
from solana_bot.core.token_scanner import TokenScanner
from solana_bot.core.trade_metrics_logger import TradeMetricsLogger, get_metrics_logger
from solana_bot.core.trader import Trader
from solana_bot.core.validator import Validator
from solana_bot.core.wallet_tracker import WalletTracker, CopySignal
from solana_bot.utils.time import utc_ts


class Bot:
    def __init__(
        self,
        settings: Settings,
        scanner: TokenScanner | None = None,
        trader: Trader | None = None,
        event_bus: EventBus | None = None,
        metrics_logger: TradeMetricsLogger | None = None,
        supervisor: RuntimeSupervisor | None = None,
        price_feed: PriceFeed | None = None,
    ) -> None:
        self.settings = settings
        self.logger = logging.getLogger("solana_bot.bot")

        self.scanner = scanner or TokenScanner(settings)
        self.validator = Validator(settings)
        self.rugchecker = Rugchecker(settings)
        self.entry_scorer = EntryScorer(settings)
        self.price_feed = price_feed or PriceFeed(settings, dexscreener=self.scanner.dex_client)
        self.trader = trader or Trader(settings)
        self.event_bus = event_bus or EventBus()
        self.state_machine = ConvexStateMachine(settings)
        self.eas_tracker = EASTracker()
        self.trailing_calc = TrailingCalculator(settings)
        self.partial_exit_manager = PartialExitManager(settings)
        self.runner_protection = RunnerProtection()
        self.narrative_analyzer = NarrativeAnalyzer()
        self.dev_tracker = DevTracker(settings)
        self.lp_monitor = LPMonitor(settings)
        self.metrics_logger = metrics_logger or get_metrics_logger(settings)
        self.metrics_logger = metrics_logger or get_metrics_logger(settings)
        self.supervisor = supervisor
        self.position_monitor = PositionMonitor(settings)
        self.insightx_client = InsightXClient(settings.INSIGHTX_API_KEY) if settings.INSIGHTX_API_KEY else None
        self.telegram = TelegramNotifier(settings) if settings.TELEGRAM_ENABLED else None

        # Pattern analyzer for pump & dump detection
        from solana_bot.core.pattern_analyzer import PatternAnalyzer
        self.pattern_analyzer = PatternAnalyzer(settings)
        
        # Entry signal detector for optimal timing
        from solana_bot.core.entry_signal_detector import EntrySignalDetector
        self.entry_signal_detector = EntrySignalDetector(settings)
        
        # Bounce recovery manager for re-entering after stop losses
        self.bounce_manager = BounceRecoveryManager(settings)
        
        # Real-time price feed for instant updates
        self.birdeye_client = BirdeyePriceClient(settings)
        self.realtime_feed = RealTimePriceFeed(
            settings=settings,
            pumpportal=self.scanner.pumpportal,
            birdeye=self.birdeye_client,
            dex_client=self.scanner.dex_client,
        )
        
        # Dedicated position price monitor (aggressive polling)
        self.position_price_monitor = PositionPriceMonitor(settings, self.realtime_feed)
        
        if settings.ENABLE_CRIMINOLOGY:
            from solana_bot.core.criminology import DevDetective
            self.dev_detective = DevDetective(settings)
        else:
            self.dev_detective = None
        
        # Copy Trading
        self.wallet_tracker: WalletTracker | None = None
        self.wallet_webhook = None
        if settings.COPY_TRADING_ENABLED:
            self.wallet_tracker = WalletTracker(settings)
            from solana_bot.core.helius_wallet_webhook import HeliusWalletWebhook
            self.wallet_webhook = HeliusWalletWebhook(settings, self.wallet_tracker)

        self.positions: dict[str, Position] = {}
        self.stats = BotStats(cash_sol=settings.SIM_STARTING_BALANCE_SOL)
        self._running = True
        self.paused = False
        self.bot_active = True  # NEW: Bot control flag (start/stop via Telegram)
        self.cooldown_until = 0.0
        self._last_scan_ts = 0.0
        
        # SOL price cache for Telegram notifications
        self._sol_price_eur: float = 200.0  # Default fallback
        self._sol_price_usd: float = 0.0
        self._cached_sol_price: float = 0.0
        self._sol_price_last_update: float = 0.0
        self._last_balance_check: float = 0.0
        self._last_position_sync: float = 0.0  # NEW: Track last position sync
        
        # Token blacklist (avoid buying specific tokens)
        self._blacklist: set[str] = set()
        self._blacklist_file = Path("logs/blacklist.json")

    async def run(self) -> None:
        await self.initialize()
        
        # Attach Supabase logging handler
        try:
            from supabase_sync import SupabaseLogHandler
            root_logger = logging.getLogger()
            root_logger.addHandler(SupabaseLogHandler())
            self.logger.info("✅ Remote logging to Supabase enabled")
        except Exception as e:
            self.logger.warning(f"Failed to enable remote logging: {e}")
            
        self.logger.info("Bot starting - STABILITY FIX APPLIED (Watchdog & SafetyNet Active)")
        tick = 0
        while self._running:
            try:
                await self.step(utc_ts())
            except Exception as e:
                self.logger.error("CRITICAL ERROR in bot loop: %s", e, exc_info=True)
                await asyncio.sleep(5.0)  # Cool down on error
                
            tick += 1
            if self.settings.SIM_MAX_TICKS and tick >= self.settings.SIM_MAX_TICKS:
                break
            await asyncio.sleep(self.settings.SIM_TICK_SEC)

        await self.shutdown()
        self.logger.info("Bot stopped")

    async def initialize(self) -> None:
        start = getattr(self.scanner, "start", None)
        if callable(start):
            await start()
        # Connect PumpPortal client to price feed for real-time pricing
        pumpportal = getattr(self.scanner, "pumpportal", None)
        if pumpportal:
            self.price_feed.set_pumpportal(pumpportal)
        
        # Start real-time price feed
        await self.realtime_feed.start()
        
        # Connect PumpPortal price updates to realtime feed
        if self.scanner.pumpportal:
            self.scanner.pumpportal.set_price_callback(
                lambda mint, price: self.realtime_feed.update_price(mint, price)
            )
        
        # --- POSITION PERSISTENCE ---
        # In PAPER mode: Clear old positions on restart to avoid ghost positions
        # In LIVE mode: Restore positions and let safety nets handle old stuck ones
        if self.settings.PAPER_TRADING_MODE:
            self.logger.info("Paper trading mode - clearing old position snapshot")
            self.position_monitor.clear_snapshot()
        else:
            # Live trading: try to restore positions
            restored = self.position_monitor.load_positions()
            if restored:
                self.positions = restored
                self.logger.info("Restored %d positions from snapshot", len(restored))
                # Subscribe to real-time prices for restored positions
                for position in restored.values():
                    await self.realtime_feed.subscribe(position.token)
                    self.position_price_monitor.add_position(position)
        
        # Start copy trading webhook if enabled
        if self.wallet_webhook:
            await self.wallet_webhook.start()
            leaders = self.wallet_tracker.get_active_leaders() if self.wallet_tracker else []
            self.logger.info("Copy trading enabled with %d leaders", len(leaders))
        
        # Start dedicated position price monitor
        await self.position_price_monitor.start()
        
        # --- LIVE MODE: Fetch real wallet balance ---
        if not self.settings.PAPER_TRADING_MODE and self.settings.SOLANA_PRIVATE_KEY:
            try:
                balance = await self.trader.get_balance()
                if balance is not None:
                    self.stats.cash_sol = balance
                    self.logger.info("💰 LIVE WALLET BALANCE: %.4f SOL", balance)
                else:
                    self.logger.warning("Could not fetch wallet balance")
            except Exception as e:
                self.logger.error("Failed to fetch wallet balance: %s", e)
        
        # Load token blacklist
        try:
            if self._blacklist_file.exists():
                with open(self._blacklist_file, 'r') as f:
                    blacklist_data = json.load(f)
                    self._blacklist = set(blacklist_data)
                    self.logger.info("🚫 Loaded %d blacklisted tokens", len(self._blacklist))
        except Exception as e:
            self.logger.warning("Failed to load blacklist: %s", e)
        
        self.logger.info("Real-time price feed initialized")
        
        self.logger.info("Real-time price feed initialized")

    async def shutdown(self) -> None:
        if self.positions:
            await self._exit_all_positions("SHUTDOWN")
        close_scanner = getattr(self.scanner, "close", None)
        if callable(close_scanner):
            await close_scanner()
        close_feed = getattr(self.price_feed, "close", None)
        if callable(close_feed):
            await close_feed()
        # Stop real-time price feed
        await self.realtime_feed.stop()
        # Stop dedicated position monitor
        await self.position_price_monitor.stop()
        # Stop copy trading webhook
        if self.wallet_webhook:
            await self.wallet_webhook.stop()
        if self.telegram:
            await self.telegram.close()

    async def step(self, now: float) -> None:
        # Handle bot control commands first
        await self._maybe_handle_telegram(now)
        
        # If bot is paused via Telegram, skip trading logic
        if not self.bot_active:
            await asyncio.sleep(1.0)  # Reduced sleep when paused
            return
        
        await self._update_sol_price()  # Keep SOL price cached for Telegram
        await self._maybe_update_balance(now) # Keep balance updated
        await self._sync_positions_with_balances(now)  # NEW: Sync positions with on-chain balances
        await self._maybe_scan(now)
        await self._process_copy_signals(now)
        await self._update_positions(now)
        self.position_monitor.maybe_log(self.positions, now, self.stats)
        await self._check_dashboard_signals()
        self._apply_supervisor()

    async def _maybe_update_balance(self, now: float) -> None:
        """Periodically update wallet balance in live mode."""
        if self.settings.PAPER_TRADING_MODE:
            return
            
        # Check every 60 seconds
        if now - self._last_balance_check < 60.0:
            return
            
        self._last_balance_check = now
        try:
            balance = await self.trader.get_balance()
            if balance is not None:
                # Log only if changed significantly (> 0.001 SOL) to reduce spam
                diff = abs(self.stats.cash_sol - balance)
                self.stats.cash_sol = balance
                
                if diff > 0.001:
                    self.logger.info("💰 BALANCE UPDATE: %.4f SOL", balance)
                else:
                    self.logger.debug("Balance check: %.4f SOL (unchanged)", balance)
                
                # SAFETY: Pause bot if balance drops below minimum threshold
                min_balance_threshold = 0.7
                if balance < min_balance_threshold:
                    self.bot_active = False
                    self.logger.critical(
                        "🛑 EMERGENCY STOP: Balance %.4f SOL < %.2f SOL minimum threshold. Bot paused to preserve capital.",
                        balance, min_balance_threshold
                    )
                    if self.telegram:
                        await self.telegram.send_message(
                            f"🛑 **EMERGENCY STOP**\n\n"
                            f"Balance: {balance:.4f} SOL\n"
                            f"Minimum: {min_balance_threshold:.2f} SOL\n\n"
                            f"Bot automatically paused to preserve capital for fees.\n"
                            f"Please top up your wallet and restart the bot manually."
                        )
        except Exception as e:
            self.logger.error("Periodic balance check failed: %s", e)

    async def _sync_positions_with_balances(self, now: float) -> None:
        """Detect manual sells and remove positions with 0 balance."""
        if self.settings.PAPER_TRADING_MODE:
            return
        
        # Run every 60 seconds
        if now - self._last_position_sync < 60.0:
            return
        
        self._last_position_sync = now
        
        # Check only copy trade positions (where manual sells are most likely)
        removed_positions = []
        for mint, position in list(self.positions.items()):
            is_copy = position.token.metadata.get("is_copy_trade", False)
            if not is_copy:
                continue
            
            try:
                # Check on-chain balance
                balance_raw = await self.trader.get_token_balance(mint)
                if balance_raw == 0 and position.size_sol > 0:
                    self.logger.warning(
                        "📊 POSITION SYNC: %s has 0 balance (manual sell detected), removing position",
                        position.token.symbol
                    )
                    removed_positions.append(mint)
                    # Unsubscribe from price updates
                    await self.realtime_feed.unsubscribe(position.token)
                    self.position_price_monitor.remove_position(mint)
            except Exception as e:
                self.logger.debug("Balance check failed for %s: %s", mint[:8], e)
        
        # Remove positions
        for mint in removed_positions:
            self.positions.pop(mint, None)
            self._save_positions()
            self.logger.info("✅ Position removed: %s", mint[:8])

    async def _maybe_scan(self, now: float) -> None:
        if self.paused:
            return
        if now < self.cooldown_until:
            return
        if len(self.positions) >= self.settings.MAX_POSITIONS:
            return
        if now - self._last_scan_ts < self.settings.SCAN_INTERVAL_SEC:
            return
        self._last_scan_ts = now

        tokens = await self.scanner.scan()
        for token in tokens:
            if len(self.positions) >= self.settings.MAX_POSITIONS:
                break
            await self._try_open_scout(token, now)

    async def _process_copy_signals(self, now: float) -> None:
        """Process copy trading signals from leader wallets."""
        if not self.wallet_tracker:
            return
        if self.paused:
            return
        
        signals = await self.wallet_tracker.drain_signals()
        for signal in signals:
            await self._try_copy_trade(signal, now)
    
    async def _try_copy_trade(self, signal: CopySignal, now: float) -> None:
        """Execute a copy trade based on leader signal."""
        # Check blacklist FIRST (before any processing)
        if signal.token_mint in self._blacklist:
            self.logger.warning("🚫 BLACKLIST: Skipping %s (blacklisted)", signal.token_symbol)
            return
        max_age_sec = getattr(self.settings, "COPY_SIGNAL_MAX_AGE_SEC", 30.0)
        if signal.timestamp and max_age_sec > 0:
            age_sec = now - signal.timestamp
            if age_sec > max_age_sec:
                self.logger.debug(
                    "COPY_SKIP %s: Stale signal (age %.1fs)",
                    signal.token_symbol,
                    age_sec,
                )
                return

        def leader_matches(position: Position) -> bool:
            leader_addr = position.token.metadata.get("copy_leader_address")
            leader_alias = position.token.metadata.get("copy_leader")
            if leader_addr:
                return leader_addr == signal.leader_address
            if leader_alias:
                return leader_alias == signal.leader_alias
            return False

        
        # Handle SELL signals - exit our position if we have one
        if signal.action == "SELL":
            position = self.positions.get(signal.token_mint)
            if position:
                if not position.token.metadata.get("is_copy_trade", False):
                    self.logger.debug("COPY SELL ignored for %s: existing position is not copy trade", signal.token_symbol)
                    return
                if not leader_matches(position):
                    self.logger.debug("COPY SELL ignored for %s: leader mismatch", signal.token_symbol)
                    return
                self.logger.info(
                    "📡 COPY SELL: %s sold %s, exiting our position",
                    signal.leader_alias, signal.token_symbol
                )
                await self._exit_position(position, f"COPY_SELL_{signal.leader_alias}")
                self.wallet_tracker.mark_signal_processed(signal, success=True)
            else:
                # Try fallback lookup (strip whitespace)
                found_mint = None
                for key in self.positions.keys():
                    if key.strip() == signal.token_mint.strip():
                        found_mint = key
                        break
                
                if found_mint:
                    self.logger.warning("⚠️ Found position for %s using fuzzy match (key mismatch)", signal.token_symbol)
                    position = self.positions[found_mint]
                    if not position.token.metadata.get("is_copy_trade", False):
                        self.logger.debug("COPY SELL ignored for %s: existing position is not copy trade", signal.token_symbol)
                        return
                    if not leader_matches(position):
                        self.logger.debug("COPY SELL ignored for %s: leader mismatch", signal.token_symbol)
                        return
                    await self._exit_position(position, f"COPY_SELL_{signal.leader_alias}_FUZZY")
                    self.wallet_tracker.mark_signal_processed(signal, success=True)
                else:
                    # Log available keys for debugging
                    keys_snippet = list(self.positions.keys())
                    self.logger.warning(
                        "⚠️ COPY SELL IGNORED: %s sold %s (%s) but we have no position! Open positions: %s",
                        signal.leader_alias, signal.token_symbol, signal.token_mint, keys_snippet
                    )
        
        # Handle BUY signals
        if signal.action != "BUY":
            return

        # Check cash first (needed for both new and existing)
        if self.stats.cash_sol < signal.copy_size_sol:
            self.logger.warning(
                "COPY_SKIP %s: Insufficient cash (need %.4f, have %.4f)",
                signal.token_symbol, signal.copy_size_sol, self.stats.cash_sol
            )
            return
        is_new_position = signal.token_mint not in self.positions
        
        # Check leader-specific position limits for NEW positions
        if is_new_position and self.wallet_tracker:
            leader = self.wallet_tracker.get_leader(signal.leader_address)
            if leader and leader.max_positions > 0:
                leader_open = 0
                for pos in self.positions.values():
                    if not pos.token.metadata.get("is_copy_trade", False):
                        continue
                    leader_addr = pos.token.metadata.get("copy_leader_address")
                    leader_alias = pos.token.metadata.get("copy_leader")
                    if (leader_addr and leader_addr == leader.address) or (
                        not leader_addr and leader_alias == leader.alias
                    ):
                        leader_open += 1
                if leader_open >= leader.max_positions:
                    self.logger.debug("COPY_SKIP: Max positions reached for leader %s", leader.alias)
                    return
        
        # Check position limits for NEW positions
        if is_new_position and len(self.positions) >= self.settings.COPY_MAX_POSITIONS:
            self.logger.debug("COPY_SKIP: Max copy positions reached")
            return
        

        
        # Add delay if configured
        if self.settings.COPY_DELAY_MS > 0:
            await asyncio.sleep(self.settings.COPY_DELAY_MS / 1000.0)
        
        # Get token price
        price = 0.0
        best_pair = None
        
        # FAST MODE: Skip price lookups, use leader's price directly for speed
        if self.settings.COPY_FAST_MODE and signal.price and signal.price > 0:
            if getattr(signal, "price_in_usd", False):
                price = signal.price
                self.logger.info("COPY FAST MODE: Using leader USD price $%.9f", price)
            else:
                sol_usd = self._cached_sol_price if hasattr(self, "_cached_sol_price") and self._cached_sol_price > 0 else 130.0
                price = signal.price * sol_usd
                self.logger.info("COPY FAST MODE: Using leader SOL price $%.9f", price)
        else:
            # Normal mode: try multiple price sources
            for attempt in range(2):
                # 1. Try RealTime FIRST
                realtime_price = self.realtime_feed.get_latest_price(signal.token_mint)
                if realtime_price and realtime_price > 0:
                    price = realtime_price
                    self.logger.info("COPY price from RealTime for %s: $%.9f", signal.token_symbol, price)
                    break
                
                # 2. Try PumpPortal/Jupiter price feed
                if price <= 0:
                    fallback_price = await self.price_feed.get_price_by_mint(signal.token_mint)
                    if fallback_price and fallback_price > 0:
                        price = fallback_price
                        self.logger.info("COPY price from PumpPortal/Jupiter for %s: $%.9f", signal.token_symbol, price)
                        break
                
                # 3. Try DexScreener (slower)
                if price <= 0:
                    pairs = await self.scanner.dex_client.get_token_pairs(signal.token_mint)
                    if pairs:
                        best_pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0.0))
                        price = float(best_pair.get("priceUsd", 0) or best_pair.get("priceNative", 0))
                        if price > 0:
                            self.logger.info("COPY price from DexScreener for %s: $%.9f", signal.token_symbol, price)
                            break
                
                if attempt < 1:
                    await asyncio.sleep(0.5)
            
            # Final fallback to leader price
            if price <= 0 and signal.price and signal.price > 0:
                if getattr(signal, "price_in_usd", False):
                    price = signal.price
                    self.logger.info("COPY price from leader USD for %s: $%.9f", signal.token_symbol, price)
                else:
                    sol_usd = self._cached_sol_price if hasattr(self, "_cached_sol_price") and self._cached_sol_price > 0 else 130.0
                    price = signal.price * sol_usd
                    self.logger.info("COPY price from leader SOL for %s: $%.9f", signal.token_symbol, price)
        
        if price <= 0:
            self.logger.warning("COPY_SKIP %s: No price data available", signal.token_symbol)
            self.wallet_tracker.mark_signal_processed(signal, success=False)
            return



        # Check if we already have a position -> DCA / Add to position
        if signal.token_mint in self.positions:
            position = self.positions[signal.token_mint]
            if not position.token.metadata.get("is_copy_trade", False):
                self.logger.debug("COPY_SKIP %s: Existing position is not copy trade", signal.token_symbol)
                return
            if not leader_matches(position):
                self.logger.debug("COPY_SKIP %s: Leader mismatch for existing position", signal.token_symbol)
                return

            
            # Optional: Check max exposure or max adds if needed here
            # For now, we trust the leader and our cash limits
            
            self.logger.info("🔄 COPY DCA: Adding to %s...", signal.token_symbol)
            
            # Execute buy
            trade = await self.trader.buy_async(signal.token_mint, signal.copy_size_sol, price, f"COPY_ADD_{signal.leader_alias}")
            if not trade.success:
                self.logger.warning("COPY_DCA_FAILED %s: Trade execution failed", signal.token_symbol)
                self.wallet_tracker.mark_signal_processed(signal, success=False)
                return

            # Update position (Weighted Average Entry)
            self.stats.cash_sol -= trade.size_sol
            
            old_size = position.size_sol
            old_entry = position.entry_price
            new_size = trade.size_sol
            new_price = trade.price
            
            total_size = old_size + new_size
            avg_price = ((old_size * old_entry) + (new_size * new_price)) / total_size
            
            position.size_sol = total_size
            position.initial_size_sol += new_size # Track total initial investment
            position.entry_price = avg_price
            position.last_update = now
            position.bounce_reentry_count += 1 # Using this counter to track adds for now
            if trade.token_amount_raw > 0:
                position.token_amount_raw = position.token_amount_raw + trade.token_amount_raw

            
            self.logger.info(
                "✅ COPY DCA SUCCESS: %s | New Avg: $%.8f | Size: %.4f SOL",
                signal.token_symbol, avg_price, total_size
            )
            
            self._log_trade("ADD_COPY", position, trade.price, trade.size_sol, f"COPY_ADD_{signal.leader_alias}")
            # Notify Telegram about the add
            # We can reuse SCOUT_OPEN or create a new type if supported, for now using log context
            # self._notify_telegram("COPY_ADD", position) # If/when supported
            
            self.wallet_tracker.mark_signal_processed(signal, success=True)
            return
        
        # Create TokenInfo with better fallbacks
        base = best_pair.get("baseToken", {}) if best_pair else {}
        # Try multiple sources for symbol
        symbol = (
            base.get("symbol") or 
            signal.token_symbol or 
            base.get("name", "")[:10] or
            signal.token_mint[:6]
        )
        
        # Extract more metadata for dashboard (with null safety)
        volume = (best_pair.get("volume", {}) or {}) if best_pair else {}
        txns = (best_pair.get("txns", {}) or {}) if best_pair else {}
        price_change = (best_pair.get("priceChange", {}) or {}) if best_pair else {}
        
        token = TokenInfo(
            mint=signal.token_mint,
            symbol=symbol,
            age_sec=0,
            liquidity_usd=float((best_pair.get("liquidity") or {}).get("usd", 0)) if best_pair else 0.0,
            volume_usd=float(volume.get("h24", 0)),
            price=price,
            source="COPY_TRADE",
            metadata={
                "name": base.get("name", symbol) if base else symbol,
                "pair_address": best_pair.get("pairAddress") if best_pair else None,
                "copy_leader": signal.leader_alias,
                "copy_leader_address": signal.leader_address,
                "copy_signature": signal.signature,
                "is_copy_trade": True,  # Flag to identify copy trades
                # Market data for dashboard - calculate mcap from price if not available
                "market_cap": float(best_pair.get("fdv", 0) or best_pair.get("marketCap", 0) or 0) if best_pair else (price * 1_000_000_000.0),
                "volume_m5": float(volume.get("m5", 0)),
                "volume_h1": float(volume.get("h1", 0)),
                "volume_h24": float(volume.get("h24", 0)),
                "price_change_m5": float(price_change.get("m5", 0)),
                "price_change_h1": float(price_change.get("h1", 0)),
                "price_change_h24": float(price_change.get("h24", 0)),
                "txns_m5_buys": int((txns.get("m5") or {}).get("buys", 0)),
                "txns_m5_sells": int((txns.get("m5") or {}).get("sells", 0)),
                "dex_id": best_pair.get("dexId", "") if best_pair else "",
            }
        )
        
        # Determine phase for proper price feed subscription
        dex_id = best_pair.get("dexId", "") if best_pair else ""
        if dex_id == "pumpfun":
             token.phase = Phase.BONDING_CURVE
        elif dex_id == "raydium":
             token.phase = Phase.RAYDIUM
        else:
             # Default to BONDING_CURVE for copy trades without DexScreener data
             # This ensures PumpPortal WebSocket is used for real-time prices
             token.phase = Phase.BONDING_CURVE
             self.logger.debug("Copy trade %s defaulting to BONDING_CURVE phase (no dex_id)", signal.token_symbol)

        
        # Execute buy
        trade = await self.trader.buy_async(signal.token_mint, signal.copy_size_sol, price, f"COPY_{signal.leader_alias}")
        if not trade.success:
            self.logger.warning("COPY_TRADE_FAILED %s: Trade execution failed", signal.token_symbol)
            self.wallet_tracker.mark_signal_processed(signal, success=False)
            return
        
        # Use Jupiter fill price (already in USD, calculated from actual SOL/tokens)
        entry_price = trade.price
        self.logger.info("📊 Entry price from fill: $%.9f", entry_price)
        
        self.stats.cash_sol -= trade.size_sol
        position = Position(
            token=token,
            state=PositionState.SCOUT,
            size_sol=trade.size_sol,
            entry_price=entry_price,  # Real fill price in USD
            opened_at=now,
            last_update=now,
            peak_price=entry_price,
            last_price=entry_price,
            scout_deadline=now + self.settings.CONVEX_SCOUT_TIMEOUT_SEC,
            initial_size_sol=trade.size_sol,
            token_amount_raw=trade.token_amount_raw,  # Save for instant sell
        )
        
        # Store entry MCap in metadata (if available from signal)
        if hasattr(signal, 'market_cap') and signal.market_cap > 0:
            position.token.metadata["market_cap"] = signal.market_cap
        
        self.positions[signal.token_mint] = position
        self.stats.daily_trades += 1
        
        # Subscribe to real-time price
        await self.realtime_feed.subscribe(token)
        self.realtime_feed.set_initial_price(token.mint, entry_price)  # Use fresh entry price
        
        # Add to dedicated position price monitor for aggressive polling
        self.position_price_monitor.add_position(position)
        
        self.logger.info(
            "✅ COPY TRADE: Copied %s buying %s @ $%.9f with %.4f SOL",
            signal.leader_alias, signal.token_symbol, position.entry_price, signal.copy_size_sol
        )
        
        self._log_trade("ENTRY_COPY", position, position.entry_price, trade.size_sol, f"COPY_{signal.leader_alias}")
        self._notify_telegram("SCOUT_OPEN", position)
        self.wallet_tracker.mark_signal_processed(signal, success=True)

    async def _try_open_scout(self, token: TokenInfo, now: float) -> None:
        # Check blacklist FIRST
        if token.mint in self._blacklist:
            self.logger.debug("🚫 BLACKLIST: Skipping scout for %s", token.symbol)
            return
        
        scout_count = sum(1 for pos in self.positions.values() if pos.state == PositionState.SCOUT)
        if scout_count >= self.settings.MAX_CONCURRENT_SCOUTS:
            return

        if not self.validator.validate(token):
            return

        # Criminology Check (Dev Detective)
        if self.dev_detective:
            creator = token.metadata.get("creator")
            # If creator missing, try to fetch it (async)
            if not creator and self.settings.PUMPFUN_ONLY:
                 creator = await self.dev_detective.get_token_creator(token.mint)
            
            if creator:
                 report = await self.dev_detective.investigate(creator)
                 if report and report.is_serial_rugger:
                      self._log_event("DEV_REJECT", token, extra={"reason": str(report.details)})
                      self.logger.info("🕵️ DevDetective REJECT %s: %s", token.symbol, report.details)
                      return

        rug = await self.rugchecker.check(token, token.phase, "SCOUT", current_pnl_pct=0.0)
        if not rug.is_safe:
            self._log_event("RUGCHECK_FAIL", token, extra={"risk": rug.risk_score, "flags": rug.flags})
            return

        # Pattern analysis - block pump & dump, distribution patterns
        pattern_safe, pattern_reason = self.pattern_analyzer.is_entry_safe(token)
        if not pattern_safe:
            self._log_event("PATTERN_BLOCK", token, extra={"reason": pattern_reason})
            self.logger.info("PATTERN BLOCK %s: %s", token.symbol, pattern_reason)
            return

        # Entry signal detection - check optimal timing
        entry_signal = self.entry_signal_detector.detect(token)
        if not entry_signal.should_enter:
            self._log_event("ENTRY_SIGNAL_WEAK", token, extra={
                "score": entry_signal.score,
                "warnings": entry_signal.warnings
            })
            self.logger.info(
                "ENTRY WAIT %s: %s (score=%d)",
                token.symbol, entry_signal.reason, entry_signal.score
            )
            return
        
        self.logger.info(
            "ENTRY SIGNAL %s: %s [%s]",
            token.symbol, entry_signal.strength.value, ", ".join(entry_signal.signals[:3])
        )

        # Ensure we have complete holder stats for dashboard display
        ensure_holder = getattr(self.scanner, "ensure_holder_stats", None)
        if callable(ensure_holder):
            await ensure_holder(token)

        if self.stats.cash_sol < self.settings.CONVEX_SCOUT_SIZE_SOL:
            return

        trade = await self.trader.buy_async(token.mint, self.settings.CONVEX_SCOUT_SIZE_SOL, token.price, "SCOUT_ENTRY")
        if not trade.success:
            return

        self.stats.cash_sol -= trade.size_sol
        position = Position(
            token=token,
            state=PositionState.SCOUT,
            size_sol=trade.size_sol,
            entry_price=trade.price,
            opened_at=now,
            last_update=now,
            peak_price=trade.price,
            last_price=trade.price,
            scout_deadline=now + self.settings.CONVEX_SCOUT_TIMEOUT_SEC,

            initial_size_sol=trade.size_sol,
        )

        # Enrich with InsightX data if available
        if self.insightx_client:
            try:
                # Run security check asynchronously without blocking flow critically
                # Note: In a stricter setup, you might want to await this BEFORE buying
                security = await self.insightx_client.get_token_security(token.mint)
                if security:
                    position.insightx_data = security
                    self.logger.info("InsightX Security for %s: Score %s/100", token.symbol, security.get('risk_score'))
            except Exception as e:
                self.logger.error("Failed to fetch InsightX data: %s", e)
        self.positions[token.mint] = position
        self.stats.daily_trades += 1
        
        # Subscribe to real-time price updates
        await self.realtime_feed.subscribe(token)
        
        self._log_trade("ENTRY_SCOUT", position, trade.price, trade.size_sol, trade.reason)
        self._notify_telegram("SCOUT_OPEN", position, rug=rug)

    async def _update_positions(self, now: float) -> None:
        for mint, position in list(self.positions.items()):
            # Ensure position is being monitored aggressively
            self.position_price_monitor.add_position(position)
            
            position.last_update = now
            
            # Priority 1: Dedicated position price monitor (aggressive 1.5s polling)
            monitor_price = self.position_price_monitor.get_price(mint)
            
            # Priority 2: Real-time feed (PumpPortal WebSocket / Birdeye)
            realtime_price = self.realtime_feed.get_latest_price(mint) if not monitor_price else None
            
            if monitor_price and monitor_price > 0:
                # Use monitor price (most reliable, always polling)
                new_price = monitor_price
                position.last_price = new_price
                position.token.price = new_price
            elif realtime_price and realtime_price > 0:
                # Use real-time feed price
                new_price = realtime_price
                position.last_price = new_price
                position.token.price = new_price
            else:
                # Fallback to periodic DexScreener refresh
                refresh = getattr(self.scanner, "refresh_token_metrics", None)
                if callable(refresh):
                    await refresh(position.token, now)
                new_price = await self.price_feed.update(position, now)
                position.last_price = new_price
            
            position.peak_price = max(position.peak_price, new_price)

            is_copy_trade = position.token.metadata.get("is_copy_trade", False)
            pnl_pct = (new_price / position.entry_price) - 1.0

            # --- SAFETY NET: Force exit for stuck SCOUT positions ---
            if position.state == PositionState.SCOUT and not is_copy_trade:
                age_sec = now - position.opened_at
                # Force exit if stuck > 15 mins (timeout is usually 3-5 mins)
                if age_sec > 900: 
                     self.logger.warning("🛡️ SAFETY NET: Forcing exit for stuck SCOUT %s (Age: %.0fs)", position.token.symbol, age_sec)
                     await self._exit_position(position, "SCOUT_STUCK_TIMEOUT_SAFETY")
                     continue
                # Hard stop safety net
                if pnl_pct < -0.40:
                     self.logger.warning("🛡️ SAFETY NET: Hard stop %s at %.1f%%", position.token.symbol, pnl_pct*100)
                     await self._exit_position(position, "SCOUT_HARD_STOP_SAFETY")
                     continue
            # ----------------------------------------------------

            signals = self.entry_scorer.score(position.token)
            
            # Skip state machine for copy trades to avoid auto-scaling or timeout exits
            # We want to wait exclusively for the leader signal (or emergency SL)
            if not is_copy_trade:
                transition = self.state_machine.evaluate(position, signals, pnl_pct, now)
                if transition:
                    await self._handle_transition(position, transition.reason, transition.new_state)
                    if transition.new_state == PositionState.EXIT:
                        continue

            eas_value = self.eas_tracker.compute(signals, pnl_pct)
            position.eas_value = eas_value
            position.eas_risk_level = self.eas_tracker.update_risk_level(position.eas_risk_level, eas_value)
            position.runner_state = self.runner_protection.get_state(pnl_pct)
            position.narrative_phase = self.narrative_analyzer.analyze(position, signals, pnl_pct)

            if self.settings.ENABLE_DEV_MONITOR:
                dev_event = self.dev_tracker.check(position)
                if dev_event:
                    self.event_bus.publish(mint, dev_event)
            if self.settings.ENABLE_LP_MONITOR:
                lp_event = self.lp_monitor.check(position)
                if lp_event:
                    self.event_bus.publish(mint, lp_event)

            if self.event_bus.should_exit(mint):
                await self._exit_position(position, "EVENT_EXIT")
                continue


            
            if is_copy_trade:
                # --- COPY TRADE SAFETY NET (ZOMBIE PROTECTION) ---
                age_sec = now - position.opened_at
                
                # 1. Emergency Stop Loss: Configurable hard stop with 30s grace period
                emergency_stop = self.settings.COPY_EMERGENCY_STOP_LOSS_PCT
                if emergency_stop > 0 and pnl_pct <= -emergency_stop and age_sec > 30:
                    self.logger.warning(
                        "🛡️ COPY SAFETY NET: Hard stop for %s at %.1f%% (limit %.1f%%)",
                        position.token.symbol, pnl_pct * 100, emergency_stop * 100
                    )
                    await self._exit_position(position, "COPY_HARD_STOP_SAFETY")
                    continue

                # 2. Zombie Timeout: If > 48 hours old and losing, force exit
                age_hours = (now - position.opened_at) / 3600
                if age_hours > 48 and pnl_pct < -0.10:
                    self.logger.warning(
                        "🛡️ COPY SAFETY NET: Timeout for %s (Age: %.1fh, PnL: %.1f%%)",
                        position.token.symbol, age_hours, pnl_pct * 100
                    )
                    await self._exit_position(position, "COPY_TIMEOUT_SAFETY")
                    continue

                # 3. Never-Positive Timeout: If > 10 minutes and NEVER went positive, force exit
                age_minutes = (now - position.opened_at) / 60
                never_positive = position.peak_price <= position.entry_price
                if age_minutes > 10 and never_positive and pnl_pct < 0:
                    self.logger.warning(
                        "🛡️ COPY SAFETY NET: Never-positive timeout for %s (Age: %.1fm, PnL: %.1f%%, Peak: $%.8f, Entry: $%.8f)",
                        position.token.symbol, age_minutes, pnl_pct * 100,
                        position.peak_price, position.entry_price
                    )
                    await self._exit_position(position, "COPY_NEVER_POSITIVE_TIMEOUT")
                    continue
                # --------------------------------------------------

                # Trailing stop for copy trades in high profit
                copy_trailing_active = getattr(position, 'copy_trailing_active', False)
                
                # Activate trailing when reaching trigger
                if pnl_pct >= self.settings.COPY_TRAILING_TRIGGER_PCT and not copy_trailing_active:
                    position.copy_trailing_active = True
                    position.copy_peak_price = new_price
                    self.logger.info(
                        "🎯 COPY TRAILING ACTIVATED for %s: PnL %.0f%% > %.0f%% trigger, peak=$%.8f",
                        position.token.symbol, pnl_pct * 100, 
                        self.settings.COPY_TRAILING_TRIGGER_PCT * 100, new_price
                    )
                    self._notify_telegram("COPY_TRAILING_ARMED", position, 
                        reason=f"PnL {pnl_pct*100:.0f}% > {self.settings.COPY_TRAILING_TRIGGER_PCT*100:.0f}%")
                
                # Check trailing stop if it was previously activated (even if PnL now below trigger!)
                if getattr(position, 'copy_trailing_active', False):
                    # Update peak price
                    if new_price > getattr(position, 'copy_peak_price', 0):
                        position.copy_peak_price = new_price
                    
                    # Check trailing stop
                    peak = getattr(position, 'copy_peak_price', position.entry_price)
                    if peak > 0:
                        drop_from_peak = (peak - new_price) / peak
                        if drop_from_peak >= self.settings.COPY_TRAILING_PCT:
                            self.logger.info(
                                "📉 COPY TRAILING STOP for %s: dropped %.1f%% from peak ($%.8f -> $%.8f)",
                                position.token.symbol, drop_from_peak * 100,
                                peak, new_price
                            )
                            await self._exit_position(position, "COPY_TRAILING_STOP")
                            continue
                
                # Skip all other exit logic for copy trades - wait for COPY_SELL signal
                continue

            # REGULAR TRADE: Normal partial exits and trailing stop
            for pct, reason in self.partial_exit_manager.maybe_take_partials(
                position, position.eas_risk_level, position.runner_state, pnl_pct
            ):
                await self._execute_partial(position, pct, reason)

            # Hybrid Strategy: Break-Even Trigger
            if self.settings.BREAK_EVEN_TRIGGER_PCT > 0:
                if not position.is_breakeven and pnl_pct >= self.settings.BREAK_EVEN_TRIGGER_PCT:
                    position.is_breakeven = True
                    self._notify_telegram("BREAK_EVEN_ARMED", position, reason=f"PROFIT > {self.settings.BREAK_EVEN_TRIGGER_PCT*100:.0f}%")

            trailing_pct = self.trailing_calc.compute(
                position.runner_state,
                position.eas_risk_level,
                position.narrative_phase,
                roi_pct=pnl_pct * 100.0,
            )
            
            # Calculate Stop Price
            stop_price = position.peak_price * (1.0 - trailing_pct)
            
            # START Hybrid Strategy: Break-Even Floor
            if position.is_breakeven:
                 # Force stop to be at least Entry + Fees (approx 1%)
                 be_price = position.entry_price * 1.01
                 stop_price = max(stop_price, be_price)
            # END Hybrid Strategy
            
            # START Hybrid Strategy: Anti-Panic Grace Period
            is_anti_panic = False
            if self.settings.ANTI_PANIC_DURATION_SEC > 0:
                age_sec = now - position.opened_at if position.opened_at > 0 else 0
                if age_sec < self.settings.ANTI_PANIC_DURATION_SEC:
                    is_anti_panic = True
            # END Hybrid Strategy

            if new_price < stop_price:
                if is_anti_panic:
                    # Ignore stop during grace period (unless it's a huge crash? No, strict anti-panic)
                    pass 
                else:
                    await self._exit_position(position, "TRAILING_STOP")
                    continue

            if position.state == PositionState.SCOUT and pnl_pct <= -self.settings.SCOUT_STOP_LOSS_PCT:
                if is_anti_panic:
                    pass
                else:
                    await self._exit_position(position, "SCOUT_STOP")
                    continue
            
            # Sync position to Supabase (every ~10 seconds to avoid spam)
            sync_interval = getattr(position, '_last_supabase_sync', 0)
            if now - sync_interval > 10:
                try:
                    from supabase_sync import safe_upsert, is_enabled
                    if is_enabled():
                        safe_upsert('positions', {
                            'wallet_id': None,  # Optional: set if tracking which wallet owns this
                            'token_mint': position.token.mint,
                            'token_symbol': position.token.symbol,
                            'amount': position.size_sol / position.last_price if position.last_price > 0 else 0,
                            'avg_buy_price': position.entry_price,
                            'current_price': position.last_price,
                            'unrealized_pnl_sol': position.size_sol * pnl_pct,
                            'unrealized_pnl_percent': pnl_pct * 100,
                            'is_open': True,
                        }, conflict_columns=['user_id', 'token_mint'])
                        position._last_supabase_sync = now
                except Exception as e:
                    pass  # Don't break bot if Supabase fails
        
        # Check bounce watchlist for re-entry opportunities
        bounce_signals = await self.bounce_manager.update_and_check_bounces(now, self.price_feed)
        for signal in bounce_signals:
            await self._handle_bounce_reentry(signal, now)

    async def _handle_transition(self, position: Position, reason: str, new_state: PositionState) -> None:
        if new_state == PositionState.CONFIRM:
            refresh = getattr(self.scanner, "ensure_holder_stats", None)
            if callable(refresh):
                await refresh(position.token)
            # Calculate current PnL for grace period logic
            pnl_pct = (position.last_price / position.entry_price) - 1.0 if position.entry_price > 0 else 0.0
            rug = await self.rugchecker.check(position.token, position.token.phase, "CONFIRM", current_pnl_pct=pnl_pct)
            if not rug.is_safe:
                await self._exit_position(position, "CONFIRM_RUGCHECK_FAIL")
                return

            add_size = self.settings.CONVEX_CONFIRM_SIZE_SOL
            if self.stats.cash_sol >= add_size:
                trade = await self.trader.buy_async(position.token.mint, add_size, position.last_price, reason)
                if trade.success:
                    position.size_sol += trade.size_sol
                    position.initial_size_sol = position.size_sol
                    self.stats.cash_sol -= trade.size_sol
                    self.stats.daily_trades += 1
                    self._log_trade("ADD_CONFIRM", position, trade.price, trade.size_sol, reason)
            position.state = PositionState.CONFIRM
            position.selection_consecutive = 0
            position.conviction_consecutive = 0
            self.stats.scout_failures = 0
            self._notify_telegram("STATE_CHANGE", position, reason=reason)
            return

        if new_state == PositionState.CONVICTION:
            refresh = getattr(self.scanner, "ensure_holder_stats", None)
            if callable(refresh):
                await refresh(position.token)
            # Calculate current PnL for grace period logic
            pnl_pct = (position.last_price / position.entry_price) - 1.0 if position.entry_price > 0 else 0.0
            rug = await self.rugchecker.check(position.token, position.token.phase, "CONVICTION", current_pnl_pct=pnl_pct)
            if not rug.is_safe:
                await self._exit_position(position, "CONVICTION_RUGCHECK_FAIL")
                return

            target_total = self.settings.CONVEX_MAX_TOTAL_SOL
            add_size = max(0.0, target_total - position.size_sol)
            if add_size > 0 and self.stats.cash_sol >= add_size:
                trade = await self.trader.buy_async(position.token.mint, add_size, position.last_price, reason)
                if trade.success:
                    position.size_sol += trade.size_sol
                    self.stats.cash_sol -= trade.size_sol
                    self.stats.daily_trades += 1
                    self._log_trade("ADD_CONVICTION", position, trade.price, trade.size_sol, reason)
            position.state = PositionState.CONVICTION
            position.conviction_consecutive = 0
            self._notify_telegram("STATE_CHANGE", position, reason=reason)
            return

        if new_state == PositionState.EXIT:
            await self._exit_position(position, reason)

    async def _execute_partial(self, position: Position, pct: float, reason: str) -> None:
        sell_size = position.size_sol * pct
        if sell_size <= 0:
            return
        trade = await self.trader.sell_async(position.token.mint, sell_size, position.last_price, reason)
        if trade.success:
            position.size_sol -= sell_size
            if self.settings.PAPER_TRADING_MODE:
                value_sol = trade.size_sol * (trade.price / position.entry_price)
                pnl_sol = value_sol - trade.size_sol
            else:
                value_sol = trade.size_sol
                pnl_sol = value_sol - sell_size
            position.realized_pnl_sol += pnl_sol
            self.stats.realized_pnl_sol += pnl_sol
            if pnl_sol < 0:
                self.stats.daily_loss_sol += abs(pnl_sol)
            position.realized_pct += max(0.0, pnl_sol) / max(
                0.0001, position.initial_size_sol
            )
            self.stats.cash_sol += value_sol
            self.stats.daily_trades += 1
            self._log_trade("PARTIAL_EXIT", position, trade.price, value_sol, reason)  # Log actual SOL received

            if position.size_sol <= position.initial_size_sol * 0.3 and position.state == PositionState.CONVICTION:
                position.state = PositionState.MOONBAG

    async def _exit_position(self, position: Position, reason: str) -> None:
        # Use sell_all_async to ensure we sell 100% of the token balance in wallet
        # instead of relying on size_sol/price calculation which drift.
        size_for_paper = position.size_sol if self.settings.PAPER_TRADING_MODE else None
        trade = await self.trader.sell_all_async(
            position.token.mint,
            position.last_price,
            reason,
            size_sol=size_for_paper,
            token_amount_raw=position.token_amount_raw,  # Use cached amount for instant sell
        )
        
        
        if not trade.success:
            self.logger.warning(
                "EXIT FAILED %s (%s): %s",
                position.token.symbol,
                position.token.mint[:8],
                trade.reason,
            )
            
            # CRITICAL FIX: If NO_BALANCE, remove position anyway (it's a ghost)
            if "NO_BALANCE" in trade.reason.upper():
                self.logger.warning(
                    "🗑️  GHOST POSITION CLEANUP: Removing %s (no balance in wallet)",
                    position.token.mint[:8]
                )
                # Clean up monitoring
                self.position_price_monitor.remove_position(position.token.mint)
                await self.realtime_feed.unsubscribe(position.token.mint)
                # Remove from positions dict
                self.positions.pop(position.token.mint, None)
                self.event_bus.clear(position.token.mint)
            
            return

        value_sol = trade.size_sol * (trade.price / position.entry_price) if position.entry_price > 0 else trade.size_sol
        # LiveBroker returns size_sol as actual SOL received on sell.
        if not self.settings.PAPER_TRADING_MODE:
            value_sol = trade.size_sol
        
        pnl_sol = value_sol - position.size_sol # Compare exit value vs cost basis
        position.realized_pnl_sol += pnl_sol
        self.stats.realized_pnl_sol += pnl_sol
        
        pnl_pct = (trade.price / position.entry_price) - 1.0 if position.entry_price > 0 else 0.0
        
        if pnl_sol < 0:
            self.stats.daily_loss_sol += abs(pnl_sol)
        self.stats.cash_sol += value_sol
        self.stats.daily_trades += 1
        self._log_trade("EXIT", position, trade.price, value_sol, reason)
        self._notify_telegram("EXIT", position, reason=reason, pnl_pct=pnl_pct)
        if position.realized_pnl_sol > 0:
            self.stats.trades_won += 1
        else:
            self.stats.trades_lost += 1
        
        # Add to bounce watchlist if exited with loss on SCOUT_STOP
        if reason in ("SCOUT_STOP", "SCOUT_STOP_LOSS") and pnl_sol < 0:
            self.bounce_manager.add_to_watchlist(position, trade.price, utc_ts(), pnl_sol)

        if position.state == PositionState.SCOUT:
            if reason in ("SCOUT_TIMEOUT", "SCOUT_STOP", "SCOUT_STOP_LOSS", "CONFIRM_RUGCHECK_FAIL"):
                self.stats.scout_failures += 1
                if self.stats.scout_failures >= self.settings.SCOUT_COOLDOWN_FAILURES:
                    self.cooldown_until = utc_ts() + self.settings.SCOUT_COOLDOWN_SEC
                    self.stats.scout_failures = 0


        # Remove from monitoring
        self.position_price_monitor.remove_position(position.token.mint)
        await self.realtime_feed.unsubscribe(position.token.mint)
        
        self.positions.pop(position.token.mint, None)
        self.event_bus.clear(position.token.mint)

    async def _exit_all_positions(self, reason: str) -> None:
        for position in list(self.positions.values()):
            await self._exit_position(position, reason)
    
    async def _handle_bounce_reentry(self, signal, now: float) -> None:
        """Handle bounce recovery re-entry."""
        from solana_bot.core.bounce_recovery import BounceSignal
        
        # Skip if we already have a position for this token
        if signal.mint in self.positions:
            self.logger.debug("BOUNCE_SKIP %s: Already have active position", signal.symbol)
            return
        
        # Check if we have enough cash
        if self.stats.cash_sol < signal.reentry_size_sol:
            self.logger.warning(
                "BOUNCE_SKIP %s: Insufficient cash (need %.4f, have %.4f)",
                signal.symbol,
                signal.reentry_size_sol,
                self.stats.cash_sol,
            )
            return
        
        # Skip if we're at max positions
        if len(self.positions) >= self.settings.MAX_POSITIONS:
            self.logger.debug("BOUNCE_SKIP %s: Max positions reached", signal.symbol)
            return
        
        # Create a minimal TokenInfo for re-entry
        # In production, we should refresh full token data
        token_info = TokenInfo(
            mint=signal.mint,
            symbol=signal.symbol,
            age_sec=0,  # Will be updated if needed
            liquidity_usd=0.0,  # Will be updated if needed
            volume_usd=0.0,
            price=signal.current_price,
            source="BOUNCE_RECOVERY",
        )
        
        # Execute buy
        trade = await self.trader.buy_async(signal.mint, signal.reentry_size_sol, signal.current_price, "BOUNCE_REENTRY")
        if not trade.success:
            self.logger.warning("BOUNCE_REENTRY_FAILED %s: Trade execution failed", signal.symbol)
            return
        
        # Create position
        self.stats.cash_sol -= trade.size_sol
        position = Position(
            token=token_info,
            state=PositionState.SCOUT,
            size_sol=trade.size_sol,
            entry_price=trade.price,
            opened_at=now,
            last_update=now,
            peak_price=trade.price,
            last_price=trade.price,
            scout_deadline=now + self.settings.CONVEX_SCOUT_TIMEOUT_SEC,
            initial_size_sol=trade.size_sol,
            bounce_reentry_count=1,  # Mark as bounce re-entry
        )
        
        self.positions[signal.mint] = position
        self.stats.daily_trades += 1
        
        self.logger.info(
            "BOUNCE_REENTRY %s: Re-entered at %.8f with %.4f SOL (bounce: +%.1f%%, volume: +%.1f%%)",
            signal.symbol,
            signal.current_price,
            signal.reentry_size_sol,
            signal.bounce_pct,
            signal.volume_spike_pct,
        )
        
        self._log_trade("BOUNCE_REENTRY", position, trade.price, trade.size_sol, "BOUNCE_RECOVERY")
        self._notify_telegram("SCOUT_OPEN", position)

    async def _maybe_handle_telegram(self, now: float) -> None:
        if not self.telegram:
            return
        actions = await self.telegram.poll_actions(now)
        for action in actions:
            if action.kind == "force_sell":
                await self._handle_force_sell(action)
            if action.kind == "status":
                status_msg = f"🤖 Bot Status: {'🟢 ACTIVE' if self.bot_active else '🔴 STOPPED'}\n"
                status_msg += f"Positions: {len(self.positions)} open\n"
                status_msg += f"Cash: {self.stats.cash_sol:.4f} SOL"
                await self.telegram.send_message(status_msg)
                await self.telegram.send_status(self.stats, self.positions)
            if action.kind == "start_bot":
                if self.bot_active:
                    await self.telegram.send_message("⚠️ Bot is already running")
                else:
                    self.bot_active = True
                    self.logger.info("✅ Bot STARTED via Telegram command")
                    await self.telegram.send_message("✅ Bot started successfully")
            if action.kind == "stop_bot":
                if not self.bot_active:
                    await self.telegram.send_message("⚠️ Bot is already stopped")
                else:
                    self.bot_active = False
                    self.logger.info("🛑 Bot STOPPED via Telegram command")
                    await self.telegram.send_message("🛑 Bot stopped successfully. Positions will not be auto-managed.")
            if action.kind == "restart_bot":
                self.logger.info("🔄 Bot RESTARTING via Telegram command")
                await self.telegram.send_message("🔄 Restarting bot...")
                self.bot_active = False
                await asyncio.sleep(2.0)
                self.bot_active = True
                await self.telegram.send_message("✅ Bot restarted successfully")

    async def _handle_force_sell(self, action: TelegramAction) -> None:
        if not action.mint:
            return
        position = self.positions.get(action.mint)
        if position:
            await self._exit_position(position, "FORCE_SELL")
        elif self.telegram:
            await self.telegram.send_message(f"Nessuna posizione aperta per {action.mint}")

    def _apply_supervisor(self) -> None:
        if not self.supervisor:
            return
        action = self.supervisor.evaluate(self.stats)
        if not action:
            self.paused = False
            return
        if action.stop_all:
            self.logger.warning("Supervisor stop: %s", action.reason)
            self._running = False
            self.paused = True
        else:
            self.logger.warning("Supervisor pause: %s", action.reason)
            self.paused = True

    def _log_trade(self, event: str, position: Position, price: float, size_sol: float, reason: str) -> None:
        # Get market cap from metadata or calculate if possible
        mcap = position.token.metadata.get("market_cap") or 0.0
        if mcap <= 0 and price > 0:
            # Fallback estimation for fresh pump.fun tokens
            mcap = price * 1_000_000_000.0

        self.metrics_logger.log_event(
            {
                "event": event,
                "mint": position.token.mint,
                "symbol": position.token.symbol,  # Added: token symbol for dashboard display
                "state": position.state,
                "price": price,
                "market_cap": mcap,
                "size_sol": size_sol,
                "reason": reason,
                "ts": utc_ts(),
            }
        )
        
        # Also log to Supabase if enabled
        try:
            from supabase_sync import safe_insert, is_enabled
            if is_enabled():
                is_buy = event in ['ENTRY_SCOUT', 'ENTRY_COPY', 'ADD_CONFIRM', 'ADD_CONVICTION', 'ADD_COPY', 'BOUNCE_REENTRY']
                safe_insert('trades', {
                    'wallet_id': None,  # Optional: for copy trading
                    'position_id': None,  # Optional: link to position
                    'token_mint': position.token.mint,
                    'token_symbol': position.token.symbol,
                    'type': 'buy' if is_buy else 'sell',
                    'amount': size_sol / price if price > 0 else 0,
                    'price_sol': price,
                    'price_usd': None,  # Optional: price in USD
                    'total_sol': size_sol,
                    'signature': '',
                    'platform': 'jupiter',
                    'block_time': int(utc_ts()),
                })
        except Exception as e:
            pass  # Don't break bot if Supabase fails

    def _log_event(self, event: str, token: TokenInfo, extra: dict | None = None) -> None:
        payload = {"event": event, "mint": token.mint, "ts": utc_ts()}
        if extra:
            payload.update(extra)
        self.metrics_logger.log_event(payload)
        
        # Also log to console for user visibility
        extra_str = f" {extra}" if extra else ""
        self.logger.info("%s %s%s", event, token.symbol, extra_str)

    def _save_positions(self) -> None:
        """Save current positions to snapshot immediately."""
        if hasattr(self, 'position_monitor'):
            # Force save by resetting timer
            self.position_monitor._last_log_ts = 0
            self.position_monitor.maybe_log(self.positions, utc_ts(), self.stats)

    def _notify_telegram(
        self,
        event: str,
        position: Position,
        rug: object | None = None,
        reason: str | None = None,
        pnl_pct: float | None = None,
    ) -> None:
        if not self.telegram:
            return
        
        # Use cached SOL price for EUR conversion
        sol_price_eur = self._sol_price_eur

        async def _send() -> None:
            try:
                await self.telegram.send_trade_event(
                    event, position, rug=rug, reason=reason, 
                    pnl_pct=pnl_pct, sol_price_eur=sol_price_eur
                )
            except Exception as exc:
                self.logger.warning("Telegram notification failed: %s", exc)

        asyncio.create_task(_send())
    
    async def _update_sol_price(self) -> None:
        """Update cached SOL price in USD/EUR (called periodically)."""
        now = utc_ts()
        # Update every 5 minutes
        if now - self._sol_price_last_update < 300:
            return
        
        try:
            # Get SOL price in USD and EUR from CoinGecko
            sol_usd, sol_eur = await self.scanner.coingecko.get_sol_price()
            if sol_usd > 0:
                self._sol_price_usd = sol_usd
                self._cached_sol_price = sol_usd
                if self.wallet_tracker:
                    self.wallet_tracker.set_sol_price_usd(sol_usd)
            if sol_eur > 0:
                self._sol_price_eur = sol_eur
            if sol_usd > 0 or sol_eur > 0:
                self._sol_price_last_update = now
                self.logger.debug("SOL price updated: $%.2f / €%.2f", sol_usd, sol_eur)
        except Exception as e:
            self.logger.warning("Failed to update SOL price: %s", e)

    async def _check_dashboard_signals(self) -> None:
        """Check for signals from the dashboard via file exchange."""
        signal_file = Path("logs/dashboard_signals.json")
        if not signal_file.exists():
            return
            
        try:
            content = signal_file.read_text(encoding="utf-8").strip()
            if not content or content == "[]":
                return
                
            signals = json.loads(content)
            # Clear file immediately to prevent double processing
            signal_file.write_text("[]", encoding="utf-8")
            
            for signal in signals:
                action = signal.get("action")
                mint = signal.get("mint", "").strip()  # FIX: Strip whitespace
                
                if action == "FORCE_SELL" and mint:
                    self.logger.info("⚠️ DASHBOARD FORCE SELL: %s", mint)
                    
                    # Try exact match first
                    position = self.positions.get(mint)
                    
                    # If not found, try fuzzy match (strip all whitespace from keys)
                    if not position:
                        for key in self.positions.keys():
                            if key.strip() == mint:
                                position = self.positions[key]
                                self.logger.info("Found position via fuzzy match: %s", key)
                                break
                    
                    if position:
                        await self._exit_position(position, "FORCE_SELL_DASHBOARD")
                    else:
                        # FIX: Fallback to direct sell if position not tracked
                        self.logger.warning("Position not found in tracking, attempting direct sell: %s", mint)
                        
                        if not self.settings.PAPER_TRADING_MODE:
                            try:
                                # Check if we have any balance for this token
                                balance_raw = await self.trader.get_token_balance(mint)
                                if balance_raw > 0:
                                    self.logger.info("Found token balance: %d units, executing sell...", balance_raw)
                                    
                                    # Execute sell with size = -1 (sell all)
                                    trade = await self.trader.sell_async(mint, -1.0, 0.0, "FORCE_SELL_DIRECT")
                                    
                                    if trade.success:
                                        self.logger.info("✅ FORCE SELL SUCCESS (direct): %s | Received: %.4f SOL", 
                                                       mint[:12], trade.size_sol)
                                        self.stats.cash_sol += trade.size_sol
                                    else:
                                        self.logger.error("❌ FORCE SELL FAILED (direct): %s", mint[:12])
                                else:
                                    self.logger.warning("No balance found for token: %s", mint[:12])
                            except Exception as e:
                                self.logger.error("Force sell direct failed: %s", e)
                        else:
                            self.logger.warning("Cannot force sell in paper mode without position")
                
                elif action == "RELOAD_LEADERS":
                    if self.wallet_tracker:
                        self.logger.info("🔄 Reloading copy trading leaders...")
                        self.wallet_tracker._load_leaders()
                        self.logger.info("Leaders reloaded")
                        
        except Exception as e:
            self.logger.error("Error checking dashboard signals: %s", e)
