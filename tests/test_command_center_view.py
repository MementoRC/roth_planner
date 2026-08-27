"""AppTest smoke tests for views/setup/command_center.py — the sync trigger +
the per-field sourced-value governance gate.

Uses ``streamlit.testing.v1.AppTest.from_function`` (mirrors the pattern in
tests/test_auto_optimizer_view.py — the wrapped function must be fully
self-contained, all imports/object construction inside its body).

The Command Center's cache paths (``engine.data_sources.paths``) are
repo-root-anchored regardless of cwd (matching every other engine/data_sources
cache file), so this test seeds/cleans those exact files directly rather than
using a tmp cwd — same isolation approach as tests/test_app_data_sources.py.

Per-field governance-card tests for ``your_ira`` (accounts),
``txn_price_now``/``grants`` (options), and ``prior_year_magi.<year>``
(assumptions) live HERE again: the Task-4/5/7 relocation of these cards into
each field's owning partial has been reversed, so ``render_command_center``
is once again the sole renderer of every ``trust_*``/``manual_*``/
``confirm_*`` governance-card widget (see that module's docstring). The
owning partials (``tests/test_setup_accounts_partial.py``,
``tests/test_setup_options_partial.py``,
``tests/test_setup_assumptions_partial.py``) now carry a negative regression
test each, asserting their partial does NOT render these cards.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.resolver import GRANTS_KEY
from engine.data_sources.scan_ingest import ScanIngestResult
from engine.pdf_import import PdfImportResult
from models.sourced import Provenance, Source, SourcedValue
from views._shared import PortfolioSyncSummary, ScanSyncSummary, SsSyncSummary, SyncEverythingResult

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, 0)

# clean_command_center_caches fixture is provided by tests/conftest.py (cleans
# up the 3 Command Center cache files BEFORE and AFTER each test) -- do not
# redeclare it here with the same name. A same-named local fixture silently
# shadows the conftest one for every test in this module, and this file's
# prior copy only cleaned up AFTER, leaking stale cache state into whichever
# test ran next when a full-suite run was interrupted mid-test.


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


# --- Per-field governance-card tests (Task-4/5/7 reversal) ------------------
#
# Restored here from tests/test_setup_accounts_partial.py,
# tests/test_setup_options_partial.py, and tests/test_setup_assumptions_partial.py
# (each of which now carries a negative regression test instead, asserting
# its partial does NOT render these cards).


def _render_command_center_with_pending(pending: set[str]) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup.command_center import render_command_center

    st.session_state["_pending_review"] = pending
    render_command_center(Household())


def _seed_pending_your_ira() -> None:
    """Committed your_ira=1.7M/UNKNOWN + a FINEXTRACT_LIVE your_ira=2.0M candidate."""
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

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


def test_command_center_renders_and_shows_pending_candidate(
    clean_command_center_caches,
) -> None:
    _seed_pending_your_ira()

    at = AppTest.from_function(_render_command_center_with_pending, kwargs={"pending": {"your_ira"}})
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "2,000,000" in rendered_text  # the FINEXTRACT_LIVE candidate value
    assert "1,700,000" in rendered_text  # the currently-committed value


def test_confirm_button_commits_chosen_source_and_syncs_session(
    clean_command_center_caches,
) -> None:
    _seed_pending_your_ira()

    at = AppTest.from_function(_render_command_center_with_pending, kwargs={"pending": {"your_ira"}})
    at.run()
    assert not at.exception

    at.button(key="confirm_your_ira").click().run()

    assert not at.exception
    assert at.session_state["your_ira"] == 2_000_000.0

    from engine.data_sources.paths import COMMITTED_PATH, TRUST_CHOICES_PATH

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == 2_000_000.0
    assert committed_json["your_ira"]["source"] == "FINEXTRACT_LIVE"

    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    choice = choices.get("your_ira")
    assert choice is not None
    assert choice.source == Source.FINEXTRACT_LIVE


def _seed_pending_txn_price_now() -> None:
    """Committed txn_price_now=100/UNKNOWN + a FINEXTRACT_LIVE=250 candidate."""
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

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


def test_command_center_shows_pending_txn_price_now_candidate(
    clean_command_center_caches,
) -> None:
    _seed_pending_txn_price_now()

    at = AppTest.from_function(
        _render_command_center_with_pending, kwargs={"pending": {"txn_price_now"}}
    )
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "250" in rendered_text  # the FINEXTRACT_LIVE candidate value
    assert "100" in rendered_text  # the currently-committed value


def test_command_center_confirm_txn_price_now_syncs_the_aliased_session_key(
    clean_command_center_caches,
) -> None:
    """Bug 2 regression: the Household attr is txn_price_now, but the Setup
    number_input widget reads/writes session_state["txn_price"] (alias). The
    confirm handler must write the SAME aliased key, or the next
    reconcile_manual_edits sees session_state.txn_price still stale and
    reverts the confirm.
    """
    _seed_pending_txn_price_now()

    at = AppTest.from_function(
        _render_command_center_with_pending, kwargs={"pending": {"txn_price_now"}}
    )
    at.run()
    assert not at.exception

    at.button(key="confirm_txn_price_now").click().run()

    assert not at.exception
    assert at.session_state["txn_price"] == 250.0
    assert "txn_price_now" not in at.session_state

    from engine.data_sources.paths import COMMITTED_PATH

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["txn_price_now"]["value"] == 250.0
    assert committed_json["txn_price_now"]["source"] == "FINEXTRACT_LIVE"


def _seed_pending_grants() -> None:
    """Committed 1 grant/UNKNOWN + a FINEXTRACT_LIVE 2-grant candidate list."""
    import dataclasses

    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
    from models.grants import StockGrant

    committed_grant = StockGrant(year=2020, strike=130.0, shares=100, expiry_year=2030)
    committed_json = {
        GRANTS_KEY: {
            "data": [dataclasses.asdict(committed_grant)],
            "prov": [Provenance(Source.UNKNOWN, _RECORDED_AT).to_json()],
        }
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    candidate_grants = [
        StockGrant(year=2019, strike=104.0, shares=200, expiry_year=2029),
        StockGrant(year=2021, strike=169.0, shares=150, expiry_year=2031),
    ]
    store = CandidateStore()
    store.record_candidate(
        GRANTS_KEY,
        candidate_grants,
        Provenance(Source.FINEXTRACT_LIVE, _RECORDED_AT, "live sync"),
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def test_command_center_shows_pending_grants_candidate(clean_command_center_caches) -> None:
    _seed_pending_grants()

    at = AppTest.from_function(_render_command_center_with_pending, kwargs={"pending": {GRANTS_KEY}})
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "2 grants" in rendered_text  # the FINEXTRACT_LIVE candidate value
    assert "1 grants" in rendered_text  # the currently-committed value


def test_command_center_confirm_grants_commits_candidate_list(
    clean_command_center_caches,
) -> None:
    _seed_pending_grants()

    at = AppTest.from_function(_render_command_center_with_pending, kwargs={"pending": {GRANTS_KEY}})
    at.run()
    assert not at.exception

    at.button(key="confirm_grants").click().run()

    assert not at.exception
    from engine.data_sources.paths import COMMITTED_PATH, TRUST_CHOICES_PATH

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert len(committed_json[GRANTS_KEY]["data"]) == 2
    assert {g["year"] for g in committed_json[GRANTS_KEY]["data"]} == {2019, 2021}

    choices = ChoiceMap.load(TRUST_CHOICES_PATH)
    choice = choices.get(GRANTS_KEY)
    assert choice is not None
    assert choice.source == Source.FINEXTRACT_LIVE


def _seed_pending_prior_year_magi_2024() -> None:
    """Committed 2024 MAGI=$200k/UNKNOWN + a Source.PDF $290k candidate."""
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

    committed_json = {
        "prior_year_magi": {
            "data": {"2024": 200_000.0},
            "prov": {"2024": Provenance(Source.UNKNOWN, _RECORDED_AT).to_json()},
        }
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    store = CandidateStore()
    store.record_candidate(
        "prior_year_magi.2024",
        290_000.0,
        Provenance(Source.PDF, _RECORDED_AT, "Form 1040 PDF"),
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def test_command_center_shows_pending_prior_year_magi_candidate(
    clean_command_center_caches,
) -> None:
    _seed_pending_prior_year_magi_2024()

    at = AppTest.from_function(
        _render_command_center_with_pending, kwargs={"pending": {"prior_year_magi.2024"}}
    )
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "290,000" in rendered_text  # the Source.PDF candidate value
    assert "200,000" in rendered_text  # the currently-committed value


def test_command_center_confirm_prior_year_magi_syncs_session_state(
    clean_command_center_caches,
) -> None:
    """Confirming prior_year_magi.2024 must update BOTH the on-disk committed
    JSON and st.session_state["prior_year_magi"] (int-keyed dict), and clear
    the field from _pending_review — same shape ``_apply_confirm_to_session``
    already guarantees for the other governed fields.
    """
    _seed_pending_prior_year_magi_2024()

    at = AppTest.from_function(
        _render_command_center_with_pending, kwargs={"pending": {"prior_year_magi.2024"}}
    )
    at.run()
    assert not at.exception

    at.button(key="confirm_prior_year_magi.2024").click().run()

    assert not at.exception
    assert at.session_state["prior_year_magi"][2024] == 290_000.0
    assert "prior_year_magi.2024" not in at.session_state["_pending_review"]

    from engine.data_sources.paths import COMMITTED_PATH

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["prior_year_magi"]["data"]["2024"] == 290_000.0
    assert committed_json["prior_year_magi"]["prov"]["2024"]["source"] == "PDF"
