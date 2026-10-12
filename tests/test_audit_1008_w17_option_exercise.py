"""audit-1008 W17: Option Exercise page and schedule bookkeeping.

- F-29: the grid's page-local share state (``_oe_shares_state``) outlived a
  change of grant set (bundle import / sync), seeding every new grant with 0.
- F-61: the Review tables looped raw ``hh.grants`` against per-key totals,
  so a split award (two rows colliding on ``key()``) was counted twice.
- F-72: a confirmed EMPTY grants list (``SourcedList([], [])``) was reported
  "missing" forever because an empty list carries no provenance.
- F-74: ``compute_exercise_completeness`` measured each raw tranche against
  the shared key total, hiding unallocated shares.
- F-75: ``ExerciseSchedule.validate`` kept only the last colliding tranche,
  so the untouched hold-to-expiry default raised a false "exceeds" error.

All figures are synthetic.
"""

from __future__ import annotations

from datetime import datetime

from streamlit.testing.v1 import AppTest

from engine.data_status import (
    GRANTS_KEY,
    compute_data_completeness,
    compute_exercise_completeness,
)
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household
from models.sourced import SourcedList

# One award split across two rows: same year+strike+expiry, empty grant_id.
SPLIT_A = StockGrant(2022, 40.0, 300, 2032)
SPLIT_B = StockGrant(2022, 40.0, 700, 2032)

FIRST_SET = [StockGrant(2021, 30.0, 800, 2031)]
SECOND_SET = [StockGrant(2023, 55.0, 1200, 2033, grant_id="SYN-2")]

NOW = datetime(2026, 6, 1)


def _render_oe(grants) -> None:
    """AppTest target. Grants come from session state when a test swaps the
    set mid-session (bundle import / sync), else from the kwarg."""
    import streamlit as st

    from models.household import Household
    from views.option_exercise import render

    st.session_state["_suppress_snapshot_autoload"] = True
    hh = Household(grants=st.session_state.get("_w17_grants", grants), base_year=2026)
    render(hh)


def _run_oe(monkeypatch, grants: list[StockGrant]) -> AppTest:
    import views.option_exercise as oe_module

    monkeypatch.setattr(oe_module, "save_exercise_schedule", lambda s: None)
    monkeypatch.setattr(oe_module, "clear_exercise_schedule", lambda: None)
    at = AppTest.from_function(_render_oe, kwargs={"grants": grants})
    at.run()
    return at


def _frame_with(at: AppTest, column: str):
    for df in at.dataframe:
        if column in df.value.columns:
            return df.value
    raise AssertionError(f"no rendered table has a {column!r} column")


# --- F-29 -----------------------------------------------------------------


def test_grid_reseeds_from_schedule_when_grant_set_changes(monkeypatch) -> None:
    at = _run_oe(monkeypatch, FIRST_SET)
    assert not at.exception
    first = at.dataframe[0].value
    assert first.iloc[0]["2031"] == 800

    at.session_state["_w17_grants"] = SECOND_SET
    at.run()

    assert not at.exception
    grid = at.dataframe[0].value
    assert list(grid["Grant"]) == ["2023 · $55 · 1,200 sh"]
    # Hold-to-expiry default: the whole grant lands in its expiry year.
    assert grid.iloc[0]["2033"] == 1200
    remaining = _frame_with(at, "Remaining")
    assert list(remaining["Scheduled"]) == ["1,200"]


def test_grid_keeps_unsaved_edits_while_grant_set_is_unchanged(monkeypatch) -> None:
    """Non-regression: the page-local state still carries edits across reruns."""
    at = _run_oe(monkeypatch, FIRST_SET)
    at.session_state["_oe_shares_state"] = {FIRST_SET[0].key(): {"2027": 100, "2031": 700}}
    at.run()

    grid = at.dataframe[0].value
    assert grid.iloc[0]["2027"] == 100
    assert grid.iloc[0]["2031"] == 700


# --- F-61 / F-75 (view) ----------------------------------------------------


def test_review_counts_a_split_award_once(monkeypatch) -> None:
    at = _run_oe(monkeypatch, [SPLIT_A, SPLIT_B])
    assert not at.exception

    remaining = _frame_with(at, "Remaining")
    assert list(remaining["Granted"]) == ["1,000"]
    assert list(remaining["Scheduled"]) == ["1,000"]
    assert list(remaining["Remaining"]) == ["0"]

    income = next(
        df.value
        for df in at.dataframe
        if "Total (ordinary income)" in list(df.value.get("Grant", []))
    )
    grant_rows = income[income["Grant"] != "Total (ordinary income)"]
    assert len(grant_rows) == 1
    total_row = income[income["Grant"] == "Total (ordinary income)"].iloc[0]
    assert total_row["2032"] == grant_rows.iloc[0]["2032"]


def test_default_schedule_for_split_award_shows_no_error(monkeypatch) -> None:
    at = _run_oe(monkeypatch, [SPLIT_A, SPLIT_B])
    assert not at.exception
    assert [e.value for e in at.error] == []


# --- F-75 (model) ----------------------------------------------------------


def test_validate_aggregates_colliding_tranches() -> None:
    schedule = ExerciseSchedule.default_at_expiry([SPLIT_A, SPLIT_B], 2026, 50.0)
    assert schedule.validate([SPLIT_A, SPLIT_B], 2026) == []


def test_validate_still_flags_a_real_overschedule() -> None:
    schedule = ExerciseSchedule(shares_by_grant_year={SPLIT_A.key(): {2030: 1001}})
    messages = schedule.validate([SPLIT_A, SPLIT_B], 2026)
    assert any("exceeds available 1000" in m for m in messages)


# --- F-74 ------------------------------------------------------------------


def test_completeness_reports_unallocated_shares_of_a_split_award() -> None:
    hh = Household(grants=[SPLIT_A, SPLIT_B], base_year=2026)
    hh.exercise_schedule = ExerciseSchedule(shares_by_grant_year={SPLIT_A.key(): {2027: 300}})

    result = compute_exercise_completeness(hh)

    assert result.total == 1
    assert result.ok == 0
    assert len(result.issues) == 1
    assert "700 shares not yet allocated" in result.issues[0].detail


def test_completeness_ok_when_split_award_fully_allocated() -> None:
    hh = Household(grants=[SPLIT_A, SPLIT_B], base_year=2026)
    hh.exercise_schedule = ExerciseSchedule(shares_by_grant_year={SPLIT_A.key(): {2027: 1000}})

    result = compute_exercise_completeness(hh)

    assert result.issues == ()
    assert result.ok == result.total == 1


# --- F-72 ------------------------------------------------------------------


def test_confirmed_empty_grants_list_is_not_missing() -> None:
    hh = Household(base_year=2026)
    hh.grants = SourcedList([], [])

    result = compute_data_completeness(hh, [GRANTS_KEY], set(), now=NOW)

    assert result.issues == ()
    assert result.is_complete


def test_unsourced_grants_still_missing() -> None:
    """Non-regression: a plain (never-confirmed) list is still flagged."""
    hh = Household(grants=[], base_year=2026)

    result = compute_data_completeness(hh, [GRANTS_KEY], set(), now=NOW)

    assert [i.severity for i in result.issues] == ["missing"]


def test_pending_empty_grants_candidate_is_still_a_conflict() -> None:
    hh = Household(base_year=2026)
    hh.grants = SourcedList([], [])

    result = compute_data_completeness(hh, [GRANTS_KEY], {GRANTS_KEY}, now=NOW)

    assert [i.severity for i in result.issues] == ["conflict"]
