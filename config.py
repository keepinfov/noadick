from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr
    admin_ids: str = ""
    db_path: Path = Path("./data/bot.db")
    storage_path: Path = Path("./storage")
    timezone: str = Field(default="Europe/Moscow", validation_alias="TZ")
    proxy: str = ""
    backup_dir: Path = Path("./data/backups")
    backup_interval_hours: int = Field(default=24, ge=0, le=24 * 30)
    backup_retention: int = Field(default=7, ge=1, le=365)
    log_format: str = "json"
    heartbeat_path: Path = Path("/tmp/noadick-heartbeat")

    @field_validator("bot_token")
    @classmethod
    def validate_bot_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        prefix, separator, secret = token.partition(":")
        if not separator or not prefix.isdigit() or len(secret) < 20:
            raise ValueError("BOT_TOKEN does not look like a Telegram bot token")
        return value

    @field_validator("admin_ids")
    @classmethod
    def validate_admin_ids(cls, value: str) -> str:
        invalid = [
            item.strip()
            for item in value.replace(";", ",").split(",")
            if item.strip() and not item.strip().lstrip("-").isdigit()
        ]
        if invalid:
            raise ValueError("ADMIN_IDS must be a comma-separated list of integers")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown TZ: {value}") from error
        return value

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls, value: str) -> str:
        value = value.strip()
        if value and urlsplit(value).scheme not in {"http", "https", "socks4", "socks5"}:
            raise ValueError("PROXY must use http, https, socks4 or socks5")
        return value

    @field_validator("log_format")
    @classmethod
    def validate_log_format(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"json", "text"}:
            raise ValueError("LOG_FORMAT must be json or text")
        return value

    @property
    def parsed_admin_ids(self) -> set[int]:
        return {
            int(item.strip())
            for item in self.admin_ids.replace(";", ",").split(",")
            if item.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    # Required values are supplied by BaseSettings from the environment.
    return AppSettings()  # pyright: ignore[reportCallIssue]
