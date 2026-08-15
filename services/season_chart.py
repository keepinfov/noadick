"""Cached two-panel renderer for Telegram weekly season cards."""

from __future__ import annotations

import asyncio
import io
import time

from services import charting, seasons

_cache: dict[tuple, tuple[float, bytes]] = {}
_CACHE_TTL = 60
_CACHE_MAX = 32
_slots = asyncio.Semaphore(2)


def _key(report: seasons.SeasonReport) -> tuple:
    return (
        report.chat_id,
        report.season_number,
        report.status,
        tuple((p.user_id, p.name, p.dick_total, p.wealth_delta) for p in report.players),
    )


def _render_sync(report: seasons.SeasonReport) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor(charting.BACKGROUND_COLOR)
    panels = (
        (axes[0], report.dick_leaders[:5], "dick_total", "Главный отросток", "см за /dick"),
        (axes[1], report.wealth_leaders[:5], "wealth_delta", "Капиталисты", "прирост состояния"),
    )
    for axis, rows, field, title, label in panels:
        axis.set_facecolor(charting.BACKGROUND_COLOR)
        shown = list(reversed(rows))
        values = [int(getattr(row, field)) for row in shown]
        names = [charting.short_label(row.name) for row in shown]
        if shown:
            colors = [
                charting.POSITIVE_COLOR if value >= 0 else charting.NEGATIVE_COLOR
                for value in values
            ]
            bars = axis.barh(range(len(shown)), values, color=colors, height=0.62)
            axis.set_yticks(range(len(shown)), names)
            axis.axvline(0, color=charting.NEUTRAL_COLOR, linewidth=1)
            span = max(1, max(abs(value) for value in values))
            for bar, value in zip(bars, values, strict=True):
                offset = span * 0.035
                axis.text(
                    value + (offset if value >= 0 else -offset),
                    bar.get_y() + bar.get_height() / 2,
                    charting.signed(value),
                    va="center",
                    ha="left" if value >= 0 else "right",
                    fontsize=10,
                    fontweight="bold",
                )
            axis.margins(x=0.18)
        else:
            axis.text(0.5, 0.5, "Пусто. Даже позор не явился.", ha="center", va="center")
            axis.set_yticks([])
            axis.set_xticks([])
        axis.set_title(title, fontsize=16, fontweight="bold", pad=14)
        axis.set_xlabel(label)
        axis.grid(axis="x", color=charting.GRID_COLOR, alpha=0.6)
        axis.set_axisbelow(True)
        for spine in axis.spines.values():
            spine.set_visible(False)
    fig.suptitle(
        "Предварительные итоги"
        if report.status == seasons.STATUS_LIVE
        else f"Сезон #{report.season_number}",
        fontsize=18,
        fontweight="bold",
    )
    fig.tight_layout()
    output = io.BytesIO()
    fig.savefig(output, format="png", metadata={"Software": "noadick seasons"})
    plt.close(fig)
    return output.getvalue()


async def render_png(report: seasons.SeasonReport) -> bytes:
    key = _key(report)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[0] <= _CACHE_TTL:
        return cached[1]
    async with _slots:
        rendered = await asyncio.to_thread(_render_sync, report)
    if len(_cache) >= _CACHE_MAX:
        oldest = min(_cache, key=lambda item: _cache[item][0])
        _cache.pop(oldest, None)
    _cache[key] = (now, rendered)
    return rendered
