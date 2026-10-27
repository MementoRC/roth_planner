"""audit-1026 F-03: the draw-aware Sweet Spot curve funds the base-year YTD-LTCG
stacking tax like run_scenario (which folds ytd_ltcg_tax into federal_tax_amt, so
it raises the cash need and the forced IRA draw).

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.scenario import run_scenario
from engine.scenario_autofill import bracket_fill_conversions
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import (
    BaseIncome,
    base_income_for_year,
    estimate_ltcg_eligible,
    fills_for_year,
    forced_draw_fills,
    sweet_spot_ltcg_eligible,
    zero_conversion_gain_fractions,
    zero_conversion_ira_draws,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import sweep_all_in
from engine.tax import BRACKETS_MFJ
from engine.tax_indexing import index_value as _index_value
from models.household import GrowthProfile, Household
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


# --- audit-1026 F-04 / F-07: fills are solved on the row's own history ----------


def _brokerage_hh(
    *,
    your_age: int,
    spouse_age: int,
    ss: tuple[float, float],
    living: float,
    start: int,
    brokerage: float,
    basis: float,
) -> Household:
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=2026,
        grants=[],
        stock_price_now=0.0,
        stock_price_late=0.0,
        your_ss_fra=ss[0],
        spouse_ss_fra=ss[1],
        your_ss_start_age=start,
        spouse_ss_start_age=start,
        cpi_assumption=0.0,
        ss_cola=0.0,
        growth_rate=0.06,
        brokerage_growth=GrowthProfile(0.06, yield_rate=0.02, qualified_fraction=0.8),
        filing_status="MFJ",
        brokerage_start=brokerage,
        brokerage_start_basis=basis,
        living_expenses=living,
        your_ira=900_000.0,
        spouse_ira=700_000.0,
        your_roth=0.0,
        spouse_roth=0.0,
    )


def _hh_f07() -> Household:
    return _brokerage_hh(
        your_age=66,
        spouse_age=65,
        ss=(3_000.0, 2_000.0),
        living=110_000.0,
        start=65,
        brokerage=300_000.0,
        basis=150_000.0,
    )


def _hh_f04() -> Household:
    return _brokerage_hh(
        your_age=64,
        spouse_age=62,
        ss=(0.0, 0.0),
        living=60_000.0,
        start=70,
        brokerage=200_000.0,
        basis=80_000.0,
    )


def _row_inputs(hh: Household) -> dict[int, tuple[BaseIncome, float, object]]:
    """Per year: the row's base, its LTCG-eligible figure and zero-conversion year,
    built exactly as compute_multi_year_summary does for the table/cards."""
    years = zero_conversion_years(hh)
    draws = {y: r.forced_your_ira_draw + r.forced_spouse_ira_draw for y, r in years.items()}
    gains = zero_conversion_sale_gains(hh, years)
    fractions = zero_conversion_gain_fractions(hh, years)
    out = {}
    for y in years:
        b = base_income_for_year(
            hh,
            y,
            ira_draw=draws[y],
            sale_gain=gains.get(y, 0.0),
            sale_gain_fraction=fractions.get(y, 0.0),
        )
        out[y] = (b, sweet_spot_ltcg_eligible(hh, b, None), years[y])
    return out


def _row_fills(hh: Household) -> dict[int, tuple[float, float]]:
    """(fill_12, fill_22) per year as the Sweet Spot table/cards show them."""
    years = zero_conversion_years(hh)
    draws = {y: r.forced_your_ira_draw + r.forced_spouse_ira_draw for y, r in years.items()}
    gains = zero_conversion_sale_gains(hh, years)
    forced = forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws=draws, sale_gains=gains)
    return {y: fills_for_year(hh, b, forced) for y, (b, _, _) in _row_inputs(hh).items()}


def _ceilings(hh: Household, year: int) -> tuple[float, float]:
    return (
        _index_value(BRACKETS_MFJ[1][0], year, hh.cpi_assumption, round50=True),
        _index_value(BRACKETS_MFJ[2][0], year, hh.cpi_assumption, round50=True),
    )


def _taxable_with_only_this_year_converted(hh: Household, year: int, conv: float) -> float:
    plan = ConversionPlan(your_conversions={year: conv}, spouse_conversions={year: 0.0})
    res = run_scenario(hh, plan, "oracle", end_age=hh.your_age + year - hh.base_year)
    return next(r for r in res.years if r.year == year).taxable_income


def test_f07_sale_gain_year_fill_12_reaches_the_ceiling_the_conversion_provokes() -> None:
    hh = _hh_f07()
    year = hh.base_year
    zero = zero_conversion_years(hh)[year]
    assert zero.forced_your_ira_draw + zero.forced_spouse_ira_draw == 0.0
    assert zero_conversion_sale_gains(hh, {year: zero})[year] > 0.0

    fill_12 = _row_fills(hh)[year][0]
    assert fill_12 > 0.0
    taxable = _taxable_with_only_this_year_converted(hh, year, fill_12)
    assert taxable == pytest.approx(_ceilings(hh, year)[0], abs=5.0)
    assert fill_12 == pytest.approx(bracket_fill_conversions(hh, None, 0.0)[year][0], abs=5.0)


@pytest.mark.parametrize("year", [2028, 2029])
def test_f04_later_year_fill_22_ignores_earlier_years_fills(year: int) -> None:
    hh = _hh_f04()
    fill_22 = _row_fills(hh)[year][1]
    assert fill_22 > 0.0
    taxable = _taxable_with_only_this_year_converted(hh, year, fill_22)
    # Pre-fix this missed the ceiling by ~$106k (2028). The residual here (~$1k)
    # is the curve's, not the fill's: it does not shrink the turnover-realized
    # gain when the extra sale lowers the brokerage balance (the per-year
    # invariant test pins the fill to the curve to $1).
    assert taxable == pytest.approx(_ceilings(hh, year)[1], abs=1_500.0)


def test_fills_land_on_the_ceiling_on_the_draw_aware_curve_every_year() -> None:
    for hh in (_hh_f07(), _hh_f04()):
        fills = _row_fills(hh)
        for year, (base, ltcg, zero_year) in _row_inputs(hh).items():
            for fill, ceiling in zip(fills[year], _ceilings(hh, year), strict=True):
                if fill <= 0.0:
                    continue
                got = sweep_all_in(
                    hh,
                    base,
                    [fill],
                    net_inv_income=0.0,
                    ltcg_eligible=ltcg,
                    zero_year=zero_year,  # type: ignore[arg-type]
                    fills={year: fills[year]},
                )[0]
                assert got.taxable_inc == pytest.approx(ceiling, abs=1.0)


def test_base_year_fills_equal_the_auto_fill_plan() -> None:
    for hh in (_hh_f07(), _hh_f04()):
        year = hh.base_year
        assert _row_fills(hh)[year] == pytest.approx(
            bracket_fill_conversions(hh, None, 0.0)[year], abs=5.0
        )
