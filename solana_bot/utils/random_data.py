from __future__ import annotations

import random
import string


SOURCES = ["pumpfun", "dexscreener", "helius", "raydium", "jupiter"]


def random_mint(rng: random.Random) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(rng.choice(alphabet) for _ in range(32))


def random_symbol(rng: random.Random) -> str:
    return "".join(rng.choice(string.ascii_uppercase) for _ in range(rng.randint(3, 5)))


def random_token_data(rng: random.Random) -> dict:
    age_sec = rng.randint(10, 24 * 60 * 60)
    liquidity = rng.uniform(50, 20000)
    volume = rng.uniform(100, 50000)
    price = rng.uniform(0.000001, 0.5)
    return {
        "mint": random_mint(rng),
        "symbol": random_symbol(rng),
        "age_sec": age_sec,
        "liquidity_usd": liquidity,
        "volume_usd": volume,
        "price": price,
        "source": rng.choice(SOURCES),
        "metadata": {
            "dev_holding": rng.uniform(0.05, 0.6),
            "top10_holding": rng.uniform(0.2, 0.9),
            "mint_authority_active": rng.random() < 0.3,
            "freeze_authority_active": rng.random() < 0.2,
            "unique_wallets_buying": rng.randint(1, 12),
            "single_buy_sol": rng.uniform(0.0, 1.2),
            "volatility": rng.uniform(0.05, 0.25),
        },
    }


def random_price_move(price: float, volatility: float, rng: random.Random) -> float:
    if price <= 0:
        price = 0.0000001
    change = rng.uniform(-volatility, volatility)
    return max(0.0000001, price * (1 + change))
