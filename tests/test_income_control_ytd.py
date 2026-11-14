"""Income Control slice 2: YTD actuals in the stack (#123). Synthetic data only."""

from __future__ import annotations

from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from models.household import GrowthProfile
from tests.test_roth_distribution_earnings import _household


def _roth_year(first_roth_year: int) -> YearResult:
    """The proven fixture from tests/test_roth_distribution_earnings.py
    (TestEarningsTaxEndToEnd._run): a Roth that doubles each year funds large
    living expenses, so the second projected year draws well over $100,000 of
    earnings; first_roth_year=2026 makes them non-qualified (ordinary income),
    2015 makes them qualified (not income)."""
    hh = _household(
        your_age=65,
        spouse_age=65,
        your_roth=1_000_000.0,
        spouse_roth=0.0,
        living_expenses=600_000.0,
        your_roth_growth=GrowthProfile(default_rate=1.0, yield_rate=0.0),
        your_first_roth_year=first_roth_year,
    )
    return run_scenario(hh, ConversionPlan(), end_age=67).years[1]


def test_roth_earnings_income_defaults_to_zero() -> None:
    yr = YearResult(year=2026, your_age=61, spouse_age=58, phase="clean")
    assert yr.roth_earnings_income == 0.0


def test_roth_earnings_income_is_the_non_qualified_earnings_in_combined_gross() -> None:
    yr = _roth_year(first_roth_year=2026)
    assert yr.forced_your_roth_earnings_draw > 100_000.0  # known from the proven fixture
    assert yr.roth_earnings_income > 0
    assert yr.roth_earnings_income == (
        yr.forced_your_roth_earnings_draw + yr.forced_spouse_roth_earnings_draw
    )
    assert yr.combined_gross >= yr.roth_earnings_income


def test_qualified_roth_earnings_are_not_exposed_as_income() -> None:
    yr = _roth_year(first_roth_year=2015)
    assert yr.forced_your_roth_earnings_draw > 0
    assert yr.roth_earnings_income == 0.0
