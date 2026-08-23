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


class CasinoCallback(CallbackData, prefix="cas"):
    """Public casino spin: repeat a fixed stake or use the clicker's default."""

    action: str
    stake: int = 0


class StatsCallback(CallbackData, prefix="stx"):
    action: str
    scope: str
    section: str
    period: str
    chat_id: int = 0
    user_id: int = 0


class SeasonCallback(CallbackData, prefix="sea"):
    """Compact public season-panel action.

    Actions: s=summary, f=full table, p/n=archive navigation,
    r=refresh the current live season.
    """

    action: str
    chat_id: int
    season_no: int
    owner_id: int
