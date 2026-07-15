"""Pure-Python exercise auto-optimizer: solve for the ExerciseSchedule that
minimizes modeled lifetime all-in cost. NO Streamlit imports (engine purity)."""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant


@dataclass
class OptimizedPlan:
    ceiling_label: str
    schedule: ExerciseSchedule
    conversions: ConversionPlan
    lifetime_all_in: float
    over_ceiling_years: list[int] = field(default_factory=list)


@dataclass
class OptimizerResult:
    best: OptimizedPlan
    candidates: list[OptimizedPlan]
    baseline_cost: float


def _build_candidate_schedule(
    grants: list[StockGrant],
    base_year: int,
    ceiling_income_by_year: dict[int, float],
    base_ex_option_by_year: dict[int, float],
    price: float,
) -> tuple[ExerciseSchedule, list[int]]:
    """Spread each grant's exercises across [base_year, expiry] filling to each
    year's ordinary-income room, then FORCE all remaining shares into the grant's
    expiry year (hold-to-expiration deadline). Returns the schedule and the list
    of years whose committed option income was pushed past the ceiling (only the
    forced expiry lumps can do this). ``committed`` is per-year; grants compete
    soonest-expiry-first for each year's room.
    """
    schedule = ExerciseSchedule()
    over_ceiling_years: list[int] = []
    if not grants:
        return schedule, over_ceiling_years

    remaining = {g.key(): g.shares for g in grants}
    committed: dict[int, float] = {}
    last_year = max(g.expiry_year for g in grants)

    for year in range(base_year, last_year + 1):
        committed.setdefault(year, 0.0)
        for grant in sorted(grants, key=lambda g: g.expiry_year):
            rem = remaining[grant.key()]
            if rem <= 0 or year < base_year or year > grant.expiry_year:
                continue
            per_share = grant.per_share_spread(price)

            if year == grant.expiry_year:
                # Hold-to-expiration deadline: force ALL remaining shares now.
                schedule.set_shares(grant.key(), year, rem)
                schedule.set_price(year, price)
                committed[year] += per_share * rem
                remaining[grant.key()] = 0
                ceiling_room = max(
                    ceiling_income_by_year.get(year, 0.0)
                    - base_ex_option_by_year.get(year, 0.0),
                    0.0,
                )
                if per_share > 0 and committed[year] > ceiling_room and year not in over_ceiling_years:
                    over_ceiling_years.append(year)
                continue

            if per_share <= 0:
                # Underwater / at-the-money: no bracket benefit to early exercise;
                # defer to the forced expiry lump (0 income).
                continue

            room_dollars = max(
                ceiling_income_by_year.get(year, 0.0)
                - base_ex_option_by_year.get(year, 0.0)
                - committed[year],
                0.0,
            )
            n = min(rem, int(room_dollars // per_share))
            if n > 0:
                schedule.set_shares(grant.key(), year, n)
                schedule.set_price(year, price)
                committed[year] += per_share * n
                remaining[grant.key()] -= n

    return schedule, over_ceiling_years
