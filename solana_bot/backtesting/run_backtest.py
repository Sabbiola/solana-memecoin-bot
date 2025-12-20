"""
Backtest Runner

Script to run backtests on the scalping strategy.
Usage: python -m solana_bot.backtesting.run_backtest --token MINT --start 2025-01-01 --end 2025-01-15
"""

import asyncio
import argparse
import logging
from pathlib import Path
import aiohttp

from .backtest_engine import BacktestEngine, BacktestResult
from .historical_data_loader import HistoricalDataLoader
from ..core.scalping_strategy import ScalpingStrategy
from ..config import (
    SCALPING_RSI_OVERSOLD,
    SCALPING_RSI_OVERBOUGHT,
    SCALPING_VOLUME_MULTIPLIER,
    SCALPING_INITIAL_STOP_LOSS_PCT,
    SCALPING_TRAILING_STOP_PCT,
    SCALPING_BREAK_EVEN_BUFFER
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def run_backtest(
    token_mint: str,
    start_date: str,
    end_date: str,
    initial_balance: float = 10.0,
    trade_size_pct: float = 10.0
):
    """
    Run backtest for a token.
    
    Args:
        token_mint: Token mint address
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        initial_balance: Starting balance in SOL
        trade_size_pct: Trade size as % of balance
    """
    logger.info("=" * 80)
    logger.info(f"🔬 BACKTEST: {token_mint[:12]}...")
    logger.info(f"📅 Period: {start_date} to {end_date}")
    logger.info(f"💰 Initial Balance: {initial_balance} SOL")
    logger.info("=" * 80)
    
    async with aiohttp.ClientSession() as session:
        # Initialize components
        data_loader = HistoricalDataLoader(session)
        
        strategy = ScalpingStrategy(
            rsi_oversold=SCALPING_RSI_OVERSOLD,
            rsi_overbought=SCALPING_RSI_OVERBOUGHT,
            volume_multiplier=SCALPING_VOLUME_MULTIPLIER,
            initial_stop_loss_pct=SCALPING_INITIAL_STOP_LOSS_PCT,
            trailing_stop_pct=SCALPING_TRAILING_STOP_PCT,
            break_even_buffer_pct=SCALPING_BREAK_EVEN_BUFFER
        )
        
        engine = BacktestEngine(
            strategy=strategy,
            initial_balance=initial_balance,
            trade_size_pct=trade_size_pct,
            max_positions=1
        )
        
        # Fetch historical data
        logger.info("📥 Fetching historical data...")
        historical_data = await data_loader.fetch_historical_data(
            mint=token_mint,
            start_date=start_date,
            end_date=end_date,
            interval="1h"
        )
        
        if not historical_data:
            logger.error("❌ No historical data available")
            return None
        
        logger.info(f"✅ Loaded {len(historical_data)} candles")
        
        # Run backtest
        logger.info("🔬 Running backtest simulation...")
        result = await engine.run_backtest(
            historical_data=historical_data,
            token_mint=token_mint,
            start_date=start_date,
            end_date=end_date
        )
        
        # Generate report
        report = engine.generate_report(result)
        
        # Print to console
        print("\n" + report)
        
        # Save to file
        output_dir = Path("backtest_reports")
        output_dir.mkdir(exist_ok=True)
        
        filename = f"backtest_{token_mint[:8]}_{start_date}_{end_date}.md"
        output_path = output_dir / filename
        
        with open(output_path, "w") as f:
            f.write(report)
        
        logger.info(f"📄 Report saved to: {output_path}")
        
        return result


async def run_multi_token_backtest(
    start_date: str,
    end_date: str,
    num_tokens: int = 5
):
    """
    Run backtest on multiple tokens.
    
    Args:
        start_date: Start date
        end_date: End date
        num_tokens: Number of tokens to test
    """
    async with aiohttp.ClientSession() as session:
        data_loader = HistoricalDataLoader(session)
        
        # Get token list
        logger.info(f"🔍 Finding {num_tokens} tokens for backtest...")
        tokens = await data_loader.get_token_list_for_backtest(limit=num_tokens)
        
        if not tokens:
            logger.error("❌ No tokens found")
            return
        
        logger.info(f"✅ Testing {len(tokens)} tokens")
        
        # Run backtest for each
        results = []
        for token in tokens:
            try:
                result = await run_backtest(
                    token_mint=token,
                    start_date=start_date,
                    end_date=end_date
                )
                if result:
                    results.append(result)
                
                # Small delay between backtests
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error backtesting {token[:8]}: {e}")
        
        # Summary
        if results:
            avg_win_rate = sum(r.win_rate for r in results) / len(results)
            avg_pnl_pct = sum(r.total_pnl_pct for r in results) / len(results)
            
            logger.info("\n" + "=" * 80)
            logger.info("📊 MULTI-TOKEN BACKTEST SUMMARY")
            logger.info("=" * 80)
            logger.info(f"Tokens tested: {len(results)}")
            logger.info(f"Average Win Rate: {avg_win_rate:.1f}%")
            logger.info(f"Average P&L: {avg_pnl_pct:+.2f}%")
            logger.info("=" * 80)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run backtest on scalping strategy")
    parser.add_argument("--token", help="Token mint address")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--balance", type=float, default=10.0, help="Initial balance (SOL)")
    parser.add_argument("--size", type=float, default=10.0, help="Trade size (%% of balance)")
    parser.add_argument("--multi", action="store_true", help="Test multiple tokens")
    parser.add_argument("--num-tokens", type=int, default=5, help="Number of tokens for multi-test")
    
    args = parser.parse_args()
    
    if args.multi:
        # Multi-token backtest
        asyncio.run(run_multi_token_backtest(
            start_date=args.start,
            end_date=args.end,
            num_tokens=args.num_tokens
        ))
    elif args.token:
        # Single token backtest
        asyncio.run(run_backtest(
            token_mint=args.token,
            start_date=args.start,
            end_date=args.end,
            initial_balance=args.balance,
            trade_size_pct=args.size
        ))
    else:
        parser.error("Either --token or --multi must be specified")


if __name__ == "__main__":
    main()
