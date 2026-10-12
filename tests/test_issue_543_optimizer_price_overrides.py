"""Issue #543: the exercise optimizer ignored the user's saved per-year price
overrides, and Apply saved its projected prices as if they were overrides.

All households here are synthetic.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from engine.exercise_optimizer import optimize_exercises
from engine.scenario_types import ConversionPlan
from models.exercise_schedule import ExerciseSchedule, explicit_price_overrides
from models.grants import StockGrant
from models.household import Household

_OVERRIDE_YEAR = 2030
_OVERRIDE_PRICE = 150.0  # base price is 200, so the projection is far above it


def _grant() -> StockGrant:
    return StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")


def _household(with_override: bool) -> Household:
    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        stock_price_now=200.0,
        grants=[_grant()],
    )
    if with_override:
        hh.exercise_schedule = ExerciseSchedule(
            shares_by_grant_year={_grant().key(): {_OVERRIDE_YEAR: 5000}},
            price_by_year={_OVERRIDE_YEAR: _OVERRIDE_PRICE},
        )
    return hh


def _optimizer_candidates(with_override: bool):
    result = optimize_exercises(_household(with_override), end_age=75)
    return [c for c in result.candidates if c.ceiling_label != "current"]


def _override_every_year(hh: Household) -> None:
    hh.exercise_schedule = ExerciseSchedule(
        shares_by_grant_year={_grant().key(): {_OVERRIDE_YEAR: 5000}},
        price_by_year=dict.fromkeys(range(2026, _OVERRIDE_YEAR + 1), _OVERRIDE_PRICE),
    )


def test_543_optimizer_prices_candidates_with_saved_override() -> None:
    hh = _household(with_override=False)
    _override_every_year(hh)
    result = optimize_exercises(hh, end_age=75)
    for cand in result.candidates:
        if cand.ceiling_label == "current":
            continue
        assert cand.schedule.price_by_year  # the candidate exercises somewhere
        for price in cand.schedule.price_by_year.values():
            assert price == pytest.approx(_OVERRIDE_PRICE)


def test_543_override_changes_the_scored_cost() -> None:
    plain = optimize_exercises(_household(with_override=False), end_age=75)
    hh = _household(with_override=False)
    _override_every_year(hh)
    over = optimize_exercises(hh, end_age=75)
    by_plain = {c.ceiling_label: c.lifetime_all_in for c in plain.candidates}
    by_over = {c.ceiling_label: c.lifetime_all_in for c in over.candidates}
    assert by_over["top-of-22"] != pytest.approx(by_plain["top-of-22"])


def test_543_without_override_uses_projection() -> None:
    hh = _household(with_override=False)
    for cand in _optimizer_candidates(with_override=False):
        assert cand.schedule.price_by_year
        for year, price in cand.schedule.price_by_year.items():
            assert price == pytest.approx(hh.projected_stock_price(year))


def test_543_explicit_price_overrides_drops_projection_equal_prices() -> None:
    def projected(year: int) -> float:
        return 100.0 + year - 2026

    kept = explicit_price_overrides(
        {2026: 100.0, 2027: 101.004, 2028: 150.0, 2029: 102.006}, projected
    )
    assert kept == {2028: 150.0, 2029: 102.006}


def _render_persistent_hh() -> None:
    import streamlit as st

    from models.grants import StockGrant
    from models.household import Household
    from views.optimizer_panel import render_exercise_half

    if "_test_hh" not in st.session_state:
        st.session_state["_test_hh"] = Household(
            your_age=60,
            spouse_age=54,
            base_year=2026,
            your_ira=1_000_000,
            spouse_ira=1_000_000,
            stock_price_now=200.0,
            grants=[
                StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")
            ],
        )
    render_exercise_half(st.session_state["_test_hh"])


def test_543_apply_keeps_override_and_drops_projected_prices(monkeypatch) -> None:
    saved: list[ExerciseSchedule] = []
    monkeypatch.setattr("views.optimizer_panel.save_exercise_schedule", saved.append)
    at = AppTest.from_function(_render_persistent_hh, default_timeout=60)
    at.run()
    at.session_state["_test_hh"].exercise_schedule = ExerciseSchedule(
        shares_by_grant_year={_grant().key(): {_OVERRIDE_YEAR: 5000}},
        price_by_year={_OVERRIDE_YEAR: _OVERRIDE_PRICE},
    )
    at.run()
    [b for b in at.button if b.label == "Run optimizer"][0].click()
    at.run()
    [b for b in at.button if b.label == "Apply exercises"][0].click()
    at.run()

    assert not at.exception
    assert len(saved) == 1
    assert saved[0].price_by_year == {_OVERRIDE_YEAR: pytest.approx(_OVERRIDE_PRICE)}
    assert at.session_state["_test_hh"].exercise_schedule.price_by_year == saved[0].price_by_year


def test_543_signature_changes_when_an_override_changes() -> None:
    from views.optimizer_panel import _signature

    hh = _household(with_override=True)
    before = _signature(hh, None, ConversionPlan())
    hh.exercise_schedule.price_by_year[_OVERRIDE_YEAR] = _OVERRIDE_PRICE + 10.0
    assert _signature(hh, None, ConversionPlan()) != before
