"""Shared renderer for the per-chat settings panel.

The same screen backs two entry points: the global admin panel (scope
``"global"``, reached from a chat detail screen, Back returns there) and the
local ``/settings`` command run by a chat admin (scope ``"local"``, which shows
a Close button instead). The renderer is permission-agnostic — callers gate
access; ``scope`` only changes the bottom navigation row.

Callback stems are ``st:*`` so they can live in ``handlers/settings.py`` (no
router-level filter) and be handled for both global admins (in DM) and local
admins (in their group).
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import texts
from services import settings


def _nav_row(chat_id: int, scope: str) -> list[InlineKeyboardButton]:
    if scope == "global":
        return [
            InlineKeyboardButton(
                text=texts.BTN_BACK_CHAT, callback_data=f"adm:chat:{chat_id}"
            )
        ]
    return [InlineKeyboardButton(text=texts.BTN_CLOSE, callback_data="st:close")]


def settings_kb(chat_id: int, eff, *, scope: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=texts.settings_btn_diseases(eff.diseases_enabled),
                callback_data=f"st:tgl:dis:{chat_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text=texts.settings_label_stake(eff.duel_stake_default),
                callback_data="st:noop",
            )
        ],
        [
            InlineKeyboardButton(text="−5", callback_data=f"st:adj:stake:{chat_id}:-5"),
            InlineKeyboardButton(text="−1", callback_data=f"st:adj:stake:{chat_id}:-1"),
            InlineKeyboardButton(text="+1", callback_data=f"st:adj:stake:{chat_id}:1"),
            InlineKeyboardButton(text="+5", callback_data=f"st:adj:stake:{chat_id}:5"),
        ],
        [
            InlineKeyboardButton(
                text=texts.settings_label_timeout(eff.duel_timeout),
                callback_data="st:noop",
            )
        ],
        [
            InlineKeyboardButton(text="−30", callback_data=f"st:adj:to:{chat_id}:-30"),
            InlineKeyboardButton(text="−10", callback_data=f"st:adj:to:{chat_id}:-10"),
            InlineKeyboardButton(text="+10", callback_data=f"st:adj:to:{chat_id}:10"),
            InlineKeyboardButton(text="+30", callback_data=f"st:adj:to:{chat_id}:30"),
        ],
        [
            InlineKeyboardButton(
                text=texts.settings_btn_tz(eff.tz), callback_data=f"st:tz:{chat_id}"
            )
        ],
        _nav_row(chat_id, scope),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_settings(
    chat_id: int, *, scope: str
) -> tuple[str, InlineKeyboardMarkup]:
    eff = await settings.get_effective(chat_id)
    text = texts.settings_screen(
        eff.tz, eff.diseases_enabled, eff.duel_stake_default, eff.duel_timeout
    )
    return text, settings_kb(chat_id, eff, scope=scope)
