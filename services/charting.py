"""Small shared primitives for non-interactive Telegram charts."""

from __future__ import annotations

POSITIVE_COLOR = "#2e8b57"
NEGATIVE_COLOR = "#c94c4c"
NEUTRAL_COLOR = "#667085"
GRID_COLOR = "#d0d5dd"
BACKGROUND_COLOR = "#fbfcfe"


def signed(value: int) -> str:
    return f"{value:+d}"


def short_label(value: str, limit: int = 18) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"
