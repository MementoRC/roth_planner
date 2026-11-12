"""Income Control engine (slice 1). Synthetic data only."""

from __future__ import annotations

import pytest

from engine.income_control import forced_exercise, window_bounds
from engine.this_year import first_rmd_year
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household


def _hh(**kw) -> Household:
    base = {
        "base_year": 2026,
        "your_age": 61,
        "spouse_age": 58,
        "filing_status": "MFJ",
        "your_ira": 800_000.0,
        "spouse_ira": 600_000.0,
        "cpi_assumption": 0.025,
        "stock_price_now": 150.0,
        "grants": [],
    }
    base.update(kw)
    return Household(**base)


def _grant(grant_id: str, shares: int, expiry: int, strike: float = 100.0) -> StockGrant:
    return StockGrant(
        year=2020, strike=strike, shares=shares, expiry_year=expiry, grant_id=grant_id
    )


def test_window_without_grants_ends_at_first_rmd_year() -> None:
    hh = _hh()
    assert window_bounds(hh) == (2026, first_rmd_year(hh))


def test_window_extends_to_a_grant_expiring_after_the_first_rmd_year() -> None:
    hh0 = _hh()
    late = first_rmd_year(hh0) + 3
    hh = _hh(grants=[_grant("g1", 100, 2030), _grant("g2", 100, late)])
    assert window_bounds(hh) == (2026, late)


def test_window_uses_first_rmd_year_when_grants_expire_earlier() -> None:
    hh = _hh(grants=[_grant("g1", 100, 2030)])
    assert window_bounds(hh) == (2026, first_rmd_year(hh))


def test_window_can_be_empty_when_end_precedes_start(monkeypatch: pytest.MonkeyPatch) -> None:
    import engine.income_control as ic

    monkeypatch.setattr(ic, "first_rmd_year", lambda hh: 2025)
    assert ic.window_bounds(_hh()) == (2026, 2025)


def _schedule_hh() -> Household:
    """g1 (1000 sh, expires 2028): 400 in 2027, 600 in 2028.
    g2 (500 sh, expires 2031): 100 in 2028. Spread = 200 - 100 = 100/share."""
    sched = ExerciseSchedule(
        shares_by_grant_year={"g1": {2027: 400, 2028: 600}, "g2": {2028: 100}},
        price_by_year=dict.fromkeys(range(2026, 2033), 200.0),
    )
    return _hh(
        grants=[_grant("g1", 1000, 2028), _grant("g2", 500, 2031)],
        exercise_schedule=sched,
    )


def test_forced_exercise_counts_only_the_expiring_grant_in_its_expiry_year() -> None:
    hh = _schedule_hh()
    assert forced_exercise(hh, 2027, hh.option_income(2027)) == 0.0  # g1 exercised early
    assert forced_exercise(hh, 2028, hh.option_income(2028)) == 60_000.0  # g1's last 600 sh
    assert hh.option_income(2028) == 70_000.0  # plus g2's 100 sh, which is discretionary


def test_forced_exercise_is_zero_without_grants() -> None:
    hh = _hh()
    assert forced_exercise(hh, 2028, 0.0) == 0.0


def test_forced_exercise_is_clamped_to_option_income() -> None:
    hh = _schedule_hh()
    assert forced_exercise(hh, 2028, 1_000.0) == 1_000.0  # forced 60k cannot exceed the year's 1k
    assert forced_exercise(hh, 2028, 0.0) == 0.0
