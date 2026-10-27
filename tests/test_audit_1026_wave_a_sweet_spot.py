"""audit-1026 F-03: the draw-aware Sweet Spot curve funds the base-year YTD-LTCG
stacking tax like run_scenario (which folds ytd_ltcg_tax into federal_tax_amt, so
it raises the cash need and the forced IRA draw).

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import (
    base_income_for_year,
    estimate_ltcg_eligible,
    forced_draw_fills,
    zero_conversion_ira_draws,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import sweep_all_in
from models.household import Household
from models.ytd_income import YTDSnapshot

CONV = 78_550.0


def _hh() -> Household:
    return Household(
        your_age=64,
        spouse_age=62,
        base_year=2026,
        grants=[],
        stock_price_now=0.0,
        stock_price_late=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        cpi_assumption=0.0,
        ss_cola=0.0,
        growth_rate=0.0,
        filing_status="MFJ",
        brokerage_start=0.0,
        living_expenses=40_000.0,
        your_ira=900_000.0,
        spouse_ira=700_000.0,
    )


def _assert_sweep_matches_run_scenario(ytd: YTDSnapshot) -> None:
    hh = _hh()
    year = hh.base_year
    draws = zero_conversion_ira_draws(hh, ytd=ytd)
    years = zero_conversion_years(hh, ytd=ytd)
    assert draws[year] > 0.0  # precondition: the waterfall forces an IRA draw
    fills = forced_draw_fills(hh, ytd=ytd, net_inv_income=0.0, ira_draws=draws)
    base = base_income_for_year(hh, year, ytd=ytd, ira_draw=draws[year])
    got = sweep_all_in(
        hh,
        base,
        [CONV],
        net_inv_income=0.0,
        ltcg_eligible=estimate_ltcg_eligible(hh, year, ytd=ytd),
        zero_year=years[year],
        fills=fills,
    )[0]

    plan = ConversionPlan(your_conversions={year: CONV}, spouse_conversions={year: 0.0})
    oracle = run_scenario(hh, plan, "oracle", end_age=hh.your_age, ytd=ytd).years[0]

    assert got.taxable_inc == pytest.approx(oracle.taxable_income, abs=5.0)
    assert got.magi == pytest.approx(oracle.magi, abs=5.0)
    assert got.conv_tax == pytest.approx(oracle.conversion_tax, abs=5.0)
    assert got.all_in == pytest.approx(oracle.all_in_cost, abs=5.0)


def test_sweep_funds_the_base_year_ytd_ltcg_stacking_tax() -> None:
    ytd = YTDSnapshot(
        tax_year=2026,
        wages_ytd=10_000.0,
        ltcg_ytd=15_000.0,
        qualified_dividends_ytd=4_000.0,
        interest_ytd=1_000.0,
    )
    _assert_sweep_matches_run_scenario(ytd)


def test_sweep_without_ytd_preferential_income_still_matches() -> None:
    ytd = YTDSnapshot(tax_year=2026, wages_ytd=10_000.0, interest_ytd=1_000.0)
    _assert_sweep_matches_run_scenario(ytd)
