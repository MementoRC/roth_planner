"""Audit-1101 W3: Sweet Spot draw gate and IRMAA Part B base (F-04, F-06).

F-04: the fill-provoked-draw gate projected the Fill as YOUR conversion only,
which run_scenario zeroes once you reach your RMD age, so a year where only the
spouse can convert never provoked a draw and was never gated.

F-06: Sweet Spot and the YTD IRMAA warning priced IRMAA on the module-default
Part B premium instead of the household's own ``medicare_part_b_base_monthly``,
as run_scenario does. Synthetic households only.
"""

from __future__ import annotations

import pytest

import engine.sweet_spot_compute as ssc
from engine.irmaa import irmaa_for_year, irmaa_payment_year, irmaa_surcharge
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import (
    all_in_at_conversion,
    base_income_for_year,
    fill_conversions_for_year,
    zero_conversion_gain_fractions,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from models.household import Household
from views.ytd_income._partials._analysis import irmaa_warning_surcharges

BASE_YEAR = 2026


def _spouse_only_hh(brokerage: float) -> Household:
    # You are past your RMD start age (no conversions); the spouse is not.
    return Household(
        your_age=76,
        spouse_age=60,
        base_year=BASE_YEAR,
        cpi_assumption=0.0,
        grants=[],
        brokerage_start=brokerage,
        growth_rate=0.0,
        living_expenses=60_000.0,
        your_ira=300_000.0,
        spouse_ira=900_000.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
    )


def _spouse_provoked_years(hh: Household) -> set[int]:
    """Oracle: years with no zero-conversion draw where the spouse's own Fill to
    22%, alone on the zero-conversion history, provokes a draw or gain sale."""
    zero = zero_conversion_years(hh)
    draws = {y: yr.forced_your_ira_draw + yr.forced_spouse_ira_draw for y, yr in zero.items()}
    gains = zero_conversion_sale_gains(hh, zero)
    fractions = zero_conversion_gain_fractions(hh, zero)
    provoked: set[int] = set()
    for year, draw in draws.items():
        if draw > 0.0 or gains.get(year, 0.0) > 0.0:
            continue
        base = base_income_for_year(hh, year, ira_draw=draw, sale_gain=0.0)
        fill = fill_conversions_for_year(hh, base)[1]
        result = run_scenario(
            hh,
            ConversionPlan(spouse_conversions={year: fill}),
            "oracle spouse fill",
            end_age=hh.your_age + (year - hh.base_year),
        )
        yr = result.years[-1]
        if (
            yr.forced_your_ira_draw + yr.forced_spouse_ira_draw > 0.0
            or yr.forced_brokerage_draw * fractions.get(year, 0.0) > 0.0
        ):
            provoked.add(year)
    return provoked


def test_f04_spouse_only_fill_provoked_draw_year_is_gated() -> None:
    for brokerage in range(100_000, 600_000, 10_000):
        hh = _spouse_only_hh(float(brokerage))
        provoked = _spouse_provoked_years(hh)
        if not provoked:
            continue
        zero = zero_conversion_years(hh)
        draws = {y: yr.forced_your_ira_draw + yr.forced_spouse_ira_draw for y, yr in zero.items()}
        gains = zero_conversion_sale_gains(hh, zero)
        got = ssc._fill_provoked_draw_years(hh, None, 0.0, draws, gains)
        assert provoked <= got, f"brokerage={brokerage}: missing {sorted(provoked - got)}"
        return
    raise AssertionError("no brokerage size gives a spouse-only provoked-draw year")


def _medicare_hh(part_b_monthly: float) -> Household:
    return Household(
        your_age=66,
        spouse_age=66,
        base_year=BASE_YEAR,
        cpi_assumption=0.0,
        grants=[],
        brokerage_start=2_000_000.0,
        your_ira=1_500_000.0,
        spouse_ira=1_500_000.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        medicare_part_b_base_monthly=part_b_monthly,
    )


def test_f06_sweet_spot_irmaa_uses_household_part_b_base() -> None:
    hh = _medicare_hh(300.0)
    base = base_income_for_year(hh, BASE_YEAR)
    conv = 400_000.0
    result = all_in_at_conversion(hh, base, conv, 0.0)
    year = irmaa_payment_year(BASE_YEAR)

    def _cost(magi: float, part_b: float | None) -> float:
        kwargs = {} if part_b is None else {"base_part_b": part_b}
        cost, _ = irmaa_for_year(
            magi, base.ya, base.sa, filing_status="MFJ", year=year, cpi=0.0, **kwargs
        )
        return cost

    expected = _cost(result.magi, 3_600.0) - _cost(base.base_magi, 3_600.0)
    default = _cost(result.magi, None) - _cost(base.base_magi, None)
    assert expected != pytest.approx(default)  # the premium base actually matters here
    assert result.irmaa_delta == pytest.approx(expected)


def test_f06_ytd_irmaa_warning_uses_household_part_b_base() -> None:
    hh = _medicare_hh(300.0)
    magi = 500_000.0
    s1, s2 = irmaa_warning_surcharges(hh, magi)
    common = {"filing_status": "MFJ", "year": BASE_YEAR + 2, "cpi": 0.0, "base_part_b": 3_600.0}
    assert s1 == pytest.approx(irmaa_surcharge(magi, 1, **common))
    assert s2 == pytest.approx(irmaa_surcharge(magi, 2, **common))
    assert s1 != pytest.approx(
        irmaa_surcharge(magi, 1, filing_status="MFJ", year=BASE_YEAR + 2, cpi=0.0)
    )
