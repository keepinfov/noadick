"""Tests for the per-chat Corporation panel and the recovery threshold rule."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace

import pytest

CHAT, USER = -1001, 42


@pytest.fixture
async def db():
    """Fresh SQLite file per test, with the global-settings cache reset.

    ``services.global_settings`` keeps a process-wide snapshot, so it must be
    invalidated around every test that touches it.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path

    from db import engine as engine_mod
    from services import global_settings

    await engine_mod.dispose_engine()
    await engine_mod.init_db()
    global_settings.invalidate()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        global_settings.invalidate()
        os.unlink(path)


async def _seed_player(size: int, **fields):
    from repositories import players as players_repo

    await players_repo.set_player_fields(CHAT, USER, size=size, **fields)


# --- pure rule ---------------------------------------------------------------


def test_recovery_deficit_scales_liability_by_configured_share():
    from services import bank
    from services.global_settings import _defaults

    strict = _defaults()
    relaxed = replace(strict, corp_recovery_liability_pct=25)

    assert strict.corp_recovery_liability_pct == 100
    assert bank.recovery_deficit(0, 0, 100, strict) == 100
    assert bank.recovery_deficit(0, 0, 100, relaxed) == 25
    # The reserve counts towards the requirement.
    assert bank.recovery_deficit(20, 30, 100, relaxed) == 0


def test_recovery_deficit_always_covers_a_negative_balance():
    from services import bank
    from services.global_settings import _defaults

    relaxed = replace(_defaults(), corp_recovery_liability_pct=0)

    assert bank.recovery_deficit(-7, 0, 0, relaxed) == 7
    assert bank.recovery_deficit(5, 0, 0, relaxed) == 0


# --- DB-backed admin actions -------------------------------------------------


async def test_adjust_corp_balance_is_ledgered_audited_and_lifts_recovery(db) -> None:
    from sqlalchemy import select

    from db.engine import get_session_factory
    from db.models import AuditLog, CorporationLedger
    from repositories import bank as repo
    from services import admin_actions

    await _seed_player(10)
    await repo.upsert_deposit(CHAT, USER, principal=30, accrued=0)
    await repo.corp_apply(CHAT, delta=-30)
    await repo.set_corp_fields(CHAT, status="recovery")

    before = await admin_actions.corp_snapshot(CHAT)
    assert before is not None
    assert (before.status, before.deficit, before.liability) == ("recovery", 60, 30)

    result = await admin_actions.adjust_corp_balance(7, CHAT, 60)
    assert result.ok

    after = await admin_actions.corp_snapshot(CHAT)
    assert after is not None
    assert (after.balance, after.status, after.deficit) == (30, "healthy", 0)

    factory = get_session_factory()
    async with factory() as session:
        ledger = list(
            (
                await session.execute(
                    select(CorporationLedger).where(
                        CorporationLedger.chat_id == CHAT,
                        CorporationLedger.reason == "admin_adjust",
                    )
                )
            ).scalars()
        )
        audits = list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "corp_adjust", AuditLog.target_chat == CHAT
                    )
                )
            ).scalars()
        )
    assert len(ledger) == 1
    assert (ledger[0].cash_delta, ledger[0].balance_after, ledger[0].user_id) == (60, 30, 7)
    assert len(audits) == 1
    assert audits[0].actor_id == 7


async def test_adjust_corp_balance_refuses_a_no_op(db) -> None:
    from repositories import bank as repo
    from services import admin_actions

    await _seed_player(10)
    await repo.corp_apply(CHAT, delta=admin_actions.CORP_BALANCE_LIMIT)

    result = await admin_actions.adjust_corp_balance(7, CHAT, 50)
    assert result.ok is False
    assert (await admin_actions.corp_snapshot(CHAT)).balance == admin_actions.CORP_BALANCE_LIMIT


async def test_recompute_status_follows_the_configurable_threshold(db) -> None:
    from repositories import bank as repo
    from services import admin_actions, global_settings

    await _seed_player(10)
    await repo.upsert_deposit(CHAT, USER, principal=30, accrued=0)
    await repo.set_corp_fields(CHAT, status="recovery")

    strict = await admin_actions.recompute_corp_status(7, CHAT)
    assert strict is not None
    assert (await repo.get_corp(CHAT)).status == "recovery"

    await global_settings.adjust("corp_recovery_liability_pct", -100)

    relaxed = await admin_actions.recompute_corp_status(7, CHAT)
    assert relaxed is not None
    assert (await repo.get_corp(CHAT)).status == "healthy"


async def test_recompute_status_returns_none_for_unknown_chat(db) -> None:
    from services import admin_actions

    await _seed_player(10)

    assert await admin_actions.recompute_corp_status(7, -999) is None


# --- panel rendering ---------------------------------------------------------


async def test_chat_screen_links_to_the_corporation_panel(db) -> None:
    from handlers import admin

    await _seed_player(10)

    _, keyboard = await admin.render_chat(CHAT)
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"adm:corp:{CHAT}" in data


async def test_corp_panel_shows_status_balances_and_rule(db) -> None:
    import texts
    from handlers import admin
    from repositories import bank as repo

    await _seed_player(10)
    await repo.upsert_deposit(CHAT, USER, principal=30, accrued=0)
    await repo.corp_apply(CHAT, delta=-30)
    await repo.set_corp_fields(CHAT, status="recovery")

    text, keyboard = await admin.render_corp(CHAT)

    assert texts.ADMIN_CORP_TITLE in text
    assert "Обязательства по вкладам: 30 см" in text
    assert "Дефицит: 60 см" in text
    assert "100%" in text
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"adm:corpadj:{CHAT}:50" in data
    assert f"adm:corpadj:{CHAT}:-50" in data
    assert f"adm:corpsync:{CHAT}" in data
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )


async def test_corp_panel_reports_an_unknown_chat(db) -> None:
    import texts
    from handlers import admin

    await _seed_player(10)

    text, _ = await admin.render_corp(-999)
    assert text == texts.RES_CORP_NOT_FOUND
