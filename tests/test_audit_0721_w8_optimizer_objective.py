"""Regression test for audit-0721 finding C9.

``yr.all_in_cost`` (engine/scenario.py) is deliberately conversion-marginal
only: conversion_tax + irmaa_cost + aca_loss + niit_cost + conversion_ltcg_cost.
It excludes the base ordinary federal tax on RMDs/SS/option income by design
(other consumers rely on that marginal-only meaning).

The exercise auto-optimizer (engine/exercise_optimizer.py) previously summed
this SAME all_in_cost as its lifetime-cost objective for ranking exercise
schedules. Because option income is not a conversion, its federal tax was
invisible to that objective whenever the auto-filled conversion for a given
year was zero -- e.g. a candidate that dumps a large NQO spread into an
RMD-phase year, where compute_conversions zeroes conversions because there is
no bracket/MAGI room left. With conversions zero, conversion_tax, aca_loss,
and conversion_ltcg_cost are ALSO identically zero (they are defined as
deltas relative to the no-conversion baseline), so the old objective
collapsed to just irmaa_cost + niit_cost -- both capped/bounded -- totally
blind to the real (and potentially enormous) progressive-bracket federal tax
on the option income itself. Result: the optimizer could rank a schedule
that concentrates option income into a high-bracket, zero-conversion RMD
year as CHEAPER than one that spreads it, defeating its own purpose.

The fix (engine/exercise_optimizer.py: ``_lifetime_total_cost``) scores
candidates on federal_tax_amt + irmaa_cost + aca_loss + niit_cost +
conversion_ltcg_cost instead -- the TOTAL tax+cost that responds to exercise
timing, not just the conversion-marginal slice.

These tests use a constant NO-CONVERSION autofill (``_zero_autofill``) to
isolate the mechanism precisely: with conversions pinned at zero in every
year for BOTH candidates, the OLD objective is reduced to exactly
irmaa_cost + niit_cost (see the collapse note above), which is nearly
identical for the concentrated and spread schedules despite their enormous
difference in real federal tax -- reproducing the defect deterministically,
without depending on auto-fill's crowding-out interactions with unrelated
conversion decisions (which are a genuinely separate, non-monotonic effect).
"""

from __future__ import annotations

import copy

from engine.exercise_optimizer import _score_candidate
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household
from models.ytd_income import YTDSnapshot

_STRIKE = 50.0
_PRICE = 250.0  # per-share spread = 200.0
_SHARES = 5000
_BASE_YEAR = 2026
# your_age=63, base_year=2026 -> birth_year=1963 -> 1960+ cohort -> RMD age 75
# -> RMD onset year = 2026 + (75 - 63) = 2038. The grant expires exactly at
# RMD onset, so hold-to-expiration dumps the whole spread into the first RMD
# year -- the scenario the audit finding describes.
_EXPIRY_YEAR = 2038


def _zero_autofill(hh: Household, ytd: YTDSnapshot | None) -> ConversionPlan:
    """Constant no-conversion autofill: pins conversions at zero in every
    year for both candidates, isolating the schedule's OWN federal-tax impact
    from any confounding interaction with an autofill strategy's bracket-room
    crowding-out (see module docstring)."""
    return ConversionPlan()


def _make_household() -> Household:
    grant = StockGrant(
        year=2019, strike=_STRIKE, shares=_SHARES, expiry_year=_EXPIRY_YEAR, grant_id="big"
    )
    return Household(
        your_age=63,
        spouse_age=61,
        base_year=_BASE_YEAR,
        your_ira=1_000_000.0,
        spouse_ira=1_000_000.0,
        grants=[grant],
        living_expenses=0.0,
    )


def _concentrated_schedule(grant: StockGrant) -> ExerciseSchedule:
    """Dumps the entire grant into the RMD-onset expiry year (hold-to-expiry)."""
    schedule = ExerciseSchedule()
    schedule.set_shares(grant.key(), _EXPIRY_YEAR, _SHARES)
    schedule.set_price(_EXPIRY_YEAR, _PRICE)
    return schedule


def _spread_schedule(grant: StockGrant) -> ExerciseSchedule:
    """Spreads the same total shares evenly across every pre-expiry year."""
    schedule = ExerciseSchedule()
    years = list(range(_BASE_YEAR, _EXPIRY_YEAR + 1))
    per_year = _SHARES // len(years)
    remainder = _SHARES - per_year * len(years)
    for i, year in enumerate(years):
        n = per_year + (1 if i < remainder else 0)
        schedule.set_shares(grant.key(), year, n)
        schedule.set_price(year, _PRICE)
    return schedule


def _old_buggy_lifetime_all_in(hh: Household, schedule: ExerciseSchedule) -> float:
    """Reimplements the PRE-FIX optimizer objective (sum of yr.all_in_cost,
    conversion-marginal only) for comparison -- this is what the optimizer
    used before the audit-0721 C9 fix."""
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = schedule
    plan = _zero_autofill(hh_copy, None)
    result = run_scenario(hh_copy, plan, end_age=95, ytd=None)
    return sum(yr.all_in_cost for yr in result.years)


def test_optimizer_prefers_spread_schedule_over_rmd_phase_concentration() -> None:
    """The FIXED optimizer objective (_score_candidate -> lifetime_all_in,
    now backed by _lifetime_total_cost) must score the spread schedule
    cheaper than the schedule that concentrates the whole spread into the
    RMD-phase expiry year -- reflecting ordinary progressive-bracket tax on
    the option income itself, not just conversion-attributable cost."""
    hh = _make_household()
    grant = hh.grants[0]

    concentrated = _score_candidate(
        hh, _concentrated_schedule(grant), "no-conversion", _zero_autofill, [], ytd=None, end_age=95
    )
    spread = _score_candidate(
        hh, _spread_schedule(grant), "no-conversion", _zero_autofill, [], ytd=None, end_age=95
    )

    assert spread.lifetime_all_in < concentrated.lifetime_all_in


def test_old_buggy_objective_failed_to_penalize_rmd_phase_concentration() -> None:
    """Documents the defect directly: the OLD objective (sum of the
    conversion-marginal yr.all_in_cost alone) does NOT correctly rank the
    concentrated schedule as meaningfully more expensive -- with conversions
    pinned at zero everywhere, it collapses to irmaa_cost + niit_cost only
    (both capped/bounded), so it stays close to the spread schedule's score
    despite the concentrated schedule creating far more real federal tax.
    This is the regression the fix (test above) closes."""
    hh = _make_household()
    grant = hh.grants[0]

    old_concentrated = _old_buggy_lifetime_all_in(hh, _concentrated_schedule(grant))
    old_spread = _old_buggy_lifetime_all_in(hh, _spread_schedule(grant))

    # The bug: with the old formula, the concentrated schedule's SCORE is not
    # dramatically higher than the spread schedule's -- both are dominated by
    # irmaa_cost/niit_cost, which are small relative to the ~$300K+ real
    # federal-tax gap the new formula (below) correctly surfaces.
    assert abs(old_concentrated - old_spread) < 50_000.0


def test_fix_delta_between_schedules_is_dominated_by_base_federal_tax() -> None:
    """Sanity check on the mechanism: the NEW objective's spread-vs-concentrated
    gap is much larger than the OLD objective's gap, because the new objective
    (unlike the old) counts the base ordinary federal tax on the option income
    that lands in the zero-conversion RMD year."""
    hh = _make_household()
    grant = hh.grants[0]

    concentrated = _score_candidate(
        hh, _concentrated_schedule(grant), "no-conversion", _zero_autofill, [], ytd=None, end_age=95
    )
    spread = _score_candidate(
        hh, _spread_schedule(grant), "no-conversion", _zero_autofill, [], ytd=None, end_age=95
    )
    new_gap = concentrated.lifetime_all_in - spread.lifetime_all_in

    old_concentrated = _old_buggy_lifetime_all_in(hh, _concentrated_schedule(grant))
    old_spread = _old_buggy_lifetime_all_in(hh, _spread_schedule(grant))
    old_gap = old_concentrated - old_spread

    assert new_gap > old_gap
    assert new_gap > 200_000.0  # the real progressive-tax cost the old formula missed
