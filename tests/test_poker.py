from __future__ import annotations

import os
import tempfile

import pytest

from models import poker as engine


def test_hand_evaluator_covers_categories_and_wheel() -> None:
    assert engine.hand_name(engine.evaluate(["As", "Ks", "Qs", "Js", "Ts"])) == "стрит-флеш"
    assert engine.hand_name(engine.evaluate(["As", "Ad", "Ah", "Ac", "2s"])) == "каре"
    assert engine.hand_name(engine.evaluate(["As", "2d", "3h", "4c", "5s"])) == "стрит"
    assert engine.evaluate(["As", "2d", "3h", "4c", "5s"]) < engine.evaluate(
        ["2s", "3d", "4h", "5c", "6s"]
    )


def test_heads_up_button_posts_small_blind_and_acts_first_preflop() -> None:
    state = engine.start_hand(
        [
            {"user_id": 1, "seat": 1, "name": "A", "stack": 40},
            {"user_id": 2, "seat": 2, "name": "B", "stack": 40},
        ],
        small_blind=1,
        big_blind=2,
    )
    assert state["dealer_seat"] == 1
    assert state["small_uid"] == "1"
    assert state["big_uid"] == "2"
    assert state["current_uid"] == "1"


def test_side_pots_take_five_percent_rake_after_flop() -> None:
    state = {
        "street": "river",
        "board": ["2c", "3d", "7h", "8s", "9c"],
        "dealer_seat": 3,
        "finished": False,
        "players": {
            "1": {
                "seat": 1,
                "name": "A",
                "stack": 0,
                "cards": ["As", "Ad"],
                "folded": False,
                "all_in": True,
                "street_bet": 0,
                "total_bet": 100,
            },
            "2": {
                "seat": 2,
                "name": "B",
                "stack": 0,
                "cards": ["Ks", "Kd"],
                "folded": False,
                "all_in": True,
                "street_bet": 0,
                "total_bet": 50,
            },
            "3": {
                "seat": 3,
                "name": "C",
                "stack": 0,
                "cards": ["Qs", "Qd"],
                "folded": False,
                "all_in": True,
                "street_bet": 0,
                "total_bet": 100,
            },
        },
    }
    engine._settle(state, showdown=True)
    assert state["result"]["rake"] == 12  # floor(150*5%) + floor(100*5%)
    assert state["players"]["1"]["stack"] == 238
    assert sum(player["stack"] for player in state["players"].values()) == 250 - 12


def test_uncalled_excess_is_returned_without_rake() -> None:
    state = {
        "street": "flop",
        "board": ["2c", "3d", "7h"],
        "dealer_seat": 2,
        "finished": False,
        "players": {
            "1": {
                "seat": 1,
                "name": "A",
                "stack": 0,
                "cards": ["As", "Ad"],
                "folded": False,
                "all_in": False,
                "street_bet": 0,
                "total_bet": 100,
            },
            "2": {
                "seat": 2,
                "name": "B",
                "stack": 0,
                "cards": ["Ks", "Kd"],
                "folded": True,
                "all_in": False,
                "street_bet": 0,
                "total_bet": 50,
            },
        },
    }
    engine._settle(state, showdown=False)
    assert state["result"]["rake"] == 5  # only the contested 100-sm layer
    assert state["players"]["1"]["stack"] == 145


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


async def test_money_room_escrows_and_returns_stacks(db) -> None:
    from repositories import players
    from services import poker

    chat = -9001
    await players.set_player_fields(chat, 1, name="Host", size=100)
    table = await poker.create_table(
        mode="money",
        chat_id=chat,
        thread_id=7,
        host_id=1,
        host_name="Host",
        access_mode="open",
        buy_in=40,
        small_blind=1,
        big_blind=2,
        max_seats=5,
        timeout=60,
    )
    assert (await players.get_player(chat, 1)).size == 60
    await poker.top_up(table.table_id, 1, 25)
    assert (await players.get_player(chat, 1)).size == 35
    await poker.close(table.table_id, 1)
    assert (await players.get_player(chat, 1)).size == 100


async def test_user_cannot_sit_at_money_and_practice_tables(db) -> None:
    from repositories import poker as poker_repo
    from services import poker

    first = await poker.create_table(
        mode="practice",
        chat_id=None,
        thread_id=None,
        host_id=10,
        host_name="Busy",
        access_mode="open",
        buy_in=100,
        small_blind=1,
        big_blind=2,
        max_seats=5,
        timeout=60,
    )
    assert first.table_id
    with pytest.raises(poker_repo.PokerRepoError, match="already_seated"):
        await poker.create_table(
            mode="practice",
            chat_id=None,
            thread_id=None,
            host_id=10,
            host_name="Busy",
            access_mode="open",
            buy_in=100,
            small_blind=1,
            big_blind=2,
            max_seats=5,
            timeout=60,
        )


async def test_ready_players_complete_persisted_hand_and_stale_action_is_rejected(db) -> None:
    from repositories import players
    from repositories import poker as poker_repo
    from services import poker

    chat = -9002
    await players.set_player_fields(chat, 1, name="A", size=100)
    await players.set_player_fields(chat, 2, name="B", size=100)
    table = await poker.create_table(
        mode="money",
        chat_id=chat,
        thread_id=None,
        host_id=1,
        host_name="A",
        access_mode="open",
        buy_in=40,
        small_blind=1,
        big_blind=2,
        max_seats=2,
        timeout=60,
    )
    await poker.ask_to_join(table.table_id, 2, "B")
    await poker.toggle_ready(table.table_id, 1)
    _, hand = await poker.toggle_ready(table.table_id, 2)
    assert hand is not None

    first_version = hand.version
    first_uid = int(hand.state["current_uid"])
    legal = engine.legal_actions(hand.state, first_uid)
    await poker.act(
        table.table_id,
        first_uid,
        "check" if legal["check"] else "call",
        version=first_version,
    )
    with pytest.raises(poker_repo.PokerRepoError, match="stale_action"):
        await poker.act(
            table.table_id,
            first_uid,
            "check" if legal["check"] else "call",
            version=first_version,
        )

    for _ in range(20):
        active = await poker_repo.get_active_hand(table.table_id)
        if active is None:
            break
        uid = int(active.state["current_uid"])
        legal = engine.legal_actions(active.state, uid)
        await poker.act(
            table.table_id,
            uid,
            "check" if legal["check"] else "call",
            version=active.version,
        )
    assert await poker_repo.get_active_hand(table.table_id) is None
    finished = await poker_repo.get_latest_hand(table.table_id)
    assert finished is not None and finished.status == "finished"
    assert (await poker_repo.get_table(table.table_id)).status == "between"
