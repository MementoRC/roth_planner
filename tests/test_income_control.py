"""Income Control engine (slice 1). Synthetic data only."""

from __future__ import annotations

import pytest

from engine.income_control import window_bounds
from engine.this_year import first_rmd_year
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
