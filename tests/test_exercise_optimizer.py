"""Tests for engine.exercise_optimizer: result dataclasses + engine-purity guard."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from engine.aca import aca_ceiling_magi
from engine.exercise_optimizer import (
    OptimizedPlan,
    OptimizerResult,
    _base_projection,
    _build_candidate_schedule,
    _ceiling_income_by_year,
    _score_candidate,
    optimize_exercises,
)
from engine.irmaa import IRMAA_TIERS_MFJ
from engine.scenario import run_no_conversion, run_scenario
from engine.scenario_autofill import auto_fill_22
from engine.scenario_types import ConversionPlan
from engine.tax import room_to_22
from engine.tax_indexing import index_value
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot


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


def test_optimizer_result_round_trips_all_fields() -> None:
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
        [grant], base_year, ceiling, base_ex_option, lambda _year: price
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
        [grant1, grant2], base_year, ceiling, base_ex_option, lambda _year: price
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
        [grant], base_year, ceiling, base_ex_option, lambda _year: price
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
        [grant_soon, grant_late], base_year, ceiling, base_ex_option, lambda _year: price
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
        [grant], base_year, ceiling, base_ex_option, lambda _year: price
    )

    assert sum(schedule.shares_by_grant_year[grant.key()].values()) == grant.shares
    assert schedule.income_for(2027, [grant]) == 0.0
    assert over_ceiling_years == []


def test_score_candidate_scores_schedule_without_mutating_caller_household() -> None:
    """_score_candidate must (a) run the scenario with the candidate schedule
    applied and (b) leave the caller's hh untouched (deepcopy isolation)."""
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    sentinel_schedule = ExerciseSchedule()
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        grants=[grant],
        txn_price_now=150.0,
        exercise_schedule=sentinel_schedule,
    )

    candidate = ExerciseSchedule.default_at_expiry(hh.grants, hh.base_year, hh.txn_price_now)

    plan = _score_candidate(
        hh,
        candidate,
        "top-of-22",
        auto_fill_22,
        [2030],
        ytd=None,
        end_age=95,
    )

    assert plan.ceiling_label == "top-of-22"
    assert plan.schedule is candidate
    assert plan.over_ceiling_years == [2030]
    assert plan.lifetime_all_in > 0

    # Recompute the expected lifetime cost independently on a fresh deepcopy.
    hh_check = copy.deepcopy(hh)
    hh_check.exercise_schedule = candidate
    expected_conversions = auto_fill_22(hh_check, None)
    expected_result = run_scenario(hh_check, expected_conversions, end_age=95, ytd=None)
    # audit-0721 C9: lifetime_all_in is TOTAL cost (federal_tax_amt + irmaa_cost +
    # aca_loss + niit_cost + conversion_ltcg_cost), NOT sum(yr.all_in_cost). The old
    # formula (sum(yr.all_in_cost), conversion-marginal only) locked in the defect
    # where option-income tax in a zero-conversion year was invisible to the
    # optimizer's objective.
    expected_lifetime = sum(
        yr.federal_tax_amt + yr.irmaa_cost + yr.aca_loss + yr.niit_cost + yr.conversion_ltcg_cost
        for yr in expected_result.years
    )
    assert plan.lifetime_all_in == pytest.approx(expected_lifetime)

    # Isolation assert: caller's hh must be untouched.
    assert hh.exercise_schedule is sentinel_schedule


def _make_hh_with_expiry_option_income() -> Household:
    """No explicit schedule -> effective_schedule() falls back to
    default_at_expiry, landing the whole in-the-money grant's spread in 2030."""
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    return Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        grants=[grant],
        txn_price_now=150.0,
    )


def test_base_projection_nets_option_income_and_nonnegative_magi_wedge() -> None:
    """base_ordinary must exclude option income (schedule-independent baseline)
    and magi_wedge must be non-negative in every projected year."""
    hh = _make_hh_with_expiry_option_income()

    base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(hh)

    result = run_no_conversion(copy.deepcopy(hh))
    yr = next(y for y in result.years if y.year == 2030)
    assert yr.option_income > 0
    assert base_ordinary[2030] == pytest.approx(yr.combined_gross - yr.option_income)
    assert base_ordinary[2030] < yr.combined_gross
    for year in magi_wedge:
        assert magi_wedge[year] >= 0
    assert set(total_deductions) == set(base_ordinary) == set(filing_status)


def test_ceiling_income_by_year_top_of_22_matches_room_to_22() -> None:
    hh = _make_hh_with_expiry_option_income()
    _base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(hh)

    ceiling = _ceiling_income_by_year(hh, "top-of-22", total_deductions, magi_wedge, filing_status)

    for year, ded in total_deductions.items():
        fs = filing_status[year]
        expected = room_to_22(0.0, ded, year=year, cpi=hh.cpi_assumption, filing_status=fs)
        assert ceiling[year] == pytest.approx(expected)


def test_ceiling_income_by_year_aca_safe_subtracts_magi_wedge() -> None:
    hh = _make_hh_with_expiry_option_income()
    _base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(hh)

    ceiling = _ceiling_income_by_year(hh, "aca-safe", total_deductions, magi_wedge, filing_status)

    for year, wedge in magi_wedge.items():
        fs = filing_status[year]
        expected = aca_ceiling_magi(fs, year, hh.cpi_assumption) - wedge
        assert ceiling[year] == pytest.approx(expected)


def test_ceiling_income_by_year_irmaa_safe_capped_at_22_bracket() -> None:
    """irmaa-safe must mirror auto_fill_irmaa_safe: min(irmaa_room, room_to_22),
    with the IRMAA term using the +2yr lookback index."""
    hh = _make_hh_with_expiry_option_income()
    _base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(hh)

    ceiling = _ceiling_income_by_year(hh, "irmaa-safe", total_deductions, magi_wedge, filing_status)

    for year, wedge in magi_wedge.items():
        fs = filing_status[year]
        cpi = hh.cpi_assumption
        irmaa_term = index_value(IRMAA_TIERS_MFJ[0][0], year + 2, cpi) - wedge
        bracket_term = room_to_22(0.0, total_deductions[year], year=year, cpi=cpi, filing_status=fs)
        assert ceiling[year] == pytest.approx(min(irmaa_term, bracket_term))


def test_ceiling_income_by_year_irmaa_safe_uses_two_year_lookback() -> None:
    """With a nonzero CPI assumption, indexing the IRMAA tier-1 threshold at
    year+2 must differ from indexing it at year, proving the lookback is real
    (not accidentally a no-op year+0 index)."""
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        grants=[grant],
        txn_price_now=150.0,
        cpi_assumption=0.03,
    )
    _base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(hh)

    ceiling = _ceiling_income_by_year(hh, "irmaa-safe", total_deductions, magi_wedge, filing_status)

    cpi = hh.cpi_assumption
    found_binding_irmaa_year = False
    for year, wedge in magi_wedge.items():
        fs = filing_status[year]
        irmaa_term_plus2 = index_value(IRMAA_TIERS_MFJ[0][0], year + 2, cpi) - wedge
        bracket_term = room_to_22(0.0, total_deductions[year], year=year, cpi=cpi, filing_status=fs)
        if irmaa_term_plus2 <= bracket_term:  # IRMAA term is the binding min
            found_binding_irmaa_year = True
            irmaa_term_plus0 = index_value(IRMAA_TIERS_MFJ[0][0], year, cpi) - wedge
            assert ceiling[year] != pytest.approx(irmaa_term_plus0)
    assert found_binding_irmaa_year


def test_ceiling_income_by_year_unknown_strategy_raises() -> None:
    hh = _make_hh_with_expiry_option_income()
    _base_ordinary, magi_wedge, total_deductions, filing_status = _base_projection(hh)

    with pytest.raises(ValueError, match="unknown strategy"):
        _ceiling_income_by_year(hh, "bogus", total_deductions, magi_wedge, filing_status)


def test_optimize_exercises_does_not_mutate_household() -> None:
    """optimize_exercises must operate on deepcopies; the caller's hh (schedule,
    grants, base_year) must be bit-for-bit unchanged afterward."""
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    sentinel_schedule = ExerciseSchedule()
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        grants=[grant],
        txn_price_now=150.0,
        exercise_schedule=sentinel_schedule,
    )
    original_grants = list(hh.grants)
    original_base_year = hh.base_year

    optimize_exercises(hh)

    assert hh.exercise_schedule is sentinel_schedule
    assert hh.grants == original_grants
    assert hh.base_year == original_base_year


def test_optimize_exercises_stays_deterministic() -> None:
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        grants=[grant],
        txn_price_now=150.0,
    )

    result1 = optimize_exercises(hh)
    result2 = optimize_exercises(hh)

    costs1 = [c.lifetime_all_in for c in result1.candidates]
    costs2 = [c.lifetime_all_in for c in result2.candidates]
    assert costs1 == costs2


def test_optimize_exercises_selects_argmin_with_current_baseline() -> None:
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        grants=[grant],
        txn_price_now=150.0,
    )

    result = optimize_exercises(hh)

    assert result.best.lifetime_all_in == min(c.lifetime_all_in for c in result.candidates)
    assert result.best in result.candidates
    assert len(result.candidates) == 6
    current_candidates = [c for c in result.candidates if c.ceiling_label == "current"]
    assert len(current_candidates) == 1
    assert current_candidates[0].lifetime_all_in == result.baseline_cost


def test_optimize_exercises_beats_status_quo_when_spreading_helps() -> None:
    """A single deeply-in-the-money grant with a large share count expiring a
    few years out: hold-to-expiry dumps the whole spread into one year (costly
    bracket-stacking), but spreading it across [base_year, expiry] fits under
    brackets/MAGI ceilings and should genuinely beat the status quo."""
    grant = StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        txn_price_now=200.0,
        grants=[grant],
    )

    result = optimize_exercises(hh)

    assert result.best.lifetime_all_in < result.baseline_cost
    assert result.best.ceiling_label != "current"
    # Universal guarantee: the optimizer never selects a worse plan than status quo.
    assert result.best.lifetime_all_in <= result.baseline_cost


def test_build_candidate_schedule_uses_per_year_projected_price() -> None:
    """price_for_year is invoked per candidate year (not a single flat value):
    a later expiry lump must be priced higher than an earlier one when the
    price-for-year callable grows over time."""
    grant_early = StockGrant(year=2019, strike=50.0, shares=100, expiry_year=2026, grant_id="early")
    grant_late = StockGrant(year=2019, strike=50.0, shares=100, expiry_year=2028, grant_id="late")
    base_year = 2026
    # Zero ceiling room forces every share into the hold-to-expiration lump.
    ceiling = dict.fromkeys(range(2026, 2029), 0.0)
    base_ex_option = dict.fromkeys(range(2026, 2029), 0.0)

    def price_for_year(year: int) -> float:
        return 100.0 * (1.07 ** (year - base_year))

    schedule, _over = _build_candidate_schedule(
        [grant_early, grant_late], base_year, ceiling, base_ex_option, price_for_year
    )

    assert schedule.price_by_year[2026] == pytest.approx(100.0)
    assert schedule.price_by_year[2028] == pytest.approx(100.0 * 1.07**2)


def test_optimize_exercises_wires_projected_price_for_future_exercise_years() -> None:
    """End-to-end wiring: a non-baseline candidate's scheduled price for a
    future exercise year equals hh.projected_txn_price(year) (7% default
    compounding), not a flat hh.txn_price_now. Hand-verified: 2030 price =
    200.0 * 1.07**4 = 262.16 (approx)."""
    grant = StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        txn_price_now=200.0,
        grants=[grant],
    )

    result = optimize_exercises(hh)

    non_baseline = [c for c in result.candidates if c.ceiling_label != "current"]
    future_priced_years = {
        year for c in non_baseline for year in c.schedule.price_by_year if year > hh.base_year
    }
    assert future_priced_years  # spreading/forced-expiry must hit at least one future year
    assert hh.projected_txn_price(2030) == pytest.approx(200.0 * 1.07**4)
    for c in non_baseline:
        for year, price in c.schedule.price_by_year.items():
            if year > hh.base_year:
                assert price == pytest.approx(hh.projected_txn_price(year))
            else:
                assert price == pytest.approx(hh.txn_price_now)


def test_build_candidate_schedule_empty_grants_returns_empty_schedule() -> None:
    """No grants at all: the early-return branch produces an empty schedule
    and no over-ceiling years."""
    schedule, over_ceiling_years = _build_candidate_schedule([], 2026, {}, {}, lambda _year: 100.0)

    assert schedule.is_empty()
    assert over_ceiling_years == []


def test_build_candidate_schedule_all_expired_grant_schedules_nothing() -> None:
    """A grant whose expiry_year is already before base_year is, intentionally,
    never scheduled — this mirrors ExerciseSchedule.default_at_expiry, which
    likewise skips grants already expired at base_year."""
    old = StockGrant(year=2015, strike=100.0, shares=1000, expiry_year=2020, grant_id="old")

    schedule, over_ceiling_years = _build_candidate_schedule([old], 2026, {}, {}, lambda _year: 200.0)

    assert schedule.total_exercised(old.key()) == 0
    assert over_ceiling_years == []


def test_optimize_exercises_handles_household_with_no_grants() -> None:
    """No grants: optimize_exercises must not crash and must still return a
    valid OptimizerResult (baseline candidate is always appended)."""
    hh = Household(your_age=61, spouse_age=55, base_year=2026, your_ira=500_000, spouse_ira=500_000, grants=[])

    result = optimize_exercises(hh)

    assert result.candidates
    assert result.best in result.candidates
    assert result.best.lifetime_all_in <= result.baseline_cost


def test_optimize_exercises_ytd_threads_into_baseline_cost() -> None:
    """baseline_cost is federal_tax_amt + irmaa_cost + aca_loss + niit_cost +
    conversion_ltcg_cost (audit-0721 C9: total, not conversion-marginal). The YTD
    wages/LTCG directly change federal_tax_amt (more ordinary income + a real
    LTCG-rate tax fold-in) AND push MAGI over the $250K MFJ NIIT threshold, so
    baseline_cost must differ between the with/without-YTD runs.
    """
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        grants=[grant],
        txn_price_now=150.0,
    )
    ytd = YTDSnapshot(tax_year=2026, wages_ytd=200_000.0, ltcg_ytd=100_000.0)

    result_with_ytd = optimize_exercises(hh, ytd=ytd)
    result_without_ytd = optimize_exercises(hh, ytd=None)

    assert result_with_ytd.candidates
    assert result_without_ytd.candidates
    assert result_with_ytd.baseline_cost != result_without_ytd.baseline_cost


def test_optimize_exercises_survives_filing_status_transition() -> None:
    """A household with a survivor/death event flips filing_status to Single
    partway through; the optimizer must not crash and _base_projection must
    capture the MFJ -> Single transition."""
    death_year = 2028
    hh = Household(
        your_age=60,
        spouse_age=58,
        base_year=2026,
        your_ira=500_000.0,
        spouse_ira=500_000.0,
        your_roth=0.0,
        spouse_roth=0.0,
        growth_rate=0.05,
        grants=[],
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        survivor=SurvivorScenario(who_dies="spouse", death_year=death_year),
    )

    result = optimize_exercises(hh)

    assert result.candidates
    assert result.best in result.candidates

    _base_ordinary, _magi_wedge, _total_deductions, filing_status = _base_projection(hh)
    assert filing_status[death_year] == "MFJ"
    assert filing_status[death_year + 1] == "Single"
