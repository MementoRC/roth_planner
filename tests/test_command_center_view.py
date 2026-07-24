"""AppTest smoke tests for views/setup/command_center.py — the sync trigger +
pending-review status (Command Center no longer renders per-field
trust/manual/confirm governance cards as of Task 4 of the
ui-shell-theme-toggle plan; see that module's docstring).

Uses ``streamlit.testing.v1.AppTest.from_function`` (mirrors the pattern in
tests/test_auto_optimizer_view.py — the wrapped function must be fully
self-contained, all imports/object construction inside its body).

The Command Center's cache paths (``engine.data_sources.paths``) are
repo-root-anchored regardless of cwd (matching every other engine/data_sources
cache file), so this test seeds/cleans those exact files directly rather than
using a tmp cwd — same isolation approach as tests/test_app_data_sources.py.

Per-field governance-card tests for ``your_ira``/``your_roth``/
``your_ss_fra`` (formerly rendered here) moved to
``tests/test_setup_accounts_partial.py`` — that behavior now lives in
``views/setup/_partials.py:render_accounts_partial``, co-located with each
field's own balance widget instead of Command Center's old generic loop.
The ``txn_price_now`` card test below is SKIPPED (not deleted): that field's
card has no owning partial yet — it moves to Options in Task 5.
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


@pytest.mark.skip(
    reason=(
        "txn_price_now's trust/manual/confirm card has no owning partial yet — "
        "it moves into views/setup/_partials.py:render_options_partial in Task 5 "
        "of docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md. Command "
        "Center's generic per-field loop (which used to render this card) was "
        "removed in Task 4 (DuplicateWidgetID fix) before Task 5 lands."
    )
)
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


def test_sync_everything_clears_suppress_snapshot_autoload_flag(
    clean_command_center_caches,
) -> None:
    """P4-1: Sync everything must clear the Reset-to-demo autoload-suppression flag.

    The only other code path that ever clears ``_suppress_snapshot_autoload``
    (``_apply_portfolio_snapshot`` in views/setup/_state.py) has no live
    caller, so without this the flag -- and the autosave-to-.user_defaults.json
    calls in parameters.py/option_exercise.py that are gated on it -- stay
    silently disabled for the rest of the session after any Reset-to-demo.
    """
    import views.setup.command_center as command_center_mod

    mock_sync = MagicMock(return_value=_canned_sync_everything_result())

    at = AppTest.from_function(_render_sync_everything)
    with patch.object(command_center_mod, "sync_everything", mock_sync):
        at.run()
        at.session_state["_suppress_snapshot_autoload"] = True

        at.button(key="sync_everything_btn").click().run()
        assert not at.exception

    assert "_suppress_snapshot_autoload" not in at.session_state
