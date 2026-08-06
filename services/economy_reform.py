"""Versioned, one-off economy reforms applied to a single chat."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from db.engine import get_session_factory
from db.models import AuditLog, Deposit, EconomyReform, Event, Player
from repositories import events as E
from repositories.players import get_chat_lock

REFORM_KEY = "health_reform_v1"


@dataclass(frozen=True)
class ReformEntry:
    user_id: int
    name: str
    before: int
    after: int
    cut: int
    liquid_cut: int
    principal_cut: int
    accrued_cut: int


@dataclass(frozen=True)
class ReformResult:
    already_applied: bool
    entries: tuple[ReformEntry, ...]
    total_before: int
    total_after: int

    @property
    def total_cut(self) -> int:
        return self.total_before - self.total_after

    @property
    def affected_players(self) -> int:
        return len(self.entries)


def progressive_cut(total: int) -> int:
    """Cut 15% of 51..100, 25% of 101..200 and 35% above 200."""
    total = max(0, int(total))
    weighted = min(max(total - 50, 0), 50) * 15
    weighted += min(max(total - 100, 0), 100) * 25
    weighted += max(total - 200, 0) * 35
    return (weighted + 99) // 100 if weighted else 0


def _allocate_cut(cut: int, assets: tuple[int, int, int]) -> tuple[int, int, int]:
    """Allocate an exact cut proportionally using largest remainders."""
    total = sum(assets)
    if cut <= 0 or total <= 0:
        return 0, 0, 0
    base = [cut * value // total for value in assets]
    left = cut - sum(base)
    order = sorted(
        range(len(assets)),
        key=lambda i: (cut * assets[i] % total, assets[i], -i),
        reverse=True,
    )
    for i in order:
        if left <= 0:
            break
        if base[i] < assets[i]:
            base[i] += 1
            left -= 1
    return base[0], base[1], base[2]


async def preview(chat_id: int) -> ReformResult:
    factory = get_session_factory()
    async with factory() as session:
        applied = await session.get(EconomyReform, (chat_id, REFORM_KEY))
        if applied is not None:
            return ReformResult(
                True,
                (),
                applied.total_before,
                applied.total_after,
            )
        players = list(
            (await session.execute(select(Player).where(Player.chat_id == chat_id))).scalars().all()
        )
        deposits = {
            d.user_id: d
            for d in (
                await session.execute(select(Deposit).where(Deposit.chat_id == chat_id))
            ).scalars()
        }
        entries, before, after = _calculate(players, deposits)
        return ReformResult(False, tuple(entries), before, after)


async def apply(chat_id: int, actor_id: int) -> ReformResult:
    """Apply the reform and its audit/event records in one DB transaction."""
    async with get_chat_lock(chat_id):
        factory = get_session_factory()
        async with factory() as session:
            applied = await session.get(EconomyReform, (chat_id, REFORM_KEY))
            if applied is not None:
                return ReformResult(
                    True,
                    (),
                    applied.total_before,
                    applied.total_after,
                )

            players = list(
                (await session.execute(select(Player).where(Player.chat_id == chat_id)))
                .scalars()
                .all()
            )
            deposits = {
                d.user_id: d
                for d in (
                    await session.execute(select(Deposit).where(Deposit.chat_id == chat_id))
                ).scalars()
            }
            entries, total_before, total_after = _calculate(players, deposits)
            player_by_id = {p.user_id: p for p in players}

            for entry in entries:
                player = player_by_id[entry.user_id]
                player.size -= entry.liquid_cut
                dep = deposits.get(entry.user_id)
                if dep is not None:
                    dep.principal -= entry.principal_cut
                    dep.accrued -= entry.accrued_cut
                    if entry.principal_cut:
                        dep.interest_remainder_ppm = (
                            dep.interest_remainder_ppm
                            * dep.principal
                            // (dep.principal + entry.principal_cut)
                        )
                session.add(
                    Event(
                        chat_id=chat_id,
                        user_id=entry.user_id,
                        type=E.HEALTH_REFORM,
                        delta=-entry.liquid_cut,
                        size_after=player.size,
                        meta={
                            "reform": REFORM_KEY,
                            "total_before": entry.before,
                            "total_after": entry.after,
                            "total_cut": entry.cut,
                            "liquid_cut": entry.liquid_cut,
                            "principal_cut": entry.principal_cut,
                            "accrued_cut": entry.accrued_cut,
                        },
                    )
                )

            marker = EconomyReform(
                chat_id=chat_id,
                reform_key=REFORM_KEY,
                actor_id=actor_id,
                affected_players=len(entries),
                total_before=total_before,
                total_after=total_after,
                total_cut=total_before - total_after,
            )
            session.add(marker)
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    action=REFORM_KEY,
                    target_chat=chat_id,
                    payload={
                        "affected_players": len(entries),
                        "total_before": total_before,
                        "total_after": total_after,
                        "total_cut": total_before - total_after,
                    },
                )
            )
            await session.commit()
            return ReformResult(False, tuple(entries), total_before, total_after)


def _calculate(players, deposits: dict[int, Deposit]) -> tuple[list[ReformEntry], int, int]:
    entries: list[ReformEntry] = []
    total_before = 0
    total_after = 0
    for player in players:
        dep = deposits.get(player.user_id)
        principal = max(0, dep.principal) if dep is not None else 0
        accrued = max(0, dep.accrued) if dep is not None else 0
        assets = (max(0, player.size), principal, accrued)
        before = sum(assets)
        cut = progressive_cut(before)
        total_before += before
        total_after += before - cut
        if cut <= 0:
            continue
        liquid_cut, principal_cut, accrued_cut = _allocate_cut(cut, assets)
        entries.append(
            ReformEntry(
                user_id=player.user_id,
                name=player.name,
                before=before,
                after=before - cut,
                cut=cut,
                liquid_cut=liquid_cut,
                principal_cut=principal_cut,
                accrued_cut=accrued_cut,
            )
        )
    entries.sort(key=lambda e: (e.cut, e.before, e.user_id), reverse=True)
    return entries, total_before, total_after
