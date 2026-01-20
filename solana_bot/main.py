import asyncio

from solana_bot.config import get_settings
from solana_bot.core.bot import Bot
from solana_bot.utils.logging import setup_logging


async def async_main() -> None:
    settings = get_settings()
    setup_logging(settings)
    bot = Bot(settings)
    await bot.run()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
