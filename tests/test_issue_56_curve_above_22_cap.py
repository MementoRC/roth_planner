"""Issue #56: the Sweet Spot curve vs run_scenario above the 22% conversion cap.

In a shortfall year run_scenario caps the conversion at the zero-conversion
22% room (``_base_headroom``, draw-blind by owner decision, PR #2). The
draw-aware curve sweeps past it on purpose: it shows what converting C would
cost in the 24% / 32% / 35% brackets.

1. Every amount the page states as a conversion -- the Fill to 12% / 22% and
   IRMAA-Safe cards, and the draw-blind Fill the fill-provoked gate projects --
   sits under the cap, so run_scenario converts it in full. The curve, the
   upper bracket / IRMAA / NIIT guide-lines and the Cost Jump Points are
   points on the hypothetical curve, not a plan run_scenario is given.
2. ``ConversionPlan(bracket_cap=False)`` projects an explicit conversion above
   the cap; the curve matches it per year at 250k-400k fills.

Synthetic households only. Oracle (PR #53): all-in = change in run_scenario's
federal tax (penalty and clawback included) + brokerage gain tax + NIIT + ACA
loss + IRMAA (lookback convention: this year's MAGI priced at the payment
year); MAGI and ltcg_delta within $25.
"""

from __future__ import annotations

import pytest

from engine.irmaa import IRMAA_TIERS_MFJ, _index_irmaa_tiers
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, ScenarioResult
from engine.sweet_spot_compute import (
    base_income_for_year,
    fill_conversions_for_year,
    fills_for_year,
    sweet_spot_ltcg_eligible,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import draw_aware_irmaa_safe_max
from models.household import Household
from tests.test_issue_1_room_matches_fill import _forced_draw_hh
from tests.test_issue_52_sweet_spot_sale_gain_leftovers import (
    BASE_YEAR,
    _big_sale_hh,
    _priced,
    _setup,
)
from tests.test_issue_54_same_year_irmaa import _HOUSEHOLDS, _year_cost

TOL = 25.0
_ABOVE_CAP = [250_000.0, 300_000.0, 400_000.0]
# "ira-draw": under 65, no brokerage; spending is an IRA draw, partly penalized.
_ALL_HOUSEHOLDS = {**_HOUSEHOLDS, "big-sale": _big_sale_hh, "ira-draw": _forced_draw_hh}


def _run(hh: Household, conv: float, *, bracket_cap: bool = True) -> ScenarioResult:
    plan = ConversionPlan(
        your_conversions={BASE_YEAR: conv},
        spouse_conversions={BASE_YEAR: 0.0},
        bracket_cap=bracket_cap,
    )
    return run_scenario(hh, plan, "oracle", end_age=95)


# --------------------------------------------------------------------------
# 1. No stated conversion amount is one run_scenario would cap
# --------------------------------------------------------------------------


def _card_amounts(hh: Household) -> dict[str, float]:
    years, fills, base = _setup(hh)
    fill_12, fill_22 = fills_for_year(hh, base, fills)
    tier1 = _index_irmaa_tiers(IRMAA_TIERS_MFJ, BASE_YEAR + 2, hh.cpi_assumption)[0][0]
    irmaa_safe = draw_aware_irmaa_safe_max(
        hh,
        base,
        tier1,
        0.0,
        sweet_spot_ltcg_eligible(hh, base, None),
        zero_year=years[BASE_YEAR],
        fills=fills,
    )
    return {"fill_12": fill_12, "fill_22": fill_22, "irmaa_safe": irmaa_safe}


@pytest.mark.parametrize("name", sorted(_ALL_HOUSEHOLDS))
def test_issue_56_card_amounts_are_converted_in_full(name: str) -> None:
    hh = _ALL_HOUSEHOLDS[name]()
    for card, amount in _card_amounts(hh).items():
        if amount <= 0.0:
            continue
        converted = _run(hh, amount).years[0].your_conversion
        assert converted == pytest.approx(amount, abs=1.0), card


@pytest.mark.parametrize("name", sorted(_ALL_HOUSEHOLDS))
def test_issue_56_fill_provoked_gate_plan_is_converted_in_full(name: str) -> None:
    # The gate (_fill_provoked_draw_years) projects the draw-blind Fill to 22%
    # of every window year; a capped year would gate on a smaller conversion.
    hh = _ALL_HOUSEHOLDS[name]()
    years = zero_conversion_years(hh)
    gains = zero_conversion_sale_gains(hh, years)
    plan = {
        y: fill_conversions_for_year(
            hh,
            base_income_for_year(
                hh,
                y,
                ira_draw=yr.forced_your_ira_draw + yr.forced_spouse_ira_draw,
                sale_gain=gains.get(y, 0.0),
            ),
        )[1]
        for y, yr in years.items()
    }
    end_age = hh.your_age + len(years) - 1
    gate = run_scenario(hh, ConversionPlan(your_conversions=plan), "gate", end_age=end_age)
    uncapped = run_scenario(
        hh, ConversionPlan(your_conversions=plan, bracket_cap=False), "gate", end_age=end_age
    )
    # Equal to the uncapped projection: the cap never binds. (A large Fill can
    # still exhaust the IRA in a later year; that limit is not the cap.)
    assert [yr.your_conversion for yr in gate.years] == pytest.approx(
        [yr.your_conversion for yr in uncapped.years], abs=1.0
    )


# --------------------------------------------------------------------------
# 2. bracket_cap=False: an explicit conversion above the cap, for the oracle
# --------------------------------------------------------------------------


@pytest.mark.parametrize("conv", _ABOVE_CAP)
@pytest.mark.parametrize("name", sorted(_ALL_HOUSEHOLDS))
def test_issue_56_cap_binds_and_opt_out_converts_in_full(name: str, conv: float) -> None:
    hh = _ALL_HOUSEHOLDS[name]()
    capped = _run(hh, conv).years[0]
    assert capped.your_conversion < conv - 1_000.0  # the cap binds here
    uncapped = _run(hh, conv, bracket_cap=False).years[0]
    assert uncapped.your_conversion == pytest.approx(conv)


def test_issue_56_opt_out_is_inert_under_the_cap() -> None:
    hh = _ALL_HOUSEHOLDS["slack"]()
    default = _run(hh, 150_000.0).years
    uncapped = _run(hh, 150_000.0, bracket_cap=False).years
    assert [y.__dict__ for y in uncapped] == [y.__dict__ for y in default]


@pytest.mark.parametrize("conv", _ABOVE_CAP)
@pytest.mark.parametrize("name", sorted(_ALL_HOUSEHOLDS))
def test_issue_56_curve_matches_uncapped_run_scenario(name: str, conv: float) -> None:
    hh = _ALL_HOUSEHOLDS[name]()
    zero = _run(hh, 0.0).years[0]
    oracle = _run(hh, conv, bracket_cap=False).years[0]
    assert oracle.your_conversion == pytest.approx(conv)
    priced = _priced(hh, conv)
    assert priced.magi == pytest.approx(oracle.magi, abs=TOL)
    assert priced.ltcg_delta == pytest.approx(oracle.conversion_ltcg_cost, abs=TOL)
    expected = _year_cost(hh, oracle) - _year_cost(hh, zero)
    assert priced.all_in == pytest.approx(expected, abs=TOL)
