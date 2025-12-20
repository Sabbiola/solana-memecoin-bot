import hashlib
from solders.pubkey import Pubkey

# ============================================
# PROGRAM IDS
# ============================================
PUMP_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMP_FEE = Pubkey.from_string("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM")
# Note: PUMP_GLOBAL and EVENT_AUTH usually require computing program address, 
# but for constants we can compute them on import or lazily.
# To match original strictly, we compute them here.
PUMP_GLOBAL, _ = Pubkey.find_program_address([b"global"], PUMP_PROGRAM)
EVENT_AUTH, _ = Pubkey.find_program_address([b"__event_authority"], PUMP_PROGRAM)

PUMP_AMM_PROGRAM = Pubkey.from_string("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
PUMP_AMM_FEE = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
RAYDIUM_V4_PROGRAM = Pubkey.from_string("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")
OPENBOOK_PROGRAM = Pubkey.from_string("srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX")

# Aliases for parser
PUMPSWAP_PROGRAM = PUMP_AMM_PROGRAM
RAYDIUM_AMM_PROGRAM = RAYDIUM_V4_PROGRAM
JUPITER_PROGRAM = Pubkey.from_string("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4")

SYSTEM_PROGRAM = Pubkey.from_string("11111111111111111111111111111111")
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
TOKEN_2022_PROGRAM = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
RENT_PROGRAM = Pubkey.from_string("SysvarRent111111111111111111111111111111111")
ASSOC_TOKEN_ACC_PROG = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
WSOL_MINT = Pubkey.from_string("So11111111111111111111111111111111111111112")

# METEORA DEX PROGRAM IDs
METEORA_DAMM_V2 = "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"
METEORA_DLMM = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
METEORA_DAMM_V1 = "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8n5EQVn5UaB"

# Other DEXs for validation
SUPPORTED_DEX_PROGRAMS = {
    str(PUMP_PROGRAM), 
    str(PUMP_AMM_PROGRAM), 
    str(RAYDIUM_V4_PROGRAM),
    METEORA_DAMM_V2,
    METEORA_DLMM,
    METEORA_DAMM_V1,
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Orca CAMM
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Whirlpool
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"   # Jupiter V6
}

# ============================================
# DISCRIMINATORS
# ============================================
PUMP_BUY_DISC = bytes([0x66, 0x06, 0x3d, 0x12, 0x01, 0xda, 0xeb, 0xea])
PUMP_SELL_DISC = bytes([0x33, 0xe6, 0x85, 0xa4, 0x01, 0x7f, 0x83, 0xad])

def anchor_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]

PUMPSWAP_BUY_DISC = anchor_discriminator("buy")
PUMPSWAP_SELL_DISC = anchor_discriminator("sell")

RAYDIUM_SWAP_BASE_IN = bytes([9])
RAYDIUM_SWAP_BASE_OUT = bytes([10])

# ============================================
# SLIPPAGE SETTINGS
# ============================================
BASE_SLIPPAGE_BPS = 500  # 5%
MAX_SLIPPAGE_BPS = 2500  # 25%

# ============================================
# API ENDPOINTS
# ============================================
JUPITER_QUOTE_API = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_API = "https://quote-api.jup.ag/v6/swap"
JITO_URL = "https://mainnet.block-engine.jito.wtf/api/v1/bundles"

JITO_TIPS = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvuiVjRokw87Hz",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49"
]

# ============================================
# MONITORING CONSTANTS (P1 FIX: Extract magic numbers)
# ============================================
MONITOR_INTERVAL_SECONDS = 10  # Position monitoring interval
TX_CONFIRMATION_TIMEOUT = 30  # Seconds to wait for TX confirmation
SELL_RETRY_WAIT_SECONDS = 30  # Wait before retrying failed sell

# P&L Notification thresholds (percentages)
NOTIFICATION_THRESHOLDS = [-10, -5, 0, 5, 10, 20, 30, 50, 100, 200]

# Cache sizes for validator
CACHE_RAYDIUM_POOL_SIZE = 500
CACHE_RAYDIUM_POOL_TTL = 1800  # 30 minutes
CACHE_DECIMALS_SIZE = 1000
CACHE_DECIMALS_TTL = 86400  # 24 hours
CACHE_METADATA_SIZE = 500
CACHE_METADATA_TTL = 3600  # 1 hour
CACHE_AGE_SIZE = 500
CACHE_AGE_TTL = 300  # 5 minutes

# Struct offsets for on-chain data parsing
BONDING_CURVE_VIRT_TOKEN_OFFSET = 8
BONDING_CURVE_VIRT_SOL_OFFSET = 16
BONDING_CURVE_REAL_TOKEN_OFFSET = 24
BONDING_CURVE_REAL_SOL_OFFSET = 32
BONDING_CURVE_CREATOR_OFFSET = 41
BONDING_CURVE_CREATOR_END = 73

PUMPSWAP_POOL_TOKEN_OFFSET = 72
PUMPSWAP_POOL_SOL_OFFSET = 80

RAYDIUM_POOL_NONCE_OFFSET = 0
RAYDIUM_POOL_BASE_VAULT_OFFSET = 208
RAYDIUM_POOL_QUOTE_VAULT_OFFSET = 240
RAYDIUM_POOL_BASE_MINT_OFFSET = 400
RAYDIUM_POOL_QUOTE_MINT_OFFSET = 432

