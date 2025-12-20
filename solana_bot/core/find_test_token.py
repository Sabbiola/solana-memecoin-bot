
import asyncio
import aiohttp
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.pumpfun_scanner import PumpFunScanner

async def main():
    session = aiohttp.ClientSession()
    try:
        # Initialize scanner with very permissive filters to find ANY new token
        scanner = PumpFunScanner(
            session=session,
            min_age_minutes=0,
            max_age_minutes=1000,
            min_market_cap=0,
            max_market_cap=10000000
        )
        
        print("Fetching fresh tokens...")
        tokens = await scanner.fetch_new_tokens(limit=10)
        
        if tokens:
            print(f"Found {len(tokens)} tokens.")
            # Print the first one's mint
            print(f"MINT={tokens[0].mint}")
            print(f"SYMBOL={tokens[0].symbol}")
            print(f"AGE={tokens[0].age_minutes:.2f}m")
        else:
            print("No tokens found.")
            
    finally:
        await session.close()

if __name__ == "__main__":
    asyncio.run(main())
