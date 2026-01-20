from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from solana_bot.config import Settings
from solana_bot.core.coingecko_client import CoinGeckoClient
from solana_bot.core.dexscreener_client import DexScreenerClient
from solana_bot.core.helius_webhook import HeliusWebhook
from solana_bot.core.models import TokenInfo
from solana_bot.core.pumpportal_client import PumpPortalClient
from solana_bot.core.rpc_cache import get_rpc_cache
from solana_bot.core.rpc_client import RPCClient
from solana_bot.utils.time import utc_ts


class TokenScanner:
    def __init__(
        self,
        settings: Settings,
        dex_client: DexScreenerClient | None = None,
        coingecko_client: CoinGeckoClient | None = None,
        rpc_client: RPCClient | None = None,
        webhook: HeliusWebhook | None = None,
        pumpportal: PumpPortalClient | None = None,
    ) -> None:
        self.settings = settings
        self.dex_client = dex_client or DexScreenerClient(settings)
        self.coingecko = coingecko_client or CoinGeckoClient(settings)
        self.rpc_client = rpc_client or RPCClient(settings)
        self.webhook = webhook or (HeliusWebhook(settings) if settings.USE_HELIUS_WEBHOOK else None)
        self.pumpportal = pumpportal or (PumpPortalClient(settings) if settings.USE_PUMPPORTAL_STREAM else None)
        self.cache = get_rpc_cache()
        self.logger = logging.getLogger("solana_bot.scanner")
        self._seen: dict[str, float] = {}
        self._last_log_ts = 0.0
        self._pumpportal_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.webhook and self.settings.USE_HELIUS_WEBHOOK:
            await self.webhook.start()
        if self.pumpportal and self.settings.USE_PUMPPORTAL_STREAM:
            self._pumpportal_task = asyncio.create_task(self.pumpportal.start())

    async def close(self) -> None:
        if self.webhook and self.settings.USE_HELIUS_WEBHOOK:
            await self.webhook.stop()
        if self.pumpportal:
            await self.pumpportal.stop()
            if self._pumpportal_task:
                self._pumpportal_task.cancel()
        await self.dex_client.close()
        await self.coingecko.close()
        await self.rpc_client.close()

    async def scan(self) -> Iterable[TokenInfo]:
        now = utc_ts()
        self._prune_seen(now)
        candidate_mints: set[str] = set()
        fallback_pairs: list[dict] = []

        # Priority 1: Real-time PumpPortal stream (Pump.fun tokens)
        if self.pumpportal and self.settings.USE_PUMPPORTAL_STREAM:
            pumpportal_mints = self.pumpportal.get_pending_mints()
            if pumpportal_mints:
                self.logger.info("PumpPortal mints pending: %d", len(pumpportal_mints))
            candidate_mints.update(pumpportal_mints)

        # Priority 2: Helius webhook (on-chain events)
        if self.webhook and self.settings.USE_HELIUS_WEBHOOK:
            candidate_mints.update(await self.webhook.drain_mints())

        if self.settings.USE_DEXSCREENER_DISCOVERY:
            profiles = await self.dex_client.get_token_profiles()
            limit = self.settings.DEXSCREENER_TOKEN_PROFILE_LIMIT
            for profile in profiles[:limit]:
                if profile.get("chainId") != self.settings.DEXSCREENER_CHAIN_ID:
                    continue
                mint = profile.get("tokenAddress")
                if isinstance(mint, str):
                    candidate_mints.add(mint)

        if not candidate_mints and self.settings.USE_DEXSCREENER_SEARCH_FALLBACK:
            fallback_pairs = await self.dex_client.search_pairs(self.settings.DEXSCREENER_SEARCH_QUERY)
            fallback_pairs = [
                pair
                for pair in fallback_pairs[: self.settings.DEXSCREENER_SEARCH_MAX_PAIRS]
                if pair.get("chainId") == self.settings.DEXSCREENER_CHAIN_ID
            ]

        tokens: list[TokenInfo] = []
        # Process candidates (prioritize PumpPortal via update order)
        candidates_list = list(candidate_mints)
        # Sort to ensure we iterate in a deterministic way if needed, but set order is arbitrary.
        # Ideally, we process NEWEST first if we had timestamps, but we don't track them here yet.
        
        processed_count = 0
        limit = self.settings.DEXSCREENER_MAX_TOKENS_PER_SCAN
        
        for mint in candidates_list:
            if processed_count >= limit:
                break
                
            if self._is_recent(mint, now):
                continue
            
            token = await self._build_token(mint, now)
            if not token:
                # If DexScreener has no data yet, don't mark as "seen" for full 5 mins.
                # Mark it with a short TTL so we retry soon (e.g., 15s).
                self._mark_seen(mint, now, ttl=15.0)
                continue
                
            # Log NEW TOKEN discovery at INFO level for visibility
            mcap = token.metadata.get("market_cap") or token.metadata.get("fdv") or 0.0
            name = token.metadata.get("name", token.symbol)
            msg = (
                f"🔭 NEW TOKEN: {token.symbol} ({name})\n"
                f"   └ 💰 ${token.price:.5f} | 💧 Liq: ${token.liquidity_usd:.0f} | 🏦 MCap: ${mcap:.0f}"
            )
            self.logger.info(msg)
            
            if not self._passes_filters(token):
                if token.mint in pumpportal_mints:
                    # If it came from PumpPortal but failed filters, likely too young/old or volume
                    pass
                continue
                
            tokens.append(token)
            self._mark_seen(mint, now)
            processed_count += 1

        if fallback_pairs and len(tokens) < limit:
            for pair in fallback_pairs:
                token = self._pair_to_token(pair, now)
                if not token.mint:
                    continue
                if self._is_recent(token.mint, now):
                    continue
                await self._enrich_onchain(
                    token, include_holders=self.settings.ONCHAIN_HOLDER_STATS_IN_SCOUT
                )
                if not self._passes_filters(token):
                    continue
                tokens.append(token)
                self._mark_seen(token.mint, now)
                if len(tokens) >= limit:
                    break

        if now - self._last_log_ts >= self.settings.SCAN_LOG_EVERY_SEC:
            self.logger.info(
                "Scan summary: candidates=%d tokens=%d",
                len(candidate_mints) or len(fallback_pairs),
                len(tokens),
            )
            self._last_log_ts = now

        return tokens

    async def refresh_token_metrics(self, token: TokenInfo, now: float) -> None:
        refresh_sec = self.settings.POSITION_METRICS_REFRESH_SEC
        if refresh_sec <= 0:
            return
        last_refresh = float(token.metadata.get("metrics_last_refresh_ts", 0.0))
        if now - last_refresh < refresh_sec:
            return
        pairs = await self.dex_client.get_token_pairs(token.mint)
        if not pairs:
            return
        preferred_pair = token.metadata.get("pair_address")
        pair = None
        if preferred_pair:
            for candidate in pairs:
                if candidate.get("pairAddress") == preferred_pair:
                    pair = candidate
                    break
        if pair is None:
            pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0.0))
        updated = self._pair_to_token(pair, now)
        if updated.mint:
            token.age_sec = updated.age_sec
            token.liquidity_usd = updated.liquidity_usd
            token.volume_usd = updated.volume_usd
            if updated.price > 0:
                token.price = updated.price
            token.metadata.update(updated.metadata)
            token.metadata["metrics_last_refresh_ts"] = now

    def _is_recent(self, mint: str, now: float) -> bool:
        last = self._seen.get(mint)
        return last is not None and now - last < self.settings.SCAN_TOKEN_TTL_SEC

    def _mark_seen(self, mint: str, now: float, ttl: float | None = None) -> None:
        if ttl:
            # If a custom TTL is provided, store the expiration time indirectly
            # by manipulating the timestamp so it expires 'ttl' seconds from now
            # Standard expiry is SCAN_TOKEN_TTL_SEC (e.g. 300s). 
            # We want (now_stored + 300) = now + ttl
            # So now_stored = now + ttl - 300
            default_ttl = self.settings.SCAN_TOKEN_TTL_SEC
            self._seen[mint] = now + ttl - default_ttl
        else:
            self._seen[mint] = now

    def _prune_seen(self, now: float) -> None:
        expiry = self.settings.SCAN_TOKEN_TTL_SEC
        self._seen = {mint: ts for mint, ts in self._seen.items() if now - ts < expiry}

    async def _build_token(self, mint: str, now: float) -> TokenInfo | None:
        """Build TokenInfo from CoinGecko (primary) or DexScreener (fallback)."""
        token: TokenInfo | None = None
        
        # Try CoinGecko first (primary source)
        if self.settings.USE_COINGECKO_PRIMARY:
            try:
                cg_data = await self.coingecko.get_token_data(mint)
                if cg_data:
                    token = self._coingecko_to_token(cg_data, mint, now)
                    if token and token.price > 0:
                        self.logger.debug("CoinGecko data for %s: price=%.8f", mint[:8], token.price)
            except Exception as e:
                self.logger.warning("CoinGecko fetch failed for %s: %s", mint[:8], e)
        
        # Fallback to DexScreener
        if token is None or token.price <= 0:
            pairs = await self.dex_client.get_token_pairs(mint)
            if not pairs:
                return None
            pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0.0))
            token = self._pair_to_token(pair, now)
            if not token.mint:
                return None
            
        # Smart enrichment: Fetch holders if explicitly enabled OR if token is in FinalStretch range
        # This prevents wasting RPC calls on fresh junk, but ensures we check insiders for good tokens.
        should_fetch_holders = self.settings.ONCHAIN_HOLDER_STATS_IN_SCOUT
        if not should_fetch_holders and self.settings.FINALSTRETCH_ENABLED:
            bonding_pct = float(token.metadata.get("bonding_pct", 0.0))
            if bonding_pct >= self.settings.FINALSTRETCH_MIN_BONDING_PCT:
                should_fetch_holders = True
        
        # Try to enrich with CoinGecko top holders if enabled
        if should_fetch_holders and self.settings.USE_COINGECKO_PRIMARY:
            try:
                holders = await self.coingecko.get_top_holders(mint)
                if holders:
                    self._enrich_with_coingecko_holders(token, holders)
            except Exception as e:
                self.logger.debug("CoinGecko holders failed for %s: %s", mint[:8], e)
        
        await self._enrich_onchain(
            token, include_holders=should_fetch_holders
        )
        return token

    def _coingecko_to_token(self, data: dict, mint: str, now: float) -> TokenInfo:
        """Convert CoinGecko token data to TokenInfo."""
        # Parse pool_created_at for age calculation
        created_at_str = data.get("pool_created_at", "")
        age_sec = 0
        if created_at_str:
            try:
                from datetime import datetime
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                age_sec = max(0, int(now - created_dt.timestamp()))
            except Exception:
                pass
        
        price_usd = _safe_float(data.get("price_usd", 0))
        fdv = _safe_float(data.get("fdv_usd", 0))
        market_cap = _safe_float(data.get("market_cap_usd", 0)) or fdv
        reserve_usd = _safe_float(data.get("reserve_in_usd", 0))
        
        # Volume data
        volume = data.get("volume_usd", {}) or {}
        txns = data.get("transactions", {}) or {}
        price_change = data.get("price_change_percentage", {}) or {}
        
        metadata = {
            "name": data.get("name", ""),
            "dex_id": "coingecko",
            "pair_address": data.get("address", ""),
            "price_usd": price_usd,
            "volume_m5": _safe_float(volume.get("m5", 0)),
            "volume_h1": _safe_float(volume.get("h1", 0)),
            "volume_h24": _safe_float(volume.get("h24", 0)),
            "txns_m5_buys": _safe_int((txns.get("m5") or {}).get("buys", 0)),
            "txns_m5_sells": _safe_int((txns.get("m5") or {}).get("sells", 0)),
            "txns_h1_buys": _safe_int((txns.get("h1") or {}).get("buys", 0)),
            "txns_h1_sells": _safe_int((txns.get("h1") or {}).get("sells", 0)),
            "price_change_m5": _safe_float(price_change.get("m5", 0)),
            "price_change_h1": _safe_float(price_change.get("h1", 0)),
            "price_change_h24": _safe_float(price_change.get("h24", 0)),
            "fdv": fdv,
            "market_cap": market_cap,
            "source": "coingecko",
        }
        
        return TokenInfo(
            mint=mint,
            symbol=data.get("symbol", "???"),
            age_sec=age_sec,
            liquidity_usd=reserve_usd or market_cap,
            volume_usd=_safe_float(volume.get("h24", 0)),
            price=price_usd,
            source="coingecko",
            metadata=metadata,
        )

    def _enrich_with_coingecko_holders(self, token: TokenInfo, holders: list[dict]) -> None:
        """Enrich token with CoinGecko holder data."""
        if not holders:
            return
        
        # Calculate top10 and dev (largest) holding percentages
        top10_pct = 0.0
        dev_pct = 0.0
        
        for i, holder in enumerate(holders[:10]):
            attrs = holder.get("attributes", {}) or holder
            pct = _safe_float(attrs.get("percentage", 0))
            top10_pct += pct
            if i == 0:
                dev_pct = pct
        
        token.metadata["top10_holding"] = top10_pct / 100.0  # Convert to decimal
        token.metadata["dev_holding"] = dev_pct / 100.0
        token.metadata["holder_data_source"] = "coingecko"


    def _pair_to_token(self, pair: dict, now: float) -> TokenInfo:
        base = pair.get("baseToken") or {}
        price_usd = _safe_float(pair.get("priceUsd"))
        price_native = _safe_float(pair.get("priceNative"))
        price = price_usd if price_usd else price_native
        created_at = pair.get("pairCreatedAt") or 0
        age_sec = max(0, int(now - (created_at / 1000))) if created_at else 0
        
        # Liquidity parsing with fallback to fdv/marketCap for bonding curve tokens
        liquidity_usd = _safe_float((pair.get("liquidity") or {}).get("usd"))
        if liquidity_usd <= 0:
            # Pump.fun bonding curve tokens don't have liquidity, use fdv/marketCap
            liquidity_usd = _safe_float(pair.get("fdv")) or _safe_float(pair.get("marketCap"))
        volume = pair.get("volume") or {}
        txns = pair.get("txns") or {}
        price_change = pair.get("priceChange") or {}

        metadata = {
            "name": base.get("name", ""),
            "dex_id": pair.get("dexId"),
            "pair_address": pair.get("pairAddress"),
            "pair_created_at": created_at,
            "price_usd": price_usd,
            "price_native": price_native,
            "volume_m5": _safe_float(volume.get("m5")),
            "volume_h1": _safe_float(volume.get("h1")),
            "volume_h6": _safe_float(volume.get("h6")),
            "volume_h24": _safe_float(volume.get("h24")),
            "txns_m5_buys": _safe_int((txns.get("m5") or {}).get("buys")),
            "txns_m5_sells": _safe_int((txns.get("m5") or {}).get("sells")),
            "txns_h1_buys": _safe_int((txns.get("h1") or {}).get("buys")),
            "txns_h1_sells": _safe_int((txns.get("h1") or {}).get("sells")),
            "txns_h6_buys": _safe_int((txns.get("h6") or {}).get("buys")),
            "txns_h6_sells": _safe_int((txns.get("h6") or {}).get("sells")),
            "price_change_m5": _safe_float(price_change.get("m5")),
            "price_change_h1": _safe_float(price_change.get("h1")),
            "price_change_h6": _safe_float(price_change.get("h6")),
            "price_change_h24": _safe_float(price_change.get("h24")),
            "fdv": _safe_float(pair.get("fdv")),
            "market_cap": _safe_float(pair.get("marketCap")),
            "url": pair.get("url"),
            "holder_count": _safe_int(pair.get("holderCount")),
            "bonding_pct": _safe_float(pair.get("bondingCurveProgress")),
            "bonding_curve_progress": _safe_float(pair.get("bondingCurveProgress")),
        }
        
        # Calculate Implied Gain for Pump.fun tokens (assuming ~$5k start)
        # If Liq ~ Mcap, it's likely a bonding curve.
        mcap = metadata["market_cap"]
        liq = liquidity_usd
        if mcap > 0 and abs(liq - mcap) < 100:
            # It's a bonding curve token
            start_mcap = 5000.0
            implied_gain = ((mcap - start_mcap) / start_mcap) * 100.0
            metadata["implied_gain"] = implied_gain
            # If DexScreener h1 is missing or 0, use this (it's more accurate for fresh pumps)
            if abs(metadata["price_change_h1"]) < 1:
                metadata["price_change_h1"] = implied_gain

        return TokenInfo(
            mint=str(base.get("address", "")),
            symbol=str(base.get("symbol", "")),
            age_sec=age_sec,
            liquidity_usd=liquidity_usd,
            volume_usd=_safe_float(volume.get("h24")),
            price=price or 0.0,
            source="dexscreener",
            metadata=metadata,
        )

    async def _enrich_onchain(self, token: TokenInfo, include_holders: bool) -> None:
        if not self.settings.RPC_URL:
            return
        mint_cache_key = f"mint-info:{token.mint}"
        holder_cache_key = f"holder-stats:{token.mint}"
        cached_mint = self.cache.get(mint_cache_key)
        cached_holders = self.cache.get(holder_cache_key) if include_holders else None
        if isinstance(cached_mint, dict):
            token.metadata.update(cached_mint)
        if isinstance(cached_holders, dict):
            token.metadata.update(cached_holders)
        if isinstance(cached_mint, dict) and (cached_holders or not include_holders):
            return
        mint_info = None
        if not isinstance(cached_mint, dict):
            mint_info = await self.rpc_client.get_mint_info(token.mint)

        metadata: dict[str, float | int | bool] = {}
        if mint_info:
            metadata["decimals"] = mint_info.decimals
            metadata["mint_authority_active"] = mint_info.mint_authority_active
            metadata["freeze_authority_active"] = mint_info.freeze_authority_active

        if include_holders and not cached_holders:
            supply_info = await self.rpc_client.get_token_supply(token.mint)
            largest_accounts = await self.rpc_client.get_token_largest_accounts(token.mint)
            supply_ui = _safe_float(
                (supply_info or {}).get("uiAmountString") or (supply_info or {}).get("uiAmount")
            )
            if supply_ui and largest_accounts:
                top_amounts = [_safe_float(acct.get("uiAmount")) for acct in largest_accounts[:10]]
                top_sum = sum(top_amounts)
                metadata["top10_holding"] = top_sum / supply_ui if supply_ui else 0.0
                metadata["dev_holding"] = top_amounts[0] / supply_ui if top_amounts else 0.0

        if metadata:
            mint_payload = {
                key: metadata[key]
                for key in ("decimals", "mint_authority_active", "freeze_authority_active")
                if key in metadata
            }
            holder_payload = {
                key: metadata[key]
                for key in ("dev_holding", "top10_holding")
                if key in metadata
            }
            if mint_payload:
                self.cache.set(
                    mint_cache_key, mint_payload, ttl_sec=self.settings.ONCHAIN_MINT_INFO_TTL_SEC
                )
            if holder_payload:
                self.cache.set(
                    holder_cache_key,
                    holder_payload,
                    ttl_sec=self.settings.ONCHAIN_HOLDER_STATS_TTL_SEC,
                )
            token.metadata.update(metadata)

    async def ensure_holder_stats(self, token: TokenInfo) -> None:
        await self._enrich_onchain(token, include_holders=True)

    def _passes_filters(self, token: TokenInfo) -> bool:
        """Check if token passes EITHER NewPairs OR FinalStretch filters."""
        if token.price <= 0:
            self.logger.debug("FILTER %s: price=0", token.symbol)
            return False
        
        # PUMPFUN_ONLY filter: reject tokens not from Pump.fun
        if self.settings.PUMPFUN_ONLY:
            dex_id = (token.metadata.get("dex_id") or "").lower()
            # Accept: pumpfun, pump, pumpswap (all pump.fun related)
            # Reject: raydium, orca, jupiter, moonshot, etc.
            if not any(x in dex_id for x in ("pump", "pumpfun", "pumpswap")):
                # Check if mint ends with "pump" (pump.fun tokens)
                if not token.mint.lower().endswith("pump"):
                    self.logger.debug("FILTER %s: Not Pump.fun (dex=%s)", token.symbol, dex_id)
                    return False

        # Try NewPairs filter first (fresh tokens)
        if self.settings.NEW_PAIRS_DISCOVERY_ENABLED and self._passes_new_pairs_filter(token):
            return True

        # Try FinalStretch filter (pre-migration tokens)
        if self.settings.FINALSTRETCH_ENABLED and self._passes_final_stretch_filter(token):
            return True

        return False

    def _passes_new_pairs_filter(self, token: TokenInfo) -> bool:
        """NewPairs: Very fresh tokens (age < 3 min, mcap > $7k, dev < 9%)."""
        market_cap = float(token.metadata.get("market_cap") or token.metadata.get("fdv") or 0.0)
        
        # Age filter (max 180s = 3 min default)
        if token.age_sec > self.settings.DEXSCREENER_MAX_TOKEN_AGE_SEC:
            # Too old - silent reject (too common) or DEBUG
            # self.logger.debug("REJECT %s: Age %ds > %ds", token.symbol, token.age_sec, self.settings.DEXSCREENER_MAX_TOKEN_AGE_SEC)
            return False
        
        # Market cap / liquidity filter
        if token.liquidity_usd < self.settings.DEXSCREENER_MIN_LIQUIDITY_USD:
            if market_cap < self.settings.DEXSCREENER_MIN_LIQUIDITY_USD:
                if abs(token.liquidity_usd - market_cap) < 1.0:
                     self.logger.info("REJECT %s: Low BondingLiq/Mcap ($%.0f)", token.symbol, market_cap)
                else:
                    self.logger.info("REJECT %s: Low Liq ($%.0f) & Mcap ($%.0f)", token.symbol, token.liquidity_usd, market_cap)
                return False
        
        # Price change filter
        price_change_m5 = float(token.metadata.get("price_change_m5", 0.0))
        if price_change_m5 < self.settings.DEXSCREENER_PRICE_CHANGE_5M_MIN:
            self.logger.info("REJECT %s: Price change %.1f%% too low", token.symbol, price_change_m5)
            return False
        if price_change_m5 > self.settings.DEXSCREENER_PRICE_CHANGE_5M_MAX:
             self.logger.info("REJECT %s: Price change +%.1f%% too high (FOMO)", token.symbol, price_change_m5)
             return False
        
        self.logger.info(
            "PASS NEW_PAIRS %s: age=%ds mcap=$%.0f pc5m=%.1f%%",
            token.symbol, token.age_sec, market_cap, price_change_m5
        )
        return True

    def _passes_final_stretch_filter(self, token: TokenInfo) -> bool:
        """FinalStretch: Pre-migration tokens (bonding > 35%, volume > $15k, dev < 5%)."""
        market_cap = float(token.metadata.get("market_cap") or token.metadata.get("fdv") or 0.0)
        bonding_pct = float(
            token.metadata.get(
                "bonding_pct",
                token.metadata.get("bonding_curve_progress", 0.0),
            )
        )
        dev_holding = float(token.metadata.get("dev_holding", 0.0))
        volume_h1 = float(token.metadata.get("volume_h1", 0.0)) or token.volume_usd
        # 1. Must be on Bonding Curve (Not Raydium)
        # Check DexID and Liq/Mcap ratio. Raydium pools usually have ratio < 0.4
        dex_id = token.metadata.get("dex_id", "").lower()
        liquidity_ratio = token.liquidity_usd / market_cap if market_cap > 0 else 0
        
        if dex_id == "raydium":
             # self.logger.debug("REJECT %s: Already on Raydium", token.symbol)
             return False

        # Note: We lowered the ratio check. Pump.fun curves often have Liq ~ 30-40% of Mcap at high vals.
        if liquidity_ratio < 0.10:
             self.logger.info(f"💧 {token.symbol} REJECT: Liq/MCap Ratio Low ({liquidity_ratio:.2f} < 0.10)")
             return False

        # 2. Volume & Mcap (Must be "Graduate Material")
        if market_cap < self.settings.FINALSTRETCH_MIN_MCAP_USD:
            if self.settings.ALLOW_LOW_MCAP_IF_RUGCHECK_PASS:
                self.logger.info(f"⚠️ {token.symbol} LOW_MCAP_BYPASS: (${market_cap:.0f}) - will check RugCheck")
            else:
                self.logger.info(f"🤏 {token.symbol} REJECT: Mcap Too Low (${market_cap:.0f} < ${self.settings.FINALSTRETCH_MIN_MCAP_USD:.0f})")
                return False
             
        if token.volume_usd < self.settings.FINALSTRETCH_MIN_VOLUME_USD:
             return False
        
        # Age filter (max 30 min)
        if token.age_sec > self.settings.FINALSTRETCH_MAX_AGE_SEC:
            self.logger.info(f"👴 {token.symbol} REJECT: Too Old ({token.age_sec}s > {self.settings.FINALSTRETCH_MAX_AGE_SEC}s)")
            return False
        
        # Bonding curve progress filter (min 35%)
        if bonding_pct < self.settings.FINALSTRETCH_MIN_BONDING_PCT:
            if market_cap > 0:
                estimated_bonding = min(100.0, (market_cap / 67000.0) * 100.0)
                if estimated_bonding < self.settings.FINALSTRETCH_MIN_BONDING_PCT:
                    self.logger.info(f"📉 {token.symbol} REJECT: Bonding Low (~{estimated_bonding:.0f}% < {self.settings.FINALSTRETCH_MIN_BONDING_PCT}%)")
                    return False
                bonding_pct = estimated_bonding
            else:
                phase_label = token.phase.value if hasattr(token.phase, "value") else str(token.phase)
                self.logger.info(f"🚫 {token.symbol} REJECT: Not on Bonding Curve (Phase={phase_label})")
                return False
        
        # Volume filter (min $15k)
        if volume_h1 < self.settings.FINALSTRETCH_MIN_VOLUME_USD:
             self.logger.info(f"🔇 {token.symbol} REJECT: Vol Low (${volume_h1:.0f} < ${self.settings.FINALSTRETCH_MIN_VOLUME_USD:.0f})")
             return False
        
        # Market cap filter (min $12k)
        if market_cap < self.settings.FINALSTRETCH_MIN_MCAP_USD:
            if self.settings.ALLOW_LOW_MCAP_IF_RUGCHECK_PASS:
                # Allow and rely on RugCheck in bot.py
                pass
            else:
                self.logger.info(f"🤏 {token.symbol} REJECT: MCap Low (${market_cap:.0f} < ${self.settings.FINALSTRETCH_MIN_MCAP_USD:.0f})")
                return False

        # Anti-Dump Filter: Reject tokens that crashed > 45% in the last hour.
        # Relaxed per user request to allow deep dips.
        change_h1 = token.metadata.get("price_change_h1", 0.0)
        if change_h1 < -45.0:
            self.logger.info(f"📉 {token.symbol} REJECT: Heavy Dump ({change_h1:.1f}%)")
            return False

        # Zombie/Roundtrip Filter:
        # If Volume is HUGE relative to Mcap, but Mcap is still LOW, it means the token 
        # already pumped and dumped (accumulated vol) and is now back at the start.
        # WARCOIN ex: Vol $99k / Mcap $25k = Ratio 4.0.
        vol_mcap_ratio = volume_h1 / market_cap if market_cap > 0 else 0
        if vol_mcap_ratio > 2.5 and market_cap < 40000:
            # Only allow if it's currently rocketing (Breakout from zombie state)
            change_m5 = token.metadata.get("price_change_m5", 0.0)
            if change_m5 < 15.0:
                self.logger.info(f"🧟 {token.symbol} REJECT: Zombie (Vol/Mcap={vol_mcap_ratio:.1f}, No Momentum)")
                return False
        
        # Dev holding filter (max 5%)
        if dev_holding > self.settings.FINALSTRETCH_MAX_DEV_HOLDING:
            self.logger.info(
                "REJECT FINAL_STRETCH %s: dev=%.1f%% > max=%.1f%%",
                token.symbol, dev_holding * 100, self.settings.FINALSTRETCH_MAX_DEV_HOLDING * 100
            )
            return False

        # Insiders filter (max 20% by default) - using Top 10 holders as proxy
        top10_holding = float(token.metadata.get("top10_holding", 0.0))
        # Note: top10_holding is only available if smart enrichment fetched it (bonding > 35%)
        # If it's 0.0, we assume it's fine or data missing, passing cautiously.
        if top10_holding > self.settings.FINALSTRETCH_MAX_INSIDERS_PCT:
            self.logger.info(
                "🐋 REJECT FINAL_STRETCH %s: insiders (top10)=%.1f%% > max=%.1f%%",
                token.symbol, top10_holding * 100, self.settings.FINALSTRETCH_MAX_INSIDERS_PCT * 100
            )
            return False

        
        self.logger.info(
            "PASS FINAL_STRETCH %s: bonding=%.0f%% vol=$%.0f mcap=$%.0f dev=%.1f%%",
            token.symbol, bonding_pct, volume_h1, market_cap, dev_holding * 100
        )
        return True


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
