"""audit-1101 W2: the exercise optimizer's ACA handling.

F-02: the ranking score must count the ACA premium the household actually
pays (``aca_premium_cost``). ``aca_loss`` is a conversion-marginal delta, so
option income -- which sits in both the with- and without-conversion MAGI --
never moved it, and a schedule that pushed MAGI over the ACA cliff looked free.

F-05: the aca-safe ceiling must be measured on ACA MAGI (IRC §36B(d)(2)(B)),
which adds the non-taxable part of Social Security back. The ceiling was put on
the ordinary basis with the IRMAA-MAGI wedge, so a household drawing SS before
65 had its option schedule pushed past the cliff by that untaxed SS.

Synthetic inputs only.
"""

from __future__ import annotations

import copy

import pytest

from engine import exercise_optimizer
from engine.aca import aca_ceiling_magi
from engine.exercise_optimizer import (
    _aca_magi,
    _base_projection,
    _base_projection_detail,
    _ceiling_income_by_year,
    _score_candidate,
    _year_cost,
    optimize_exercises,
)
from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_aca, auto_fill_irmaa_safe
from engine.scenario_types import ConversionPlan, YearResult
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household

_ACA_ONLY = [("aca-safe", "aca-safe", auto_fill_aca)]


def _aca_household(**overrides: object) -> Household:
    """Both 62, both on the ACA marketplace, both claiming SS at 62. One grant
    that expires well after both reach Medicare, so no forced expiry lump
    lands in a pre-Medicare year."""
    grant = StockGrant(year=2019, strike=100.0, shares=4000, expiry_year=2036, grant_id="g1")
    kwargs: dict[str, object] = {
        "your_age": 62,
        "spouse_age": 62,
        "filing_status": "MFJ",
        "base_year": 2026,
        "your_ira": 400_000.0,
        "spouse_ira": 300_000.0,
        "your_roth": 0.0,
        "spouse_roth": 0.0,
        "your_ss_fra": 2_400.0,
        "spouse_ss_fra": 1_800.0,
        "your_ss_start_age": 62,
        "spouse_ss_start_age": 62,
        "living_expenses": 60_000.0,
        "brokerage_start": 200_000.0,
        "your_aca_enrolled": True,
        "spouse_aca_enrolled": True,
        "grants": [grant],
        "stock_price_now": 150.0,
        "inherited_iras": [],
    }
    kwargs.update(overrides)
    return Household(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- F-02


def test_f02_year_cost_counts_premium_actually_paid() -> None:
    yr = YearResult(year=2026, your_age=62, spouse_age=62, phase="options")
    yr.federal_tax_amt = 10_000.0
    yr.irmaa_cost = 0.0
    yr.aca_loss = 0.0  # no conversion -> no conversion-marginal delta
    yr.aca_premium_cost = 18_000.0  # option income pushed MAGI over the cliff
    yr.niit_cost = 0.0
    yr.conversion_ltcg_cost = 0.0
    yr.forced_early_withdrawal_penalty = 0.0
    assert _year_cost(yr) == pytest.approx(28_000.0)


def test_f02_option_income_over_cliff_raises_score_by_premium() -> None:
    """Same household, no conversions: exercising the whole grant in a
    pre-Medicare year crosses the ACA cliff. aca_loss stays 0 (no conversion),
    but the premium paid jumps; the year's cost must include that jump."""
    hh = _aca_household()
    sched = ExerciseSchedule()
    sched.set_shares("g1", 2026, 4000)
    hh.exercise_schedule = sched
    result = run_scenario(copy.deepcopy(hh), ConversionPlan())
    yr = next(y for y in result.years if y.year == 2026)
    assert yr.option_income > 0
    assert yr.aca_loss == pytest.approx(0.0)
    assert yr.aca_premium_cost > 0.0
    expected = (
        yr.federal_tax_amt
        + yr.irmaa_cost
        + yr.aca_premium_cost
        + yr.niit_cost
        + yr.conversion_ltcg_cost
        + yr.forced_early_withdrawal_penalty
    )
    assert _year_cost(yr) == pytest.approx(expected)


# --------------------------------------------------------------------------- F-05


def test_f05_aca_ceiling_uses_aca_magi_wedge() -> None:
    hh = _aca_household()
    _base_ordinary, magi_wedge, deds, fs = _base_projection(hh)
    detail = _base_projection_detail(hh)
    ceilings = _ceiling_income_by_year(
        hh, "aca-safe", deds, magi_wedge, fs, aca_magi_wedge=detail.aca_magi_wedge
    )
    for year in (2026, 2027, 2028):  # both under 65
        expected = (
            aca_ceiling_magi(fs[year], year, hh.cpi_assumption) - (detail.aca_magi_wedge[year])
        )
        assert ceilings[year] == pytest.approx(expected)
        # the untaxed SS makes the ACA wedge strictly larger than the IRMAA one
        assert detail.aca_magi_wedge[year] > magi_wedge[year]


def test_f05_aca_safe_schedule_stays_under_cliff_with_ss_before_65() -> None:
    """End to end: the aca-safe candidate's voluntary exercises must not
    carry ACA MAGI over the cliff in any pre-Medicare year."""
    hh = _aca_household()
    result = optimize_exercises(hh, ceilings=_ACA_ONLY)
    cand = result.candidates[0]
    assert cand.ceiling_label == "aca-safe"
    hh_run = copy.deepcopy(hh)
    hh_run.exercise_schedule = cand.schedule
    scen = run_scenario(hh_run, ConversionPlan())
    exercised = False
    for yr in scen.years:
        if yr.year > 2028:  # 2029: both 65
            break
        if yr.option_income <= 0:
            continue
        exercised = True
        cliff = aca_ceiling_magi(yr.filing_status or "MFJ", yr.year, hh.cpi_assumption)
        assert yr.aca_magi <= cliff + 1.0, (yr.year, yr.aca_magi, cliff)
    assert exercised, "fixture should place some option income before Medicare"


def _no_conversions(_hh: Household, _ytd: object, _nii: float) -> ConversionPlan:
    return ConversionPlan()


def test_f05_over_ceiling_flag_tests_aca_magi() -> None:
    """A year whose IRMAA MAGI is under the ceiling but whose ACA MAGI (with
    the untaxed SS) is over it is flagged when the aca-safe MAGI is tested."""
    hh = _aca_household()
    base = run_scenario(copy.deepcopy(hh), ConversionPlan())
    by_year = {yr.year: yr for yr in base.years}
    yr26 = by_year[2026]
    assert yr26.aca_magi > yr26.magi  # SS before 65 -> untaxed SS present
    between = (yr26.magi + yr26.aca_magi) / 2

    def ceiling(year: int, _fs: str) -> float:
        return between if year == 2026 else float("inf")

    kwargs = {"magi_ceiling_fn": ceiling}
    plain = _score_candidate(hh, ExerciseSchedule(), "x", _no_conversions, [], None, 95, **kwargs)
    aca = _score_candidate(
        hh, ExerciseSchedule(), "x", _no_conversions, [], None, 95, magi_of=_aca_magi, **kwargs
    )
    assert 2026 not in plain.over_ceiling_years
    assert 2026 in aca.over_ceiling_years


def test_f05_optimizer_tests_aca_safe_on_aca_magi(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    real = exercise_optimizer._score_candidate

    def spy(*args: object, **kwargs: object) -> object:
        seen[str(args[2])] = kwargs.get("magi_of")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(exercise_optimizer, "_score_candidate", spy)
    optimize_exercises(
        _aca_household(),
        ceilings=[*_ACA_ONLY, ("irmaa-safe", "irmaa-safe", auto_fill_irmaa_safe)],
    )
    assert seen["aca-safe"] is _aca_magi
    assert seen["irmaa-safe"] is None
