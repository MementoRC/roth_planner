"""Age widgets must not lag a rerun (Profile > Household & Accounts).

An unkeyed ``value=hh.<age>`` number_input is re-identified when its value
changes: the Auto RMD label showed the pre-edit derivation and the NEXT typed
edit was discarded. The Auto label must follow the age the widget returned.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _page() -> None:
    import streamlit as st

    from models.household import Household, rmd_age_kwargs
    from views.setup._partials import render_household_partial

    s = st.session_state
    s.setdefault("filing_status", "MFJ")
    s.setdefault("your_age", 55)
    s.setdefault("spouse_age", 55)
    # Like app.get_household(): built from PRE-edit session state.
    hh = Household(
        your_age=s["your_age"],
        spouse_age=s["spouse_age"],
        base_year=2026,
        **rmd_age_kwargs(s, "your"),
        **rmd_age_kwargs(s, "spouse"),
    )
    render_household_partial(hh, st, "your")
    render_household_partial(hh, st, "spouse")


def _num(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


def _auto_label(at: AppTest, label: str) -> str:
    sb = next(w for w in at.selectbox if w.label == label)
    return sb.options[0]


def _check(label: str, rmd_label: str, state_key: str) -> None:
    at = AppTest.from_function(_page)
    at.run()
    assert not at.exception
    assert _auto_label(at, rmd_label).endswith(": 75")

    _num(at, label).set_value(67).run()  # born 1959 -> 73
    assert at.session_state[state_key] == 67
    assert _auto_label(at, rmd_label).endswith(": 73")

    _num(at, label).set_value(61).run()  # must not be discarded
    assert at.session_state[state_key] == 61
    assert _num(at, label).value == 61
    assert _auto_label(at, rmd_label).endswith(": 75")


def test_your_age_edit_updates_auto_label_same_run_and_next_edit_sticks() -> None:
    _check("Your Age", "Your RMD start age", "your_age")


def test_spouse_age_edit_updates_auto_label_same_run_and_next_edit_sticks() -> None:
    _check("Spouse Age", "Spouse RMD start age", "spouse_age")


def test_external_age_change_reseeds_widget() -> None:
    at = AppTest.from_function(_page)
    at.run()
    at.session_state["your_age"] = 70
    at.run()
    assert _num(at, "Your Age").value == 70
    assert _auto_label(at, "Your RMD start age").endswith(": 73")
