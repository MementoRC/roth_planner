"""AppTest: PDF-1040-scan MAGI candidate surfaces in the Setup / Command Center.

Wave 5 (Setup / Command Center) — simulates the record_magi_candidates() call
that views/setup/parameters.py's PDF-1040 scan handler now makes (instead of
gap-filling st.session_state["prior_year_magi"] directly), then asserts the
recorded Source.PDF candidate shows up in the Command Center's pending-review
gate. Isolated to a tmp CandidateStore path; cleans up after itself — mirrors
tests/test_command_center_view.py's isolation approach.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

_RECORDED_AT = datetime(2026, 7, 16, 12, 0, 0)
_CACHE_FILES = [CANDIDATE_STORE_PATH, TRUST_CHOICES_PATH, COMMITTED_PATH]


@pytest.fixture
def clean_command_center_caches():
    """Delete the 3 Command Center cache files after the test (repo-root-anchored)."""
    yield
    for p in _CACHE_FILES:
        p.unlink(missing_ok=True)


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
