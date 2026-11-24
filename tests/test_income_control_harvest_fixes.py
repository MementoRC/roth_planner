"""#123 slice 3 harvest bug fixes C2-C7 (synthetic figures only)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from engine.income_control import IncomeControlYear, _embedded_gain, build_income_control
from engine.room_lines import HARVEST_BINDINGS, HarvestSizing
from engine.scenario import ProjectionState, project_year_with_draw
from engine.scenario_types import ConversionPlan, YearResult
from models.household import GrowthProfile, Household
from models.ytd_income import (
    YTDSnapshot,
    adjusted_taxable_income,
    capital_loss_carryover,
    capital_loss_limit,
    net_forecast_capital,
    net_year_capital,
)
from tests.test_income_control_view import _result, _year
from views.income_control import (
    HARVEST_PHRASES,
    HARVEST_USED_BY_CONVERSION,
    harvest_notes,
    harvest_text,
    table_rows,
)

_YEAR = 2026


def _household(age: int, **overrides: object) -> Household:
    fields: dict[str, object] = {
        "your_age": age,
        "spouse_age": age,
        "filing_status": "MFJ",
        "base_year": _YEAR,
        "cpi_assumption": 0.0,
        "your_ira": 1_000_000.0,
        "spouse_ira": 0.0,
        "your_roth": 0.0,
        "spouse_roth": 0.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "grants": [],
        "brokerage_start": 1_000_000.0,
        "brokerage_start_basis": None,
        "brokerage_growth": GrowthProfile(default_rate=0.0, yield_rate=0.0),
        "brok_turnover": 0.0,
        "living_expenses": 0.0,
        "your_aca_enrolled": False,
        "spouse_aca_enrolled": False,
        "income_tax_paid_from_outside_cash": True,
    }
    fields.update(overrides)
    return Household(**fields)  # type: ignore[arg-type]


def _row(hh: Household, conversion: float, ytd: YTDSnapshot | None = None) -> IncomeControlYear:
    plan = ConversionPlan(your_conversions={_YEAR: conversion}, bracket_cap=False)
    rows = build_income_control(hh, plan, plan_label="t", ytd=ytd).years
    return next(r for r in rows if r.year == _YEAR)


# --- C2: the no-draw shortfall is a brokerage SALE that realizes and prices its gain ---


def test_c2_no_draw_shortfall_sale_realizes_gain_in_the_same_year() -> None:
    hh = _household(60, your_ira=0.0, brokerage_start_basis=200_000.0, living_expenses=200_000.0)
    plan = ConversionPlan(your_conversions={}, bracket_cap=False)
    yr = project_year_with_draw(0, hh, plan, None, 0.0, ProjectionState.initial(hh), None).yr
    assert yr.forced_brokerage_draw == pytest.approx(200_000.0)
    assert yr.realized_gains == pytest.approx(160_000.0)  # 200,000 x (1 - 0.2)
    assert yr.magi == pytest.approx(160_000.0)
    # (160,000 - 32,200) preferential: 98,900 at 0%, 28,900 at 15%.
    assert yr.federal_tax_amt + yr.brokerage_gain_tax == pytest.approx(4_335.0, abs=1.0)
    assert yr.brokerage_basis == pytest.approx(160_000.0)
    assert yr.unfunded_need == 0.0


def test_c2_sale_is_resized_when_its_gain_raises_the_need() -> None:
    # The sale's gain lifts MAGI over the NIIT threshold; NIIT is a cash need.
    hh = _household(60, your_ira=0.0, brokerage_start_basis=100_000.0, living_expenses=400_000.0)
    plan = ConversionPlan(your_conversions={}, bracket_cap=False)
    yr = project_year_with_draw(0, hh, plan, None, 0.0, ProjectionState.initial(hh), None).yr
    assert yr.niit_cost > 0.0
    assert yr.income_needed == pytest.approx(400_000.0 + yr.niit_cost, abs=1.0)
    assert yr.forced_brokerage_draw == pytest.approx(yr.income_needed, abs=1.0)
    assert yr.realized_gains == pytest.approx(yr.forced_brokerage_draw * 0.9, abs=1.0)


def test_c2_no_shortfall_keeps_the_no_draw_year() -> None:
    hh = _household(60, brokerage_start_basis=200_000.0)
    plan = ConversionPlan(your_conversions={}, bracket_cap=False)
    yr = project_year_with_draw(0, hh, plan, None, 0.0, ProjectionState.initial(hh), None).yr
    assert yr.forced_brokerage_draw == 0.0
    assert yr.realized_gains == 0.0


# --- C3: the displayed harvest never exceeds the engine amount ---


def test_c3_harvest_display_floors_to_whole_dollars() -> None:
    harvest = HarvestSizing(71_099.6, "0% LTCG", 71_099.6, True)
    assert "up to $71,099 " in harvest_text(harvest)
    row = replace(_year(_YEAR, ltcg_zero=1.0), harvest=harvest)
    assert table_rows(_result(row))[0]["0% LTCG harvest"] == "$71,099"


def test_c6_view_note_reads_the_engine_room_without_conversion() -> None:
    zero = HarvestSizing(0.0, "0% LTCG", 0.0, True)
    used = replace(_year(_YEAR, ltcg_zero=-320.0), harvest=zero, ltcg_room_without_conversion=400.0)
    full = replace(used, ltcg_room_without_conversion=-50.0)
    assert harvest_notes(used) == [HARVEST_USED_BY_CONVERSION]
    assert harvest_notes(full) == []


# --- C4: a net capital loss is netted before the harvest is priced ---


def test_c4_loss_year_harvest_stops_where_the_ordinary_offset_is_forfeited() -> None:
    hh = _household(60)
    ytd = YTDSnapshot(tax_year=_YEAR, ltcg_ytd=-10_000.0)
    harvest = _row(hh, 60_000.0, ytd).harvest
    assert harvest is not None
    # The first 7,000 only shrink the disallowed loss; the $3,000 offset survives.
    assert harvest.amount == pytest.approx(7_000.0, abs=1.0)
    assert harvest.binding == "capital loss offset"


def test_c4_no_loss_year_is_unchanged() -> None:
    harvest = _row(_household(60), 60_000.0).harvest
    assert harvest is not None
    assert harvest.amount == pytest.approx(98_900.0 - (60_000.0 - 32_200.0), abs=1.0)
    assert harvest.binding == "0% LTCG"


# --- C5: the embedded-gain cap is the year-END unrealized gain ---


def test_c5_embedded_gain_is_closing_balance_less_closing_basis() -> None:
    yr = YearResult(year=_YEAR, your_age=60, spouse_age=60, phase="clean")
    yr.brokerage_balance = 1_000_000.0
    yr.realized_gains = 10_000.0
    yr.brokerage_balance_end = 1_050_000.0
    yr.brokerage_basis = 1_000_000.0
    assert _embedded_gain(yr, 990_000.0, None) == pytest.approx(50_000.0)
    assert _embedded_gain(yr, None, None) is None
    ytd = YTDSnapshot(tax_year=_YEAR, ltcg_ytd=5_000.0)
    assert _embedded_gain(yr, 990_000.0, ytd) == pytest.approx(45_000.0)


def test_c5_growth_year_harvest_is_capped_at_the_year_end_gain() -> None:
    hh = _household(
        60,
        brokerage_start_basis=990_000.0,
        brokerage_growth=GrowthProfile(default_rate=0.05, yield_rate=0.0),
        brok_turnover=0.2,
    )
    row = _row(hh, 60_000.0)
    assert row.harvest is not None
    yr = row.year_result
    assert row.harvest.amount == pytest.approx(yr.brokerage_balance_end - yr.brokerage_basis)
    assert row.harvest.amount == pytest.approx(50_000.0, abs=1.0)
    assert row.harvest.binding == "embedded gain"


# --- C6: the conversion's room is measured from its actual effect ---


def test_c6_room_without_conversion_restores_the_senior_bonus_it_cost() -> None:
    hh = _household(70)
    ytd = YTDSnapshot(tax_year=_YEAR, ira_distributions_ytd=146_000.0)
    row = _row(hh, 10_000.0, ytd)
    room = row.rooms["ltcg_zero"]
    assert room is not None
    assert room == pytest.approx(-10_320.0, abs=1.0)  # costs 10,720 > 10,000 face
    assert row.ltcg_room_without_conversion == pytest.approx(400.0, abs=1.0)
    assert HARVEST_USED_BY_CONVERSION in harvest_notes(row)


def test_c6_no_conversion_no_counterfactual() -> None:
    assert _row(_household(60), 0.0).ltcg_room_without_conversion is None


def _funded_from_brokerage(conversion_tax_outside: bool) -> Household:
    return _household(
        60,
        brokerage_start_basis=200_000.0,
        living_expenses=40_000.0,
        income_tax_paid_from_outside_cash=conversion_tax_outside,
    )


def test_c9_room_without_conversion_drops_the_conversions_funding_sale() -> None:
    hh = _funded_from_brokerage(False)
    row = _row(hh, 60_000.0)
    rerun = _row(hh, 0.0)
    assert row.year_result.conversion_funding_gain > 0.0
    assert row.ltcg_room_without_conversion == pytest.approx(rerun.rooms["ltcg_zero"], abs=1.0)


def test_c9_no_funding_gain_when_the_tax_is_paid_from_outside_cash() -> None:
    hh = _funded_from_brokerage(True)
    row = _row(hh, 60_000.0)
    assert row.year_result.conversion_funding_gain == 0.0
    assert row.ltcg_room_without_conversion == pytest.approx(
        _row(hh, 0.0).rooms["ltcg_zero"], abs=1.0
    )


# --- C7: a shrinking senior deduction has its own label ---


def test_c7_senior_phase_out_is_not_labelled_social_security() -> None:
    hh = _household(66)
    hh.std_deduction = 90_000.0
    ytd = YTDSnapshot(tax_year=_YEAR, ira_distributions_ytd=140_000.0)
    row = _row(hh, 0.0, ytd)
    harvest = row.harvest
    assert harvest is not None
    assert harvest.binding == "Senior deduction phase-out"
    assert row.year_result.magi_phaseout_basis + harvest.amount == pytest.approx(150_000.0, abs=1.0)
    assert harvest_text(harvest).endswith("the point where the senior deduction starts phasing out")


def test_c7_binding_order_puts_ordinary_causes_after_ltcg_and_before_cliffs() -> None:
    order = list(HARVEST_BINDINGS)
    assert order[:4] == [
        "0% LTCG",
        "Social Security tax",
        "Senior deduction phase-out",
        "capital loss offset",
    ]
    assert set(HARVEST_BINDINGS) <= set(HARVEST_PHRASES)


# --- C8: forecast-year net capital loss, IRC §1211(b) limit and §1212(b) carryover ---


def test_c8_limit_helper_is_filing_status_aware() -> None:
    assert capital_loss_limit("MFJ") == 3_000.0
    assert capital_loss_limit("Single") == 3_000.0
    assert capital_loss_limit("MFS") == 1_500.0


def test_c8_a_gain_year_is_unaffected() -> None:
    assert net_forecast_capital(19_000.0, 0.0, "MFJ") == (19_000.0, 0.0)
    assert capital_loss_carryover(0.0, 50_000.0, "MFJ") == 0.0


def test_c8_a_loss_smaller_than_the_limit_is_fully_used() -> None:
    in_agi, loss = net_forecast_capital(-2_000.0, 0.0, "MFJ")
    assert (in_agi, loss) == (-2_000.0, 2_000.0)
    assert capital_loss_carryover(loss, 50_000.0, "MFJ") == 0.0


def test_c8_the_excess_over_the_limit_carries_over_and_nets_against_the_next_gain() -> None:
    in_agi, loss = net_forecast_capital(-10_000.0, 0.0, "MFJ")
    assert in_agi == -3_000.0
    carried = capital_loss_carryover(loss, 50_000.0, "MFJ")
    assert carried == 7_000.0
    assert net_forecast_capital(19_000.0, carried, "MFJ") == (12_000.0, 0.0)
    # A carryover larger than the next gain is a loss of that year, capped again.
    assert net_forecast_capital(1_000.0, carried, "MFJ") == (-3_000.0, 6_000.0)


def test_c8_mfs_allows_only_half_the_limit() -> None:
    assert net_forecast_capital(-10_000.0, 0.0, "MFS") == (-1_500.0, 10_000.0)
    assert capital_loss_carryover(10_000.0, 50_000.0, "MFS") == 8_500.0


def test_c8_low_adjusted_taxable_income_uses_less_than_the_limit() -> None:
    assert capital_loss_carryover(10_000.0, 500.0, "MFJ") == 9_500.0
    assert capital_loss_carryover(10_000.0, -2_500.0, "MFJ") == 10_000.0  # negative ATI uses none
    assert capital_loss_carryover(2_000.0, 500.0, "MFJ") == 1_500.0


def _first_year(hh: Household, conversion: float, ytd: YTDSnapshot | None = None):  # type: ignore[no-untyped-def]
    plan = ConversionPlan(your_conversions={_YEAR: conversion}, bracket_cap=False)
    return project_year_with_draw(0, hh, plan, ytd, 0.0, ProjectionState.initial(hh), None)


def _loss_household(**overrides: object) -> Household:
    return _household(
        55,
        brok_turnover=0.2,
        brokerage_growth=GrowthProfile(default_rate=-0.05, yield_rate=0.002),
        **overrides,
    )


def test_c8e_qualified_dividends_count_in_ati() -> None:
    # 32,700 + 2,000 qualified dividends - 32,200 = 2,500 before the 3,000 loss:
    # taxable income -500, ATI 2,500, used 2,500, carryover 7,500.
    out = _first_year(_loss_household(), 32_700.0)
    assert out.yr.brokerage_qual_div == pytest.approx(2_000.0)
    # Reinvested dividends lift the balance, so the loss is a little over 10,000.
    assert out.capital_loss_carryover == pytest.approx(-out.yr.realized_gains - 2_500.0)


def test_c8e_ati_adds_back_the_allowed_loss_and_may_be_negative() -> None:
    assert adjusted_taxable_income(-500.0, 10_000.0, "MFJ") == 2_500.0
    assert adjusted_taxable_income(-500.0, 10_000.0, "MFS") == 1_000.0
    assert adjusted_taxable_income(-500.0, 1_000.0, "MFJ") == 500.0
    assert capital_loss_carryover(10_000.0, 2_500.0, "MFJ") == 7_500.0


def test_c8e_negative_ati_uses_nothing_and_carries_the_whole_loss() -> None:
    ati = adjusted_taxable_income(-8_000.0, 10_000.0, "MFJ")
    assert ati == -5_000.0
    assert capital_loss_carryover(10_000.0, ati, "MFJ") == 10_000.0


def test_c8f_ytd_loss_over_the_limit_carries_to_next_year() -> None:
    ytd = YTDSnapshot(tax_year=_YEAR, wages_ytd=100_000.0, ltcg_ytd=-10_000.0)
    out = _first_year(_household(55), 0.0, ytd)
    assert out.capital_loss_carryover == pytest.approx(7_000.0)
    assert out.yr.combined_gross == pytest.approx(97_000.0)


def test_c8f_ytd_loss_under_the_limit_carries_nothing() -> None:
    ytd = YTDSnapshot(tax_year=_YEAR, wages_ytd=100_000.0, ltcg_ytd=-2_000.0)
    assert _first_year(_household(55), 0.0, ytd).capital_loss_carryover == 0.0


def test_c8f_ytd_net_gain_is_unchanged() -> None:
    ytd = YTDSnapshot(tax_year=_YEAR, wages_ytd=100_000.0, ltcg_ytd=10_000.0)
    out = _first_year(_household(55), 0.0, ytd)
    assert out.capital_loss_carryover == 0.0
    assert ytd.net_capital_loss_ytd == 0.0


def test_c8f_the_ytd_carryover_nets_against_the_next_years_gain() -> None:
    hh = _household(
        55,
        brok_turnover=0.2,
        brokerage_growth=GrowthProfile(default_rate=0.10, yield_rate=0.0),
    )
    plan = ConversionPlan(your_conversions={}, bracket_cap=False)
    ytd = YTDSnapshot(tax_year=_YEAR, wages_ytd=100_000.0, ltcg_ytd=-10_000.0)
    state = ProjectionState.initial(hh)
    first = project_year_with_draw(0, hh, plan, ytd, 0.0, state, None)
    state.advance(first)
    second = project_year_with_draw(1, hh, plan, ytd, 0.0, state, None)
    assert second.yr.realized_gains > 7_000.0
    assert second.yr.magi == pytest.approx(second.yr.realized_gains - 7_000.0, abs=1.0)


def test_c8_the_projection_carries_the_excess_loss_through_the_state() -> None:
    hh = _household(
        55,
        brok_turnover=0.2,
        brokerage_growth=GrowthProfile(default_rate=-0.05, yield_rate=0.0),
    )
    plan = ConversionPlan(your_conversions={_YEAR: 100_000.0}, bracket_cap=False)
    state = ProjectionState.initial(hh)
    first = project_year_with_draw(0, hh, plan, None, 0.0, state, None)
    assert first.yr.realized_gains == pytest.approx(-10_000.0)
    assert first.capital_loss_carryover == pytest.approx(7_000.0)
    state.advance(first)
    assert state.capital_loss_carryover == pytest.approx(7_000.0)
    assert state.copy().capital_loss_carryover == pytest.approx(7_000.0)
    # Only the allowed $3,000 reduces ordinary income and MAGI: 100,000 - 3,000.
    assert first.yr.combined_gross == pytest.approx(97_000.0)
    assert first.yr.magi == pytest.approx(97_000.0)


# --- C8g: the base year's YTD actuals and the forced sale's gain net into ONE figure ---


def _sale_household(**overrides: object) -> Household:
    """Age 55, no IRA: a living-expense shortfall forces a brokerage sale at a 40% gain."""
    return _household(
        55,
        your_ira=0.0,
        brokerage_start=1_000_000.0,
        brokerage_start_basis=600_000.0,
        living_expenses=70_000.0,
        **overrides,
    )


def test_c8g_ytd_loss_and_forced_sale_gain_net_to_one_figure() -> None:
    ytd = YTDSnapshot(tax_year=_YEAR, wages_ytd=60_000.0, ltcg_ytd=-10_000.0)
    out = _first_year(_sale_household(), 0.0, ytd)
    gain = out.yr.realized_gains
    assert 0.0 < gain < 10_000.0
    # One net loss (gain - 10,000): $3,000 allowed against ordinary income, rest carried.
    assert out.yr.magi - 60_000.0 == pytest.approx(-3_000.0)
    assert out.yr.combined_gross == pytest.approx(57_000.0)
    assert out.capital_loss_carryover == pytest.approx(10_000.0 - gain - 3_000.0)
    assert out.yr.brokerage_gain_tax == 0.0


def test_c8g_forced_sale_gain_over_the_ytd_loss_is_only_the_net_gain() -> None:
    ytd = YTDSnapshot(tax_year=_YEAR, wages_ytd=60_000.0, ltcg_ytd=-500.0)
    out = _first_year(_sale_household(), 0.0, ytd)
    gain = out.yr.realized_gains
    assert gain > 500.0
    assert out.yr.magi - 60_000.0 == pytest.approx(gain - 500.0)
    assert out.yr.combined_gross == pytest.approx(60_000.0)
    assert out.capital_loss_carryover == 0.0


def test_c8g_ytd_net_gain_and_carry_in_loss_net_together() -> None:
    hh = _household(55)
    plan = ConversionPlan(your_conversions={}, bracket_cap=False)
    state = ProjectionState.initial(hh)
    state.capital_loss_carryover = 4_000.0
    ytd = YTDSnapshot(tax_year=_YEAR, wages_ytd=60_000.0, ltcg_ytd=10_000.0)
    out = project_year_with_draw(0, hh, plan, ytd, 0.0, state, None)
    assert out.yr.magi - 60_000.0 == pytest.approx(6_000.0)
    assert out.yr.combined_gross == pytest.approx(60_000.0)
    assert out.capital_loss_carryover == 0.0
    assert out.yr.ytd_ltcg_tax >= 0.0


def test_c8g_mfs_ytd_loss_uses_the_half_limit_in_the_ordinary_part_and_carryover() -> None:
    # The scenario does not model MFS, so the one netting helper is exercised directly:
    # a YTD long-term loss plus a sale gain nets once, at the $1,500 MFS limit.
    ordinary, preferential, net_loss = net_year_capital(0.0, -10_000.0 + 3_740.0, 0.0, "MFS")
    assert (ordinary, preferential, net_loss) == (-1_500.0, 0.0, 6_260.0)
    ati = adjusted_taxable_income(20_000.0, net_loss, "MFS")
    assert ati == 21_500.0
    assert capital_loss_carryover(net_loss, ati, "MFS") == pytest.approx(4_760.0)
    # Short- and long-term losses cross-net into the same single limit.
    assert net_year_capital(-2_000.0, -500.0, 0.0, "MFS")[0] == -1_500.0
