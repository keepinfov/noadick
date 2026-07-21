from __future__ import annotations

from unittest.mock import AsyncMock

from handlers import admin


async def test_resolved_send_uses_selected_topic(monkeypatch):
    bot = AsyncMock()
    monkeypatch.setattr(
        admin.threads_repo,
        "resolve_thread",
        AsyncMock(return_value=(321, "explicit")),
    )

    ok, reason, thread_id = await admin._send_to_resolved_thread(
        bot, -1001, "акт"
    )

    assert (ok, reason, thread_id) == (True, "explicit", 321)
    bot.send_message.assert_awaited_once_with(
        -1001,
        "акт",
        parse_mode="HTML",
        message_thread_id=321,
    )


async def test_resolved_send_falls_back_to_general(monkeypatch):
    bot = AsyncMock()
    bot.send_message.side_effect = [RuntimeError("topic deleted"), None]
    monkeypatch.setattr(
        admin.threads_repo,
        "resolve_thread",
        AsyncMock(return_value=(321, "explicit")),
    )

    ok, reason, thread_id = await admin._send_to_resolved_thread(
        bot, -1001, "акт"
    )

    assert (ok, reason, thread_id) == (True, "explicit", 321)
    assert bot.send_message.await_args_list[0].kwargs["message_thread_id"] == 321
    assert bot.send_message.await_args_list[1].kwargs["message_thread_id"] is None
