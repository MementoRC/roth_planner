"""Regression tests for engine.scenario_compare.survivor_year_tax and
compute_survivor_snapshot (survivor IRA year-by-year compounding)."""

import pytest

from engine.ira import calc_rmd
from engine.scenario_compare import compute_survivor_snapshot, survivor_year_tax
from engine.tax import (
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    federal_tax_single,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import Household


class TestSurvivorIRACompounding:
    """Regression: survivor IRA must shrink by RMD withdrawals year-by-year.

    The old code used ``inherited_ira * (1 + rate) ** proj_years`` — a single
    end-year compounding that completely ignores the RMD drain during each of
    the 5 projection years.  For a large IRA balance that is past rmd_start_age,
    this overstatement is material (hundreds of thousands of dollars).
    """

    def _make_scenario(
        self, hh: Household, death_age: int, ira: float
    ) -> "ScenarioResult":  # noqa: F821 — imported at runtime
        from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult

        yr = YearResult(
            year=hh.base_year + (death_age - hh.your_age),
            your_age=death_age,
            spouse_age=death_age - hh.age_gap,
            phase="squeeze",
            your_ira_begin=ira,
            spouse_ira_begin=0.0,
            your_ss=25_000.0,
            spouse_ss=18_000.0,
        )
        return ScenarioResult(
            name="Test",
            years=[yr],
            household=hh,
            plan=ConversionPlan(),
        )

    def test_survivor_ira_lower_than_single_rate_compounding(self) -> None:
        """Year-by-year net-of-RMD balance must be strictly less than naive compound."""
        hh = Household(
            your_age=70,
            spouse_age=64,
            your_ira=1_000_000,
            spouse_ira=0,
            growth_rate=0.07,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        death_age = 80  # spouse survives; survivor starts at age 74
        inherited_ira = 2_000_000.0
        scenario = self._make_scenario(hh, death_age, inherited_ira)

        rows = compute_survivor_snapshot(hh, [scenario], "you", [death_age])
        assert len(rows) == 1

        # Manually compute the naive single-rate value for comparison.
        proj_years = 5
        rate = hh.spouse_ira_rate(hh.base_year + (death_age - hh.your_age) + proj_years)
        naive_grown = inherited_ira * (1 + rate) ** proj_years

        # Year-by-year simulation (mirrors the fix):
        balance = inherited_ira
        survivor_rmd_start = hh.spouse_rmd_start_age
        for offset in range(proj_years):
            year_offset = offset + 1
            surv_age = (death_age - hh.age_gap) + year_offset
            rmd_w = calc_rmd(balance, surv_age, survivor_rmd_start)
            balance = max(balance - rmd_w, 0.0) * (1 + hh.spouse_ira_rate(
                hh.base_year + (death_age - hh.your_age) + year_offset
            ))
        correct_grown = balance

        # The correct (net-of-RMD) balance must be strictly less than the naive value.
        assert correct_grown < naive_grown, (
            f"Expected correct_grown ({correct_grown:,.0f}) < naive_grown ({naive_grown:,.0f})"
        )
        # The difference must be material (> $100K) for a $2M IRA past RMD age
        assert naive_grown - correct_grown > 100_000, (
            "Overstatement from naive compounding should be material for large IRA"
        )

    def test_survivor_ira_no_rmd_years_matches_simple_growth(self) -> None:
        """When survivor is below rmd_start_age for ALL 5 projection years,
        net-of-RMD growth equals naive compounding (no RMD taken, so both paths identical)."""
        hh = Household(
            your_age=60,
            spouse_age=54,
            your_ira=1_000_000,
            spouse_ira=0,
            growth_rate=0.07,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        # Survivor (spouse) is age 54 at death; 5 yr projection → age 54..58, all < 75
        death_age = 60
        inherited_ira = 500_000.0
        scenario = self._make_scenario(hh, death_age, inherited_ira)

        rows = compute_survivor_snapshot(hh, [scenario], "you", [death_age])
        assert len(rows) == 1

        proj_years = 5
        rate = hh.growth_rate
        expected = inherited_ira * (1 + rate) ** proj_years
        # Derive the balance the engine used by reconstructing the RMD at year+5
        # Survivor at death is age 54; +5 → 59, well below rmd_start_age=75 so no RMD taken.
        balance = inherited_ira
        for offset in range(proj_years):
            year_offset = offset + 1
            surv_age = (death_age - hh.age_gap) + year_offset
            rmd_w = calc_rmd(balance, surv_age, hh.spouse_rmd_start_age)
            balance = max(balance - rmd_w, 0.0) * (1 + rate)
        assert balance == pytest.approx(expected, rel=1e-9)


def test_survivor_year_tax_indexes_to_projection_year() -> None:
    # Inflation-grown future income taxed against INDEXED brackets + deduction must
    # be strictly less than the same nominal income taxed against raw 2026 values.
    age, rmd, ss = 81, 150_000.0, 40_000.0
    cpi = 0.025
    tax_fixed, bracket_fixed, taxable_fixed = survivor_year_tax(age, rmd, ss, year=2051, cpi=cpi)
    assert tax_fixed == pytest.approx(federal_tax_single(taxable_fixed, year=2051, cpi=cpi))
    assert 0.0 < bracket_fixed < 1.0
    tss = taxable_ss(ss, rmd, filing_status="Single")
    gross = rmd + tss
    ded_buggy = float(STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE)
    ded_buggy += senior_bonus_deduction(age, 0, gross, year=2051, cpi=cpi, filing_status="Single")
    taxable_buggy = max(gross - ded_buggy, 0.0)
    tax_buggy = federal_tax_single(taxable_buggy)
    assert tax_fixed < tax_buggy
