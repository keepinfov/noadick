from __future__ import annotations

import random
import time
from dataclasses import dataclass

from sqlalchemy import Integer, case, cast, distinct, func, select

from db.engine import get_session_factory
from db.models import ChatCorporation, Deposit, Event, Loan, Player, PokerSeat, PokerTable
from models.disease import DISEASE_CHANCE, DISEASES
from repositories.events import PISYAGO, SEASON_SEKASKO_PRIZE
from services.bank import pisyago_coverage_pct
from services.game import WEIGHTED_RANGES


@dataclass(frozen=True)
class EconomySnapshot:
    players: int
    liquid: int
    poker_escrow: int
    deposits: int
    deposit_interest: int
    loans: int
    defaults: int
    roll_debts: int
    deposits_due_24h: int
    deposits_due_7d: int
    deposits_later: int
    loans_overdue: int
    loans_due_24h: int
    loans_due_7d: int
    loans_later: int
    corporation: int
    net_delta_7d: int
    net_delta_30d: int
    active_7d: int
    active_30d: int
    pisyago_covered_7d: int
    pisyago_covered_30d: int

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
    mean_roll_debt: float
    debt_p90: int


async def snapshot(now: int | None = None) -> EconomySnapshot:
    now = int(time.time()) if now is None else now
    factory = get_session_factory()
    async with factory() as session:
        players, liquid = (
            await session.execute(
                select(func.count(Player.user_id), func.coalesce(func.sum(Player.size), 0))
            )
        ).one()
        poker_escrow = (
            await session.execute(
                select(func.coalesce(func.sum(PokerSeat.stack + PokerSeat.committed), 0))
                .join(PokerTable, PokerTable.table_id == PokerSeat.table_id)
                .where(
                    PokerSeat.status == "active",
                    PokerTable.mode == "money",
                    PokerTable.status != "closed",
                )
            )
        ).scalar_one()
        deposit_count, deposit_principal, deposit_interest = (
            await session.execute(
                select(
                    func.count(Deposit.user_id),
                    func.coalesce(func.sum(Deposit.principal), 0),
                    func.coalesce(func.sum(Deposit.accrued), 0),
                )
            )
        ).one()
        dep_due_24h, dep_due_7d, dep_later = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Deposit.matures_at <= now + 86400,
                                    Deposit.principal + Deposit.accrued,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Deposit.matures_at.between(now + 86401, now + 7 * 86400),
                                    Deposit.principal + Deposit.accrued,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Deposit.matures_at > now + 7 * 86400,
                                    Deposit.principal + Deposit.accrued,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
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
        roll_debts, loan_overdue, loan_due_24h, loan_due_7d, loan_later = (
            await session.execute(
                select(
                    func.coalesce(func.sum(Loan.roll_debt_principal), 0),
                    func.coalesce(
                        func.sum(
                            case(
                                (Loan.due_at <= now, Loan.principal + Loan.accrued_interest),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Loan.due_at.between(now + 1, now + 86400),
                                    Loan.principal + Loan.accrued_interest,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Loan.due_at.between(now + 86401, now + 7 * 86400),
                                    Loan.principal + Loan.accrued_interest,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Loan.due_at > now + 7 * 86400,
                                    Loan.principal + Loan.accrued_interest,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                )
            )
        ).one()
        corporation = await session.scalar(
            select(
                func.coalesce(
                    func.sum(ChatCorporation.balance + ChatCorporation.insurance_reserve), 0
                )
            )
        )

        async def event_window(days: int) -> tuple[int, int]:
            net, active = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(Event.delta), 0),
                        func.count(distinct(Event.user_id)),
                    ).where(
                        Event.created_at >= now - days * 86400,
                        Event.type != SEASON_SEKASKO_PRIZE,
                    )
                )
            ).one()
            return int(net), int(active)

        delta_7d, active_7d = await event_window(7)
        delta_30d, active_30d = await event_window(30)

        async def pisyago_window(days: int) -> int:
            covered = (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(cast(func.json_extract(Event.meta, "$.covered"), Integer)),
                            0,
                        )
                    ).where(Event.created_at >= now - days * 86400, Event.type == PISYAGO)
                )
            ).scalar_one()
            return int(covered)

        pisyago_7d = await pisyago_window(7)
        pisyago_30d = await pisyago_window(30)
        return EconomySnapshot(
            players=int(players),
            liquid=int(liquid),
            poker_escrow=int(poker_escrow),
            deposits=int(deposit_principal),
            deposit_interest=int(deposit_interest),
            loans=int(loan_total),
            defaults=int(defaults),
            roll_debts=int(roll_debts),
            deposits_due_24h=int(dep_due_24h),
            deposits_due_7d=int(dep_due_7d),
            deposits_later=int(dep_later),
            loans_overdue=int(loan_overdue),
            loans_due_24h=int(loan_due_24h),
            loans_due_7d=int(loan_due_7d),
            loans_later=int(loan_later),
            corporation=int(corporation or 0),
            net_delta_7d=delta_7d,
            net_delta_30d=delta_30d,
            active_7d=active_7d,
            active_30d=active_30d,
            pisyago_covered_7d=pisyago_7d,
            pisyago_covered_30d=pisyago_30d,
        )


def simulate_growth(
    days: int,
    initial_size: int,
    *,
    trials: int = 5_000,
    seed: int = 20260804,
    diseases_enabled: bool = True,
    insurance_threshold: int = 20,
    insurance_limit: int = 20,
    insurance_period_days: int = 7,
) -> GrowthSimulation:
    if (
        days < 0
        or initial_size < 0
        or trials < 1
        or insurance_threshold < 0
        or insurance_limit < 0
        or insurance_period_days < 1
    ):
        raise ValueError("days and initial_size must be non-negative; trials must be positive")
    rng = random.Random(seed)
    ranges, weights = zip(*WEIGHTED_RANGES, strict=True)
    outcomes: list[int] = []
    debts: list[int] = []

    for _ in range(trials):
        size = initial_size
        disease = None
        disease_days_left = 0
        roll_debt = 0
        insurance_used = 0
        insurance_reset_day: int | None = None
        for _day in range(days):
            if insurance_reset_day is not None and _day >= insurance_reset_day:
                insurance_used = 0
                insurance_reset_day = None
            if disease_days_left <= 0:
                disease = None
            selected = rng.choices(ranges, weights=weights, k=1)[0]
            delta = rng.randint(selected[0], selected[1])
            if disease is not None:
                delta = max(0, int(delta * disease.growth_mod))
            if delta < 0:
                loss = -delta
                coverage = pisyago_coverage_pct(size, insurance_threshold)
                nominal_cover = loss * coverage // 100
                covered = min(nominal_cover, max(0, insurance_limit - insurance_used))
                if covered:
                    if insurance_reset_day is None:
                        insurance_reset_day = _day + insurance_period_days
                    insurance_used += covered
                roll_debt += loss - covered
            else:
                size += delta
            disease_days_left -= 1
            if diseases_enabled and rng.random() < DISEASE_CHANCE:
                disease = rng.choice(DISEASES)
                disease_days_left = disease.days
        outcomes.append(size)
        debts.append(roll_debt)

    outcomes.sort()
    debts.sort()
    return GrowthSimulation(
        days=days,
        initial_size=initial_size,
        trials=trials,
        mean=sum(outcomes) / trials,
        p10=outcomes[int((trials - 1) * 0.10)],
        median=outcomes[int((trials - 1) * 0.50)],
        p90=outcomes[int((trials - 1) * 0.90)],
        zero_percent=sum(value == 0 for value in outcomes) / trials * 100,
        mean_roll_debt=sum(debts) / trials,
        debt_p90=debts[int((trials - 1) * 0.90)],
    )
