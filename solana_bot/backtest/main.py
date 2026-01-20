import argparse
import asyncio
from pathlib import Path

from solana_bot.backtest.harness import BacktestPriceFeed, BacktestRunner, BacktestScanner, load_ticks
from solana_bot.config import get_settings
from solana_bot.core.bot import Bot
from solana_bot.utils.logging import setup_logging


async def async_main(data_path: Path) -> None:
    settings = get_settings()
    setup_logging(settings)
    ticks = load_ticks(data_path)
    scanner = BacktestScanner()
    price_feed = BacktestPriceFeed()
    bot = Bot(settings, scanner=scanner, price_feed=price_feed)
    runner = BacktestRunner(bot, scanner, price_feed, ticks)
    await runner.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay historical ticks into the bot.")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path(get_settings().BACKTEST_DATA_PATH),
        help="Path to JSONL backtest ticks.",
    )
    args = parser.parse_args()
    asyncio.run(async_main(args.data))


if __name__ == "__main__":
    main()
