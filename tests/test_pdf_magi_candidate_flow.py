"""AppTest: PDF-1040-scan MAGI candidate surfaces in Setup's Command Center.

Wave 5 (Setup / Command Center) — simulates the record_magi_candidates() call
that views/setup/parameters.py's PDF-1040 scan handler now makes (instead of
gap-filling st.session_state["prior_year_magi"] directly), then asserts the
recorded Source.PDF candidate shows up in the pending-review governance card.
Isolated to a tmp CandidateStore path; cleans up after itself — mirrors
tests/test_command_center_view.py's isolation approach.

Originally asserted against ``views.setup.command_center.render_command_center``
and was SKIPPED (Command Center's generic per-pending-field loop, which used
to render this card, was removed in Task 4 of the ui-shell-theme-toggle plan
before this field got its own owning partial). Task 7 gave
``prior_year_magi.<year>`` its owning partial
(``views.setup._partials._assumptions.render_assumptions_partial``). PR #449
reversed that relocation: Classic mode's ``st.tabs()`` executes every tab
body per script run regardless of which tab is visually selected, so
registering the same ``trust_<field>``/``manual_<field>``/``confirm_<field>``
widget keys from both an owning partial AND Command Center's loop in the same
run raised ``DuplicateWidgetID``. Command Center is once again the sole
renderer of these governance cards, so this test drives
``render_command_center`` directly.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_after_pdf_scan() -> None:
    from datetime import datetime

    import streamlit as st

    from engine.data_sources.record import record_magi_candidates
    from models.household import Household
    from models.sourced import Source
    from views.setup.command_center import render_command_center

    # Simulates the scan-handler seam in views/setup/parameters.py
    # (_render_pdf_1040_import): a parsed 1040 PDF records a MAGI candidate
    # instead of writing session_state["prior_year_magi"] directly.
    record_magi_candidates(
        {2024: 290_000.0},
        Source.PDF,
        "Form 1040 PDF",
        datetime(2026, 7, 16, 12, 0, 0),
    )

    st.session_state["_pending_review"] = {"prior_year_magi.2024"}
    render_command_center(Household())


def test_pdf_scan_magi_candidate_appears_pending_in_command_center(
    clean_command_center_caches,
) -> None:
    at = AppTest.from_function(_render_after_pdf_scan)
    at.run()

    assert not at.exception

    rendered_text = (
        "\n".join(m.value for m in at.markdown)
        + "\n".join(c.value for c in at.caption)
        + "\n".join(s.value for s in at.subheader)
    )
    assert "290,000" in rendered_text
    assert "2024" in rendered_text
