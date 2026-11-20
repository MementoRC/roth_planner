"""Conversion tax paid from cash outside the accounts (income_tax_paid_from_outside_cash).

Synthetic figures only. The flag removes the conversion's own tax (yr.conversion_tax)
from the year's cash need, so it no longer forces a brokerage sale whose realized
gain would use up 0% LTCG room.
"""

from __future__ import annotations

import pytest

from engine.income_control import build_income_control
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from models.household import GrowthProfile, Household

YEAR = 2026
CONVERSION = 60_000.0
BROKERAGE = 400_000.0
BASIS = 100_000.0


def _hh(*, outside: bool, basis: float | None = BASIS) -> Household:
    return Household(
        your_age=61,
        spouse_age=61,
        filing_status="MFJ",
        base_year=YEAR,
        cpi_assumption=0.0,
        your_ira=1_000_000.0,
        spouse_ira=0.0,
        your_roth=0.0,
        spouse_roth=0.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
        grants=[],
        brokerage_start=BROKERAGE,
        brokerage_start_basis=basis,
        brokerage_growth=GrowthProfile(default_rate=0.0, yield_rate=0.0),
        brok_turnover=0.0,
        living_expenses=0.0,
        your_aca_enrolled=False,
        spouse_aca_enrolled=False,
        income_tax_paid_from_outside_cash=outside,
    )


def _plan() -> ConversionPlan:
    return ConversionPlan(your_conversions={YEAR: CONVERSION}, bracket_cap=False)


def _year(outside: bool):
    result = run_scenario(_hh(outside=outside), _plan(), "t", end_age=62)
    return next(y for y in result.years if y.year == YEAR)


def test_tax_paid_outside_default_is_off() -> None:
    assert Household().income_tax_paid_from_outside_cash is False


def test_tax_paid_outside_off_conversion_tax_is_funded_by_a_brokerage_sale() -> None:
    yr = _year(outside=False)
    assert yr.conversion_tax > 0
    # Regression: the whole conversion tax (no other tax) is the cash need.
    assert yr.income_needed == pytest.approx(yr.federal_tax_amt + yr.niit_cost)
    assert yr.forced_brokerage_draw > 0
    sold_gain = yr.forced_brokerage_draw * (1 - BASIS / BROKERAGE)
    assert yr.realized_gains == pytest.approx(sold_gain)


def test_tax_paid_outside_on_removes_the_funding_sale() -> None:
    yr = _year(outside=True)
    assert yr.conversion_tax > 0
    assert yr.income_needed == pytest.approx(0.0)
    assert yr.forced_brokerage_draw == pytest.approx(0.0)
    assert yr.realized_gains == pytest.approx(0.0)


def test_tax_paid_outside_on_does_not_change_the_tax_itself() -> None:
    on, off = _year(outside=True), _year(outside=False)
    assert on.conversion_tax == pytest.approx(off.conversion_tax)


def _harvest(outside: bool, basis: float | None):
    row = build_income_control(_hh(outside=outside, basis=basis), _plan(), plan_label="t").years[0]
    assert row.harvest is not None
    return row


def test_tax_paid_outside_on_harvest_equals_room_without_funding_reduction() -> None:
    on = _harvest(True, BASIS)
    off = _harvest(False, BASIS)
    assert on.harvest.amount == pytest.approx(on.harvest.room_uncapped)
    assert on.harvest.amount > off.harvest.amount


def test_tax_paid_outside_on_embedded_gain_cap_not_reduced_by_a_funding_sale() -> None:
    # Embedded gain well below the room: the cap binds at balance - basis, with no sale taken out.
    basis = BROKERAGE - 5_000.0
    row = _harvest(True, basis)
    assert row.year_result.realized_gains == pytest.approx(0.0)
    assert row.harvest.binding == "embedded gain"
    assert row.harvest.amount == pytest.approx(BROKERAGE - basis)
