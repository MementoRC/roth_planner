"""Issue #54: a conversion's IRMAA lands only at Y+2, never in Y as well.

With no lookback MAGI for year Y (no prior_year_magi, Y-2 before the
projection), run_scenario estimated year Y's IRMAA from year Y's own MAGI,
conversion and the sale/draw it forces included. The conversion then paid
IRMAA in Y (a same-year cash need, funded by an extra sale) and again at Y+2
through magi_history. The estimate is now the year's zero-conversion MAGI.

Synthetic households only. The draw-aware Sweet Spot curve is checked against
run_scenario per year: all-in = change in federal tax (penalty and clawback
included) + brokerage gain tax + NIIT + ACA loss + IRMAA (lookback
convention: this year's MAGI priced at the payment year).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from engine.irmaa import irmaa_for_year, irmaa_payment_year
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult
from models.household import Household
from tests.test_issue_52_sweet_spot_sale_gain_leftovers import BASE_YEAR, _priced, _slack_hh

TOL = 25.0
_LOOKBACK = {BASE_YEAR - 2: 300_000.0, BASE_YEAR - 1: 300_000.0}


def _sale_hh() -> Household:
    """Medicare-age couple whose spending is funded by selling 80%-gain
    brokerage at zero conversion."""
    return replace(
        _slack_hh(),
        brokerage_start=1_000_000.0,
        brokerage_start_basis=200_000.0,
        living_expenses=150_000.0,
    )


def _with_lookback(make: Callable[[], Household]) -> Callable[[], Household]:
    def _make() -> Household:
        return replace(make(), prior_year_magi=dict(_LOOKBACK))

    return _make


_HOUSEHOLDS = {
    "slack": _slack_hh,
    "slack-lookback": _with_lookback(_slack_hh),
    "sale": _sale_hh,
    "sale-lookback": _with_lookback(_sale_hh),
}


def _run(hh: Household, conv: float) -> ScenarioResult:
    plan = ConversionPlan(your_conversions={BASE_YEAR: conv}, spouse_conversions={BASE_YEAR: 0.0})
    return run_scenario(hh, plan, "oracle", end_age=95)


def _lookback_irmaa(hh: Household, magi: float) -> float:
    cost, _ = irmaa_for_year(
        magi,
        hh.your_age,
        hh.spouse_age,
        base_part_b=hh.medicare_part_b_base_monthly * 12,
        filing_status=hh.filing_status,
        year=irmaa_payment_year(BASE_YEAR),
        cpi=0.0,
    )
    return cost


# --------------------------------------------------------------------------
# run_scenario: no same-year IRMAA from the conversion
# --------------------------------------------------------------------------


@pytest.mark.parametrize("make_hh", [_slack_hh, _sale_hh])
def test_conversion_irmaa_lands_once_at_y_plus_2_without_lookback(
    make_hh: Callable[[], Household],
) -> None:
    hh = make_hh()
    assert not hh.prior_year_magi
    zero = _run(hh, 0.0).years
    conv = _run(hh, 160_000.0).years
    expected = _lookback_irmaa(hh, conv[0].magi) - _lookback_irmaa(hh, zero[0].magi)
    assert expected > 1_000.0  # the conversion crosses an IRMAA tier
    # Year Y: the premium is the zero-conversion estimate, unchanged.
    assert conv[0].irmaa_cost == pytest.approx(zero[0].irmaa_cost, abs=0.01)
    # Year Y+2: the conversion's IRMAA, through magi_history.
    assert conv[2].irmaa_cost - zero[2].irmaa_cost == pytest.approx(expected, abs=0.01)


def test_lookback_magi_still_wins() -> None:
    hh = _with_lookback(_slack_hh)()
    conv = _run(hh, 160_000.0).years[0]
    anchored, _ = irmaa_for_year(
        _LOOKBACK[BASE_YEAR - 2],
        hh.your_age - 2,
        hh.spouse_age - 2,
        base_part_b=hh.medicare_part_b_base_monthly * 12,
        filing_status=hh.filing_status,
        year=BASE_YEAR,
        cpi=0.0,
    )
    assert anchored > 0.0
    assert conv.irmaa_cost == pytest.approx(anchored, abs=0.01)


# --------------------------------------------------------------------------
# Draw-aware curve vs run_scenario
# --------------------------------------------------------------------------


def _year_cost(hh: Household, yr: YearResult) -> float:
    return (
        yr.federal_tax_amt
        + yr.brokerage_gain_tax
        + yr.niit_cost
        + yr.aca_loss
        + _lookback_irmaa(hh, yr.magi)
    )


# Fills stay under run_scenario's _base_headroom cap (22% ceiling, ~$200k+).
@pytest.mark.parametrize("conv", [100_000.0, 120_000.0, 160_000.0, 200_000.0])
@pytest.mark.parametrize("name", sorted(_HOUSEHOLDS))
def test_curve_matches_run_scenario(name: str, conv: float) -> None:
    hh = _HOUSEHOLDS[name]()
    zero = _run(hh, 0.0).years[0]
    oracle = _run(hh, conv).years[0]
    assert oracle.your_conversion == pytest.approx(conv)  # cap does not bind
    assert oracle.forced_brokerage_draw > zero.forced_brokerage_draw
    priced = _priced(hh, conv)
    assert priced.magi == pytest.approx(oracle.magi, abs=TOL)
    assert priced.ltcg_delta == pytest.approx(oracle.conversion_ltcg_cost, abs=TOL)
    expected = _year_cost(hh, oracle) - _year_cost(hh, zero)
    assert priced.all_in == pytest.approx(expected, abs=TOL)
