
import asyncio
import base64
import struct
import time
import logging
from typing import Dict, Optional, Any, Tuple
from collections import OrderedDict
import aiohttp
from solders.pubkey import Pubkey # type: ignore
from solana.rpc.async_api import AsyncClient

logger = logging.getLogger(__name__)

from ..constants import (
    PUMP_PROGRAM, PUMP_AMM_PROGRAM, RAYDIUM_V4_PROGRAM, TOKEN_PROGRAM, 
    OPENBOOK_PROGRAM, WSOL_MINT, JUPITER_QUOTE_API
)
from ..constants import PUMP_BUY_DISC, PUMPSWAP_BUY_DISC
from ..utils.helpers import sane_reserves
from .rpc_cache import get_rpc_cache, get_credit_limiter


class LRUCache(OrderedDict):
    """Simple LRU cache with max size and optional TTL."""
    
    def __init__(self, maxsize: int = 500, ttl_seconds: float = 3600):
        super().__init__()
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self._timestamps: Dict[str, float] = {}
    
    def get(self, key, default=None):
        if key not in self:
            return default
        # Check TTL
        if self.ttl and time.time() - self._timestamps.get(key, 0) > self.ttl:
            del self[key]
            self._timestamps.pop(key, None)
            return default
        # Move to end (most recently used)
        self.move_to_end(key)
        return super().__getitem__(key)
    
    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        self._timestamps[key] = time.time()
        # Evict oldest if over capacity
        while len(self) > self.maxsize:
            oldest = next(iter(self))
            del self[oldest]
            self._timestamps.pop(oldest, None)
    
    def __contains__(self, key):
        if not super().__contains__(key):
            return False
        # Check TTL
        if self.ttl and time.time() - self._timestamps.get(key, 0) > self.ttl:
            del self[key]
            self._timestamps.pop(key, None)
            return False
        return True


class Validator:
    def __init__(self, session: aiohttp.ClientSession, client: AsyncClient):
        self.session = session
        self.client = client
        # P1 FIX: Use bounded LRU caches to prevent memory leak
        self.raydium_pool_cache = LRUCache(maxsize=500, ttl_seconds=1800)  # 30 min TTL
        self.decimals_cache = LRUCache(maxsize=1000, ttl_seconds=86400)  # 24h TTL (rarely changes)
        self.token_metadata_cache = LRUCache(maxsize=500, ttl_seconds=3600)  # 1h TTL
        self._age_cache = LRUCache(maxsize=500, ttl_seconds=300)  # 5 min TTL

    async def get_token_decimals(self, mint_str: str) -> int:
        if mint_str in self.decimals_cache: return self.decimals_cache[mint_str]
        try:
            resp = await self.client.get_account_info(Pubkey.from_string(mint_str))
            if resp.value:
                # Mint layout: Option<u32> (4), Decimals (1) at offset 44
                decimals = resp.value.data[44]
                self.decimals_cache[mint_str] = decimals
                return decimals
        except Exception as e:
            print(f"[WARN] Failed to get decimals for {mint_str[:8]}: {e}")
        return 6 # Default fallback

    async def get_bonding_curve_state(self, mint_str: str) -> Optional[Dict]:
        """Fetch bonding curve state with retries (Robust for fresh tokens)."""
        # Increased to 10 retries with backoff for new tokens (RPC lag)
        max_retries = 10
        
        for attempt in range(max_retries):
            try:
                mint = Pubkey.from_string(mint_str)
                curve, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint)], PUMP_PROGRAM)
                
                resp = await self.client.get_account_info(curve)
                if resp.value:
                    raw_data = resp.value.data
                    if isinstance(raw_data, (list, tuple)):
                        data = base64.b64decode(raw_data[0])
                    elif isinstance(raw_data, str):
                        data = base64.b64decode(raw_data)
                    else:
                        data = bytes(raw_data)
                    
                    sol_reserves = struct.unpack_from("<Q", data, 16)[0]
                    token_reserves = struct.unpack_from("<Q", data, 8)[0]
                    
                    return {
                        "progress": (struct.unpack_from("<Q", data, 32)[0] / 1e9 / 85) * 100,
                        "token_reserves": token_reserves, 
                        "sol_reserves": sol_reserves
                    }
                else:
                    if attempt < max_retries - 1:
                        # Exponential backoff: 0.2, 0.4, 0.8, ... max 1.5s
                        sleep_time = min(0.2 * (2 ** attempt), 1.5)
                        # logger.debug(f"⏳ BC lookup attempt {attempt+1} failed, retrying in {sleep_time:.1f}s...")
                        await asyncio.sleep(sleep_time)
                        continue
            except Exception as e:
                # print(f"[WARN] BC lookup error: {e}")
                if attempt < max_retries - 1: await asyncio.sleep(0.5)
        
        return None

    async def get_pumpswap_pool_state(self, mint_str: str) -> Optional[Dict]:
        try:
            mint = Pubkey.from_string(mint_str)
            # WSOL_MINT is already a Pubkey from constants.py, no need to convert
            wsol_pk = WSOL_MINT if isinstance(WSOL_MINT, Pubkey) else Pubkey.from_string(str(WSOL_MINT))
            
            for order in [(mint, wsol_pk), (wsol_pk, mint)]:
                # Use __bytes__() method for Pubkey objects
                pda, _ = Pubkey.find_program_address(
                    [b"pool", bytes(order[0]), bytes(order[1])], 
                    PUMP_AMM_PROGRAM
                )
                resp = await self.client.get_account_info(pda)
                if resp.value:
                    raw_data = resp.value.data
                    if isinstance(raw_data, (list, tuple)): 
                        data = base64.b64decode(raw_data[0])
                    elif isinstance(raw_data, str): 
                        data = base64.b64decode(raw_data)
                    else: 
                        data = bytes(raw_data)
                    return {
                        "token_reserves": struct.unpack_from("<Q", data, 8)[0], 
                        "sol_reserves": struct.unpack_from("<Q", data, 16)[0]
                    }
        except Exception as e:
            print(f"[ERR] get_pumpswap_pool_state failed: {e}")
        return None

    async def find_raydium_pool(self, mint_str: str) -> Optional[Dict]:
        if mint_str in self.raydium_pool_cache:
            return self.raydium_pool_cache[mint_str]
        
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_str}"
            async with self.session.get(url, timeout=2) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", []) or []
                    for pair in pairs:
                         if pair.get("dexId") == "raydium" and pair.get("pairAddress"):
                             pool_data = {
                                 "amm_id": Pubkey.from_string(pair["pairAddress"]),
                                 "base_mint": Pubkey.from_string(pair["baseToken"]["address"]),
                                 "quote_mint": Pubkey.from_string(pair["quoteToken"]["address"]),
                                 "market_id": Pubkey.from_string(pair.get("marketId", pair["pairAddress"])), # Fallback?
                                 "nonce": 255, # Mock, will be fetched/checked
                                 "is_quote": pair["quoteToken"]["address"] == WSOL_MINT
                             }
                             # We need to fetch the actual pool account to get open_orders, vaults etc.
                             # But original code actually PARSES the pool account data if found via RPC scan.
                             # If found via DexScreener, it calls `parse_raydium_pool`? 
                             # Wait, original `find_raydium_pool` line 2379 (trunc) 
                             # actually fetches account info of the pairAddress found from DexScreener!
                             
                             pool_info = await self.client.get_account_info(pool_data["amm_id"])
                             if pool_info.value:
                                 parsed = self.parse_raydium_pool(pool_info.value.data)
                                 if parsed:
                                     parsed["amm_id"] = pool_data["amm_id"] # ensure ID is set
                                     self.raydium_pool_cache[mint_str] = parsed
                                     return parsed
        except Exception as e:
            logger.debug(f"Failed to find Raydium pool: {e}")
            pass
        return None

    def parse_raydium_pool(self, data: bytes, is_quote: bool = False) -> Optional[Dict]:
        try:
            if isinstance(data, str): data = base64.b64decode(data)
            # Layout V4:
            # status: u64 (0)
            # nonce: u64 (8)
            # max_order: u64 (16)
            # depth: u64 (24)
            # base_decimal: u64 (32)
            # quote_decimal: u64 (40)
            # state: u64 (48)
            # reset_flag: u64 (56)
            # min_size: u64 (64)
            # vol_max: u64 (72)
            # vol_accum: u64 (80)
            # min_separate: u64 (88)
            # base_vault: Pubkey (208) ? No, offsets might differ.
            # Original code used:
            # nonce = struct.unpack_from("<Q", data, 8)[0]
            # base_mint = data[400:432]
            # quote_mint = data[432:464]
            # base_vault = data[208:240]
            # quote_vault = data[240:272]
            # open_orders = data[336:368]
            # market_id = data[368:400]
            
            nonce = struct.unpack_from("<Q", data, 8)[0]
            return {
                "amm_id": None, "nonce": nonce,
                "base_mint": Pubkey.from_bytes(data[400:432]), "quote_mint": Pubkey.from_bytes(data[432:464]),
                "base_vault": Pubkey.from_bytes(data[208:240]), "quote_vault": Pubkey.from_bytes(data[240:272]),
                "open_orders": Pubkey.from_bytes(data[336:368]), "market_id": Pubkey.from_bytes(data[368:400]),
                "is_quote": is_quote
            }
        except Exception as e:
            logger.debug(f"Raydium pool parsing failed: {e}")
            return None

    async def get_raydium_reserves(self, pool: Dict) -> Optional[Dict]:
        try:
             base = await self.client.get_token_account_balance(pool['base_vault'])
             quote = await self.client.get_token_account_balance(pool['quote_vault'])
             if base.value and quote.value:
                 b, q = int(base.value.amount), int(quote.value.amount)
                 # If quote mint is WSOL, quote reserves is SOL
                 if str(pool.get('quote_mint')) == WSOL_MINT:
                     return {"token_reserves": b, "sol_reserves": q}
                 else:
                     return {"token_reserves": q, "sol_reserves": b}
        except Exception as e:
            logger.debug(f"Raydium reserves fetch failed: {e}")
            pass
        return None

    async def get_openbook_market_accounts(self, market_id: Pubkey) -> Optional[Dict]:
        try:
            resp = await self.client.get_account_info(market_id)
            if not resp.value: return None
            data = base64.b64decode(resp.value.data) if isinstance(resp.value.data, str) else resp.value.data
            nonce = struct.unpack_from("<Q", data, 216)[0] # Offset 216? Original code says 216? 
            # Original code: nonce = struct.unpack_from("<Q", data, 216)[0]
            # Checks out with some OpenBook layouts (MarketStateV2)
            
            # Offsets from original:
            # bids: 40-72
            # asks: 72-104
            # event_queue: 104-136
            # base_vault: 144-176
            # quote_vault: 184-216
            
            market_auth, _ = Pubkey.find_program_address(
                [bytes(market_id), struct.pack("<Q", nonce)], 
                OPENBOOK_PROGRAM
            ) # Wait, original uses create_program_address logic? 
            # Original: Pubkey.create_program_address([bytes(market_id), struct.pack("<Q", nonce)], OPENBOOK_PROGRAM)
            
            vault_signer = Pubkey.create_program_address([bytes(market_id), struct.pack("<Q", nonce)], OPENBOOK_PROGRAM)
            
            return {
                "bids": Pubkey.from_bytes(data[40:72]), "asks": Pubkey.from_bytes(data[72:104]),
                "event_queue": Pubkey.from_bytes(data[104:136]), "base_vault": Pubkey.from_bytes(data[144:176]),
                "quote_vault": Pubkey.from_bytes(data[184:216]),
                "vault_signer": vault_signer
            }
        except Exception as e:
            logger.debug(f"OpenBook market accounts fetch failed: {e}")
            return None

    async def detect_token_phase(self, mint_str: str) -> str: # Returns "BONDING_CURVE" | "PUMPSWAP" | "RAYDIUM" | "JUPITER" | "UNKNOWN"
        logger.info(f"🔍 [PHASE] Starting detection for {mint_str[:12]}...")
        try:
            # 1. Bonding Curve - only if it has actual liquidity (TIMEOUT: 3s)
            logger.info(f"🔍 [PHASE] Step 1/4: Checking Bonding Curve...")
            try:
                bc = await asyncio.wait_for(self.get_bonding_curve_state(mint_str), timeout=3.0)
                if bc and bc.get('sol_reserves', 0) > 0:
                    logger.info(f"✅ [PHASE] BONDING_CURVE detected | Liq: {bc['sol_reserves']/1e9:.4f} SOL")
                    return "BONDING_CURVE"
                elif bc:
                    logger.info(f"🔍 [PHASE] BC exists but empty (graduated)")
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ [PHASE] BC check timeout (3s) - skipping")

            # 2. PumpSwap (TIMEOUT: 3s)
            logger.info(f"🔍 [PHASE] Step 2/4: Checking PumpSwap...")
            try:
                ps = await asyncio.wait_for(self.get_pumpswap_pool_state(mint_str), timeout=3.0)
                if ps:
                    logger.info(f"✅ [PHASE] PUMPSWAP detected")
                    return "PUMPSWAP"
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ [PHASE] PumpSwap check timeout (3s) - skipping")

            # 3. Raydium (TIMEOUT: 5s)
            logger.info(f"🔍 [PHASE] Step 3/4: Checking Raydium...")
            try:
                ray = await asyncio.wait_for(self.find_raydium_pool(mint_str), timeout=5.0)
                if ray:
                    logger.info(f"✅ [PHASE] RAYDIUM detected")
                    return "RAYDIUM"
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ [PHASE] Raydium check timeout (5s) - skipping")

            # 4. Jupiter (Quote Check) - with retry (TIMEOUT: 5s per attempt)
            logger.info(f"🔍 [PHASE] Step 4/4: Checking Jupiter Quote...")
            wsol_str = str(WSOL_MINT) if not isinstance(WSOL_MINT, str) else WSOL_MINT
            quote_params = {
                "inputMint": wsol_str, "outputMint": mint_str, "amount": "1000000000", 
                "slippageBps": "500"
            }
            
            for attempt in range(2):  # Reduced to 2 attempts
                try:
                    async with self.session.get(JUPITER_QUOTE_API, params=quote_params, timeout=5) as r:
                        if r.status == 200:
                            q = await r.json()
                            if q.get("outAmount"):
                                logger.info(f"✅ [PHASE] JUPITER detected (quote success)")
                                return "JUPITER"
                        logger.debug(f"Jupiter quote status: {r.status}")
                        break  # Non-200 response, stop retrying
                except asyncio.TimeoutError:
                    logger.warning(f"⏱️ [PHASE] Jupiter attempt {attempt+1} timeout (5s)")
                    if attempt < 1:
                        continue
                except Exception as jup_err:
                    logger.debug(f"Jupiter attempt {attempt+1} failed: {jup_err}")
                    if attempt < 1:
                        continue
                    
        except Exception as e:
            logger.error(f"❌ [PHASE] Detection error: {e}")
        
        # 5. Fallback: If token has market data, assume tradeable via Jupiter
        logger.info(f"🔍 [PHASE] Fallback: Checking metadata...")
        try:
            meta = await asyncio.wait_for(self.get_token_metadata(mint_str), timeout=3.0)
            if meta and meta.get("mcap", 0) > 0:
                logger.info(f"✅ [PHASE] JUPITER (fallback via mcap: ${meta.get('mcap'):,.0f})")
                return "JUPITER"
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ [PHASE] Metadata fallback timeout (3s)")
        except Exception as e:
            logger.debug(f"Fallback phase detection failed: {e}")
        
        logger.warning(f"❌ [PHASE] UNKNOWN - All checks failed for {mint_str[:12]}")
        return "UNKNOWN"

    async def get_sol_price(self) -> float:
        """Fetch SOL price in USD with caching (1 min TTL)."""
        now = time.time()
        if now - getattr(self, "_sol_price_cache_ts", 0) < 60:
            return getattr(self, "_sol_price_cache", 0.0)
            
        try:
            # Use DexScreener for SOL wrapper
            url = f"https://api.dexscreener.com/latest/dex/tokens/{WSOL_MINT}"
            async with self.session.get(url, timeout=2) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        price = float(pairs[0].get("priceUsd", 0.0))
                        if price > 0:
                            self._sol_price_cache = price
                            self._sol_price_cache_ts = now
                            return price
        except Exception as e:
            logger.debug(f"SOL price fetch failed: {e}")
        
        # Fallback if cache exists (even if old)
        return getattr(self, "_sol_price_cache", 0.0)

    async def get_token_metadata(self, mint_str: str) -> Dict:
        """Fetch Name, Symbol, MCap from DexScreener with Bonding Curve Fallback."""
        if mint_str in self.token_metadata_cache:
            return self.token_metadata_cache[mint_str]
        
        meta = {"name": "Unknown", "symbol": "???", "mcap": 0.0}
        
        # 1. Try DexScreener
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_str}"
            async with self.session.get(url, timeout=2) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        best = pairs[0] # Usually the most liquid
                        if float(best.get("fdv", 0.0)) > 0:
                            meta = {
                                "name": best.get("baseToken", {}).get("name", "Unknown"),
                                "symbol": best.get("baseToken", {}).get("symbol", "???"),
                                "mcap": float(best.get("fdv", 0.0))
                            }
                            self.token_metadata_cache[mint_str] = meta
                            return meta
        except Exception as e:
            pass # Continue to fallback

        # 2. Fallback: Bonding Curve Calc (for Pump.fun)
        try:
            if meta["mcap"] == 0:
                bc = await self.get_bonding_curve_state(mint_str)
                if bc:
                    # Calc Market Cap:
                    # K = vSol * vToken
                    # Price_SOL = vSol / vToken
                    # Supply = 1,000,000,000 (1B)
                    
                    v_sol = bc.get("sol_reserves", 0) / 1e9
                    v_token = bc.get("token_reserves", 0) / 1e6
                    
                    if v_token > 0:
                        price_sol = v_sol / v_token
                        supply = 1_000_000_000 # 1B supply for Pump.fun
                        mcap_sol = price_sol * supply
                        
                        # Get SOL price
                        sol_price = await self.get_sol_price()
                        if sol_price > 0:
                            mcap_usd = mcap_sol * sol_price
                            meta["mcap"] = mcap_usd
                            meta["name"] = "Pump Token" # Generic fallback if name unknown
                            meta["symbol"] = "PUMP"
                            
                            # Cache this result
                            self.token_metadata_cache[mint_str] = meta
                            logger.info(f"🧮 Calculated Bonding Curve Mcap: ${mcap_usd:,.0f} ({price_sol:.6f} SOL)")
                            return meta
        except Exception as e:
             logger.debug(f"Bonding Curve Mcap calc failed: {e}")

        return meta

    async def get_market_context(self, mint_str: str) -> Dict[str, float]:
        ctx = {"m5": 0.0, "h1": 0.0, "vol_h1": 0.0, "liquidity": 0.0}
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint_str}"
            async with self.session.get(url, timeout=3) as r:
                if r.status == 200:
                    data = await r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        best = next((p for p in pairs if p.get("chainId") == "solana"), None)
                        if best:
                            pc = best.get("priceChange", {})
                            ctx["m5"] = float(pc.get("m5", 0.0))
                            ctx["h1"] = float(pc.get("h1", 0.0))
                            ctx["vol_h1"] = float(best.get("volume", {}).get("h1", 0.0))
                            ctx["liquidity"] = float(best.get("liquidity", {}).get("usd", 0.0))
        except Exception as e:
            print(f"[WARN] Market Context Fetch Err: {e}")
        return ctx

    async def check_holder_concentration(self, mint_str: str, phase: str) -> bool:
        """Check if any holder has >30% concentration (cached to reduce RPC load)."""
        if phase == "BONDING_CURVE": return True
        
        # Credit limiter check - skip if over budget
        limiter = get_credit_limiter()
        if limiter.should_skip("low"):
            logger.debug(f"Skipping holder check for {mint_str[:8]} - credit budget")
            return True  # Safe default
        
        cache = get_rpc_cache()
        cache_key = f"{mint_str}:holders"
        
        # Check cache first (5 min TTL)
        cached = cache.get("holder_distribution", cache_key)
        if cached is not None:
            return cached
        
        try:
            mint = Pubkey.from_string(mint_str)
            largest = await self.client.get_token_largest_accounts(mint)
            limiter.record_call(5)  # Record credit cost
            
            if not largest.value:
                cache.set("holder_distribution", cache_key, True)
                return True
            
            supply_resp = await self.client.get_token_supply(mint)
            limiter.record_call(1)
            
            if not supply_resp.value:
                cache.set("holder_distribution", cache_key, True)
                return True
            total_supply = int(supply_resp.value.amount)
            
            top_accounts = largest.value[:3]
            if not top_accounts:
                cache.set("holder_distribution", cache_key, True)
                return True
            
            # Batch fetch account info for top 3
            account_pubkeys = [acc.address for acc in top_accounts]
            account_infos = []
            try:
                resp = await self.client.get_multiple_accounts(account_pubkeys)
                account_infos = resp.value
                limiter.record_call(1)  # Batch counts as 1
            except Exception as e:
                print(f"[WARN] Batch holder fetch failed: {e}")
                cache.set("holder_distribution", cache_key, True)
                return True

            result = True
            for i, acc in enumerate(top_accounts):
                try:
                    pct = (int(acc.amount) / total_supply) * 100
                    addr = str(acc.address)
                    
                    acc_info = account_infos[i]
                    if acc_info:
                        owner = str(acc_info.owner)
                        if owner in [str(PUMP_PROGRAM), str(RAYDIUM_V4_PROGRAM), str(PUMP_AMM_PROGRAM), "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"]:
                            continue
                    
                    if pct > 30.0:
                        print(f"[RISK] {mint_str[:8]} Too Concentrated: Holder {addr[:8]} has {pct:.1f}%")
                        result = False
                        break
                except: pass
            
            cache.set("holder_distribution", cache_key, result)
            return result
        except Exception as e:
            print(f"Conc Check Err: {e}")
            return True

    async def is_fake_migration(self, mint_str: str) -> bool:
        try:
            mint = Pubkey.from_string(mint_str)
            curve_pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint)], PUMP_PROGRAM)
            resp = await self.client.get_account_info(curve_pda)
            # Check if curve exists but logic says "Fake Migration"?
            # Original: if data len > 40 and data[40] == 0 
            # offset 40 is token_total_supply? No. 
            # In get_bonding_curve_state layout: 
            # 32 (real sol) + 8 = 40 (token_total_supply)
            # 48 (complete)
            # data[40] check seems to check first byte of total supply?
            # Or is it checking "complete" flag at offset 40? 
            # In BC state function, I noted 48 for complete. 
            # Original code said: "checks if curve exists ... and resp.value.data[40] == 0"
            # If 48 is complete, then 40 is total supply MSB? 
            # Wait, `get_bonding_curve_state` comments: "Coin creator is at offset 48... Offset = 41 for complete flag"
            # Wait, 8+8+8+8+8 = 40. 
            # disc (0-8)
            # virt token (8-16)
            # virt sol (16-24)
            # real token (24-32)
            # real sol (32-40)
            # total supply (40-48)
            # complete (48-49) ?
            # Original code says "complete flag at 41"? That contradicts layout of 5 u64s + disc.
            # Let's trust "is_fake_migration" logic: `data[40] == 0`
            # If data[40] == 0, it returns True (Fake).
            # If curve exists, it should be migrating? 
            # This logic mimics original code strictly.
        
            if resp.value and len(resp.value.data) > 40 and resp.value.data[40] == 0:
                return True
                
            pool = await self.find_raydium_pool(mint_str)
            if pool:
                res = await self.get_raydium_reserves(pool)
                if res and res['sol_reserves'] / 1e9 < 0.1:
                    return True
        except Exception as e:
            print(f"[WARN] Fake migration check failed: {e}")
        return False

    async def get_token_age_hours(self, mint: str) -> float:
        """Get token age in hours from DexScreener (with Caching)."""
        if mint in self._age_cache:
            return self._age_cache[mint]
            
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
            async with self.session.get(url, timeout=3) as resp:
                data = await resp.json()
                if data.get("pairs"):
                    created_at = data["pairs"][0].get("pairCreatedAt", 0)
                    if created_at > 0:
                        age_ms = (time.time() * 1000) - created_at
                        age_hours = age_ms / (1000 * 3600)
                        if 0 <= age_hours <= 87600:
                            self._age_cache[mint] = age_hours
                            return age_hours
        except Exception as e:
            print(f"[WARN] Age fetch failed for {mint[:8]}: {e}")
        return 999.0

    async def pool_quality_filter(self, phase: str, mint_str: str) -> Dict[str, Any]:
        result = {"passed": False, "liquidity_sol": 0, "token_reserves": 0, "sol_reserves": 0, "progress": 0.0, "reason": "Unknown", "safety_score": 0}
        try:
            if phase == "BONDING_CURVE":
                data = await self.get_bonding_curve_state(mint_str)
                if data:
                    sol = data['sol_reserves'] / 1e9
                    result["liquidity_sol"] = sol
                    result["token_reserves"] = data['token_reserves']
                    result["sol_reserves"] = data['sol_reserves']
                    result["progress"] = data.get('progress', 0.0)
                    # 🔧 LOWERED from 0.3 to 0.1 SOL to catch EARLY like whales do
                    if sol >= 0.1:  
                        result["passed"] = True
                        result["reason"] = f"Bonding Curve: {sol:.2f} SOL"
                        result["safety_score"] = min(100, int(60 + (sol * 3)))  # Base 60 for BC
                    else:
                        result["reason"] = f"Low BC liquidity: {sol:.2f} SOL < 0.1 SOL min"
                        result["safety_score"] = 30
                else:
                    # No bonding curve data (token is graduated or not on pump.fun)
                    result["reason"] = "No bonding curve data (token graduated or not on pump.fun)"
                    result["safety_score"] = 0
            elif phase == "RAYDIUM":
                pool = await self.find_raydium_pool(mint_str)
                if pool:
                     res = await self.get_raydium_reserves(pool)
                     if res:
                         sol = res['sol_reserves'] / 1e9
                         result["liquidity_sol"] = sol
                         result["token_reserves"] = res['token_reserves']
                         result["sol_reserves"] = res['sol_reserves']
                         if sol >= 0.5:
                             result["passed"] = True
                             result["reason"] = f"Raydium pool: {sol:.2f} SOL"
                             result["safety_score"] = min(100, int(55 + (sol * 2.5)))  # Base 55
                         else:
                             result["passed"] = False
                             result["reason"] = f"Low Raydium liquidity: {sol:.2f} SOL < 0.5 SOL min"
                             result["safety_score"] = 25
                     else:
                         # Pool found but no reserves data - TRUST fallback
                         result["passed"] = True  # Let Jupiter try
                         result["reason"] = "Raydium pool found but reserves unavailable - trusting Jupiter quote"
                         result["safety_score"] = 35
                         logger.warning(f"⚠️ [{mint_str[:8]}] Raydium reserves unavailable, allowing via Jupiter fallback")
                else:
                    # No Raydium pool found - TRUST Jupiter quote (like JUPITER phase)
                    # Token might be on other DEXs that Jupiter can route through
                    result["passed"] = True
                    result["reason"] = "No Raydium pool found - trusting Jupiter quote"
                    result["safety_score"] = 35
                    result["min_required"] = 0.0
                    logger.warning(f"⚠️ [{mint_str[:8]}] No Raydium pool, allowing via Jupiter fallback")
            elif phase == "JUPITER":
                # For Jupiter/Generic phase, fetch liquidity from DexScreener
                ctx = await self.get_market_context(mint_str)
                liq_usd = ctx.get("liquidity", 0)
                
                # Convert to SOL (rough estimate: 1 SOL ≈ $200)
                liq_sol = liq_usd / 200.0 if liq_usd else 0.0
                result["liquidity_sol"] = liq_sol
                
                # Get token age from DexScreener pairCreatedAt
                age_hours = await self.get_token_age_hours(mint_str)
                result["age_hours"] = age_hours
                
                # 🔄 FALLBACK 1: Try Bonding Curve data (sometimes phase detection is wrong)
                if liq_sol == 0:
                    bc = await self.get_bonding_curve_state(mint_str)
                    if bc and bc.get('sol_reserves', 0) > 0:
                        sol = bc['sol_reserves'] / 1e9
                        result["liquidity_sol"] = sol
                        result["token_reserves"] = bc['token_reserves']
                        result["sol_reserves"] = bc['sol_reserves']
                        logger.info(f"💧 [{mint_str[:8]}] Fallback: Bonding Curve liquidity = {sol:.2f} SOL")
                        liq_sol = sol
                
                # 🔄 FALLBACK 2: Try Raydium pool lookup (in case it's actually on Raydium)
                if liq_sol == 0:
                    try:
                        pool = await self.find_raydium_pool(mint_str)
                        if pool:
                            res = await self.get_raydium_reserves(pool)
                            if res:
                                sol = res['sol_reserves'] / 1e9
                                result["liquidity_sol"] = sol
                                result["token_reserves"] = res['token_reserves']
                                result["sol_reserves"] = res['sol_reserves']
                                logger.info(f"💧 [{mint_str[:8]}] Fallback: Raydium pool = {sol:.2f} SOL")
                                liq_sol = sol
                    except Exception as e:
                        logger.debug(f"Raydium fallback failed for {mint_str[:8]}: {e}")
                
                # ✅ DECISION LOGIC: For Jupiter phase, trust that Jupiter can quote it
                # Even if we have 0 liquidity data, Jupiter aggregator will fail later if truly illiquid
                if liq_sol > 0:
                    # We have actual liquidity data - use normal threshold
                    if liq_sol >= 0.5:  # Min 0.5 SOL for safety
                        result["passed"] = True
                        result["reason"] = f"DexScreener/Fallback: ${liq_usd:.0f} ({liq_sol:.1f} SOL)"
                        result["safety_score"] = min(100, int(50 + (liq_sol * 2)))  # Base 50, +2 per SOL
                        logger.info(f"✅ [{mint_str[:8]}] Jupiter pool quality OK: {liq_sol:.1f} SOL")
                    else:
                        result["passed"] = False
                        result["reason"] = f"Low liquidity: {liq_sol:.1f} SOL < 0.5 SOL min"
                        result["safety_score"] = 20
                        logger.warning(f"❌ [{mint_str[:8]}] Low liquidity: {liq_sol:.1f} SOL")
                else:
                    # No liquidity data from any source - TRUST Jupiter quote check
                    # This is the FIX: don't reject just because DexScreener has no data
                    result["passed"] = True
                    result["reason"] = "DexScreener unavailable - trusting Jupiter quote"
                    result["safety_score"] = 40  # Lower score but still allow
                    result["min_required"] = 0.0
                    logger.warning(
                        f"⚠️ [{mint_str[:8]}] No liquidity data from DexScreener/BC/Raydium. "
                        f"Allowing token to proceed - Jupiter will validate during quote."
                    )
        except Exception as e:
            print(f"Filter err: {e}")
        return result
