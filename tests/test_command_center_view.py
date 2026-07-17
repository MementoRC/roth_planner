"""AppTest smoke tests for views/setup/command_center.py — the review/confirm gate.

Uses ``streamlit.testing.v1.AppTest.from_function`` (mirrors the pattern in
tests/test_auto_optimizer_view.py — the wrapped function must be fully
self-contained, all imports/object construction inside its body).

The Command Center's cache paths (``engine.data_sources.paths``) are
repo-root-anchored regardless of cwd (matching every other engine/data_sources
cache file), so this test seeds/cleans those exact files directly rather than
using a tmp cwd — same isolation approach as tests/test_app_data_sources.py.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.data_sources.scan_ingest import ScanIngestResult
from engine.pdf_import import PdfImportResult
from models.sourced import Provenance, Source, SourcedValue
from views._shared import PortfolioSyncSummary, ScanSyncSummary, SsSyncSummary, SyncEverythingResult

_RECORDED_AT = datetime(2026, 7, 16, 12, 0, 0)
_CACHE_FILES = [CANDIDATE_STORE_PATH, TRUST_CHOICES_PATH, COMMITTED_PATH]


@pytest.fixture
def clean_command_center_caches():
    """Delete the 3 Command Center cache files after the test (repo-root-anchored)."""
    yield
    for p in _CACHE_FILES:
        p.unlink(missing_ok=True)


def _seed_pending_txn_price_now() -> None:
    """Committed txn_price_now=100/UNKNOWN + a FINEXTRACT_LIVE=250 candidate."""
    committed_json = {
        "txn_price_now": SourcedValue(100.0, Provenance(Source.UNKNOWN, _RECORDED_AT)).to_json()
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    store = CandidateStore()
    store.record_candidate(
        "txn_price_now", 250.0, Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync")
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def _render_with_pending_txn_price_now() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup.command_center import render_command_center

    st.session_state["_pending_review"] = {"txn_price_now"}
    render_command_center(Household())


def test_confirm_txn_price_now_syncs_the_aliased_session_key(
    clean_command_center_caches,
) -> None:
    """Bug 2 regression: the Household attr is txn_price_now, but the Setup
    number_input widget reads/writes session_state["txn_price"] (alias). The
    confirm handler must write the SAME aliased key, or the next
    reconcile_manual_edits sees session_state.txn_price still stale and
    reverts the confirm.
    """
    _seed_pending_txn_price_now()

    at = AppTest.from_function(_render_with_pending_txn_price_now)
    at.run()
    assert not at.exception

    at.button(key="confirm_txn_price_now").click().run()

    assert not at.exception
    assert at.session_state["txn_price"] == 250.0
    assert "txn_price_now" not in at.session_state

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["txn_price_now"]["value"] == 250.0
    assert committed_json["txn_price_now"]["source"] == "FINEXTRACT_LIVE"


def _seed_pending_your_ira() -> None:
    """Committed your_ira=1.7M/UNKNOWN + a FINEXTRACT_LIVE your_ira=2.0M candidate."""
    committed_json = {
        "your_ira": SourcedValue(1_700_000.0, Provenance(Source.UNKNOWN, _RECORDED_AT)).to_json()
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    store = CandidateStore()
    store.record_candidate(
        "your_ira", 2_000_000.0, Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync")
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def _render_with_pending_your_ira() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup.command_center import render_command_center

    st.session_state["_pending_review"] = {"your_ira"}
    render_command_center(Household())


def test_command_center_renders_and_shows_pending_candidate(
    clean_command_center_caches,
) -> None:
    _seed_pending_your_ira()

    at = AppTest.from_function(_render_with_pending_your_ira)
    at.run()

    assert not at.exception
    assert at.metric[0].value == "1"

    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)
    assert "2,000,000" in rendered_text  # the FINEXTRACT_LIVE candidate value
    assert "1,700,000" in rendered_text  # the currently-committed value


def test_command_center_no_pending_shows_reconciled_message(
    clean_command_center_caches,
) -> None:
    def _render_no_pending() -> None:
        import streamlit as st

        from models.household import Household
        from views.setup.command_center import render_command_center

        st.session_state["_pending_review"] = set()
        render_command_center(Household())

    at = AppTest.from_function(_render_no_pending)
    at.run()

    assert not at.exception
    assert len(at.success) == 1
    assert "reconciled" in at.success[0].value.lower()


def test_confirm_button_commits_chosen_source_and_syncs_session(
    clean_command_center_caches,
) -> None:
    _seed_pending_your_ira()

    at = AppTest.from_function(_render_with_pending_your_ira)
    at.run()
    assert not at.exception

    at.button(key="confirm_your_ira").click().run()

    assert not at.exception
    assert at.session_state["your_ira"] == 2_000_000.0

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == 2_000_000.0
    assert committed_json["your_ira"]["source"] == "FINEXTRACT_LIVE"

    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    choice = choices.get("your_ira")
    assert choice is not None
    assert choice.source == Source.FINEXTRACT_LIVE


def _canned_sync_everything_result() -> SyncEverythingResult:
    """A canned SyncEverythingResult with a distinguishable count per source."""
    scan_result = ScanIngestResult(
        brokerage_count=1,
        form_1040_count=1,
        koinly_count=0,
        skipped_count=0,
        unrecognized_count=0,
        magi_candidates_recorded=1,
        errors=[("bad.pdf", "unreadable")],
        raw=PdfImportResult(),
        pdf_cache={},
    )
    return SyncEverythingResult(
        portfolio=PortfolioSyncSummary(candidates_recorded=2, server_available=True, error=None),
        ss=SsSyncSummary(candidates_recorded=1, warnings=[]),
        scan=ScanSyncSummary(result=scan_result, error=None),
    )


def _render_sync_everything() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup.command_center import render_command_center

    # 3 pending fields, one contributed by each source (portfolio/SS/scan) --
    # exercises the EXISTING review-gate metric, not a reimplementation of it.
    st.session_state["_pending_review"] = {"your_ira", "your_ss_fra", "prior_year_magi.2023"}
    render_command_center(Household())


def test_sync_everything_button_invokes_handler_and_renders_summary(
    clean_command_center_caches,
) -> None:
    import views.setup.command_center as command_center_mod

    mock_sync = MagicMock(return_value=_canned_sync_everything_result())

    at = AppTest.from_function(_render_sync_everything)
    with patch.object(command_center_mod, "sync_everything", mock_sync):
        at.run()
        assert not at.exception

        at.button(key="sync_everything_btn").click().run()
        assert not at.exception

    mock_sync.assert_called_once()

    rendered = "\n".join(i.value for i in at.info)
    assert "portfolio: 2 candidates" in rendered
    assert "SS: 1 candidates" in rendered
    assert "scan: 3 files, 1 errors" in rendered

    # Pending count reflects contributions from all three sources (existing
    # review-gate mechanism, untouched by this change).
    assert at.metric[0].value == "3"
