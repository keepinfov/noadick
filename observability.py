from __future__ import annotations

import contextvars
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineQuery, Message

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)


def _anonymous_id(value: int | None) -> str | None:
    if value is None:
        return None
    return hashlib.blake2s(str(value).encode(), digest_size=6, person=b"ndlog").hexdigest()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **(_context.get() or {}),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(log_format: str) -> None:
    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class LoggingContextMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = None
        chat_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            chat_id = event.chat.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            chat_id = event.message.chat.id if event.message else None
        elif isinstance(event, InlineQuery):
            user_id = event.from_user.id
        update = data.get("event_update")
        token = _context.set(
            {
                "update_id": getattr(update, "update_id", None),
                "event_type": type(event).__name__,
                "user": _anonymous_id(user_id),
                "chat": _anonymous_id(chat_id),
            }
        )
        try:
            return await handler(event, data)
        finally:
            _context.reset(token)
