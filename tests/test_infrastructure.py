from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from callbacks import BankCallback, DuelCallback, SettingsCallback
from config import AppSettings
from scripts.healthcheck import main as healthcheck
from services.backups import create_backup
from services.economy_metrics import simulate_growth

VALID_TOKEN = "123456789:" + "x" * 35


def test_settings_validate_external_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("ADMIN_IDS", "11,-22")
    monkeypatch.setenv("TZ", "Europe/Moscow")
    monkeypatch.setenv("PROXY", "socks5://localhost:1080")

    settings = AppSettings(_env_file=None)

    assert settings.parsed_admin_ids == {11, -22}
    assert settings.log_format == "json"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BOT_TOKEN", "not-a-token"),
        ("ADMIN_IDS", "1,admin"),
        ("TZ", "Mars/Olympus"),
        ("PROXY", "ftp://localhost/proxy"),
        ("LOG_FORMAT", "xml"),
    ],
)
def test_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        AppSettings(_env_file=None)


def test_backup_is_consistent_private_and_rotated(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    destination = tmp_path / "backups"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE values_for_test (value INTEGER NOT NULL)")
        database.execute("INSERT INTO values_for_test VALUES (42)")

    generated = [create_backup(source, destination, retention=2) for _ in range(3)]
    backups = sorted(destination.glob("bot-*.db"))

    assert len(set(generated)) == 3
    assert len(backups) == 2
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in backups)
    with sqlite3.connect(backups[-1]) as database:
        assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert database.execute("SELECT value FROM values_for_test").fetchone() == (42,)


def test_healthcheck_accepts_fresh_and_rejects_stale_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    monkeypatch.setenv("HEARTBEAT_PATH", str(heartbeat))
    assert healthcheck() == 0

    stale = time.time() - 76
    os.utime(heartbeat, (stale, stale))
    assert healthcheck() == 1


def test_callback_payloads_are_typed_and_fit_telegram_limit() -> None:
    payloads = [
        BankCallback(action="lrepayc", user_id=9_223_372_036_854_775_807).pack(),
        SettingsCallback(action="timezone", chat_id=-9_223_372_036_854_775_807).pack(),
        DuelCallback(token="a" * 32).pack(),
    ]

    assert all(len(payload.encode()) <= 64 for payload in payloads)
    assert BankCallback.unpack(payloads[0]).action == "lrepayc"
    assert SettingsCallback.unpack(payloads[1]).chat_id == -9_223_372_036_854_775_807
    assert DuelCallback.unpack(payloads[2]).token == "a" * 32


def test_economy_simulation_is_deterministic_and_validates_inputs() -> None:
    first = simulate_growth(30, 50, trials=100, seed=7)
    second = simulate_growth(30, 50, trials=100, seed=7)

    assert first == second
    assert first.p10 <= first.median <= first.p90
    with pytest.raises(ValueError):
        simulate_growth(30, 50, trials=0)
