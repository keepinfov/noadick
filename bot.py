import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent
from dotenv import load_dotenv

from db.engine import dispose_engine, init_db
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
        # Log only the host part (after '@'); never the user:pass credentials.
        safe_proxy = proxy.rsplit("@", 1)[-1] if "@" in proxy else proxy
        logging.info("Proxy enabled: %s", safe_proxy)
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

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logging.exception("Update handling failed: %s", event.exception, exc_info=event.exception)
        return True

    dp.include_routers(
        admin.router, settings.router, dick.router, duel.router, profile.router,
        top.router, help.router, ping.router,
    )

    await bot.set_my_commands([
        BotCommand(command="dick", description="Испытать удачу"),
        BotCommand(command="duel", description="Вызвать на дуэль (ответом)"),
        BotCommand(command="me", description="Твой профиль и статистика"),
        BotCommand(command="top", description="Топ-10 по размеру"),
        BotCommand(command="help", description="Список команд"),
        BotCommand(command="ping", description="ping-pong"),
    ])

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
