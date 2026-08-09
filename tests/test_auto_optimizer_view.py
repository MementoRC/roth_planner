"""Smoke test for views/auto_optimizer.py — Exercise Auto-Optimizer page.

Uses ``streamlit.testing.v1.AppTest.from_function`` (extracts the function's
source and re-runs it as a standalone script), so the script function must be
fully self-contained — all imports and object construction live inside its
body, mirroring the pattern documented in
``streamlit.testing.v1.app_test.AppTest.from_function``.
"""

from streamlit.testing.v1 import AppTest


def _render_two_grants() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.auto_optimizer import render

    grant1 = StockGrant(year=2019, strike=100.0, shares=1000, expiry_year=2028, grant_id="g1")
    grant2 = StockGrant(year=2020, strike=130.0, shares=500, expiry_year=2029, grant_id="g2")
    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=500_000,
        spouse_ira=500_000,
        txn_price_now=150.0,
        grants=[grant1, grant2],
    )
    render(hh)


def test_auto_optimizer_view_smoke_renders_without_exception() -> None:
    at = AppTest.from_function(_render_two_grants)
    at.run()
    assert not at.exception


def _render_spreadable_household() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.auto_optimizer import render

    hh = Household(
        your_age=61,
        spouse_age=55,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        txn_price_now=200.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")],
    )
    render(hh)


def _click(at: AppTest, label: str) -> None:
    next(b for b in at.button if b.label == label).click()
    at.run()


def _spreadable_app() -> AppTest:
    """Build the spreadable-household AppTest with a generous timeout.

    Running the optimizer (grid solve across ceilings) plus a rerun can
    exceed Streamlit's 3s default under full-suite CPU contention; these
    tests assert on behavior/state, not wall-clock, so give real headroom.
    """
    return AppTest.from_function(_render_spreadable_household, default_timeout=60)


def test_run_optimizer_caches_result_and_renders_without_exception() -> None:
    at = _spreadable_app()
    at.run()
    _click(at, "Run optimizer")

    assert not at.exception

    from engine.exercise_optimizer import OptimizerResult

    result = at.session_state["_auto_opt_result"]
    assert isinstance(result, OptimizerResult)
    assert result.best.ceiling_label != "current"


def test_apply_conversions_writes_planner_session_keys() -> None:
    at = _spreadable_app()
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply conversions")

    assert not at.exception

    best = at.session_state["_auto_opt_result"].best
    assert at.session_state["conv_plan_your"] == best.conversions.your_conversions
    assert at.session_state["conv_plan_spouse"] == best.conversions.spouse_conversions


def test_apply_exercises_does_not_error(monkeypatch) -> None:
    saved = []
    monkeypatch.setattr(
        "views.auto_optimizer.save_exercise_schedule", lambda schedule: saved.append(schedule)
    )
    at = _spreadable_app()
    at.run()
    _click(at, "Run optimizer")
    _click(at, "Apply exercises")

    assert not at.exception
    assert len(saved) == 1  # the view invoked the (stubbed) save exactly once
