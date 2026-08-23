import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, ErrorEvent

from config import get_settings
from db.engine import dispose_engine, init_db
from handlers import (
    admin,
    bank,
    casino,
    dick,
    duel,
    help,
    modtools,
    ping,
    poker,
    profile,
    season,
    settings,
    stats,
    top,
)
from middlewares.registry import RegistryMiddleware
from observability import LoggingContextMiddleware, configure_logging
from services import backups, global_settings, stats_digest
from services import bank as bank_service

logger = logging.getLogger(__name__)


async def main() -> None:
    settings_config = get_settings()
    configure_logging(settings_config.log_format)

    if settings_config.db_path.exists() and settings_config.db_path.stat().st_size > 0:
        migration_backup = await asyncio.to_thread(
            backups.create_backup,
            settings_config.db_path,
            settings_config.backup_dir,
            settings_config.backup_retention,
        )
        logger.info("Pre-migration database backup completed: %s", migration_backup.name)
    await init_db()
    await global_settings.refresh()

    token = settings_config.bot_token.get_secret_value()
    proxy = settings_config.proxy
    if proxy:
        # Log only the host part (after '@'); never the user:pass credentials.
        safe_proxy = proxy.rsplit("@", 1)[-1] if "@" in proxy else proxy
        logger.info("Proxy enabled: %s", safe_proxy)
        session = AiohttpSession(proxy=proxy)
    else:
        session = None

    bot = Bot(
        token=token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    dp.message.outer_middleware(LoggingContextMiddleware())
    dp.callback_query.outer_middleware(LoggingContextMiddleware())
    dp.inline_query.outer_middleware(LoggingContextMiddleware())
    dp.message.outer_middleware(RegistryMiddleware())
    dp.callback_query.outer_middleware(RegistryMiddleware())
    dp.inline_query.outer_middleware(RegistryMiddleware())

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        logger.error("Update handling failed", exc_info=event.exception)
        return True

    dp.include_routers(
        admin.router,
        settings.router,
        modtools.router,
        dick.router,
        casino.router,
        duel.router,
        poker.router,
        bank.router,
        profile.router,
        stats.router,
        top.router,
        season.router,
        help.router,
        ping.router,
    )

    await bot.set_my_commands(
        [
            BotCommand(command="dick", description="Испытать удачу"),
            BotCommand(command="casino", description="Крутить Telegram-слот"),
            BotCommand(command="duel", description="Вызвать на дуэль (ответом)"),
            BotCommand(command="poker", description="Создать или открыть покерный стол"),
            BotCommand(command="me", description="Твой профиль и статистика"),
            BotCommand(command="stats", description="Подробная статистика и графики"),
            BotCommand(command="top", description="Топ-10 по размеру"),
            BotCommand(command="season", description="Итоги недельного сезона"),
            BotCommand(command="bank", description="Банк: вклады и кредиты"),
            BotCommand(command="corp", description="Счёт Корпорации"),
            BotCommand(command="settings", description="Настройки чата (админам)"),
            BotCommand(command="help", description="Список команд"),
            BotCommand(command="ping", description="ping-pong"),
        ]
    )

    background_tasks = [
        asyncio.create_task(_collector_loop(bot), name="bank-collector"),
        asyncio.create_task(poker.watchdog_loop(bot), name="poker-watchdog"),
        asyncio.create_task(stats_digest.loop(bot), name="stats-digest"),
        asyncio.create_task(
            backups.backup_loop(
                settings_config.db_path,
                settings_config.backup_dir,
                settings_config.backup_interval_hours,
                settings_config.backup_retention,
            ),
            name="database-backup",
        ),
        asyncio.create_task(
            backups.heartbeat_loop(settings_config.heartbeat_path), name="heartbeat"
        ),
    ]

    try:
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await bot.session.close()
        await dispose_engine()


async def _collector_loop(bot: Bot) -> None:
    """Periodically accrue loan interest, default the overdue, nag debtors and
    roll deposit confiscations. The interval is an admin-tunable global setting."""
    while True:
        interval = max(60, global_settings.get_config_sync().collector_interval_sec)
        await asyncio.sleep(interval)
        try:
            await bank_service.run_collector_pass(bot)
        except Exception:
            logger.exception("Bank collector pass failed")


if __name__ == "__main__":
    asyncio.run(main())
