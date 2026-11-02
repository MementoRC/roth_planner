"""Audit-1101 W1 regression tests for engine/scenario_autofill.py (synthetic inputs only).

F-01: _auto_fill_core priced forecast-year brokerage dividends and realized
gains off a local total-return proxy that started at brokerage_start and never
received the RMD surplus run_scenario flows into the account. A household whose
living expenses are covered by RMDs grows a real brokerage the proxy never saw,
so the fill sized rooms without that income and overshot its target.

F-22: add_bracket_fill_withdrawals indexed the bracket ceiling without the
$50 rounding the tax engine applies, so up to $49.99 of each fill spilled into
the next bracket.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.irmaa import irmaa_tier1_threshold
from engine.scenario import run_scenario
from engine.scenario_autofill import (
    add_bracket_fill_withdrawals,
    auto_fill_22,
    auto_fill_irmaa_safe,
)
from engine.scenario_types import ConversionPlan
from engine.tax import indexed_bracket_ceiling
from models.household import GrowthProfile, Household

# Rounding slack for the bisection that sizes each year's room.
_TOL = 1.0


def _surplus_household() -> Household:
    """You in RMDs on a large IRA, spouse converting; RMDs exceed living costs."""
    return Household(
        your_age=75,
        spouse_age=60,
        base_year=2026,
        your_ira=3_000_000.0,
        spouse_ira=1_500_000.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=3_000.0,
        spouse_ss_fra=2_000.0,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
        living_expenses=40_000.0,
        brokerage_start=0.0,
        brokerage_start_basis=0.0,
        brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.03, qualified_fraction=0.5),
        grants=[],
        your_rmd_start_age=75,
        spouse_rmd_start_age=75,
        spouse_is_sole_beneficiary=False,
        inherited_iras=[],
        survivor=None,
        prior_year_magi={},
        cpi_assumption=0.0,
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
    )


def _converting_years(plan: ConversionPlan) -> list[int]:
    years = set(plan.your_conversions) | set(plan.spouse_conversions)
    return sorted(
        y
        for y in years
        if (plan.your_conversions.get(y) or 0.0) + (plan.spouse_conversions.get(y) or 0.0) > 0
    )


class TestF01FillSeesRmdSurplusBrokerage:
    def test_fill_22_stays_inside_22_bracket_in_forecast_years(self) -> None:
        hh = _surplus_household()
        plan = auto_fill_22(hh)
        res = run_scenario(hh, plan)
        by_year = {yr.year: yr for yr in res.years}
        conv_years = _converting_years(plan)
        # Not vacuous: the surplus-funded brokerage only matters after the base year.
        assert any(y > hh.base_year for y in conv_years)
        for y in conv_years:
            yr = by_year[y]
            assert yr.brokerage_balance >= 0.0
            ceiling = indexed_bracket_ceiling(
                2, year=y, cpi=hh.cpi_assumption, filing_status=yr.filing_status
            )
            assert yr.taxable_income <= ceiling + _TOL, (y, yr.taxable_income, ceiling)

    def test_irmaa_safe_stays_under_tier1_in_forecast_years(self) -> None:
        hh = _surplus_household()
        plan = auto_fill_irmaa_safe(hh)
        res = run_scenario(hh, plan)
        by_year = {yr.year: yr for yr in res.years}
        conv_years = _converting_years(plan)
        assert any(y > hh.base_year for y in conv_years)
        for y in conv_years:
            yr = by_year[y]
            tier1 = irmaa_tier1_threshold(y, yr.filing_status, hh.cpi_assumption)
            assert yr.magi <= tier1 + _TOL, (y, yr.magi, tier1)


class TestF22BracketFillCeilingRounded:
    @pytest.mark.parametrize("base_year", [2030, 2033])
    def test_fill_never_spills_past_statutory_ceiling(self, base_year: int) -> None:
        hh = dataclasses.replace(
            Household(),
            your_age=75,
            spouse_age=75,
            your_ira=4_000_000.0,
            spouse_ira=200_000.0,
            your_ss_fra=2_500.0,
            spouse_ss_fra=1_500.0,
            grants=[],
            exercise_schedule=None,
            inherited_iras=[],
            cpi_assumption=0.025,
            brokerage_start=0.0,
            brokerage_start_basis=0.0,
            living_expenses=0.0,
            base_year=base_year,
        )
        plan = add_bracket_fill_withdrawals(hh, ConversionPlan(), target_bracket=0.22)
        assert plan.extra_withdrawals
        res = run_scenario(hh, plan, "fill", end_age=95)
        checked = 0
        for yr in res.years:
            if not plan.extra_withdrawals.get(yr.year):
                continue
            ceiling = indexed_bracket_ceiling(
                2, year=yr.year, cpi=hh.cpi_assumption, filing_status=yr.filing_status
            )
            assert yr.taxable_income <= ceiling + 0.01, (yr.year, yr.taxable_income, ceiling)
            checked += 1
        assert checked
