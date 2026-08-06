from __future__ import annotations

import random
import time
from dataclasses import dataclass

from sqlalchemy import distinct, func, select

from db.engine import get_session_factory
from db.models import Corporation, Deposit, Event, Loan, Player
from models.disease import DISEASE_CHANCE, DISEASES
from services.game import WEIGHTED_RANGES


@dataclass(frozen=True)
class EconomySnapshot:
    players: int
    liquid: int
    deposits: int
    deposit_interest: int
    loans: int
    defaults: int
    corporation: int
    net_delta_7d: int
    net_delta_30d: int
    active_7d: int
    active_30d: int

    @property
    def deposit_liability(self) -> int:
        return self.deposits + self.deposit_interest

    @property
    def coverage_percent(self) -> float:
        if self.deposit_liability <= 0:
            return 100.0
        return self.corporation / self.deposit_liability * 100


@dataclass(frozen=True)
class GrowthSimulation:
    days: int
    initial_size: int
    trials: int
    mean: float
    p10: int
    median: int
    p90: int
    zero_percent: float


async def snapshot(now: int | None = None) -> EconomySnapshot:
    now = int(time.time()) if now is None else now
    factory = get_session_factory()
    async with factory() as session:
        players, liquid = (
            await session.execute(
                select(func.count(Player.user_id), func.coalesce(func.sum(Player.size), 0))
            )
        ).one()
        deposit_count, deposit_principal, deposit_interest = (
            await session.execute(
                select(
                    func.count(Deposit.user_id),
                    func.coalesce(func.sum(Deposit.principal), 0),
                    func.coalesce(func.sum(Deposit.accrued), 0),
                )
            )
        ).one()
        loan_count, loan_total, defaults = (
            await session.execute(
                select(
                    func.count(Loan.user_id),
                    func.coalesce(func.sum(Loan.principal + Loan.accrued_interest), 0),
                    func.coalesce(func.sum(Loan.defaulted), 0),
                )
            )
        ).one()
        corporation = await session.get(Corporation, 1)

        async def event_window(days: int) -> tuple[int, int]:
            net, active = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(Event.delta), 0),
                        func.count(distinct(Event.user_id)),
                    ).where(Event.created_at >= now - days * 86400)
                )
            ).one()
            return int(net), int(active)

        delta_7d, active_7d = await event_window(7)
        delta_30d, active_30d = await event_window(30)
        return EconomySnapshot(
            players=int(players),
            liquid=int(liquid),
            deposits=int(deposit_principal),
            deposit_interest=int(deposit_interest),
            loans=int(loan_total),
            defaults=int(defaults),
            corporation=int(corporation.balance if corporation else 0),
            net_delta_7d=delta_7d,
            net_delta_30d=delta_30d,
            active_7d=active_7d,
            active_30d=active_30d,
        )


def simulate_growth(
    days: int,
    initial_size: int,
    *,
    trials: int = 5_000,
    seed: int = 20260804,
    diseases_enabled: bool = True,
) -> GrowthSimulation:
    if days < 0 or initial_size < 0 or trials < 1:
        raise ValueError("days and initial_size must be non-negative; trials must be positive")
    rng = random.Random(seed)
    ranges, weights = zip(*WEIGHTED_RANGES, strict=True)
    outcomes: list[int] = []

    for _ in range(trials):
        size = initial_size
        disease = None
        disease_days_left = 0
        for _day in range(days):
            if disease_days_left <= 0:
                disease = None
            selected = rng.choices(ranges, weights=weights, k=1)[0]
            delta = rng.randint(selected[0], selected[1])
            if disease is not None:
                delta = max(0, int(delta * disease.growth_mod))
            size = max(0, size + delta)
            disease_days_left -= 1
            if diseases_enabled and rng.random() < DISEASE_CHANCE:
                disease = rng.choice(DISEASES)
                disease_days_left = disease.days
        outcomes.append(size)

    outcomes.sort()
    return GrowthSimulation(
        days=days,
        initial_size=initial_size,
        trials=trials,
        mean=sum(outcomes) / trials,
        p10=outcomes[int((trials - 1) * 0.10)],
        median=outcomes[int((trials - 1) * 0.50)],
        p90=outcomes[int((trials - 1) * 0.90)],
        zero_percent=sum(value == 0 for value in outcomes) / trials * 100,
    )
