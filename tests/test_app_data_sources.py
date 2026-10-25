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
``.gitignore``. Every test runs on its own in-memory store, so a developer's personal
committed/candidate state never leaks in. ``_suppress_snapshot_autoload`` is
pre-seeded so a developer's real ``.portfolio_cache.json`` (personal data)
cannot influence the migration-identity assertion below.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from streamlit.testing.v1 import AppTest

from config.defaults import DEFAULTS
from engine.data_sources.committed import load_committed
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot, save_snapshot
from engine.store import get_store
from engine.store.namespaces import PORTFOLIO
from models.sourced import Provenance, Source, SourcedValue

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
REPO_ROOT = APP_PATH.parent
# Full-page AppTest runs exercise the projection engine. Streamlit's 3s
# per-run default is not a behavioural signal here: settrace-based coverage
# (CI's Python 3.11 job; GitHub runners are also slower than a dev box)
# multiplies render time ~10-30x, so an explicit generous timeout is used.
_PAGE_TIMEOUT = 60


def test_setup_page_renders_without_exception() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception


def test_dashboard_page_renders_without_exception() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception

    at.session_state["nav_page"] = "overview"
    at.run()

    assert not at.exception


def test_get_household_exposes_pending_review_gate() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception
    assert "_pending_review" in at.session_state


def test_fresh_run_creates_committed_file_with_migration_identity(monkeypatch) -> None:
    """First-ever load (no committed baseline on disk) migrates the session
    default numerically unchanged — the "migration is a numeric no-op"
    invariant resolve_for_app relies on.

    A genuinely fresh instance now auto-defaults its identity inside
    ``render_command_center`` (see ``views/setup/command_center.py``), which
    unlocks the Data tab's two import sections in this same render -- unlike
    before, when a no-owner instance kept them behind a placeholder. Neutralise
    their heavy/network-facing entry points the same way
    ``tests/test_setup_shell_characterization.py``'s fixture already does, so
    this migration-identity assertion isn't coupled to their behaviour.
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod
    from engine.data_sources.paths import COMMITTED_PATH

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception

    committed_path = COMMITTED_PATH
    assert committed_path.exists()

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["your_ira"]["value"] == DEFAULTS["your_ira"]


def test_manual_setup_edit_to_sourced_field_sticks_across_reruns() -> None:
    """Regression (Wave 3.1b): apply_committed froze sourced fields onto the
    committed baseline on every render, so a Setup-form edit to your_ira was
    silently reverted on the very next rerun (the FinExtract snapshot no
    longer writes st.session_state.your_ira directly, so the number_input is
    the only writer left — its edits must stick, not get clobbered back to
    the frozen committed value).
    """
    from engine.data_sources.paths import COMMITTED_PATH

    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception

    committed_path = COMMITTED_PATH
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


def test_workplace_plan_session_state_flows_to_household() -> None:
    """W3: your_has_workplace_plan in session_state flows through get_household()
    into the Household consumed by views. Verified via the Roth Eligibility
    read-only redirect display, which renders hh.your_has_workplace_plan
    directly (plain scalar — no candidate/committed machinery involved)."""
    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["your_has_workplace_plan"] = False
    at.run()
    assert not at.exception

    at.session_state["nav_page"] = "elig"
    at.run()
    assert not at.exception

    joined = "\n".join(el.value for group in ("markdown", "caption") for el in getattr(at, group))
    assert "You have a workplace plan (401k/403b)" in joined
    assert "### No" in joined


def test_ytd_snapshot_autoload_respects_suppress_flag() -> None:
    """C111 (audit-0805, MEDIUM): app.py's ytd_snapshot autoload guard lacks
    the ``_suppress_snapshot_autoload`` check present on the portfolio_snapshot
    autoload immediately above it, so a stale on-disk ``.ytd_cache.json``
    repopulates session_state even when the reset-to-demo flow explicitly set
    the suppress sentinel."""
    from engine.portfolio_sync import save_ytd_snapshot
    from models.ytd_income import YTDSnapshot

    save_ytd_snapshot(YTDSnapshot(wages_ytd=250_000.0, nqo_exercise_ytd=96_000.0))

    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "review"
    at.run()

    assert not at.exception
    assert "ytd_snapshot" not in at.session_state, (
        "Stale on-disk YTD snapshot repopulated session_state despite "
        "_suppress_snapshot_autoload=True"
    )


def test_finextract_sync_snapshot_does_not_bypass_the_gate() -> None:
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
    from engine.data_sources.paths import COMMITTED_PATH

    get_store().delete(PORTFOLIO)

    try:
        committed_path = COMMITTED_PATH
        committed_json = {
            "your_ira": SourcedValue(
                1_600_000.0, Provenance(Source.UNKNOWN, datetime(2026, 1, 1))
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

        at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
        at.session_state["your_ira"] = 1_600_000
        at.run()

        assert not at.exception
        assert "your_ira" in at.session_state["_pending_review"]

        reloaded = load_committed(committed_path)
        assert reloaded is not None
        assert reloaded["your_ira"]["value"] == 1_600_000.0
        assert reloaded["your_ira"]["source"] != "MANUAL"
    finally:
        get_store().delete(PORTFOLIO)


def test_resolved_sourced_values_are_written_back_to_session_state() -> None:
    """Post-resolve writeback: on a fresh (no prior committed baseline)
    load, session_state mirrors the migrated/committed household every
    render — including through the stock_price_now -> "stock_price" alias.
    """
    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()

    assert not at.exception
    assert at.session_state["your_ira"] == DEFAULTS["your_ira"]
    assert at.session_state["stock_price"] == DEFAULTS["stock_price_now"]


def test_command_center_stock_price_confirm_sticks_and_next_render_does_not_revert() -> None:
    """Bug 2 regression, end-to-end through the real app.py router + Setup
    page's Portfolio tab (``render_options_partial`` as of Task 5 of the
    ui-shell-theme-toggle plan — previously Command Center's generic loop,
    removed in Task 4): confirming a pending stock_price_now candidate
    must (a) write session_state["stock_price"] — the aliased key the Setup
    widget actually reads, not the raw "stock_price_now" field key — and (b)
    stick on the next render rather than reverting because that aliased key
    was left stale.

    Pre-seeds session_state["stock_price"] to match the committed baseline
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
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

    recorded_at = datetime(2026, 1, 1)
    committed_path = COMMITTED_PATH
    committed_path.write_text(
        json.dumps(
            {
                "stock_price_now": SourcedValue(
                    100.0, Provenance(Source.UNKNOWN, recorded_at)
                ).to_json()
            }
        )
    )
    store = CandidateStore()
    store.record_candidate(
        "stock_price_now", 250.0, Provenance(Source.FINEXTRACT_LIVE, recorded_at, "live sync")
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)

    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["stock_price"] = 100
    at.session_state["nav_page"] = "review"
    at.run()
    assert not at.exception

    at.button(key="confirm_stock_price_now").click().run()
    assert not at.exception

    # (a) the confirm handler wrote the ALIASED key, not "stock_price_now".
    assert at.session_state["stock_price"] == 250.0
    assert "stock_price_now" not in at.session_state

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["stock_price_now"]["value"] == 250.0
    assert committed_json["stock_price_now"]["source"] == "FINEXTRACT_LIVE"

    # (b) the next render (a fresh AppTest instance carrying forward the
    # session_state the first instance's writeback produced) must not revert
    # the confirm.
    at2 = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    at2.session_state["_suppress_snapshot_autoload"] = True
    at2.session_state["stock_price"] = at.session_state["stock_price"]
    at2.session_state["nav_page"] = "review"
    at2.run()
    assert not at2.exception

    committed_json = load_committed(committed_path)
    assert committed_json is not None
    assert committed_json["stock_price_now"]["value"] == 250.0
    assert committed_json["stock_price_now"]["source"] == "FINEXTRACT_LIVE"
