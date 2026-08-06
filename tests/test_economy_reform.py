from __future__ import annotations

import os
import tempfile

import pytest

from services.economy_reform import progressive_cut


@pytest.fixture
async def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DB_PATH"] = path

    from db import engine as engine_mod

    await engine_mod.dispose_engine()
    await engine_mod.init_db()
    try:
        yield
    finally:
        await engine_mod.dispose_engine()
        os.unlink(path)


@pytest.mark.parametrize(
    ("total", "cut"),
    [(0, 0), (50, 0), (51, 1), (100, 8), (101, 8), (200, 33), (201, 33), (500, 138)],
)
def test_progressive_cut_boundaries(total, cut):
    assert progressive_cut(total) == cut


async def test_reform_cuts_all_assets_once_and_keeps_corp_unchanged(db):
    from repositories import bank as bank_repo
    from repositories import events as events_repo
    from repositories import players as players_repo
    from services import economy_reform

    chat_id = -2001
    user_id = 42
    await players_repo.set_player_fields(chat_id, user_id, name="Жиробас", size=100)
    await players_repo.set_player_fields(chat_id, 43, name="Новичок", size=50)
    await bank_repo.upsert_deposit(
        chat_id,
        user_id,
        principal=100,
        accrued=20,
        interest_remainder_ppm=500_000,
    )
    await bank_repo.corp_apply(delta=123)

    preview = await economy_reform.preview(chat_id)
    assert preview.already_applied is False
    assert preview.total_before == 270
    assert preview.total_cut == 40
    assert preview.affected_players == 1

    result = await economy_reform.apply(chat_id, actor_id=777)
    assert result.already_applied is False
    assert result.total_cut == 40
    assert result.entries[0].liquid_cut == 18
    assert result.entries[0].principal_cut == 18
    assert result.entries[0].accrued_cut == 4

    player = await players_repo.get_player(chat_id, user_id)
    dep = await bank_repo.get_deposit(chat_id, user_id)
    assert player.size == 82
    assert (dep.principal, dep.accrued) == (82, 16)
    assert dep.interest_remainder_ppm == 410_000
    assert (await bank_repo.get_corp()).balance == 123

    events = await events_repo.get_events(chat_id, user_id, types=[events_repo.HEALTH_REFORM])
    assert len(events) == 1
    assert events[0].delta == -18
    assert events[0].size_after == 82
    assert events[0].meta["total_cut"] == 40

    # Bookkeeping events with their legacy size_after=0 must not corrupt the
    # liquid-size chart after the reform.
    from services import stats

    await events_repo.log_event(
        chat_id, user_id, events_repo.DEPOSIT_INTEREST, meta={"interest": 1}
    )
    assert await stats.size_timeline(chat_id, user_id) == [(events[0].created_at, 82)]

    again = await economy_reform.apply(chat_id, actor_id=888)
    assert again.already_applied is True
    assert (await players_repo.get_player(chat_id, user_id)).size == 82
    assert (
        len(await events_repo.get_events(chat_id, user_id, types=[events_repo.HEALTH_REFORM])) == 1
    )
