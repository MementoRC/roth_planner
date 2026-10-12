"""Redesign PR 1b: whose-numbers toggle, unsaved drafts, explicit save and provenance."""

from __future__ import annotations

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from models.ytd_income import ManualMark, YTDSnapshot

YTD = "income"
DASH = "overview"


def _two_people() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={
            "you": {"wages_ytd": 100_000.0, "federal_withholding_ytd": 9_000.0},
            "spouse": {"wages_ytd": 70_000.0},
        }
    )


def _render_ytd(filing_status: str = "MFJ") -> None:
    import streamlit as st

    from models.household import Household
    from views.ytd_income import render

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("instance_owner", "you")
    render(Household(filing_status=filing_status))


@pytest.fixture
def run_ytd(monkeypatch):
    import views.ytd_income as ytd_income_mod

    monkeypatch.setattr(ytd_income_mod, "save_ytd_snapshot", lambda ytd: None)

    def _run(ytd: YTDSnapshot | None = None, filing_status: str = "MFJ") -> AppTest:
        at = AppTest.from_function(
            _render_ytd, kwargs={"filing_status": filing_status}, default_timeout=30
        )
        if ytd is not None:
            at.session_state["ytd_snapshot"] = ytd
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    return _run


@pytest.fixture
def run_app(clean_command_center_caches, monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    def _run(ytd: YTDSnapshot) -> AppTest:
        at = AppTest.from_file("app.py", default_timeout=30)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.session_state["instance_owner"] = "you"
        at.session_state["nav_page"] = YTD
        at.session_state["ytd_snapshot"] = ytd
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        return at

    return _run


def _go(at: AppTest, page: str) -> None:
    at.session_state["nav_page"] = page
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def _num(at: AppTest, key: str):
    return at.number_input(key=key)


def _click(at: AppTest, label: str) -> None:
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception, [e.value for e in at.exception]


def _snap(at: AppTest) -> YTDSnapshot:
    return at.session_state["ytd_snapshot"]


def _warning(at: AppTest, needle: str) -> str | None:
    return next((w.value for w in at.warning if needle in w.value), None)


# ---- toggle / save / provenance ----------------------------------------------


def test_toggle_defaults_to_the_operator_and_shows_their_slot(run_ytd):
    at = run_ytd(_two_people())
    assert at.radio(key="ytd_manual_owner").value == "you"
    assert _num(at, "ytd_w_you_wages_ytd").value == 100_000


def test_switching_person_shows_that_persons_slot(run_ytd):
    at = run_ytd(_two_people())
    at.radio(key="ytd_manual_owner").set_value("spouse").run()
    assert _num(at, "ytd_w_spouse_wages_ytd").value == 70_000
    assert _num(at, "ytd_w_spouse_federal_withholding_ytd").value == 0


def test_save_writes_only_the_selected_person_and_marks_it(run_ytd):
    at = run_ytd(_two_people())
    at.radio(key="ytd_manual_owner").set_value("spouse").run()
    _num(at, "ytd_w_spouse_wages_ytd").set_value(123_456).run()
    at.text_input(key="ytd_manual_note_spouse").input("paystub").run()
    _click(at, "Save entries")
    snap = _snap(at)
    assert snap.by_owner["spouse"]["wages_ytd"] == 123_456.0
    assert snap.by_owner["you"]["wages_ytd"] == 100_000.0
    assert snap.manual_marks["spouse.wages_ytd"] == ManualMark(
        123_456.0, date.today().isoformat(), "paystub"
    )


def test_a_value_below_the_other_persons_is_saved_not_ignored(run_ytd):
    at = run_ytd(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(50_000).run()
    _click(at, "Save entries")
    assert _snap(at).by_owner["you"]["wages_ytd"] == 50_000.0
    assert _snap(at).wages_ytd == 120_000.0


def test_typing_without_save_writes_nothing_to_the_snapshot(run_ytd):
    at = run_ytd(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(1).run()
    assert _snap(at).by_owner == _two_people().by_owner
    assert _snap(at).manual_marks == {}


def test_saving_untouched_values_marks_nothing(run_ytd):
    at = run_ytd(_two_people())
    _click(at, "Save entries")
    assert _snap(at).manual_marks == {}


def test_single_filer_has_no_toggle(run_ytd):
    at = run_ytd(YTDSnapshot(by_owner={"you": {"wages_ytd": 1.0}}), filing_status="Single")
    assert not [r for r in at.radio if r.key == "ytd_manual_owner"]


def test_legacy_snapshot_is_attributed_to_the_operator(run_ytd):
    at = run_ytd(YTDSnapshot(wages_ytd=90_000.0))
    assert _snap(at).by_owner["you"]["wages_ytd"] == 90_000.0
    assert _num(at, "ytd_w_you_wages_ytd").value == 90_000


def test_breakdown_caption_shows_both_people(run_ytd):
    at = run_ytd(_two_people())
    assert any(
        "Wages \\$170,000 (You \\$100,000 + Spouse \\$70,000)" in c.value for c in at.caption
    )


def test_entered_by_hand_table_lists_marks(run_ytd):
    ytd = _two_people()
    ytd.manual_marks["spouse.wages_ytd"] = ManualMark(70_000.0, "2026-09-30", "paystub")
    at = run_ytd(ytd)
    table = next(d.value for d in at.dataframe if "Note" in d.value.columns)
    row = table.iloc[0]
    assert (row["Field"], row["Whose"], row["As of"], row["Note"]) == (
        "Wages",
        "Spouse",
        "2026-09-30",
        "paystub",
    )


def test_input_bounds(run_ytd):
    at = run_ytd(_two_people())
    assert _num(at, "ytd_w_household_hsa_contribution_ytd").min == 0
    assert _num(at, "ytd_w_household_deductible_ira_contribution_ytd").min == 0
    assert at.date_input(key="ytd_manual_as_of_you").max == date.today()


# ---- drafts -----------------------------------------------------------------


def test_edit_survives_a_page_switch_with_warning_and_sidebar_marker(run_app):
    at = run_app(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(123_456).run()
    _go(at, DASH)
    assert any("Unsaved YTD entries" in c.value for c in at.sidebar.caption)
    _go(at, YTD)
    assert _num(at, "ytd_w_you_wages_ytd").value == 123_456
    assert "You: Wages" in (_warning(at, "unsaved changes") or "")
    assert _snap(at).by_owner["you"]["wages_ytd"] == 100_000.0


def test_desktop_warning_promises_save_keeps_entries(run_app):
    at = run_app(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(123_456).run()
    _go(at, DASH)
    _go(at, YTD)
    warning = _warning(at, "unsaved changes") or ""
    assert "Click **Save entries** to keep them." in warning
    assert "browser version" not in warning


def test_browser_warning_does_not_promise_save_survives_a_reload(run_app, monkeypatch):
    import views.ytd_income._partials._manual_entry as manual_entry_mod

    monkeypatch.setattr(manual_entry_mod, "is_pyodide", lambda: True)
    at = run_app(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(123_456).run()
    _go(at, DASH)
    _go(at, YTD)
    warning = _warning(at, "unsaved changes") or ""
    assert "In this browser version a full page reload also loses saved entries." in warning
    assert "to keep them" not in warning


def test_toggling_person_preserves_the_other_persons_drafts(run_ytd):
    at = run_ytd(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(1).run()
    at.radio(key="ytd_manual_owner").set_value("spouse").run()
    _num(at, "ytd_w_spouse_wages_ytd").set_value(2).run()
    at.radio(key="ytd_manual_owner").set_value("you").run()
    assert _num(at, "ytd_w_you_wages_ytd").value == 1
    warning = _warning(at, "unsaved changes") or ""
    assert "You: Wages" in warning
    assert "Spouse: Wages" in warning


def test_save_commits_every_draft_and_clears_the_warning_and_marker(run_app):
    at = run_app(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(1).run()
    at.radio(key="ytd_manual_owner").set_value("spouse").run()
    _num(at, "ytd_w_spouse_wages_ytd").set_value(2).run()
    _click(at, "Save entries")
    assert _snap(at).by_owner["you"]["wages_ytd"] == 1.0
    assert _snap(at).by_owner["spouse"]["wages_ytd"] == 2.0
    assert _warning(at, "unsaved changes") is None
    assert not any("Unsaved YTD entries" in c.value for c in at.sidebar.caption)


def test_discard_restores_the_saved_values(run_ytd):
    at = run_ytd(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(1).run()
    _click(at, "Discard changes")
    assert _num(at, "ytd_w_you_wages_ytd").value == 100_000
    assert _warning(at, "unsaved changes") is None
    assert _snap(at).manual_marks == {}


def test_as_of_and_note_are_drafts_per_person(run_ytd):
    at = run_ytd(_two_people())
    at.text_input(key="ytd_manual_note_you").input("mine").run()
    at.radio(key="ytd_manual_owner").set_value("spouse").run()
    assert at.text_input(key="ytd_manual_note_spouse").value == ""
    at.radio(key="ytd_manual_owner").set_value("you").run()
    assert at.text_input(key="ytd_manual_note_you").value == "mine"


def test_drafts_stay_reachable_with_manual_entry_off(run_ytd):
    at = run_ytd(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(1).run()
    at.checkbox(key="ytd_manual_entry").uncheck().run()
    assert "You: Wages" in (_warning(at, "unsaved changes") or "")
    _click(at, "Save entries")
    assert _snap(at).by_owner["you"]["wages_ytd"] == 1.0


def test_an_import_that_changes_a_drafted_field_discards_the_draft(run_ytd):
    at = run_ytd(_two_people())
    _num(at, "ytd_w_you_wages_ytd").set_value(1).run()
    imported = _two_people()
    imported.set_owned("you", wages_ytd=90_000.0)  # an import landed meanwhile
    at.session_state["ytd_snapshot"] = imported
    at.run()
    assert "You: Wages" in (_warning(at, "changed since you started editing") or "")
    assert _num(at, "ytd_w_you_wages_ytd").value == 90_000
    assert _warning(at, "unsaved changes") is None


# ---- UBS withholding component (Task 9) --------------------------------------


def test_withholding_field_edits_the_base_and_shows_ubs_read_only(run_ytd):
    ytd = _two_people()
    ytd.set_ubs_withholding("you", 1_000.0)
    at = run_ytd(ytd)
    assert _num(at, "ytd_w_you_federal_withholding_ytd").value == 9_000
    assert any(
        "withheld on option exercises" in c.value and "10,000" in c.value for c in at.caption
    )
