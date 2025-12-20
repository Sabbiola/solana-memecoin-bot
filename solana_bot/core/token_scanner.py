"""
Token Scanner

Autonomous market scanner for finding scalping opportunities.
Scans high-volume, high-volatility tokens and applies anti-rug filters.
"""

import asyncio
import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class TokenOpportunity:
    """Potential scalping opportunity"""
    mint: str
    symbol: str
    name: str
    price_usd: float
    volume_24h: float
    liquidity_usd: float
    price_change_24h: float
    market_cap: float
    safety_score: int  # 0-100
    phase: str  # BONDING_CURVE, PUMPSWAP, RAYDIUM, JUPITER
    age_hours: float = 0.0  # Token age in hours
    whale_address: str = ""  # If this was a whale buy


class TokenScanner:
    """
    Scans Solana markets for high-quality scalping opportunities.
    
    Features:
    - DexScreener trending tokens
    - Volatility + volume filtering
    - Anti-rug filters (liquidity, age, holder concentration)
    - Safety scoring for ranking
    """
    
    def __init__(
        self,
        session: aiohttp.ClientSession,
        validator,
        min_volume_24h: float = 500,
        min_liquidity: float = 10,
        min_price_change: float = 5.0,  # 5% min movement for volatility
        max_results: int = 10
    ):
        self.session = session
        self.validator = validator
        self.min_volume_24h = min_volume_24h
        self.min_liquidity = min_liquidity
        self.min_price_change = min_price_change
        self.max_results = max_results
        
        logger.info(
            f"📡 TokenScanner initialized: "
            f"MinVol=${min_volume_24h}, MinLiq=${min_liquidity}, "
            f"MinChange={min_price_change}%"
        )
    
    async def scan_opportunities(self) -> List[TokenOpportunity]:
        """
        Scan market for top scalping opportunities.
        
        Sources:
        1. DexScreener - Trending tokens (mainly Jupiter)
        2. Pump.fun API - Early bonding curve tokens
        
        Returns:
            List of TokenOpportunity sorted by safety_score (best first)
        """
        opportunities = []
        
        # 📊 Cycle statistics for summary
        stats = {
            "pump_tokens": 0,
            "pump_errors": 0,
            "dex_tokens_raw": 0,
            "dex_tokens_deduped": 0,
            "evaluated": 0,
            "rejected_volume": 0,
            "rejected_liquidity": 0,
            "rejected_volatility": 0,
            "rejected_phase": 0,
            "rejected_pool": 0,
            "rejected_holder": 0,
            "accepted": 0,
        }
        
        # Import config for pump.fun filters
        from ..config import (
            EARLY_TOKEN_MIN_AGE_MINUTES, 
            EARLY_TOKEN_MAX_AGE_MINUTES,
            EARLY_TOKEN_MIN_LIQUIDITY_SOL
        )
        
        # ========== SOURCE 1: PUMP.FUN (Early Bonding Curve) ==========
        try:
            from .pumpfun_scanner import PumpFunScanner, PumpToken
            
            pump_scanner = PumpFunScanner(
                session=self.session,
                min_age_minutes=EARLY_TOKEN_MIN_AGE_MINUTES,
                max_age_minutes=EARLY_TOKEN_MAX_AGE_MINUTES,
                min_market_cap=1000,  # $1k min
                max_market_cap=100000  # $100k max (early only)
            )
            
            pump_tokens = await pump_scanner.fetch_new_tokens(limit=30)
            
            # Convert PumpToken to TokenOpportunity format
            for pt in pump_tokens:
                try:
                    # Quick validation
                    if pt.virtual_sol_reserves < EARLY_TOKEN_MIN_LIQUIDITY_SOL:
                        continue
                    
                    opp = TokenOpportunity(
                        mint=pt.mint,
                        symbol=pt.symbol,
                        name=pt.name,
                        price_usd=0,  # Will be calculated
                        volume_24h=0,  # Not available from pump.fun
                        liquidity_usd=pt.virtual_sol_reserves * 200,  # Rough SOL->USD
                        price_change_24h=0,
                        market_cap=pt.market_cap,
                        safety_score=60,  # Base score for pump.fun tokens
                        phase="BONDING_CURVE"
                    )
                    opportunities.append(opp)
                    stats["pump_tokens"] += 1
                    logger.info(
                        f"🎰 [{pt.symbol}] PUMP.FUN added: "
                        f"MC=${pt.market_cap:,.0f} | Age={pt.age_minutes:.0f}m"
                    )
                except Exception as e:
                    logger.debug(f"Error converting pump token: {e}")
                    
        except Exception as e:
            logger.warning(f"Pump.fun scan failed: {e}")
            stats["pump_errors"] += 1
        
        # ========== SOURCE 2: DEXSCREENER (Trending/Jupiter) ==========
        trending_tokens = await self._fetch_trending_tokens()
        
        # Guard against None
        if not trending_tokens:
            trending_tokens = []
        
        stats["dex_tokens_raw"] = len(trending_tokens)
        
        # 📊 DEDUPLICATE: Keep only best pair per token (highest volume)
        best_pairs = {}
        for pair in trending_tokens:
            base_token = pair.get("baseToken", {})
            mint = base_token.get("address")
            if not mint:
                continue
            
            volume = float(pair.get("volume", {}).get("h24", 0) or 0)
            
            # Keep pair with highest volume for each token
            if mint not in best_pairs or volume > best_pairs[mint].get("_volume", 0):
                pair["_volume"] = volume  # Store for comparison
                best_pairs[mint] = pair
        
        # Convert back to list
        deduped_tokens = list(best_pairs.values())
        stats["dex_tokens_deduped"] = len(deduped_tokens)
        
        logger.info(f"📊 Found {len(deduped_tokens)} unique DexScreener tokens (from {len(trending_tokens)} pairs) + {stats['pump_tokens']} pump.fun tokens")
        
        # Filter and score DexScreener tokens (increased from 20 to 50)
        for token_data in deduped_tokens[:50]:
            try:
                stats["evaluated"] += 1
                opportunity = await self._evaluate_token(token_data, stats)
                if opportunity:
                    opportunities.append(opportunity)
                    stats["accepted"] += 1
            except Exception as e:
                logger.debug(f"Error evaluating {token_data.get('baseToken', {}).get('address', 'unknown')}: {e}")
        
        # Sort by safety score (highest first)
        opportunities.sort(key=lambda x: x.safety_score, reverse=True)
        
        # Return top results
        top_opportunities = opportunities[:self.max_results]
        
        if top_opportunities:
            logger.info(
                f"✅ Found {len(top_opportunities)} total opportunities "
                f"(Safety: {top_opportunities[0].safety_score}-{top_opportunities[-1].safety_score}/100)"
            )
        else:
            logger.warning("⚠️ No opportunities found matching criteria")
        
        # 📊 Log cycle summary
        logger.info(
            f"📈 CYCLE SUMMARY: "
            f"Evaluated={stats['evaluated']} | Accepted={stats['accepted']} | "
            f"Rejected: vol={stats['rejected_volume']}, liq={stats['rejected_liquidity']}, "
            f"volatility={stats['rejected_volatility']}, phase={stats['rejected_phase']}, "
            f"pool={stats['rejected_pool']}, holder={stats['rejected_holder']} | "
            f"PumpFun: ok={stats['pump_tokens']}, err={stats['pump_errors']}"
        )
        
        return top_opportunities
    
    async def _fetch_trending_tokens(self) -> List[Dict]:
        """
        Fetch trending tokens from DexScreener.
        
        Returns:
            List of token pair data from DexScreener API
        """
        try:
            # First try: get boosted/trending tokens
            boosted_url = "https://api.dexscreener.com/token-boosts/top/v1"
            
            async with self.session.get(boosted_url, timeout=10) as resp:
                if resp.status == 200:
                    boosts = await resp.json()
                    # Filter for Solana tokens from boosts
                    solana_mints = [
                        b.get("tokenAddress") 
                        for b in boosts 
                        if b.get("chainId") == "solana" and b.get("tokenAddress")
                    ][:10]  # Top 10 boosted
                    
                    if solana_mints:
                        # Fetch pair data for these tokens (increased from 5 to 15)
                        pairs_url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(solana_mints[:15])}"
                        async with self.session.get(pairs_url, timeout=10) as pairs_resp:
                            if pairs_resp.status == 200:
                                data = await pairs_resp.json()
                                pairs = data.get("pairs", [])
                                solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                                logger.debug(f"Fetched {len(solana_pairs)} Solana pairs from DexScreener boosts")
                                return solana_pairs
            
            # Fallback: search for high-volume Solana pairs
            search_url = "https://api.dexscreener.com/latest/dex/search?q=sol"
            async with self.session.get(search_url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"DexScreener search returned {resp.status}")
                    return []
                
                data = await resp.json()
                pairs = data.get("pairs", [])
                
                # Filter for Solana pairs only and sort by volume
                solana_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                solana_pairs.sort(key=lambda x: float(x.get("volume", {}).get("h24", 0) or 0), reverse=True)
                
                logger.debug(f"Fetched {len(solana_pairs)} Solana pairs from DexScreener search")
                return solana_pairs
                
        except Exception as e:
            logger.error(f"Failed to fetch trending tokens: {e}")
            return []
    
    async def _evaluate_token(self, pair_data: Dict, stats: Dict = None) -> Optional[TokenOpportunity]:
        """
        Evaluate a token for scalping potential.
        
        Args:
            pair_data: DexScreener pair data
            stats: Optional dict to track rejection statistics
            
        Returns:
            TokenOpportunity if passes filters, None otherwise
        """
        try:
            base_token = pair_data.get("baseToken", {})
            mint = base_token.get("address")
            
            if not mint:
                return None
            
            symbol = base_token.get("symbol", "???")
            
            # Extract metrics - with fallback for different API response formats
            volume_data = pair_data.get("volume", {})
            # Try h24 first, then m5 * 288 (approximate daily), then txns count
            volume_24h = float(volume_data.get("h24", 0) or 0)
            if volume_24h == 0:
                # Try h6 and multiply
                volume_h6 = float(volume_data.get("h6", 0) or 0)
                if volume_h6 > 0:
                    volume_24h = volume_h6 * 4
                else:
                    # Try h1 and multiply
                    volume_h1 = float(volume_data.get("h1", 0) or 0)
                    volume_24h = volume_h1 * 24
            
            # Liquidity with fallback
            liquidity_data = pair_data.get("liquidity", {})
            liquidity_usd = float(liquidity_data.get("usd", 0) or 0)
            
            # FALLBACK: If liquidity is 0, try base/quote or estimate from reserves
            if liquidity_usd == 0:
                # Try base liquidity
                base_liq = float(liquidity_data.get("base", 0) or 0)
                quote_liq = float(liquidity_data.get("quote", 0) or 0)
                if base_liq > 0 or quote_liq > 0:
                    price = float(pair_data.get("priceUsd", 0) or 0)
                    liquidity_usd = (base_liq * price) + quote_liq
                else:
                    # Estimate from FDV (market cap / 10 is rough liquidity estimate)
                    fdv = float(pair_data.get("fdv", 0) or 0)
                    if fdv > 0:
                        liquidity_usd = fdv / 10  # Conservative estimate
                        logger.debug(f"   📊 Liquidity fallback from FDV: ${liquidity_usd:.0f}")
            
            price_change_24h = abs(float(pair_data.get("priceChange", {}).get("h24", 0) or 0))
            price_usd = float(pair_data.get("priceUsd", 0) or 0)
            market_cap = float(pair_data.get("fdv", 0) or 0)
            
            # Calculate real age from pairCreatedAt timestamp
            import time
            pair_created_at = pair_data.get("pairCreatedAt", 0)
            if pair_created_at:
                age_seconds = time.time() - (pair_created_at / 1000)  # Convert ms to s
                dex_age_hours = max(0, age_seconds / 3600)
            else:
                dex_age_hours = 0
            
            # Log token being evaluated with key metrics
            logger.info(
                f"🔍 [{symbol}] Evaluating: "
                f"Vol=${volume_24h:,.0f} | Liq=${liquidity_usd:,.0f} | "
                f"MC=${market_cap:,.0f} | Chg={price_change_24h:+.1f}%"
            )
            
            # Quick filters (no RPC calls needed)
            if volume_24h < self.min_volume_24h:
                logger.info(f"   ❌ [{symbol}] REJECTED: Low volume ${volume_24h:.0f} < ${self.min_volume_24h}")
                if stats: stats["rejected_volume"] += 1
                return None
            
            if liquidity_usd < self.min_liquidity:
                logger.info(f"   ❌ [{symbol}] REJECTED: Low liquidity ${liquidity_usd:.0f} < ${self.min_liquidity}")
                if stats: stats["rejected_liquidity"] += 1
                return None
            
            if price_change_24h < self.min_price_change:
                logger.info(f"   ❌ [{symbol}] REJECTED: Low volatility {price_change_24h:.1f}% < {self.min_price_change}%")
                if stats: stats["rejected_volatility"] += 1
                return None
            
            logger.info(f"   ✅ [{symbol}] Passed basic filters, checking on-chain...")
            
            # Detect phase (on-chain check)
            phase = await self.validator.detect_token_phase(mint)
            if phase == "UNKNOWN":
                logger.info(f"   ❌ [{symbol}] REJECTED: Unknown token phase")
                return None
            
            logger.info(f"   📍 [{symbol}] Phase: {phase}")
            
            # Import target phases from config
            from ..config import TARGET_PHASES, EARLY_TOKEN_MIN_AGE_MINUTES, EARLY_TOKEN_MAX_AGE_MINUTES
            
            # Check if phase is in target list
            if phase not in TARGET_PHASES:
                logger.info(f"   ❌ [{symbol}] REJECTED: Phase {phase} not in target {TARGET_PHASES}")
                if stats: stats["rejected_phase"] += 1
                return None
            
            logger.info(f"   ✅ [{symbol}] Phase {phase} is targeted!")
            
            # Pool quality filter (includes age, volume, fake migration checks)
            pool_check = await self.validator.pool_quality_filter(phase, mint)
            pool_liquidity_sol = pool_check.get("liquidity_sol", 0)
            pool_age_hours = pool_check.get("age_hours", 0)
            pool_age_minutes = pool_age_hours * 60
            
            # Age filter ONLY for early phases (BONDING_CURVE, PUMPSWAP)
            # Jupiter tokens skip age check since validator doesn't track age for them
            if phase in ["BONDING_CURVE", "PUMPSWAP"]:
                if pool_age_minutes > 0 and pool_age_minutes < EARLY_TOKEN_MIN_AGE_MINUTES:
                    logger.info(f"   ❌ [{symbol}] REJECTED: Too new ({pool_age_minutes:.0f}m < {EARLY_TOKEN_MIN_AGE_MINUTES}m)")
                    return None
                
                if pool_age_minutes > EARLY_TOKEN_MAX_AGE_MINUTES:
                    logger.info(f"   ❌ [{symbol}] REJECTED: Too old ({pool_age_minutes:.0f}m > {EARLY_TOKEN_MAX_AGE_MINUTES}m)")
                    return None
                
                if pool_age_minutes > 0:
                    logger.info(f"   ✅ [{symbol}] Age OK: {pool_age_minutes:.0f} minutes")
            else:
                # Jupiter/Raydium - skip strict age filter
                logger.info(f"   ⏭️ [{symbol}] Skipping age filter for {phase}")
            
            if not pool_check.get("passed"):
                logger.info(
                    f"   ❌ [{symbol}] REJECTED: Pool quality failed - {pool_check.get('reason')} "
                    f"(Liq={pool_liquidity_sol:.1f} SOL, Age={pool_age_hours:.1f}h)"
                )
                if stats: stats["rejected_pool"] += 1
                return None
            
            logger.info(f"   ✅ [{symbol}] Pool quality OK (Liq={pool_liquidity_sol:.1f} SOL, Age={pool_age_minutes:.0f}m)")
            
            # Holder concentration check
            holder_check = await self.validator.check_holder_concentration(mint, phase)
            if not holder_check:
                logger.info(f"   ❌ [{symbol}] REJECTED: Top holders concentration too high")
                if stats: stats["rejected_holder"] += 1
                return None
            
            logger.info(f"   ✅ [{symbol}] Holder distribution OK")
            
            # L3: Volume Quality Check (wash trading detection)
            from .volume_analyzer import VolumeAnalyzer
            
            volume_analyzer = VolumeAnalyzer(self.session, vqr_threshold=5000.0)
            volume_metrics = await volume_analyzer.analyze(
                mint=mint,
                volume_24h=volume_24h,
                timeframe="5m"
            )
            
            if volume_metrics and volume_metrics.is_suspicious:
                logger.info(
                    f"   ❌ [{symbol}] REJECTED: Wash trading detected "
                    f"(VQR={volume_metrics.vqr:,.0f}, {volume_metrics.unique_wallets} wallets)"
                )
                if stats: stats["rejected_volume"] += 1
                return None
            
            if volume_metrics:
                logger.info(f"   ✅ [{symbol}] Volume quality OK (VQR={volume_metrics.vqr:,.0f})")
            
            # Get safety score from pool_quality_filter
            safety_score = pool_check.get("safety_score", 50)
            
            # PASSED ALL FILTERS!
            logger.info(
                f"   🎯 [{symbol}] ALL CHECKS PASSED! "
                f"Safety={safety_score}/100 | Phase={phase}"
            )
            
            # Create opportunity
            opportunity = TokenOpportunity(
                mint=mint,
                symbol=base_token.get("symbol", "???"),
                name=base_token.get("name", "Unknown"),
                price_usd=price_usd,
                volume_24h=volume_24h,
                liquidity_usd=liquidity_usd,
                price_change_24h=price_change_24h,
                market_cap=market_cap,
                safety_score=safety_score,
                phase=phase,
                age_hours=dex_age_hours if dex_age_hours > 0 else pool_age_hours  # Prefer DEX age
            )
            
            logger.info(
                f"🎯 [{mint[:8]}] {opportunity.symbol}: "
                f"Vol=${volume_24h:.0f}, Chg={price_change_24h:+.1f}%, "
                f"Safety={safety_score}/100"
            )
            
            return opportunity
            
        except Exception as e:
            logger.error(f"Token evaluation error: {e}")
            return None
    
    async def get_opportunity_details(self, mint: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed info for a specific opportunity.
        
        Args:
            mint: Token mint address
            
        Returns:
            Dict with detailed token info
        """
        try:
            # Get metadata
            metadata = await self.validator.get_token_metadata(mint)
            
            # Get phase
            phase = await self.validator.detect_token_phase(mint)
            
            # Get pool quality
            pool_check = await self.validator.pool_quality_filter(phase, mint)
            
            # Get age
            age_hours = await self.validator.get_token_age_hours(mint)
            
            return {
                "mint": mint,
                "name": metadata.get("name"),
                "symbol": metadata.get("symbol"),
                "market_cap": metadata.get("mcap"),
                "phase": phase,
                "liquidity_sol": pool_check.get("liquidity_sol", 0),
                "age_hours": age_hours,
                "volume_24h": pool_check.get("volume_24h", 0),
                "safety_score": pool_check.get("safety_score", 0)
            }
            
        except Exception as e:
            logger.error(f"Failed to get details for {mint[:8]}: {e}")
            return None
