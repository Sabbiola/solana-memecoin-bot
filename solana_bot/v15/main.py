import asyncio

from solana_bot.config import get_settings
from solana_bot.core.bot import Bot
from solana_bot.core.runtime_supervisor import RuntimeSupervisor
from solana_bot.utils.logging import setup_logging


async def async_main() -> None:
    settings = get_settings()
    setup_logging(settings)
    supervisor = RuntimeSupervisor(settings)
    bot = Bot(settings, supervisor=supervisor)
    await bot.run()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
