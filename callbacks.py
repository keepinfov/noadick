from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class BankCallback(CallbackData, prefix="bank"):
    action: str
    user_id: int
    value: str = "_"


class SettingsCallback(CallbackData, prefix="settings"):
    action: str
    chat_id: int
    value: str = "_"


class DuelCallback(CallbackData, prefix="duel"):
    token: str
