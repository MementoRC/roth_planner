"""Follow-up to audit-1026 F-04/F-07: the fill-provoked-draw gate is per year.

A year is gated into the draw solver only if ITS OWN Fill (applied on the
zero-conversion history, no earlier fills) provokes a forced IRA draw or a
gain-realizing brokerage sale in that year -- not because earlier years' fills
drained the brokerage. Synthetic households only.
"""

from __future__ import annotations

import engine.sweet_spot_compute as ssc
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from engine.sweet_spot_compute import (
    base_income_for_year,
    fill_conversions_for_year,
    zero_conversion_gain_fractions,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from models.household import Household
from tests.test_issue_1_room_matches_fill import BASE_YEAR, _hh


def _household() -> Household:
    # Zero conversion lasts several years on the brokerage; a Fill's tax added
    # to spending exhausts it earlier when every earlier year was filled too.
    return _hh(brokerage_start=450_000.0, growth_rate=0.0)


def _inputs(hh: Household) -> tuple[dict[int, float], dict[int, float]]:
    years = zero_conversion_years(hh)
    draws = {y: yr.forced_your_ira_draw + yr.forced_spouse_ira_draw for y, yr in years.items()}
    return draws, zero_conversion_sale_gains(hh, years)


def _fill_22(hh: Household, year: int, draws: dict[int, float], gains: dict[int, float]) -> float:
    base = base_income_for_year(hh, year, ira_draw=draws[year], sale_gain=gains.get(year, 0.0))
    return fill_conversions_for_year(hh, base)[1]


def _cumulative_flagged(
    hh: Household, draws: dict[int, float], gains: dict[int, float]
) -> set[int]:
    """The pre-fix gate: every year filled in one multi-year projection."""
    conversions = {y: _fill_22(hh, y, draws, gains) for y in draws}
    result = run_scenario(
        hh,
        ConversionPlan(your_conversions=conversions),
        "oracle cumulative",
        end_age=hh.your_age + len(draws) - 1,
    )
    projected = {yr.year: yr for yr in result.years}
    fractions = zero_conversion_gain_fractions(hh, projected)
    return {
        y
        for y, yr in projected.items()
        if yr.forced_your_ira_draw + yr.forced_spouse_ira_draw > 0.0
        or yr.forced_brokerage_draw * fractions.get(y, 0.0) > 0.0
    }


def _isolated_flagged(hh: Household, draws: dict[int, float], gains: dict[int, float]) -> set[int]:
    """Year Y flagged iff Y's own Fill, alone, provokes a draw in Y."""
    flagged: set[int] = set()
    zero = zero_conversion_years(hh)
    fractions = zero_conversion_gain_fractions(hh, zero)
    for year in draws:
        result = run_scenario(
            hh,
            ConversionPlan(your_conversions={year: _fill_22(hh, year, draws, gains)}),
            "oracle isolated",
            end_age=hh.your_age + (year - hh.base_year),
        )
        yr = result.years[-1]
        if (
            yr.forced_your_ira_draw + yr.forced_spouse_ira_draw > 0.0
            or yr.forced_brokerage_draw * fractions.get(year, 0.0) > 0.0
        ):
            flagged.add(year)
    return flagged


def test_earlier_years_fills_do_not_gate_a_later_year() -> None:
    hh = _household()
    draws, gains = _inputs(hh)
    assert BASE_YEAR in draws
    old = _cumulative_flagged(hh, draws, gains)
    own = _isolated_flagged(hh, draws, gains)
    # Precondition: the cumulative history flags a year its own fill does not.
    spurious = old - own
    assert spurious

    got = ssc._fill_provoked_draw_years(hh, None, 0.0, draws, gains)

    assert got.isdisjoint(spurious)
    assert got == own


def test_a_year_whose_own_fill_provokes_a_draw_is_still_flagged() -> None:
    # Scan brokerage sizes for a year with no zero-conversion draw whose own
    # fill (tax added to spending) exhausts the brokerage and so draws the IRA.
    for brokerage in range(300_000, 700_000, 5_000):
        hh = _hh(brokerage_start=float(brokerage), growth_rate=0.0)
        draws, gains = _inputs(hh)
        provoked = {y for y in _isolated_flagged(hh, draws, gains) if draws[y] == 0.0}
        if provoked:
            assert provoked <= ssc._fill_provoked_draw_years(hh, None, 0.0, draws, gains)
            return
    raise AssertionError("no brokerage size gives a provoked-draw year")
