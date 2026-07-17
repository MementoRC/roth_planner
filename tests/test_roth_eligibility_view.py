"""AppTest tests for views/roth_eligibility.py — W1 Command Center redirect.

Golden characterization pins the eligibility math for a fixed Household so the
read-only-field refactor cannot move numeric outputs. The redirect tests prove
the canonical fields (filing status, ages, IRA balances) no longer expose
editable inputs and instead render read-only displays + a jump button.

Uses streamlit.testing.v1.AppTest.from_function (mirrors
tests/test_command_center_view.py): the wrapped function must be fully
self-contained — AppTest runs the closure's source in an isolated namespace, so
all imports/object construction live INSIDE each closure body (no references to
module-level sibling helpers).
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_canonical() -> None:
    from datetime import datetime

    import streamlit as st

    from models.household import Household
    from models.sourced import Provenance, Source, SourcedValue
    from views.roth_eligibility import render

    recorded_at = datetime(2026, 7, 16, 12, 0, 0)
    hh = Household(your_age=61, spouse_age=55, base_year=2026, filing_status="MFJ")
    hh.your_ira = SourcedValue(
        1_700_000.0, Provenance(Source.FINEXTRACT_LIVE, recorded_at, "live sync")
    )
    hh.spouse_ira = SourcedValue(
        1_500_000.0, Provenance(Source.MANUAL, recorded_at, "manual entry")
    )
    # MAGI in the MFJ 2026 Roth phase-out band (242k-252k) -> partial-allowed branch.
    st.session_state["prior_year_magi"] = {2024: 245_000.0}
    render(hh)


def _render_button_only() -> None:
    from views._shared import command_center_button

    command_center_button(key="probe")


def _all_text(at) -> list[str]:
    """Concatenated text of every markdown/caption/success/warning/error/info element."""
    out: list[str] = []
    for group in ("markdown", "caption", "success", "warning", "error", "info"):
        out.extend(el.value for el in getattr(at, group))
    return out


# --- Step 0: characterization golden ------------------------------------------


def test_golden_eligibility_outputs_for_fixed_household():
    at = AppTest.from_function(_render_canonical)
    at.run()
    assert not at.exception

    text = _all_text(at)
    joined = "\n".join(text)

    # Contribution limit: both 61 and 55 are >=50 -> 7,500 + 1,100 catch-up = 8,600.
    assert sum("IRA contribution limit" in t and "$8,600" in t for t in text) == 2
    # MAGI 245,000 sits in the MFJ phase-out band -> partial allowed of $6,020 each.
    assert (
        sum(
            "Partial Roth contribution allowed" in w.value and "$6,020" in w.value
            for w in at.warning
        )
        == 2
    )
    assert "MAGI $245,000 is in phase-out range ($242,000 – $252,000)" in joined
    # Balances flow through to the per-person pro-rata backdoor block.
    assert "$1,700,000" in joined
    assert "$1,500,000" in joined


# --- Step 1: navigation primitive ---------------------------------------------


def test_command_center_button_sets_nav_key_on_click():
    at = AppTest.from_function(_render_button_only)
    at.run()
    assert not at.exception
    at.button(key="probe").click().run()
    assert at.session_state["nav_page"] == "⚙️ Setup"


def test_roth_eligibility_renders_command_center_jump_buttons():
    at = AppTest.from_function(_render_canonical)
    at.run()
    assert not at.exception
    keys = [b.key for b in at.button]
    assert "nav_filing" in keys
    assert "nav_your_age" in keys
    assert "nav_your_trad_balance" in keys
    # Clicking one routes to Setup on the next run.
    at.button(key="nav_filing").click().run()
    assert at.session_state["nav_page"] == "⚙️ Setup"


# --- Step 2: filing status is read-only ---------------------------------------


def test_filing_status_is_read_only_no_selectbox():
    at = AppTest.from_function(_render_canonical)
    at.run()
    assert not at.exception
    labels = [s.label for s in at.selectbox]
    assert "Filing Status" not in labels  # converted to read-only
    assert "Tax Year" in labels  # Tax Year selectbox stays
    assert "### MFJ" in "\n".join(_all_text(at))


# --- Step 3: ages are read-only -----------------------------------------------


def test_ages_are_read_only_no_number_inputs():
    at = AppTest.from_function(_render_canonical)
    at.run()
    assert not at.exception
    labels = [n.label for n in at.number_input]
    assert not any(label.startswith("Your Age") for label in labels)
    assert not any(label.startswith("Spouse Age") for label in labels)
    joined = "\n".join(_all_text(at))
    assert "### 61" in joined
    assert "### 55" in joined


# --- Step 4: IRA balances are read-only with a source chip --------------------


def test_ira_balances_are_read_only_with_source_chip():
    at = AppTest.from_function(_render_canonical)
    at.run()
    assert not at.exception
    labels = [n.label for n in at.number_input]
    assert "Your Total Trad IRA Balance" not in labels
    assert "Spouse Total Trad IRA Balance" not in labels
    joined = "\n".join(_all_text(at))
    # Read-only displays show the committed balances...
    assert "### $1,700,000" in joined
    assert "### $1,500,000" in joined
    # ...with a provenance chip naming the committed Source.
    assert "FINEXTRACT_LIVE" in joined
    assert "MANUAL" in joined
