from types import SimpleNamespace
from unittest.mock import AsyncMock


async def test_race_keyboard_opens_public_chat_leaderboard() -> None:
    from handlers.top import _race_keyboard

    bot = AsyncMock()
    bot.me.return_value = SimpleNamespace(username="noadick_bot")

    keyboard = await _race_keyboard(bot, -1007001)

    assert keyboard is not None
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "🏁 Гонка лидеров"
    assert button.url == "https://t.me/noadick_bot?start=stats_l_-1007001_0"


async def test_race_keyboard_requires_bot_username() -> None:
    from handlers.top import _race_keyboard

    bot = AsyncMock()
    bot.me.return_value = SimpleNamespace(username=None)

    assert await _race_keyboard(bot, -1007001) is None
