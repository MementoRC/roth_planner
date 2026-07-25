"""AppTest: PDF-1040-scan MAGI candidate surfaces in Setup's Assumptions partial.

Wave 5 (Setup / Command Center) — simulates the record_magi_candidates() call
that views/setup/parameters.py's PDF-1040 scan handler now makes (instead of
gap-filling st.session_state["prior_year_magi"] directly), then asserts the
recorded Source.PDF candidate shows up in its owning partial's pending-review
governance card. Isolated to a tmp CandidateStore path; cleans up after
itself — mirrors tests/test_command_center_view.py's isolation approach.

Originally asserted against ``views.setup.command_center.render_command_center``
and was SKIPPED (Command Center's generic per-pending-field loop, which used
to render this card, was removed in Task 4 of the ui-shell-theme-toggle plan
before this field got its own owning partial). Task 7 gave
``prior_year_magi.<year>`` its owning partial
(``views.setup._partials._assumptions.render_assumptions_partial``), so this
test now drives that partial directly and is un-skipped.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_after_pdf_scan() -> None:
    from datetime import datetime

    import streamlit as st

    from engine.data_sources.record import record_magi_candidates
    from models.household import Household
    from models.sourced import Source
    from views.setup._partials import render_assumptions_partial

    # Simulates the scan-handler seam in views/setup/parameters.py
    # (_render_pdf_1040_import): a parsed 1040 PDF records a MAGI candidate
    # instead of writing session_state["prior_year_magi"] directly.
    record_magi_candidates(
        {2024: 290_000.0},
        Source.PDF,
        "Form 1040 PDF",
        datetime(2026, 7, 16, 12, 0, 0),
    )

    # render_assumptions_partial reads these two unkeyed "controlled" widgets
    # via bare session_state attribute access (no .get() fallback) — every
    # other field it reads has a .get(..., default) fallback.
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", 60_000)

    st.session_state["_pending_review"] = {"prior_year_magi.2024"}
    render_assumptions_partial(Household(), st)


def test_pdf_scan_magi_candidate_appears_pending_in_assumptions_partial(
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
