"""The optimizer's aca-safe ceiling applies only in pre-Medicare years.

Mirrors engine.scenario_autofill.auto_fill_aca (room: IRMAA tier-1 once nobody
is under 65) and engine.scenario._strategy_magi_ceiling (constraint: lifted).
"""

from __future__ import annotations

import pytest

from engine.aca import aca_ceiling_magi
from engine.exercise_optimizer import _base_projection, _ceiling_income_by_year, optimize_exercises
from engine.irmaa import irmaa_tier1_threshold
from engine.scenario_autofill import auto_fill_aca
from models.grants import StockGrant
from models.household import Household

_ACA_ONLY = [("aca-safe", "aca-safe", auto_fill_aca)]


def _hh(your_age: int, spouse_age: int, filing_status: str = "MFJ") -> Household:
    grant = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2030, grant_id="g1")
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        filing_status=filing_status,
        base_year=2026,
        grants=[grant],
        stock_price_now=150.0,
    )


def _ceilings(hh: Household) -> tuple[dict[int, float], dict[int, float], dict[int, str]]:
    _base, wedge, deds, fs = _base_projection(hh)
    return _ceiling_income_by_year(hh, "aca-safe", deds, wedge, fs), wedge, fs


def _aca_capped(hh: Household, year: int) -> bool:
    ceilings, wedge, fs = _ceilings(hh)
    aca = aca_ceiling_magi(fs[year], year, hh.cpi_assumption) - wedge[year]
    return ceilings[year] == pytest.approx(aca)


def test_single_67_not_flagged_over_ceiling_and_not_aca_capped() -> None:
    hh = _hh(67, 0, "Single")
    result = optimize_exercises(hh, ceilings=_ACA_ONLY)
    assert result.candidates[0].over_ceiling_years == []
    ceilings, _wedge, _fs = _ceilings(hh)
    assert all(not _aca_capped(hh, y) for y in ceilings)


def test_mfj_both_65_plus_not_flagged_and_not_aca_capped() -> None:
    hh = _hh(66, 65)
    result = optimize_exercises(hh, ceilings=_ACA_ONLY)
    assert result.candidates[0].over_ceiling_years == []
    ceilings, _wedge, _fs = _ceilings(hh)
    assert all(not _aca_capped(hh, y) for y in ceilings)


def test_post_medicare_year_falls_back_to_irmaa_tier1() -> None:
    hh = _hh(67, 0, "Single")
    ceilings, wedge, fs = _ceilings(hh)
    for year, value in ceilings.items():
        tier1 = irmaa_tier1_threshold(year, fs[year], hh.cpi_assumption)
        assert value == pytest.approx(tier1 - wedge[year])


def test_mfj_62_and_66_aca_ceiling_until_younger_turns_65() -> None:
    hh = _hh(66, 62)  # younger person turns 65 in 2029
    ceilings, _wedge, _fs = _ceilings(hh)
    for year in ceilings:
        if year <= 2028:
            assert _aca_capped(hh, year), year
        else:
            assert not _aca_capped(hh, year), year


def test_under_65_household_still_aca_capped_while_pre_medicare() -> None:
    hh = _hh(50, 48)
    ceilings, _wedge, _fs = _ceilings(hh)
    for year in ceilings:
        if hh.your_age + (year - 2026) < 65:
            assert _aca_capped(hh, year), year
