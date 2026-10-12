"""Audit 2026-10-08, wave W4: auto-fill must size the base year on run_scenario's basis.

F-21  Auto-fill ignored realized NQO exercises above the schedule, so its plans
      overshot the IRMAA tier-1 and 22% ceilings.
F-22  Auto-fill stored the base-year conversion as the room LEFT after YTD
      conversions, while run_scenario reads it as the YEAR TOTAL and subtracts the
      YTD conversions again. Decision D5: the stored figure is the year total.
F-23  Auto-fill's bracket base used raw STCG, left out crypto income and never
      subtracted HSA/deductible-IRA adjustments, unlike run_scenario.

End-to-end check for every case: the auto-fill plan, fed into run_scenario with the
same YTD snapshot, lands exactly on the ceiling it targeted. All figures are synthetic.
"""

import dataclasses

import pytest

from engine.irmaa import irmaa_tier1_threshold
from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_12, auto_fill_22, auto_fill_irmaa_safe
from engine.tax import BRACKETS_MFJ
from models.household import Household
from models.ytd_income import YTDSnapshot

BASE_YEAR = 2026


def _ceiling(rate: float) -> float:
    return next(ceil for ceil, r in BRACKETS_MFJ if r == rate)


TOP_12 = _ceiling(0.12)
TOP_22 = _ceiling(0.22)


def _hh(**kw) -> Household:
    hh = Household()
    base = {
        "base_year": BASE_YEAR,
        "cpi_assumption": 0.0,
        "grants": [],
        "your_age": 60,
        "spouse_age": 60,
        "your_ira": 1_000_000.0,
        "spouse_ira": 1_000_000.0,
        "your_ss_start_age": 70,
        "spouse_ss_start_age": 70,
        "living_expenses": 0.0,
        "brokerage_start": 2_000_000.0,
        "brokerage_start_basis": 2_000_000.0,
    }
    base.update(kw)
    names = {f.name for f in dataclasses.fields(hh)}
    for k, v in base.items():
        assert k in names, k
        setattr(hh, k, v)
    assert hh.filing_status == "MFJ"
    return hh


def _ytd(**kw) -> YTDSnapshot:
    return YTDSnapshot(tax_year=BASE_YEAR, **kw)


def _year_total(plan) -> float:
    return plan.your_conversions.get(BASE_YEAR, 0.0) + plan.spouse_conversions.get(BASE_YEAR, 0.0)


def _base_year(hh, plan, ytd):
    yr = run_scenario(hh, plan, ytd=ytd).years[0]
    assert yr.year == BASE_YEAR
    return yr


class TestF22BaseYearCellIsYearTotal:
    def test_your_ytd_conversions_are_part_of_the_year_total(self):
        hh = _hh()
        plan_none = auto_fill_22(hh, _ytd())
        ytd = _ytd(ira_conversions_ytd=50_000.0)
        plan = auto_fill_22(hh, ytd)
        assert _year_total(plan) == pytest.approx(_year_total(plan_none), abs=1.0)
        assert _base_year(hh, plan, ytd).taxable_income == pytest.approx(TOP_22, abs=1.0)

    def test_spouse_ytd_conversions_are_part_of_the_year_total(self):
        # Spouse older -> auto-fill gives the room to the spouse first.
        hh = _hh(your_age=58, spouse_age=62)
        ytd = _ytd(spouse_ira_conversions_ytd=50_000.0)
        plan = auto_fill_22(hh, ytd)
        assert plan.spouse_conversions[BASE_YEAR] >= 50_000.0
        assert _base_year(hh, plan, ytd).taxable_income == pytest.approx(TOP_22, abs=1.0)

    def test_ytd_conversion_by_the_person_auto_fill_does_not_pick(self):
        # Spouse older gets the room; "you" already converted this year.
        hh = _hh(your_age=58, spouse_age=62)
        ytd = _ytd(ira_conversions_ytd=50_000.0)
        plan = auto_fill_22(hh, ytd)
        assert plan.your_conversions[BASE_YEAR] == pytest.approx(50_000.0)
        assert _base_year(hh, plan, ytd).taxable_income == pytest.approx(TOP_22, abs=1.0)

    def test_forecast_years_are_unchanged(self):
        hh = _hh()
        plan_none = auto_fill_22(hh, _ytd())
        plan = auto_fill_22(hh, _ytd(ira_conversions_ytd=50_000.0))
        for year in range(BASE_YEAR + 1, BASE_YEAR + 4):
            assert plan.your_conversions.get(year, 0.0) + plan.spouse_conversions.get(
                year, 0.0
            ) == pytest.approx(
                plan_none.your_conversions.get(year, 0.0)
                + plan_none.spouse_conversions.get(year, 0.0),
                rel=0.02,
            )


class TestF23BracketBaseMatchesRunScenario:
    @pytest.mark.parametrize(
        "extra",
        [
            pytest.param({}, id="baseline"),
            pytest.param({"crypto_income_ytd": 15_000.0, "crypto_stcg_ytd": 10_000.0}, id="crypto"),
            pytest.param(
                {"hsa_contribution_ytd": 8_000.0, "deductible_ira_contribution_ytd": 7_000.0},
                id="above-the-line",
            ),
            pytest.param({"stcg_ytd": 15_000.0, "ltcg_ytd": -15_000.0}, id="netted-stcg"),
            pytest.param({"stcg_ytd": -20_000.0}, id="capped-loss"),
        ],
    )
    def test_fill_12_lands_on_the_12_percent_ceiling(self, extra):
        hh = _hh()
        ytd = _ytd(wages_ytd=20_000.0, **extra)
        plan = auto_fill_12(hh, ytd)
        assert _base_year(hh, plan, ytd).taxable_income == pytest.approx(TOP_12, abs=1.0)

    def test_fill_22_with_crypto_income_lands_on_the_22_percent_ceiling(self):
        hh = _hh()
        ytd = _ytd(wages_ytd=20_000.0, crypto_income_ytd=15_000.0, crypto_stcg_ytd=10_000.0)
        plan = auto_fill_22(hh, ytd)
        assert _base_year(hh, plan, ytd).taxable_income == pytest.approx(TOP_22, abs=1.0)


class TestF21RealizedNqoBoundsScheduledOptionIncome:
    def _hh(self):
        return _hh(your_age=63, spouse_age=63, your_ira=2_000_000.0, spouse_ira=2_000_000.0)

    def test_irmaa_safe_stays_under_tier_1(self):
        hh = self._hh()
        ytd = _ytd(nqo_exercise_ytd=60_000.0)
        assert hh.option_income(BASE_YEAR) == 0.0
        plan = auto_fill_irmaa_safe(hh, ytd)
        yr = _base_year(hh, plan, ytd)
        threshold = irmaa_tier1_threshold(BASE_YEAR, "MFJ", cpi=0.0)
        assert yr.magi <= threshold + 1.0
        assert yr.magi == pytest.approx(threshold, abs=500.0)

    def test_fill_22_stops_at_the_22_percent_ceiling(self):
        hh = self._hh()
        ytd = _ytd(nqo_exercise_ytd=60_000.0)
        plan = auto_fill_22(hh, ytd)
        assert _base_year(hh, plan, ytd).taxable_income == pytest.approx(TOP_22, abs=1.0)

    def test_realized_nqo_matching_the_schedule_is_unchanged(self):
        # Same income split as wages vs NQO must size the same room (control).
        hh = self._hh()
        plan_wages = auto_fill_22(hh, _ytd(wages_ytd=60_000.0))
        plan_nqo = auto_fill_22(hh, _ytd(nqo_exercise_ytd=60_000.0))
        assert _year_total(plan_nqo) == pytest.approx(_year_total(plan_wages), abs=1.0)
