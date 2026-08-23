from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


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


def _migration_config(path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.attributes["configure_logger"] = False
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    return config


def _user_columns(path: Path) -> dict[str, tuple[str, int, object]]:
    with sqlite3.connect(path) as database:
        return {
            str(row[1]): (str(row[2]), int(row[3]), row[4])
            for row in database.execute("PRAGMA table_info(users)")
        }


def test_0010_migration_roundtrip_from_0009(tmp_path: Path) -> None:
    path = tmp_path / "public-label-0009.db"
    config = _migration_config(path)
    command.upgrade(config, "0009_telegram_casino")

    # 0001 adopts Base.metadata, so remove the future column to faithfully
    # reconstruct a database whose actual schema is at revision 0009.
    with sqlite3.connect(path) as database:
        database.execute("ALTER TABLE users DROP COLUMN public_label")
        database.execute(
            "INSERT INTO users "
            "(user_id, first_name, is_banned, created_at, updated_at) "
            "VALUES (7, 'Игрок', 0, 1, 1)"
        )

    command.upgrade(config, "0010_public_profile_label")
    assert "public_label" in _user_columns(path)
    assert _user_columns(path)["public_label"] == ("VARCHAR(80)", 0, None)
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT public_label FROM users WHERE user_id = 7").fetchone() == (
            None,
        )

    command.downgrade(config, "0009_telegram_casino")
    assert "public_label" not in _user_columns(path)

    command.upgrade(config, "0010_public_profile_label")
    assert "public_label" in _user_columns(path)


async def test_public_label_is_global_and_user_upsert_safe(db) -> None:
    from db.engine import get_session_factory
    from db.models import User
    from repositories import chats

    await chats.upsert_user(42, "Игрок", None)
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, 42)
        assert user is not None
        user.public_label = "Первый игрок"
        await session.commit()

    await chats.upsert_user(42, "Новое имя", "player")
    user = await chats.get_user(42)
    assert user is not None
    assert user.first_name == "Новое имя"
    assert user.public_label == "Первый игрок"


async def test_stats_and_profiles_show_escaped_public_label_only(db) -> None:
    from db.engine import get_session_factory
    from db.models import User
    from presentation import public
    from repositories import chats, players
    from services import stats

    chat_id = -100
    user_id = 42
    await chats.upsert_user(user_id, "Игрок <&>", "player")
    await players.set_player_fields(chat_id, user_id, name="Игрок <&>", size=12)
    factory = get_session_factory()
    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.notes = "ПРИВАТНАЯ ЗАМЕТКА"
        user.public_label = "<легенда> & тест"
        await session.commit()

    local_stats = await stats.compute_profile(chat_id, user_id)
    global_stats = await stats.compute_global_profile(user_id)
    assert local_stats.public_label == "<легенда> & тест"
    assert global_stats.public_label == "<легенда> & тест"
    assert global_stats.ban_reason is None

    expected = "🏷 <b>От разработчиков:</b> &lt;легенда&gt; &amp; тест"
    local_text = public.profile(local_stats, user_id=user_id)
    global_text = public.global_profile(global_stats)
    assert local_text.splitlines()[1] == expected
    assert global_text.splitlines()[1] == expected
    assert "ПРИВАТНАЯ ЗАМЕТКА" not in local_text
    assert "ПРИВАТНАЯ ЗАМЕТКА" not in global_text

    async with factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.public_label = None
        await session.commit()
    local_without = public.profile(await stats.compute_profile(chat_id, user_id), user_id=user_id)
    global_without = public.global_profile(await stats.compute_global_profile(user_id))
    assert "От разработчиков" not in local_without
    assert "От разработчиков" not in global_without
