"""Regression test: audit-0706 — option income must flow into available_income / brokerage.

Bug: engine/scenario.py available_income excluded yr.option_income even though it was
already included in combined_gross (and thus taxed via federal_tax_amt).  The after-tax
option cash was silently discarded — never accumulated in the projected brokerage balance.

Fix: add yr.option_income to the available_income calculation.
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from models.household import Household, StockGrant


def _minimal_hh_with_options() -> Household:
    """Household where year 0 (base_year) has a non-zero option grant spread."""
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=0.0,        # no IRA — isolates option cash path
        spouse_ira=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        ss_start_age=70,
        living_expenses=0.0,  # zero expenses — all income becomes excess
        growth_rate=0.0,      # no growth — exact arithmetic
        expense_inflation=0.0,
        brok_turnover=0.0,
        ltcg_rate=0.0,
        std_deduction=32_200,
        senior_extra=1_650,
        grants=[StockGrant(year=2019, strike=100.0, shares=1_000, expiry_year=2029)],
        txn_price_now=200.0,  # spread = (200-100)*1000 = $100,000 in 2026
        rmd_start_age=75,
        qcd_limit=0.0,
    )
    return hh


def test_option_income_appears_in_brokerage_next_year() -> None:
    """
    With zero IRA / SS / living expenses and a $100K option spread in base year,
    the after-tax option proceeds must accumulate in brokerage by year 2
    (year index 1, since brokerage carries forward from the previous year).

    Before fix: available_income omitted option_income, so excess_rmd=0 and
    brokerage stayed at 0 even though the person received (and was taxed on) $100K.
    After fix: brokerage_balance in year 2 equals the after-tax option proceeds
    from year 1, i.e. option_income - federal_tax_amt > 0.
    """
    hh = _minimal_hh_with_options()
    plan = ConversionPlan()  # no conversions
    result = run_scenario(hh, plan, name="OptionBrokerageTest", end_age=62)

    assert len(result.years) >= 2, "Need at least 2 years to check carry-forward"

    yr0 = result.years[0]  # year 2026 — option fires
    yr1 = result.years[1]  # year 2027 — brokerage should reflect yr0 excess

    # Sanity-check: option income was captured and taxed
    assert yr0.option_income == pytest.approx(100_000.0), (
        f"option_income should be 100_000, got {yr0.option_income}"
    )
    assert yr0.federal_tax_amt > 0, "Option income should have generated federal tax"

    # The after-tax option proceeds = option_income - federal_tax_amt
    expected_excess = yr0.option_income - yr0.federal_tax_amt
    assert expected_excess > 0, "After-tax option proceeds should be positive"

    # yr0.excess_rmd should equal the after-tax option proceeds (living_expenses=0)
    assert yr0.excess_rmd == pytest.approx(expected_excess, rel=1e-6), (
        f"excess_rmd in option year should be {expected_excess:.2f}, "
        f"got {yr0.excess_rmd:.2f}  "
        f"(bug: option_income missing from available_income)"
    )

    # yr1.brokerage_balance carries forward yr0.excess_rmd (growth_rate=0)
    assert yr1.brokerage_balance == pytest.approx(expected_excess, rel=1e-6), (
        f"brokerage_balance in year 2027 should be {expected_excess:.2f} "
        f"(after-tax option proceeds from 2026), got {yr1.brokerage_balance:.2f}  "
        f"(bug: option cash never flowed into brokerage)"
    )
