"""Integration smoke test for app.py's Setup / Command Center wiring (Wave 3.1b).

Runs the REAL app.py end-to-end via ``streamlit.testing.v1.AppTest.from_file``
to verify ``get_household()`` is correctly wired to
``engine.data_sources.orchestrator.resolve_for_app`` and that the per-load
snapshot-clobber block has been removed.

Isolation: the three new Setup / Command Center cache files
(``.candidate_store.json``, ``.trust_choices.json``, ``.committed_household.json``)
are written at repo-root (mirroring the existing ``__file__``-anchored cache
path convention used throughout ``engine/*`` — see e.g.
``engine/exercise_schedule_store.py``). They are already covered by
``.gitignore``. The shared ``clean_command_center_caches`` fixture
(``tests/conftest.py``) deletes them before AND after each test so repeated
local test runs never leave stray state behind and a developer's personal
committed/candidate state never leaks in. ``_suppress_snapshot_autoload`` is
pre-seeded so a developer's real ``.portfolio_cache.json`` (personal data)
cannot influence the migration-identity assertion below.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config.defaults import DEFAULTS
from engine.data_sources.committed import load_committed
from engine.portfolio_sync import _CACHE_PATH as _PORTFOLIO_CACHE_PATH
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot, save_snapshot
from models.sourced import Provenance, Source, SourcedValue

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
REPO_ROOT = APP_PATH.parent

# Mirrors tests/conftest.py's _COMMAND_CENTER_CACHE_FILES — several tests
# below need an explicit mid-test pre-clean (e.g. before writing a seeded
# committed baseline), in addition to the shared fixture's before/after clean.
_NEW_CACHE_FILES = [
    REPO_ROOT / ".candidate_store.json",
    REPO_ROOT / ".trust_choices.json",
    REPO_ROOT / ".committed_household.json",
]


def test_setup_page_renders_without_exception(clean_command_center_caches) -> None:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception


def test_dashboard_page_renders_without_exception(clean_command_center_caches) -> None:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception

    at.sidebar.radio[0].set_value("📊 Dashboard")
    at.run()

    assert not at.exception


def test_get_household_exposes_pending_review_gate(clean_command_center_caches) -> None:
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception
    assert "_pending_review" in at.session_state


def test_fresh_run_creates_committed_file_with_migration_identity(
    clean_command_center_caches,
) -> None:
    """First-ever load (no committed baseline on disk) migrates the session
    default numerically unchanged — the "migration is a numeric no-op"
    invariant resolve_for_app relies on.
    """
    for p in _NEW_CACHE_FILES:
        p.unlink(missing_ok=True)

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception

    committed_path = REPO_ROOT / ".committed_household.json"
    assert committed_path.exists()

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == DEFAULTS["your_ira"]


def test_manual_setup_edit_to_sourced_field_sticks_across_reruns(
    clean_command_center_caches,
) -> None:
    """Regression (Wave 3.1b): apply_committed froze sourced fields onto the
    committed baseline on every render, so a Setup-form edit to your_ira was
    silently reverted on the very next rerun (the FinExtract snapshot no
    longer writes st.session_state.your_ira directly, so the number_input is
    the only writer left — its edits must stick, not get clobbered back to
    the frozen committed value).
    """
    for p in _NEW_CACHE_FILES:
        p.unlink(missing_ok=True)

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception

    committed_path = REPO_ROOT / ".committed_household.json"
    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == DEFAULTS["your_ira"]

    edited_value = DEFAULTS["your_ira"] + 500_000
    at.session_state["your_ira"] = edited_value
    at.run()
    assert not at.exception

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == edited_value
    assert committed_json["your_ira"]["source"] == "MANUAL"


def test_workplace_plan_session_state_flows_to_household(clean_command_center_caches) -> None:
    """W3: your_has_workplace_plan in session_state flows through get_household()
    into the Household consumed by views. Verified via the Roth Eligibility
    read-only redirect display, which renders hh.your_has_workplace_plan
    directly (plain scalar — no candidate/committed machinery involved)."""
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["your_has_workplace_plan"] = False
    at.run()
    assert not at.exception

    at.sidebar.radio[0].set_value("✅ Roth Eligibility")
    at.run()
    assert not at.exception

    joined = "\n".join(
        el.value for group in ("markdown", "caption") for el in getattr(at, group)
    )
    assert "You have a workplace plan (401k/403b)" in joined
    assert "### No" in joined


def test_finextract_sync_snapshot_does_not_bypass_the_gate(
    clean_command_center_caches,
) -> None:
    """Bug 1 regression: a fresh FinExtract snapshot (loaded at boot from the
    on-disk portfolio cache) must be recorded as a FINEXTRACT_LIVE candidate
    (pending review), never written straight into
    st.session_state["your_ira"] — that direct write makes
    reconcile_manual_edits see a diff against the committed baseline and
    wrongly promote the live value to a MANUAL commit, silently bypassing the
    freeze-until-confirm gate.

    Pre-seeds session_state["your_ira"] to match the existing committed
    baseline (simulating a session that already has the confirmed value
    loaded, as in normal continued use) so the only possible source of a
    "your_ira" mismatch is the snapshot autoload block itself — isolating
    the exact mechanism the bug report describes, rather than the unrelated
    seeded-config-default-vs-committed-baseline gap that exists for a
    never-before-seen browser session.
    """
    for p in _NEW_CACHE_FILES:
        p.unlink(missing_ok=True)
    _PORTFOLIO_CACHE_PATH.unlink(missing_ok=True)

    try:
        committed_path = REPO_ROOT / ".committed_household.json"
        committed_json = {
            "your_ira": SourcedValue(
                1_700_000.0, Provenance(Source.UNKNOWN, datetime(2026, 1, 1))
            ).to_json()
        }
        committed_path.write_text(json.dumps(committed_json))

        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(account_type="trad_ira", owner="you", total_value=2_000_000.0)
            ],
            server_available=True,
        )
        save_snapshot(snap)

        at = AppTest.from_file(str(APP_PATH))
        at.session_state["your_ira"] = 1_700_000
        at.run()

        assert not at.exception
        assert "your_ira" in at.session_state["_pending_review"]

        reloaded = load_committed(committed_path)
        assert reloaded is not None
        assert reloaded["your_ira"]["value"] == 1_700_000.0
        assert reloaded["your_ira"]["source"] != "MANUAL"
    finally:
        _PORTFOLIO_CACHE_PATH.unlink(missing_ok=True)


def test_resolved_sourced_values_are_written_back_to_session_state(
    clean_command_center_caches,
) -> None:
    """Post-resolve writeback: on a fresh (no prior committed baseline)
    load, session_state mirrors the migrated/committed household every
    render — including through the txn_price_now -> "txn_price" alias.
    """
    for p in _NEW_CACHE_FILES:
        p.unlink(missing_ok=True)

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception
    assert at.session_state["your_ira"] == DEFAULTS["your_ira"]
    assert at.session_state["txn_price"] == DEFAULTS["stock_price_now"]


@pytest.mark.skip(
    reason=(
        "txn_price_now's trust/manual/confirm card has no owning partial yet — "
        "it moves into views/setup/_partials.py:render_options_partial in Task 5 "
        "of docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md. Command "
        "Center's generic per-field loop (which used to render this card, "
        "including the confirm_txn_price_now button this test drives) was "
        "removed in Task 4 (DuplicateWidgetID fix) before Task 5 lands."
    )
)
def test_command_center_txn_price_confirm_sticks_and_next_render_does_not_revert(
    clean_command_center_caches,
) -> None:
    """Bug 2 regression, end-to-end through the real app.py router + Setup
    page's Command Center tab: confirming a pending txn_price_now candidate
    must (a) write session_state["txn_price"] — the aliased key the Setup
    widget actually reads, not the raw "txn_price_now" field key — and (b)
    stick on the next render rather than reverting because that aliased key
    was left stale.

    Pre-seeds session_state["txn_price"] to match the committed baseline
    before the first run (simulating a continuing session, not a
    never-before-seen browser tab) so reconcile_manual_edits only ever sees
    a genuine change caused by the confirm itself. Step (b) is checked via a
    second, independent AppTest instance carrying forward the session state
    the first instance's writeback would have produced — two `.run()` calls
    plus a button click on a single AppTest instance hits an unrelated
    streamlit.testing.v1 internal limitation (a stale keyed-widget lookup)
    when the confirmed field's radio disappears from a subsequent render.
    """
    from engine.data_sources.candidate_store import CandidateStore
    from engine.data_sources.choices import ChoiceMap
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, TRUST_CHOICES_PATH

    for p in _NEW_CACHE_FILES:
        p.unlink(missing_ok=True)

    recorded_at = datetime(2026, 1, 1)
    committed_path = REPO_ROOT / ".committed_household.json"
    committed_path.write_text(
        json.dumps(
            {"txn_price_now": SourcedValue(100.0, Provenance(Source.UNKNOWN, recorded_at)).to_json()}
        )
    )
    store = CandidateStore()
    store.record_candidate(
        "txn_price_now", 250.0, Provenance(Source.FINEXTRACT_LIVE, recorded_at, "live sync")
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)

    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["txn_price"] = 100
    at.run()
    assert not at.exception

    at.button(key="confirm_txn_price_now").click().run()
    assert not at.exception

    # (a) the confirm handler wrote the ALIASED key, not "txn_price_now".
    assert at.session_state["txn_price"] == 250.0
    assert "txn_price_now" not in at.session_state

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["txn_price_now"]["value"] == 250.0
    assert committed_json["txn_price_now"]["source"] == "FINEXTRACT_LIVE"

    # (b) the next render (a fresh AppTest instance carrying forward the
    # session_state the first instance's writeback produced) must not revert
    # the confirm.
    at2 = AppTest.from_file(str(APP_PATH))
    at2.session_state["_suppress_snapshot_autoload"] = True
    at2.session_state["txn_price"] = at.session_state["txn_price"]
    at2.run()
    assert not at2.exception

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["txn_price_now"]["value"] == 250.0
    assert committed_json["txn_price_now"]["source"] == "FINEXTRACT_LIVE"
