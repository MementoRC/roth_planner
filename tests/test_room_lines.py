"""Room lines (Income Control slice 1): thresholds + applicability. Synthetic data only."""

from __future__ import annotations

import pytest

from engine.aca import aca_ceiling_magi
from engine.irmaa import irmaa_tier1_threshold
from engine.niit import niit_threshold
from engine.room_lines import (
    LINE_KEYS,
    RoomLines,
    living_flags,
    living_persons,
    room_after,
    room_lines_for_year,
)
from engine.scenario_types import YearResult
from engine.tax import LTCG_THRESHOLDS_MFJ, LTCG_THRESHOLDS_SINGLE, indexed_bracket_ceiling
from engine.tax_indexing import index_tuple
from models.household import Household, SurvivorScenario

CPI = 0.025


def _hh(**kw) -> Household:
    base = {
        "base_year": 2026,
        "your_age": 61,
        "spouse_age": 58,
        "filing_status": "MFJ",
        "cpi_assumption": CPI,
        "your_aca_enrolled": True,
        "spouse_aca_enrolled": False,
    }
    base.update(kw)
    return Household(**base)


def _yr(**kw) -> YearResult:
    base = {
        "year": 2030,
        "your_age": 64,
        "spouse_age": 61,
        "phase": "clean",
        "filing_status": "MFJ",
    }
    base.update(kw)
    return YearResult(**base)


def test_line_keys_order_is_stable() -> None:
    assert LINE_KEYS == ("top_12", "top_22", "top_24", "ltcg_zero", "irmaa_t1", "aca_cliff", "niit")


@pytest.mark.parametrize("year", [2026, 2030, 2035])
@pytest.mark.parametrize("status", ["MFJ", "Single"])
def test_threshold_lines_match_the_engine_helpers(year: int, status: str) -> None:
    hh = _hh()
    lines = room_lines_for_year(hh, _yr(year=year, filing_status=status))
    assert lines.year == year
    assert lines.filing_status == status
    for idx, value in ((1, lines.top_12), (2, lines.top_22), (3, lines.top_24)):
        assert value == indexed_bracket_ceiling(idx, year=year, cpi=CPI, filing_status=status)
    assert lines.irmaa_t1 == irmaa_tier1_threshold(year, status, CPI)
    assert lines.aca_cliff == aca_ceiling_magi(status, year, CPI)
    assert lines.niit == niit_threshold(status)


@pytest.mark.parametrize("year", [2026, 2031])
@pytest.mark.parametrize("status", ["MFJ", "Single"])
def test_ltcg_zero_matches_the_scenario_open_coded_formula(year: int, status: str) -> None:
    # Drift guard: engine/scenario.py:1135-1138 indexes the 0% LTCG threshold this way.
    base = LTCG_THRESHOLDS_SINGLE if status == "Single" else LTCG_THRESHOLDS_MFJ
    expected = index_tuple(base, year, CPI, round50=True)[0]
    lines = room_lines_for_year(_hh(), _yr(year=year, filing_status=status))
    assert lines.ltcg_zero == expected


def test_ltcg_zero_base_year_values_are_the_statutory_constants() -> None:
    hh = _hh(cpi_assumption=0.0)
    assert room_lines_for_year(hh, _yr(year=2026, filing_status="MFJ")).ltcg_zero == 98_900
    assert room_lines_for_year(hh, _yr(year=2026, filing_status="Single")).ltcg_zero == 49_450


def test_irmaa_is_none_when_nobody_is_63() -> None:
    hh = _hh(your_age=55, spouse_age=52)
    lines = room_lines_for_year(hh, _yr(your_age=62, spouse_age=59))
    assert lines.irmaa_t1 is None


def test_irmaa_applies_when_either_person_is_63() -> None:
    lines = room_lines_for_year(_hh(), _yr(your_age=60, spouse_age=63))
    assert lines.irmaa_t1 is not None


def test_aca_is_none_when_nobody_enrolled() -> None:
    hh = _hh(your_aca_enrolled=False, spouse_aca_enrolled=False)
    assert room_lines_for_year(hh, _yr()).aca_cliff is None


def test_aca_is_none_when_enrolled_person_is_65_or_older() -> None:
    hh = _hh(your_aca_enrolled=True, spouse_aca_enrolled=False)
    lines = room_lines_for_year(hh, _yr(your_age=65, spouse_age=60))
    assert lines.aca_cliff is None  # spouse is young but not enrolled


def test_single_household_ignores_the_spouse() -> None:
    hh = _hh(filing_status="Single", your_age=60, spouse_age=70, spouse_aca_enrolled=True)
    yr = _yr(your_age=60, spouse_age=70, filing_status="Single")
    assert living_persons(hh, yr) == ((60, True),)
    assert room_lines_for_year(hh, yr).irmaa_t1 is None  # spouse's 70 must not count


def test_survivor_year_when_spouse_dies_keeps_only_you() -> None:
    hh = _hh(
        survivor=SurvivorScenario(who_dies="spouse", death_year=2028),
        your_aca_enrolled=False,
        spouse_aca_enrolled=True,
    )
    # The engine does NOT zero spouse_age in survivor years (scenario.py:170-188).
    yr = _yr(year=2030, your_age=66, spouse_age=60, filing_status="Single")
    assert living_persons(hh, yr) == ((66, False),)
    lines = room_lines_for_year(hh, yr)
    assert lines.aca_cliff is None  # only the deceased spouse was enrolled
    assert lines.irmaa_t1 == irmaa_tier1_threshold(2030, "Single", CPI)
    assert lines.top_12 == indexed_bracket_ceiling(1, year=2030, cpi=CPI, filing_status="Single")


def test_survivor_year_when_you_die_keeps_only_the_spouse() -> None:
    hh = _hh(
        survivor=SurvivorScenario(who_dies="you", death_year=2028),
        your_aca_enrolled=False,
        spouse_aca_enrolled=True,
    )
    yr = _yr(year=2030, your_age=66, spouse_age=60, filing_status="Single")
    assert living_persons(hh, yr) == ((60, True),)
    lines = room_lines_for_year(hh, yr)
    assert lines.irmaa_t1 is None  # survivor is 60; the deceased's 66 must not count
    assert lines.aca_cliff == aca_ceiling_magi("Single", 2030, CPI)


def test_year_before_the_survivor_transition_still_counts_both() -> None:
    hh = _hh(survivor=SurvivorScenario(who_dies="spouse", death_year=2030))
    yr = _yr(year=2030, filing_status="MFJ")  # death year itself still files MFJ
    assert len(living_persons(hh, yr)) == 2


def test_living_flags_cover_every_case() -> None:
    mfj = _yr(filing_status="MFJ")
    single = _yr(filing_status="Single")
    assert living_flags(_hh(), mfj) == (True, True)
    assert living_flags(_hh(filing_status="Single"), single) == (True, False)
    spouse_dies = _hh(survivor=SurvivorScenario(who_dies="spouse", death_year=2028))
    you_die = _hh(survivor=SurvivorScenario(who_dies="you", death_year=2028))
    assert living_flags(spouse_dies, single) == (True, False)
    assert living_flags(you_die, single) == (False, True)
    assert living_flags(you_die, mfj) == (True, True)


def test_room_after_is_line_minus_measure_with_negative_and_none() -> None:
    lines = RoomLines(
        year=2030,
        filing_status="MFJ",
        top_12=100_000.0,
        top_22=200_000.0,
        top_24=300_000.0,
        ltcg_zero=90_000.0,
        irmaa_t1=250_000.0,
        aca_cliff=None,
        niit=250_000.0,
    )
    yr = _yr(
        taxable_income=120_000.0,
        brokerage_qual_div=10_000.0,
        magi=260_000.0,
        aca_magi=999_999.0,  # must be ignored: the ACA line is not applicable
        niit_magi=200_000.0,
    )
    assert room_after(yr, lines) == {
        "top_12": -20_000.0,
        "top_22": 80_000.0,
        "top_24": 180_000.0,
        "ltcg_zero": 90_000.0 - (120_000.0 + 10_000.0),
        "irmaa_t1": -10_000.0,
        "aca_cliff": None,
        "niit": 50_000.0,
    }


def test_room_after_uses_aca_magi_for_the_aca_line() -> None:
    lines = RoomLines(2030, "MFJ", 1.0, 2.0, 3.0, 4.0, None, 80_000.0, 5.0)
    yr = _yr(aca_magi=75_000.0, magi=1.0)
    assert room_after(yr, lines)["aca_cliff"] == 5_000.0
