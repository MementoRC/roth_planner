"""Tests for engine.exercise_optimizer: result dataclasses + engine-purity guard."""

from __future__ import annotations

from pathlib import Path

from engine.exercise_optimizer import OptimizedPlan, OptimizerResult, _build_candidate_schedule
from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant


def _make_schedule() -> ExerciseSchedule:
    sched = ExerciseSchedule()
    sched.set_shares("grant-1", 2030, 100)
    sched.set_price(2030, 150.0)
    return sched


def _make_plan() -> ConversionPlan:
    return ConversionPlan(your_conversions={2030: 50_000.0})


def test_optimized_plan_round_trips_fields() -> None:
    sched = _make_schedule()
    plan = _make_plan()

    optimized = OptimizedPlan(
        ceiling_label="top-of-22",
        schedule=sched,
        conversions=plan,
        lifetime_all_in=123.0,
        over_ceiling_years=[2030],
    )

    assert optimized.ceiling_label == "top-of-22"
    assert optimized.schedule is sched
    assert optimized.conversions is plan
    assert optimized.lifetime_all_in == 123.0
    assert optimized.over_ceiling_years == [2030]


def test_optimizer_result_round_trips_fields() -> None:
    sched = _make_schedule()
    plan = _make_plan()
    optimized = OptimizedPlan(
        ceiling_label="top-of-22",
        schedule=sched,
        conversions=plan,
        lifetime_all_in=123.0,
        over_ceiling_years=[2030],
    )

    result = OptimizerResult(best=optimized, candidates=[optimized], baseline_cost=200.0)

    assert result.best is optimized
    assert result.candidates == [optimized]
    assert result.baseline_cost == 200.0


def test_exercise_optimizer_has_no_streamlit_import() -> None:
    src = (Path(__file__).resolve().parent.parent / "engine/exercise_optimizer.py").read_text()
    assert "streamlit" not in src


def test_build_candidate_schedule_forces_remaining_shares_into_expiry_year() -> None:
    """A low per-year ceiling can't absorb the whole grant before expiry, so
    the deadline (hold-to-expiration) forces the remainder into 2028."""
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2028, grant_id="g1")
    base_year = 2026
    price = 150.0  # per-share spread = 50
    ceiling = {2026: 10_000.0, 2027: 10_000.0, 2028: 10_000.0}
    base_ex_option = {2026: 0.0, 2027: 0.0, 2028: 0.0}

    schedule, _over_ceiling_years = _build_candidate_schedule(
        [grant], base_year, ceiling, base_ex_option, price
    )

    assert sum(schedule.shares_by_grant_year[grant.key()].values()) == grant.shares


def test_build_candidate_schedule_respects_share_cap_and_validates() -> None:
    """Two grants, different expiry years: every grant's total scheduled shares
    equals its share count and the resulting schedule is well-formed."""
    grant1 = StockGrant(year=2019, strike=50.0, shares=300, expiry_year=2027, grant_id="g1")
    grant2 = StockGrant(year=2020, strike=80.0, shares=500, expiry_year=2029, grant_id="g2")
    base_year = 2026
    price = 120.0
    ceiling = dict.fromkeys(range(2026, 2030), 50_000.0)
    base_ex_option = dict.fromkeys(range(2026, 2030), 0.0)

    schedule, _over_ceiling_years = _build_candidate_schedule(
        [grant1, grant2], base_year, ceiling, base_ex_option, price
    )

    for grant in (grant1, grant2):
        scheduled = sum(schedule.shares_by_grant_year.get(grant.key(), {}).values())
        assert scheduled == grant.shares
    assert schedule.validate([grant1, grant2], base_year) == []


def test_build_candidate_schedule_stays_within_ceiling_except_forced_expiry_lump() -> None:
    """In every non-expiry year the scheduled option income stays within that
    year's ceiling room; only the forced expiry lump may exceed it, and that
    year is reported in over_ceiling_years."""
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2028, grant_id="g1")
    base_year = 2026
    price = 150.0
    ceiling = {2026: 10_000.0, 2027: 10_000.0, 2028: 10_000.0}
    base_ex_option = {2026: 0.0, 2027: 0.0, 2028: 0.0}

    schedule, over_ceiling_years = _build_candidate_schedule(
        [grant], base_year, ceiling, base_ex_option, price
    )

    for year in (2026, 2027):  # non-expiry years
        shares_that_year = schedule.shares(grant.key(), year)
        income = grant.per_share_spread(price) * shares_that_year
        room = ceiling[year] - base_ex_option[year]
        assert income <= room
    assert 2028 in over_ceiling_years


def test_build_candidate_schedule_prioritizes_soonest_expiry_for_scarce_room() -> None:
    """Two in-the-money grants share a scarce year: the sooner-expiry grant
    must win that year's limited room (soonest-expiry-first competition),
    with the later-expiry grant's remainder pushed into its own expiry lump."""
    grant_soon = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2027, grant_id="soon")
    grant_late = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2029, grant_id="late")
    base_year = 2026
    price = 150.0  # per-share spread = 50
    ceiling = {2026: 10_000.0}  # room in 2026 = 10_000 / 50 = 200 shares total
    base_ex_option: dict[int, float] = {}

    schedule, _over_ceiling_years = _build_candidate_schedule(
        [grant_soon, grant_late], base_year, ceiling, base_ex_option, price
    )

    assert schedule.shares(grant_soon.key(), 2026) == 200
    assert schedule.shares(grant_late.key(), 2026) == 0
    assert schedule.total_exercised(grant_soon.key()) == 1000
    assert schedule.total_exercised(grant_late.key()) == 1000
    assert schedule.validate([grant_soon, grant_late], base_year) == []


def test_build_candidate_schedule_underwater_grant_schedules_zero_income() -> None:
    """Price below strike: shares are still all forced into the expiry year
    (deadline), but the scheduled option income is 0 and the forced 0-income
    lump does not count as over-ceiling."""
    grant = StockGrant(year=2019, strike=200.0, shares=400, expiry_year=2027, grant_id="g1")
    base_year = 2026
    price = 150.0  # underwater
    ceiling = {2026: 50_000.0, 2027: 50_000.0}
    base_ex_option = {2026: 0.0, 2027: 0.0}

    schedule, over_ceiling_years = _build_candidate_schedule(
        [grant], base_year, ceiling, base_ex_option, price
    )

    assert sum(schedule.shares_by_grant_year[grant.key()].values()) == grant.shares
    assert schedule.income_for(2027, [grant]) == 0.0
    assert over_ceiling_years == []
