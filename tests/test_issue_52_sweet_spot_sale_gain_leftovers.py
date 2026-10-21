"""Issue #52: Sweet Spot sale-gain leftovers from PR #51.

1. A brokerage sale a conversion forces in a year with slack at zero conversion
   is gated in (the curve then prices its gain, as run_scenario does).
2. The ACA + IRMAA Explorer stacks the zero-conversion sale gain with the
   year's LTCG, like Sweet Spot (``sweet_spot_ltcg_eligible``).
3. Sweet Spot's LTCG-cost component equals run_scenario's
   ``conversion_ltcg_cost`` for the same conversion.

Synthetic households only. run_scenario is the oracle.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from engine.aca_irmaa_compute import _swept_federal_tax, compute_cost_curves
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from engine.sweet_spot_compute import (
    BaseIncome,
    ConversionResult,
    all_in_at_conversion,
    base_income_for_year,
    forced_draw_fills,
    sweet_spot_ltcg_eligible,
    zero_conversion_gain_fractions,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import sweep_all_in
from engine.tax import deductions
from models.household import Household
from tests.test_issue_1_room_matches_fill import BASE_YEAR, _hh

TOL = 25.0


def _slack_hh() -> Household:
    """Social Security covers spending with slack at zero conversion; a
    conversion's tax uses the slack up and forces a low-basis brokerage sale."""
    return Household(
        your_age=70,
        spouse_age=70,
        base_year=BASE_YEAR,
        cpi_assumption=0.0,
        grants=[],
        brokerage_start=300_000.0,
        brokerage_start_basis=60_000.0,
        living_expenses=80_000.0,
        your_ira=900_000.0,
        spouse_ira=700_000.0,
        your_ss_fra=3_000.0,
        spouse_ss_fra=3_000.0,
        your_ss_start_age=70,
        spouse_ss_start_age=70,
    )


def _big_sale_hh() -> Household:
    """Spending funded by selling 80%-gain brokerage, large enough that the
    zero-conversion LTCG stack already crosses the 0% band."""
    return _hh(
        brokerage_start=1_000_000.0, brokerage_start_basis=200_000.0, living_expenses=200_000.0
    )


def _setup(
    hh: Household,
) -> tuple[dict[int, YearResult], dict[int, tuple[float, float]], BaseIncome]:
    years = zero_conversion_years(hh)
    draws = {y: yr.forced_your_ira_draw + yr.forced_spouse_ira_draw for y, yr in years.items()}
    gains = zero_conversion_sale_gains(hh, years)
    fills = forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws=draws, sale_gains=gains)
    base = base_income_for_year(
        hh,
        BASE_YEAR,
        ira_draw=draws[BASE_YEAR],
        sale_gain=gains[BASE_YEAR],
        sale_gain_fraction=zero_conversion_gain_fractions(hh, years)[BASE_YEAR],
    )
    return years, fills, base


def _priced(hh: Household, conv: float) -> ConversionResult:
    years, fills, base = _setup(hh)
    return sweep_all_in(
        hh,
        base,
        [0.0, conv],
        net_inv_income=0.0,
        ltcg_eligible=sweet_spot_ltcg_eligible(hh, base, None),
        zero_year=years[BASE_YEAR],
        fills=fills,
    )[1]


def _scenario_year(hh: Household, conv: float) -> YearResult:
    plan = ConversionPlan(your_conversions={BASE_YEAR: conv}, spouse_conversions={BASE_YEAR: 0.0})
    return run_scenario(hh, plan, "oracle", end_age=95).years[0]


# --------------------------------------------------------------------------
# Item 1: slack-year provoked sale
# --------------------------------------------------------------------------


def test_precondition_slack_year_conversion_forces_a_sale() -> None:
    hh = _slack_hh()
    zero = _scenario_year(hh, 0.0)
    conv = _scenario_year(hh, 120_000.0)
    assert zero.excess_rmd > 0.0
    assert zero.forced_brokerage_draw == 0.0
    assert zero.forced_your_ira_draw + zero.forced_spouse_ira_draw == 0.0
    assert conv.forced_brokerage_draw > 1_000.0
    assert conv.forced_your_ira_draw + conv.forced_spouse_ira_draw == 0.0


def test_slack_year_provoked_sale_is_gated_in() -> None:
    _years, fills, _base = _setup(_slack_hh())
    assert BASE_YEAR in fills


# MAGI stays under IRMAA tier 1: run_scenario's base year (no MAGI history)
# funds a same-year IRMAA surcharge the curve leaves to the 2-year lookback.
@pytest.mark.parametrize("conv", [100_000.0, 120_000.0])
def test_slack_year_curve_prices_the_provoked_sale_like_run_scenario(conv: float) -> None:
    hh = _slack_hh()
    base = _setup(hh)[2]
    aware = _priced(hh, conv)
    oracle = _scenario_year(hh, conv)
    assert oracle.forced_brokerage_draw > 0.0
    assert oracle.irmaa_cost == 0.0
    assert aware.magi == pytest.approx(oracle.magi, abs=TOL)
    assert aware.ltcg_delta == pytest.approx(oracle.conversion_ltcg_cost, abs=TOL)
    # The plain (ungated) sweep misses the provoked sale's gain.
    blind = all_in_at_conversion(hh, base, conv, 0.0)
    assert aware.magi > blind.magi + 1_000.0


# --------------------------------------------------------------------------
# Item 2: Explorer LTCG estimate includes the sale gain
# --------------------------------------------------------------------------


def test_explorer_stacks_the_zero_conversion_sale_gain() -> None:
    hh = _hh(brokerage_start=200_000.0, brokerage_start_basis=40_000.0)
    base = _setup(hh)[2]
    assert base.sale_gain > 0.0
    magi = 150_000.0
    curves = compute_cost_curves([magi], magi, 0.0, hh, year=BASE_YEAR, cpi=hh.cpi_assumption)
    ded = deductions(
        hh.your_age, hh.spouse_age, hh.std_deduction, hh.senior_extra, year=BASE_YEAR, cpi=0.0
    )
    expected, _ = _swept_federal_tax(
        magi,
        hh=hh,
        ded=ded,
        preferential=sweet_spot_ltcg_eligible(hh, base, None),
        tax_exempt=0.0,
        year=BASE_YEAR,
        cpi=0.0,
    )
    assert curves.fed_tax_vals[0] == pytest.approx(expected, abs=0.01)
    # The sale gain is net investment income too, as on Sweet Spot.
    assert curves.auto_nii == pytest.approx(base.net_investment_income_addl, abs=0.01)


# --------------------------------------------------------------------------
# Item 3: LTCG-cost attribution matches run_scenario's conversion_ltcg_cost
# --------------------------------------------------------------------------


@pytest.mark.parametrize("conv", [60_000.0, 100_000.0, 150_000.0])
def test_sale_year_ltcg_delta_matches_conversion_ltcg_cost(conv: float) -> None:
    hh = _big_sale_hh()
    zero = _scenario_year(hh, 0.0)
    oracle = _scenario_year(hh, conv)
    assert zero.forced_brokerage_draw > 0.0
    assert oracle.forced_brokerage_draw > zero.forced_brokerage_draw + 1_000.0
    # The extra sale's own gain tax is in brokerage_gain_tax, not the attribution.
    gain_tax_delta = oracle.brokerage_gain_tax - zero.brokerage_gain_tax
    assert gain_tax_delta > oracle.conversion_ltcg_cost + 100.0
    priced = _priced(hh, conv)
    assert priced.ltcg_delta == pytest.approx(oracle.conversion_ltcg_cost, abs=TOL)
    # The rest of the change in brokerage_gain_tax is the forced sale's own
    # gain tax, still a cost of converting and reported on its own component.
    assert priced.sale_gain_tax == pytest.approx(
        gain_tax_delta - oracle.conversion_ltcg_cost, abs=TOL
    )


def _year_cost(yr: YearResult) -> float:
    """Every cost the all-in claims: federal tax (incl. the 10% penalty and any
    ACA clawback), the brokerage-gain tax, NIIT, the ACA subsidy loss and IRMAA."""
    return yr.federal_tax_amt + yr.brokerage_gain_tax + yr.niit_cost + yr.aca_loss + yr.irmaa_cost


@pytest.mark.parametrize(
    ("make_hh", "conv"),
    [
        (_big_sale_hh, 60_000.0),
        (_big_sale_hh, 100_000.0),
        (_big_sale_hh, 150_000.0),
        (_slack_hh, 100_000.0),
        (_slack_hh, 120_000.0),
    ],
)
def test_sale_year_all_in_matches_run_scenario_total_cost(
    make_hh: Callable[[], Household], conv: float
) -> None:
    # The true oracle: what converting adds to the year's whole tax bill. No
    # IRMAA in these years (under 65, or MAGI below tier 1).
    hh = make_hh()
    zero = _scenario_year(hh, 0.0)
    oracle = _scenario_year(hh, conv)
    assert oracle.irmaa_cost == zero.irmaa_cost == 0.0
    expected = _year_cost(oracle) - _year_cost(zero)
    assert _priced(hh, conv).all_in == pytest.approx(expected, abs=TOL)
