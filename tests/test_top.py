from types import SimpleNamespace
from unittest.mock import AsyncMock


async def test_top_sends_public_race_image(monkeypatch) -> None:
    from handlers import top
    from services.analytics import Dashboard

    message = SimpleNamespace(
        chat=SimpleNamespace(id=-1007001, type="supergroup"),
        from_user=SimpleNamespace(id=71),
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
    )
    bot = AsyncMock()
    bot.me.return_value = SimpleNamespace(username="noadick_bot")
    data = Dashboard(
        title="Чат",
        section="leaders",
        period="30",
        metrics=[("#1", "Игрок: 100 см")],
        labels=["01.08", "02.08"],
        values=[90.0, 100.0],
        chart_title="Гонка текущих лидеров",
        chart_kind="line",
        series=[("Игрок", [90.0, 100.0])],
    )
    monkeypatch.setattr(top.cooldowns, "passes", AsyncMock(return_value=True))
    monkeypatch.setattr(top, "get_storage", AsyncMock(return_value={"71": {"size": 100}}))
    monkeypatch.setattr(top, "check_expire", lambda _player: False)
    dashboard = AsyncMock(return_value=data)
    monkeypatch.setattr(top.analytics, "dashboard", dashboard)
    monkeypatch.setattr(top.analytics, "render_png", AsyncMock(return_value=b"png"))

    await top.cmd_top(message, bot)

    scope = dashboard.await_args.args[0]
    assert (scope.kind, scope.chat_id) == ("leaderboard", -1007001)
    message.answer.assert_not_awaited()
    message.answer_photo.assert_awaited_once()
    photo = message.answer_photo.await_args.args[0]
    assert photo.filename == "leader-race.png"
    keyboard = message.answer_photo.await_args.kwargs["reply_markup"]
    button = keyboard.inline_keyboard[0][0]
    assert button.text == "📊 Подробнее в ЛС"
    assert button.url == "https://t.me/noadick_bot?start=stats_l_-1007001_0"
