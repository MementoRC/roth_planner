"""audit-1026 F-01: the aca_safe Medicare-age gate counts only living, present people.

A Single filer has spouse_age == 0, and the old gate projected that phantom
spouse forward as 1, 2, 3... (so "pre-Medicare" forever). A deceased spouse's
age likewise kept the ACA cliff on after the survivor was 65+.
"""

from __future__ import annotations

import pytest

from engine.aca import aca_ceiling_magi, present_household_ages
from engine.scenario import _strategy_magi_ceiling, run_scenario
from engine.scenario_autofill import auto_fill_aca, auto_fill_irmaa_safe
from engine.scenario_types import ConversionPlan
from models.household import Household, SurvivorScenario


def _single(age: int) -> Household:
    return Household(
        filing_status="Single",
        your_age=age,
        your_ira=2_000_000,
        spouse_age=0,
        spouse_ira=0,
        spouse_roth=0,
        spouse_ss_fra=0.0,
    )


def _ceiling_for(hh: Household, year: int) -> float | None:
    ya, sa = present_household_ages(hh, year)
    return _strategy_magi_ceiling("aca_safe", "Single", year, hh.cpi_assumption, ya, sa)


def test_f01_aca_gate_single_phantom_spouse_does_not_pin_cliff() -> None:
    hh = _single(67)
    for year in (hh.base_year + 1, hh.base_year + 2, hh.base_year + 3):
        assert _ceiling_for(hh, year) is None


def test_f01_aca_gate_single_run_scenario_not_capped_at_cliff() -> None:
    hh = _single(67)
    year = hh.base_year + 1
    plan = ConversionPlan(your_conversions={year: 90_000.0}, magi_strategy="aca_safe")
    res = run_scenario(hh, plan)
    yr = next(y for y in res.years if y.year == year)
    cliff = aca_ceiling_magi("Single", year, hh.cpi_assumption)
    assert yr.your_conversion == pytest.approx(90_000.0)
    assert yr.aca_magi > cliff


def test_f01_aca_gate_single_auto_fill_later_years_use_tier1_fallback() -> None:
    hh = _single(66)
    plan = auto_fill_aca(hh)
    irmaa = auto_fill_irmaa_safe(hh)
    # Past 65 the aca_safe room IS the IRMAA tier-1 room, every year.
    for year in (hh.base_year, hh.base_year + 1, hh.base_year + 2, hh.base_year + 3):
        assert plan.your_conversions.get(year, 0.0) == pytest.approx(
            irmaa.your_conversions.get(year, 0.0)
        ), year


def _survivor_hh() -> Household:
    return Household(
        filing_status="MFJ",
        your_age=66,
        spouse_age=58,
        your_ira=2_000_000,
        spouse_ira=500_000,
        survivor=SurvivorScenario(who_dies="spouse", death_year=2026),
    )


def test_f01_aca_gate_survivor_years_ignore_dead_spouse_age() -> None:
    hh = _survivor_hh()
    assert present_household_ages(hh, 2026)[1] > 0  # death year itself is still MFJ
    for year in (2027, 2028):
        ya, sa = present_household_ages(hh, year)
        assert sa == 0
        assert _strategy_magi_ceiling("aca_safe", "Single", year, hh.cpi_assumption, ya, sa) is None


def test_f01_aca_gate_survivor_is_you_dead_survivor_spouse_pre65_keeps_cap() -> None:
    hh = Household(
        filing_status="MFJ",
        your_age=70,
        spouse_age=58,
        survivor=SurvivorScenario(who_dies="you", death_year=2026),
    )
    ya, sa = present_household_ages(hh, 2027)
    assert ya == 0
    assert sa == 59
    assert _strategy_magi_ceiling("aca_safe", "Single", 2027, hh.cpi_assumption, ya, sa) is not None


def test_f01_aca_gate_control_mfj_both_alive_pre65_spouse_keeps_cap() -> None:
    hh = Household(filing_status="MFJ", your_age=67, spouse_age=58, your_ira=2_000_000)
    year = hh.base_year + 1
    ya, sa = present_household_ages(hh, year)
    assert (ya, sa) == (68, 59)
    ceiling = _strategy_magi_ceiling("aca_safe", "MFJ", year, hh.cpi_assumption, ya, sa)
    assert ceiling == pytest.approx(aca_ceiling_magi("MFJ", year, hh.cpi_assumption))
