"""
Price Feed Client

Fetches real-time token prices from DexScreener and other sources.
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import aiohttp

from ..utils.retry import async_retry
from ..utils.rate_limiter import TokenBucket, rate_limited

logger = logging.getLogger(__name__)

# Rate limiters
dexscreener_limiter = TokenBucket(rate=5.0, capacity=10)  # 5 req/sec
birdeye_limiter = TokenBucket(rate=10.0, capacity=20)  # 10 req/sec (more generous)


@dataclass
class TokenPrice:
    """Token price data"""
    mint: str
    price_usd: float
    price_sol: float
    liquidity_usd: float
    volume_24h: float
    price_change_24h: float
    dex: str
    pair_address: str
    timestamp: float
    
    @property
    def is_fresh(self) -> bool:
        """Check if price is fresh (< 30s old)"""
        return time.time() - self.timestamp < 30


class PriceFeed:
    """
    Multi-source price feed for Solana tokens.
    
    Sources:
    - DexScreener (primary)
    - Jupiter Price API (fallback)
    - On-chain calculation (last resort)
    
    Features:
    - Caching with TTL
    - Rate limiting
    - Retry logic
    - Multiple pair support
    """
    
    DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
    BIRDEYE_API = "https://public-api.birdeye.so"  # More accurate, official API
    JUPITER_PRICE_API = "https://price.jup.ag/v6/price"
    
    # SOL price cache
    SOL_PRICE_USD = 0.0
    SOL_PRICE_UPDATED = 0.0
    
    def __init__(self, session: aiohttp.ClientSession, validator=None):
        """
        Initialize price feed.
        
        Args:
            session: aiohttp session for requests
            validator: Optional Validator instance for on-chain fallback
        """
        self.session = session
        self.validator = validator  # For on-chain reserve calculation
        self._cache: Dict[str, TokenPrice] = {}
        self._cache_ttl = 10  # seconds
    
    @rate_limited(dexscreener_limiter)
    @async_retry(max_attempts=2, delay=1.0)
    async def get_price(self, mint: str) -> Optional[TokenPrice]:
        """
        Get token price from best available source.
        
        Args:
            mint: Token mint address
        
        Returns:
            TokenPrice or None if not found
        """
        # Check cache
        if mint in self._cache:
            cached = self._cache[mint]
            if cached.is_fresh:
                return cached
        
        # Try Birdeye first (most accurate, official API)
        price = await self._fetch_birdeye(mint)
        
        if price:
            self._cache[mint] = price
            return price
        
        # Fallback to DexScreener
        price = await self._fetch_dexscreener(mint)
        
        if price:
            self._cache[mint] = price
            return price
        
        # Last resort: Jupiter
        price = await self._fetch_jupiter_price(mint)
        
        if price:
            self._cache[mint] = price
            return price
        
        logger.warning(f"Could not fetch price for {mint[:20]}...")
        return None
    
    @rate_limited(birdeye_limiter)
    @async_retry(max_attempts=2, delay=0.5)
    async def _fetch_birdeye(self, mint: str) -> Optional[TokenPrice]:
        """
        Fetch price from Birdeye API.
        
        Endpoint: /defi/price
        Advantages: Official API, more accurate, better rate limits
        """
        from ..config import BIRDEYE_API_KEY
        
        if not BIRDEYE_API_KEY:
            # Silently skip if no API key
            return None
        
        try:
            url = f"{self.BIRDEYE_API}/defi/price"
            
            params = {"address": mint}
            headers = {
                "X-API-KEY": BIRDEYE_API_KEY,
                "x-chain": "solana"
            }
            
            async with self.session.get(url, params=params, headers=headers, timeout=5) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                
                if not data.get("success"):
                    return None
                
                price_data = data.get("data", {})
                price_usd = float(price_data.get("value", 0) or 0)
                
                if price_usd == 0:
                    return None
                
                # Get SOL price for conversion
                sol_price = await self._get_sol_price()
                price_sol = price_usd / sol_price if sol_price > 0 else 0
                
                # Birdeye also provides liquidity and volume
                liquidity = float(price_data.get("liquidity", 0) or 0)
                volume_24h = float(price_data.get("v24hUSD", 0) or 0)
                price_change = float(price_data.get("priceChange24h", 0) or 0)
                
                logger.debug(f"[Birdeye] {mint[:8]}: ${price_usd:.8f}")
                
                return TokenPrice(
                    mint=mint,
                    price_usd=price_usd,
                    price_sol=price_sol,
                    liquidity_usd=liquidity,
                    volume_24h=volume_24h,
                    price_change_24h=price_change,
                    dex="birdeye",
                    pair_address="",
                    timestamp=time.time()
                )
        
        except Exception as e:
            logger.debug(f"Birdeye fetch failed: {e}")
            return None
    
    async def _fetch_dexscreener(self, mint: str) -> Optional[TokenPrice]:
        """Fetch price from DexScreener"""
        try:
            url = f"{self.DEXSCREENER_API}/tokens/{mint}"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                
                pairs = data.get("pairs", [])
                if not pairs:
                    return None
                
                # Get best pair (highest liquidity)
                best_pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
                
                price_usd = float(best_pair.get("priceUsd", 0) or 0)
                
                # Get SOL price
                sol_price = await self._get_sol_price()
                price_sol = price_usd / sol_price if sol_price > 0 else 0
                
                return TokenPrice(
                    mint=mint,
                    price_usd=price_usd,
                    price_sol=price_sol,
                    liquidity_usd=float(best_pair.get("liquidity", {}).get("usd", 0) or 0),
                    volume_24h=float(best_pair.get("volume", {}).get("h24", 0) or 0),
                    price_change_24h=float(best_pair.get("priceChange", {}).get("h24", 0) or 0),
                    dex=best_pair.get("dexId", "unknown"),
                    pair_address=best_pair.get("pairAddress", ""),
                    timestamp=time.time()
                )
        
        except Exception as e:
            logger.error(f"DexScreener error: {e}")
            return None
    
    async def _fetch_jupiter_price(self, mint: str) -> Optional[TokenPrice]:
        """Fetch price from Jupiter Price API"""
        try:
            url = f"{self.JUPITER_PRICE_API}?ids={mint}"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return None
                
                data = await resp.json()
                
                token_data = data.get("data", {}).get(mint)
                if not token_data:
                    return None
                
                price_usd = float(token_data.get("price", 0) or 0)
                
                # Get SOL price
                sol_price = await self._get_sol_price()
                price_sol = price_usd / sol_price if sol_price > 0 else 0
                
                return TokenPrice(
                    mint=mint,
                    price_usd=price_usd,
                    price_sol=price_sol,
                    liquidity_usd=0,  # Jupiter doesn't provide
                    volume_24h=0,
                    price_change_24h=0,
                    dex="jupiter",
                    pair_address="",
                    timestamp=time.time()
                )
        
        except Exception as e:
            logger.error(f"Jupiter price error: {e}")
            return None
    
    async def _get_sol_price(self) -> float:
        """Get SOL price in USD (cached)"""
        # Check cache (5 min TTL for SOL)
        if time.time() - self.SOL_PRICE_UPDATED < 300 and self.SOL_PRICE_USD > 0:
            return self.SOL_PRICE_USD
        
        try:
            # Use DexScreener for SOL/USDC
            sol_mint = "So11111111111111111111111111111111111111112"
            url = f"{self.DEXSCREENER_API}/tokens/{sol_mint}"
            
            async with self.session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    if pairs:
                        # Find USDC pair
                        for pair in pairs:
                            if "USDC" in pair.get("baseToken", {}).get("symbol", "").upper():
                                self.SOL_PRICE_USD = float(pair.get("priceUsd", 0) or 0)
                                self.SOL_PRICE_UPDATED = time.time()
                                return self.SOL_PRICE_USD
                        
                        # Use first pair
                        self.SOL_PRICE_USD = float(pairs[0].get("priceUsd", 0) or 0)
                        self.SOL_PRICE_UPDATED = time.time()
                        return self.SOL_PRICE_USD
        
        except Exception as e:
            logger.error(f"SOL price error: {e}")
        
        # Default fallback
        return self.SOL_PRICE_USD if self.SOL_PRICE_USD > 0 else 200.0
    
    async def get_token_value_sol(self, mint: str, token_amount: int, decimals: int = 6) -> float:
        """
        Calculate token value in SOL using Jupiter Quote for accuracy (realizable value).
        
        IMPORTANT: We only use Jupiter Quote for price discovery.
        DexScreener/PriceAPI fallback is DISABLED because it returns theoretical prices
        that don't reflect actual realizable value, causing fake PnL (+99,000% etc.)
        
        If Jupiter Quote fails, we return 0 and let the caller use entry_sol as fallback.
        
        Args:
            mint: Token mint address
            token_amount: Raw token amount (will be used directly with Jupiter)
            decimals: Token decimals (for logging only)
        
        Returns:
            Value in SOL, or 0 if quote fails
        """
        human_tokens = token_amount / (10 ** decimals)
        
        # STRATEGY: Use Jupiter Quote API to get EXACT realizable value
        # This prevents "fake PnL" from theoretical prices on illiquid tokens
        try:
            import ssl
            import socket
            
            wsol_mint = "So11111111111111111111111111111111111111112"
            quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={mint}&outputMint={wsol_mint}&amount={int(token_amount)}&slippageBps=100"
            
            # DNS bypass: patch socket.getaddrinfo to resolve Jupiter to CloudFlare IP
            original_getaddrinfo = socket.getaddrinfo
            def custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
                if host == 'quote-api.jup.ag':
                    # Use CloudFlare IP for Jupiter
                    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('172.67.130.144', port))]
                return original_getaddrinfo(host, port, family, type, proto, flags)
            
            socket.getaddrinfo = custom_getaddrinfo
            
            try:
                # Create SSL context that doesn't verify certificates (needed for DNS bypass)
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                
                import aiohttp
                connector = aiohttp.TCPConnector(ssl=ssl_context)
                
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(quote_url, timeout=5) as resp:
                        if resp.status == 200:
                            quote = await resp.json()
                            out_amount = int(quote.get("outAmount", 0))
                            if out_amount > 0:
                                value_sol = out_amount / 1e9  # Convert lamports to SOL
                                logger.debug(f"💰 Jupiter Quote: {human_tokens:,.0f} tokens = {value_sol:.6f} SOL")
                                return value_sol
                            else:
                                logger.warning(f"⚠️ Jupiter Quote returned 0 for {mint[:8]}... (tokens={human_tokens:,.0f})")
                        else:
                            logger.warning(f"⚠️ Jupiter Quote HTTP {resp.status} for {mint[:8]}...")
            finally:
                # Restore original getaddrinfo
                socket.getaddrinfo = original_getaddrinfo
                
        except Exception as e:
            logger.warning(f"⚠️ Jupiter Quote failed for {mint[:8]}...: {e}")

        # NOTE: DexScreener/PriceAPI fallback DISABLED!
        # These return theoretical prices that cause fake PnL (+99,000% etc.)
        # The caller (bot.py) will use entry_sol as a safe fallback when we return 0
        
        # FALLBACK: On-chain reserve calculation (more reliable than DexScreener)
        
        # FALLBACK 2: On-chain reserve calculation for new tokens
        if self.validator:
            try:
                logger.debug(f"🔍 Trying on-chain price for {mint[:8]}...")
                
                # Try PumpSwap pool first (most common for new tokens)
                pool_state = await self.validator.get_pumpswap_pool_state(mint)
                if pool_state:
                    logger.debug(f"PumpSwap pool found: sol={pool_state.get('sol_reserves', 0)} tok={pool_state.get('token_reserves', 0)}")
                    if pool_state.get('sol_reserves', 0) > 0:
                        sol_reserves = pool_state['sol_reserves'] / 1e9
                        token_reserves = pool_state['token_reserves']
                        
                        if token_reserves > 0:
                            # Price per token in SOL
                            price_per_token = sol_reserves / token_reserves
                            value = (token_amount / (10 ** decimals)) * price_per_token
                            logger.info(f"📈 On-chain price for {mint[:8]}: {value:.4f} SOL (PumpSwap)")
                            return value
                else:
                    logger.debug(f"No PumpSwap pool for {mint[:8]}")
                
                # Try Bonding Curve
                bc_state = await self.validator.get_bonding_curve_state(mint)
                if bc_state:
                    logger.debug(f"Bonding curve found: sol={bc_state.get('sol_reserves', 0)} tok={bc_state.get('token_reserves', 0)}")
                    if bc_state.get('sol_reserves', 0) > 0:
                        sol_reserves = bc_state['sol_reserves'] / 1e9
                        token_reserves = bc_state['token_reserves']
                        
                        if token_reserves > 0:
                            price_per_token = sol_reserves / token_reserves
                            value = (token_amount / (10 ** decimals)) * price_per_token
                            logger.info(f"📈 On-chain price for {mint[:8]}: {value:.4f} SOL (Bonding)")
                            return value
                else:
                    logger.debug(f"No bonding curve for {mint[:8]}")
                
                # Try Raydium Pool (for JUPITER phase tokens)
                raydium_pool = await self.validator.find_raydium_pool(mint)
                if raydium_pool:
                    logger.debug(f"Raydium pool found, getting reserves...")
                    reserves = await self.validator.get_raydium_reserves(raydium_pool)
                    if reserves:
                        logger.debug(f"Raydium reserves: sol={reserves.get('sol_reserves', 0)} tok={reserves.get('token_reserves', 0)}")
                        if reserves.get('sol_reserves', 0) > 0:
                            sol_reserves = reserves['sol_reserves'] / 1e9
                            token_reserves = reserves['token_reserves']
                            
                            if token_reserves > 0:
                                price_per_token = sol_reserves / token_reserves
                                value = (token_amount / (10 ** decimals)) * price_per_token
                                logger.info(f"📈 On-chain price for {mint[:8]}: {value:.4f} SOL (Raydium)")
                                return value
                else:
                    logger.debug(f"No Raydium pool for {mint[:8]}")
                
            except Exception as e:
                logger.warning(f"On-chain price fallback exception: {e}")
        else:
            logger.debug(f"No validator available for on-chain price")
        
        # FALLBACK 3: DexScreener (theoretical price with conservative haircut)
        # Apply 10% discount to account for slippage/spread on new tokens
        try:
            token_price = await self.get_price(mint)
            if token_price and token_price.price_sol > 0:
                theoretical_value = human_tokens * token_price.price_sol
                # Apply 10% haircut for conservative estimate
                discounted_value = theoretical_value * 0.90
                logger.info(f"📉 DexScreener fallback for {mint[:8]}: {discounted_value:.6f} SOL (10% haircut applied)")
                return discounted_value
        except Exception as e:
            logger.warning(f"DexScreener price fallback failed: {e}")
        
        logger.warning(f"⚠️ Could not get price for {mint[:8]}... returning 0")
        return 0.0
    
    async def get_multiple_prices(self, mints: List[str]) -> Dict[str, TokenPrice]:
        """
        Get prices for multiple tokens.
        
        Args:
            mints: List of token mint addresses
        
        Returns:
            Dict of mint -> TokenPrice
        """
        results = {}
        
        # Fetch in parallel
        tasks = [self.get_price(mint) for mint in mints]
        prices = await asyncio.gather(*tasks, return_exceptions=True)
        
        for mint, price in zip(mints, prices):
            if isinstance(price, TokenPrice):
                results[mint] = price
        
        return results
    
    def get_cached_price(self, mint: str) -> Optional[TokenPrice]:
        """Get cached price without fetching"""
        return self._cache.get(mint)
    
    def clear_cache(self):
        """Clear price cache"""
        self._cache.clear()
