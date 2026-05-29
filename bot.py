import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from dotenv import load_dotenv

from db.engine import init_db
from handlers import admin, dick, duel, help, ping, profile, settings, top
from middlewares.registry import RegistryMiddleware


async def main() -> None:
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    await init_db()

    token = os.environ["BOT_TOKEN"]

    proxy = os.environ.get("PROXY", "").strip()
    if proxy:
        logging.info("Proxy enabled: %s", proxy.split("@")[0] if "@" in proxy else proxy)
        session = AiohttpSession(proxy=proxy)
    else:
        session = None

    bot = Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.message.outer_middleware(RegistryMiddleware())
    dp.callback_query.outer_middleware(RegistryMiddleware())

    dp.include_routers(
        admin.router, settings.router, dick.router, duel.router, profile.router,
        top.router, help.router, ping.router,
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
