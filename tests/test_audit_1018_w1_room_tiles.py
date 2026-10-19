"""audit-1018 W1 (F-02, F-03, F-06): the bracket rooms land on the bracket top.

F-02: compute_headroom's rooms held the OBBBA senior bonus at its
      pre-conversion size, so a 65+ couple's room overshot the bracket top.
F-03/F-06 (owner decision D1): the Room This Year / YTD-page 12% / 22% tiles
      always equal what "Fill to X%" converts (full-year projection basis),
      in fill-provoked-draw years and in no-draw years with forecast income.

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.headroom import compute_headroom
from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_12, auto_fill_22, bracket_fill_conversions
from engine.scenario_types import ConversionPlan, YearResult
from engine.tax import indexed_bracket_ceiling
from engine.this_year import draw_aware_bracket_rooms
from models.household import Household
from models.ytd_income import YTDSnapshot
from views.room_this_year import BRACKET_TILE_HELP, TILES_LABEL
from views.ytd_income._partials._analysis import ytd_bracket_rooms

YEAR = 2026
TOL = 100.0


def _hh(your_age: int, spouse_age: int, **kwargs: float) -> Household:
    fields: dict[str, object] = {
        "brokerage_start": 3_000_000.0,
        "brok_turnover": 0.0,  # no forecast realized gains: actuals == projection basis
        "living_expenses": 60_000.0,
        "your_ira": 1_000_000.0,
        "spouse_ira": 1_000_000.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
    }
    fields.update(kwargs)
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=YEAR,
        cpi_assumption=0.0,
        grants=[],
        **fields,  # type: ignore[arg-type]
    )


def _ceiling(level: int) -> float:
    return indexed_bracket_ceiling(level, year=YEAR, cpi=0.0, filing_status="MFJ")


def _year_at(hh: Household, conversion: float) -> YearResult:
    plan = ConversionPlan(your_conversions={YEAR: conversion}, spouse_conversions={YEAR: 0.0})
    return run_scenario(hh, plan, "t", end_age=95).years[0]


def _fill_total(plan: ConversionPlan) -> float:
    return plan.your_conversions.get(YEAR, 0.0) + plan.spouse_conversions.get(YEAR, 0.0)


# --------------------------------------------------------------------------
# F-02: senior-bonus phase-out per converted dollar
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("attr", "level"), [("room_to_12pct", 1), ("room_to_22pct", 2)])
def test_headroom_room_lands_on_bracket_top_with_senior_bonus(attr: str, level: int) -> None:
    hh = _hh(66, 66)
    room = getattr(compute_headroom(hh, YTDSnapshot(tax_year=YEAR), "MFJ"), attr)
    yr = _year_at(hh, room)
    assert yr.your_conversion == pytest.approx(room, abs=1.0)  # not trimmed by a cap
    assert yr.taxable_income == pytest.approx(_ceiling(level), abs=TOL)


@pytest.mark.parametrize(("fill", "level"), [(auto_fill_12, 1), (auto_fill_22, 2)])
def test_fill_lands_on_bracket_top_with_senior_bonus(fill, level: int) -> None:  # type: ignore[no-untyped-def]
    hh = _hh(66, 66)
    yr = _year_at(hh, _fill_total(fill(hh, None)))
    assert yr.taxable_income == pytest.approx(_ceiling(level), abs=TOL)


def test_headroom_room_matches_fill_with_senior_bonus() -> None:
    hh = _hh(66, 66)
    headroom = compute_headroom(hh, YTDSnapshot(tax_year=YEAR), "MFJ")
    f12, f22 = bracket_fill_conversions(hh, None)[YEAR]
    assert headroom.room_to_12pct == pytest.approx(f12, abs=TOL)
    assert headroom.room_to_22pct == pytest.approx(f22, abs=TOL)


def test_headroom_room_unchanged_without_senior_bonus() -> None:
    """Control: ages 61/55 have no bonus, so the room is the plain bracket room."""
    hh = _hh(61, 55)
    room = compute_headroom(hh, YTDSnapshot(tax_year=YEAR), "MFJ").room_to_22pct
    assert _year_at(hh, room).taxable_income == pytest.approx(_ceiling(2), abs=TOL)


# --------------------------------------------------------------------------
# F-03: fill-provoked draw year
# --------------------------------------------------------------------------


def test_tiles_equal_fill_in_fill_provoked_draw_year() -> None:
    hh = _hh(61, 55, brokerage_start=70_000.0, your_ira=900_000.0, spouse_ira=700_000.0)
    f12, f22 = bracket_fill_conversions(hh, None)[YEAR]
    r12, r22 = draw_aware_bracket_rooms(hh, None)
    assert r12 == pytest.approx(f12, abs=0.01)
    assert r22 == pytest.approx(f22, abs=0.01)
    headroom = compute_headroom(hh, YTDSnapshot(tax_year=YEAR), "MFJ")
    assert headroom.room_to_22pct > f22 + 5_000.0  # the draw-blind room overstates


# --------------------------------------------------------------------------
# F-06: no-draw year with forecast RMD
# --------------------------------------------------------------------------


def test_tiles_equal_fill_in_no_draw_year_with_forecast_rmd() -> None:
    hh = _hh(75, 70, your_ira=2_000_000.0, spouse_ira=500_000.0)
    ytd = YTDSnapshot(tax_year=YEAR)
    f12, f22 = bracket_fill_conversions(hh, ytd)[YEAR]
    headroom = compute_headroom(hh, ytd, "MFJ")
    assert headroom.room_to_22pct > f22 + 50_000.0  # actuals-only ignores the RMD
    assert draw_aware_bracket_rooms(hh, ytd) == pytest.approx((f12, f22), abs=0.01)
    assert ytd_bracket_rooms(hh, ytd) == pytest.approx((f12, f22), abs=0.01)


def test_tile_copy_describes_the_fill_basis() -> None:
    assert "Fill" in BRACKET_TILE_HELP
    assert "full-year projection" in TILES_LABEL
