"""Regression tests for audit 2026-07-06: bracket-ceiling uses per-year filing status.

Bug: views/planner.py and views/rmd_squeeze.py computed the 12% (and 22%) bracket
ceiling lines using ``hh.filing_status`` (the household's initial filing status,
always MFJ for a surviving-spouse scenario) instead of ``yr.filing_status`` (the
per-year status set by the engine to "Single" after the death year).

This means post-death years in a SurvivorScenario displayed a 12% ceiling based on
BRACKETS_MFJ[1][0] (~$100,800) rather than BRACKETS_SINGLE[1][0] (~$50,400) --
overstating available conversion headroom by ~$50,400 per year.

Fix: compute ceil_12_values / ceil_22_values with a per-year bracket lookup:
    BRACKETS_SINGLE if yr.filing_status == "Single" else BRACKETS_MFJ
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE
from engine.tax_indexing import index_value as _index_value
from models.household import Household, SurvivorScenario

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_survivor_hh(death_year_offset: int = 2) -> Household:
    """Return an MFJ household whose survivor scenario triggers within the window.

    death_year_offset=2 means the spouse dies 2 years after base_year, so
    post-death years have yr.filing_status == "Single".
    """
    base_year = 2026
    return Household(
        your_age=65,
        spouse_age=62,
        filing_status="MFJ",
        your_ira=1_500_000.0,
        spouse_ira=500_000.0,
        your_ss_fra=30_000.0,
        spouse_ss_fra=18_000.0,
        survivor=SurvivorScenario(
            who_dies="spouse",
            death_year=base_year + death_year_offset,
        ),
    )


def _ceil_12_buggy(yr, hh_filing_status: str, cpi: float) -> float:
    """Reproduce the PRE-FIX logic: always uses hh.filing_status (static)."""
    _br = BRACKETS_SINGLE if hh_filing_status == "Single" else BRACKETS_MFJ
    return yr.total_deductions + _index_value(_br[1][0], yr.year, cpi)


def _ceil_12_correct(yr, cpi: float) -> float:
    """Reproduce the POST-FIX logic: uses yr.filing_status (per-year)."""
    _br = BRACKETS_SINGLE if yr.filing_status == "Single" else BRACKETS_MFJ
    return yr.total_deductions + _index_value(_br[1][0], yr.year, cpi)


def _ceil_22_buggy(yr, hh_filing_status: str, cpi: float) -> float:
    """Reproduce the PRE-FIX rmd_squeeze 22%-ceiling logic."""
    _br = BRACKETS_SINGLE if hh_filing_status == "Single" else BRACKETS_MFJ
    return yr.total_deductions + _index_value(_br[2][0], yr.year, cpi)


def _ceil_22_correct(yr, cpi: float) -> float:
    """Reproduce the POST-FIX rmd_squeeze 22%-ceiling logic."""
    _br = BRACKETS_SINGLE if yr.filing_status == "Single" else BRACKETS_MFJ
    return yr.total_deductions + _index_value(_br[2][0], yr.year, cpi)


# ---------------------------------------------------------------------------
# Preconditions -- confirm the test is meaningful
# ---------------------------------------------------------------------------


class TestPreconditions:
    """Confirm that MFJ and Single bracket values differ in the expected direction."""

    def test_brackets_single_12pct_lower_than_mfj(self):
        """BRACKETS_SINGLE[1][0] < BRACKETS_MFJ[1][0] -- fix is non-trivial."""
        assert BRACKETS_SINGLE[1][0] < BRACKETS_MFJ[1][0], (
            f"Expected Single 12%-bracket top ({BRACKETS_SINGLE[1][0]}) "
            f"< MFJ ({BRACKETS_MFJ[1][0]})"
        )

    def test_brackets_single_22pct_lower_than_mfj(self):
        """BRACKETS_SINGLE[2][0] < BRACKETS_MFJ[2][0] -- fix is non-trivial."""
        assert BRACKETS_SINGLE[2][0] < BRACKETS_MFJ[2][0], (
            f"Expected Single 22%-bracket top ({BRACKETS_SINGLE[2][0]}) "
            f"< MFJ ({BRACKETS_MFJ[2][0]})"
        )

    def test_survivor_hh_produces_single_years(self):
        """After the death year, at least one YearResult must have filing_status == 'Single'."""
        hh = _make_survivor_hh()
        result = run_scenario(hh, ConversionPlan())
        single_years = [yr for yr in result.years if yr.filing_status == "Single"]
        assert single_years, (
            "Survivor scenario must produce at least one year with filing_status='Single'. "
            "Check survivor_death_age is within the projection window."
        )


# ---------------------------------------------------------------------------
# Core regression: planner.py ceil_12_values (per-year bracket lookup)
# ---------------------------------------------------------------------------


class TestPlannerCeil12PerYearFilingStatus:
    """The 12% ceiling in planner.py must use yr.filing_status, not hh.filing_status.

    Bug: post-death Single years used BRACKETS_MFJ[1][0] -> ceiling ~$50K too high.
    Fix: per-year lookup uses BRACKETS_SINGLE[1][0] for years where yr.filing_status=='Single'.
    """

    def test_post_death_year_uses_single_bracket_not_mfj(self):
        """For a post-death Single year the correct ceiling must be lower than the buggy one."""
        hh = _make_survivor_hh()
        result = run_scenario(hh, ConversionPlan())
        cpi = hh.cpi_assumption

        single_years = [yr for yr in result.years if yr.filing_status == "Single"]
        assert single_years, "Need at least one Single year for this test"

        yr = single_years[0]
        correct = _ceil_12_correct(yr, cpi)
        buggy = _ceil_12_buggy(yr, hh.filing_status, cpi)

        # The correct (Single) ceiling must be strictly lower than the buggy (MFJ) one.
        # The difference should be approximately index_value(MFJ[1][0] - SINGLE[1][0]).
        expected_delta = _index_value(BRACKETS_MFJ[1][0] - BRACKETS_SINGLE[1][0], yr.year, cpi)
        assert correct < buggy, (
            f"Post-death Single year {yr.year}: correct ceiling ({correct:,.0f}) "
            f"must be less than buggy MFJ ceiling ({buggy:,.0f})"
        )
        assert abs((buggy - correct) - expected_delta) < 5.0, (
            f"Gap between buggy and correct ceiling should be ~{expected_delta:,.0f}, "
            f"got {buggy - correct:,.0f}"
        )

    def test_pre_death_mfj_year_unaffected(self):
        """For a pre-death MFJ year, both formulas must agree (no regression)."""
        hh = _make_survivor_hh()
        result = run_scenario(hh, ConversionPlan())
        cpi = hh.cpi_assumption

        mfj_years = [yr for yr in result.years if yr.filing_status == "MFJ"]
        assert mfj_years, "Need at least one MFJ year"

        yr = mfj_years[0]
        correct = _ceil_12_correct(yr, cpi)
        buggy = _ceil_12_buggy(yr, hh.filing_status, cpi)

        assert correct == pytest.approx(buggy, abs=0.01), (
            f"MFJ year {yr.year}: per-year and static formulas must agree, "
            f"got correct={correct:,.0f} vs buggy={buggy:,.0f}"
        )

    def test_ceil_12_correct_formula_matches_single_bracket_exactly(self):
        """The per-year formula produces exactly total_deductions + index_value(SINGLE[1][0])."""
        hh = _make_survivor_hh()
        result = run_scenario(hh, ConversionPlan())
        cpi = hh.cpi_assumption

        single_years = [yr for yr in result.years if yr.filing_status == "Single"]
        assert single_years

        yr = single_years[0]
        expected = yr.total_deductions + _index_value(BRACKETS_SINGLE[1][0], yr.year, cpi)
        got = _ceil_12_correct(yr, cpi)
        assert got == pytest.approx(expected, abs=0.01)

    def test_source_uses_yr_filing_status_not_hh_filing_status(self):
        """views/planner.py must reference yr.filing_status in the ceil_12_values expression."""
        import inspect

        import views.planner as planner_mod

        src = inspect.getsource(planner_mod)
        assert "yr.filing_status" in src, (
            "planner.py must use yr.filing_status in the bracket-ceiling computation"
        )
        assert "BRACKETS_SINGLE if yr.filing_status" in src, (
            "planner.py ceil_12_values must select BRACKETS_SINGLE based on yr.filing_status, "
            "not hh.filing_status"
        )


# ---------------------------------------------------------------------------
# Core regression: rmd_squeeze.py ceil_12_values and ceil_22_values
# ---------------------------------------------------------------------------


class TestRmdSqueezeCeilingsPerYearFilingStatus:
    """Both 12% and 22% ceiling lines in rmd_squeeze.py must use yr.filing_status.

    Same bug pattern as planner.py: a single static _brackets assignment used
    hh.filing_status, causing post-death Single years to show MFJ bracket values.
    """

    def test_post_death_22pct_ceiling_uses_single_bracket(self):
        """For a post-death Single year, the 22% correct ceiling must be lower than buggy."""
        hh = _make_survivor_hh()
        result = run_scenario(hh, ConversionPlan())
        cpi = hh.cpi_assumption

        single_years = [yr for yr in result.years if yr.filing_status == "Single"]
        assert single_years, "Need at least one Single year for this test"

        yr = single_years[0]
        correct = _ceil_22_correct(yr, cpi)
        buggy = _ceil_22_buggy(yr, hh.filing_status, cpi)

        assert correct < buggy, (
            f"Post-death Single year {yr.year}: correct 22% ceiling ({correct:,.0f}) "
            f"must be less than buggy MFJ ceiling ({buggy:,.0f})"
        )

    def test_post_death_12pct_ceiling_uses_single_bracket_rmd(self):
        """For a post-death Single year in rmd_squeeze context, 12% ceiling must use Single."""
        hh = _make_survivor_hh()
        result = run_scenario(hh, ConversionPlan())
        cpi = hh.cpi_assumption

        single_years = [yr for yr in result.years if yr.filing_status == "Single"]
        assert single_years

        yr = single_years[0]
        correct = _ceil_12_correct(yr, cpi)
        buggy = _ceil_12_buggy(yr, hh.filing_status, cpi)
        assert correct < buggy

    def test_source_uses_yr_filing_status_rmd_squeeze(self):
        """views/rmd_squeeze.py must reference yr.filing_status in ceiling comprehensions."""
        import inspect

        import views.rmd_squeeze as rmd_mod

        src = inspect.getsource(rmd_mod)
        assert "yr.filing_status" in src, (
            "rmd_squeeze.py must use yr.filing_status in bracket-ceiling computation"
        )
        assert "BRACKETS_SINGLE if yr.filing_status" in src, (
            "rmd_squeeze.py ceil_12_values / ceil_22_values must select brackets "
            "based on yr.filing_status, not hh.filing_status"
        )
