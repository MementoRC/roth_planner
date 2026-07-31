"""Live-verification tests for ``views/option_exercise`` — Task 22 of the
UI Shell Phase 4 plan (``docs/superpowers/plans/2026-07-29-ui-shell-phase4-plan.md``).

Mirrors ``tests/test_ytd_shell.py``'s ``AppTest.from_function`` pattern: a
single self-contained target function seeds a minimal ``Household`` (with
option grants and an optional ``exercise_schedule``) and calls
``views.option_exercise.render(...)``.

Monkeypatch target note: ``save_exercise_schedule``/``clear_exercise_schedule``
are patched on the PARENT module (``views.option_exercise``), not on
``views.option_exercise._partials._validate_save`` — the latter never imports
these names directly (it resolves them via a module-attribute indirection
back through the parent, established in Tasks 16/19 to keep OTHER existing
tests' monkeypatches working across the partial-extraction refactor), so
patching the submodule would raise AttributeError.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant


def _grant(*, year=2019, strike=104.0, shares=1000, expiry_year=2029) -> StockGrant:
    return StockGrant(year=year, strike=strike, shares=shares, expiry_year=expiry_year)


def _render_oe(schedule=None) -> None:
    import streamlit as st

    from models.grants import StockGrant
    from models.household import Household
    from views.option_exercise import render

    st.session_state["_suppress_snapshot_autoload"] = True
    grant = StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029)
    hh = Household(grants=[grant], base_year=2026)
    hh.exercise_schedule = schedule
    render(hh, theme=None)


def _run_oe(monkeypatch, schedule: ExerciseSchedule | None = None, ui_theme: str = "Classic") -> AppTest:
    import views.option_exercise as oe_module

    monkeypatch.setattr(oe_module, "save_exercise_schedule", lambda s: None)
    monkeypatch.setattr(oe_module, "clear_exercise_schedule", lambda: None)

    at = AppTest.from_function(_render_oe, kwargs={"schedule": schedule})
    at.session_state["ui_theme"] = ui_theme
    at.run()
    return at


def _badge_captions(at: AppTest) -> list[str]:
    return [c.value for c in at.caption if c.value.startswith("⚠️")]


# --- Step 2: completeness-badge tests ---------------------------------------


def test_badge_shown_when_schedule_missing(monkeypatch) -> None:
    at = _run_oe(monkeypatch, schedule=None, ui_theme="Classic")
    assert not at.exception
    assert any("No exercise plan confirmed" in b for b in _badge_captions(at))


def test_badge_shown_when_partially_allocated(monkeypatch) -> None:
    schedule = ExerciseSchedule()
    schedule.set_shares(_grant().key(), 2029, 500)  # 500 of 1000 shares allocated
    at = _run_oe(monkeypatch, schedule=schedule, ui_theme="Classic")
    assert not at.exception
    assert any("not yet allocated" in b for b in _badge_captions(at))


def test_badge_absent_when_fully_allocated(monkeypatch) -> None:
    schedule = ExerciseSchedule()
    schedule.set_shares(_grant().key(), 2029, 1000)  # fully allocated
    at = _run_oe(monkeypatch, schedule=schedule, ui_theme="Classic")
    assert not at.exception
    assert _badge_captions(at) == []


# --- Step 3: Domains-layout test ---------------------------------------------


def test_domains_layout_has_two_tabs(monkeypatch) -> None:
    at = _run_oe(monkeypatch, ui_theme="Domains")
    assert not at.exception

    tab_container = next(
        child
        for child in at.main.children.values()
        if getattr(child, "type", None) == "tab_container"
    )
    labels = [tab.label for tab in tab_container.children.values()]
    assert labels == ["Edit Allocation", "Review Impact"]


# --- Step 4: key-stability test ----------------------------------------------


def test_widget_keys_stable_across_theme_switch(monkeypatch) -> None:
    import views.option_exercise as oe_module

    monkeypatch.setattr(oe_module, "save_exercise_schedule", lambda s: None)
    monkeypatch.setattr(oe_module, "clear_exercise_schedule", lambda: None)

    at = AppTest.from_function(_render_oe, kwargs={"schedule": None})
    at.session_state["ui_theme"] = "Classic"
    at.run()
    assert not at.exception

    growth_input = next(w for w in at.number_input if w.label == "Assumed TXN growth (%/yr)")
    growth_input.set_value(9.0).run()
    assert not at.exception

    at.session_state["ui_theme"] = "Domains"
    at.run()
    assert not at.exception
    assert next(w for w in at.number_input if w.label == "Assumed TXN growth (%/yr)").value == 9.0

    at.session_state["ui_theme"] = "Classic"
    at.run()
    assert not at.exception
    assert next(w for w in at.number_input if w.label == "Assumed TXN growth (%/yr)").value == 9.0


# --- Step 5: Reset-button interception test ----------------------------------


def test_reset_button_clears_schedule(monkeypatch) -> None:
    import views.option_exercise as oe_module

    clear_calls: list[bool] = []
    monkeypatch.setattr(oe_module, "save_exercise_schedule", lambda s: None)
    monkeypatch.setattr(oe_module, "clear_exercise_schedule", lambda: clear_calls.append(True))

    schedule = ExerciseSchedule()
    schedule.set_shares(_grant().key(), 2029, 1000)  # fully allocated, no badge initially
    at = AppTest.from_function(_render_oe, kwargs={"schedule": schedule})
    at.session_state["ui_theme"] = "Classic"
    at.run()
    assert not at.exception
    assert _badge_captions(at) == []  # sanity: starts fully allocated, no badge

    reset_button = next(b for b in at.button if b.label == "Reset to default (hold to expiry)")
    reset_button.click().run()

    assert not at.exception
    assert clear_calls == [True]  # clear_exercise_schedule interception genuinely invoked
