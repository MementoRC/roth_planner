"""Cross-store flows (spec §2, store PR 4): a failure part-way through a flow
that writes several stores leaves every store, and the session keys the flow
touched, as they were.

Reuses the mocked-``st`` harnesses of the per-flow test modules (imported as
modules, so their tests are not collected twice). Synthetic values only.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import engine.data_sources.scan_ingest as scan_ingest_mod
import views.setup.data_bridge as data_bridge_mod
from engine.data_sources.orchestrator import BUNDLE_SCALARS_KEY
from engine.data_sources.scan_ingest import ScanIngestResult
from engine.netbenefits_statement_pdf import parse_netbenefits_text
from engine.pdf_import import PdfImportResult
from engine.portfolio_sync.portfolio import save_snapshot
from engine.portfolio_sync.shapes import AccountSummary, PortfolioSnapshot
from engine.ssa_statement_pdf import parse_ssa_statement_text
from engine.store import StoreUnavailableError
from engine.store.namespaces import (
    BROKERAGE_STATEMENTS,
    CANDIDATE_STORE,
    IRA_ACCOUNT_LINKS,
    PDF_LEDGER,
    PORTFOLIO,
    SSA,
    SSA_PDF,
    TAX_PDF,
    YTD,
)
from models.ytd_income import YTDSnapshot
from tests import test_sync_scan_ira_statements as ira_tests
from tests import test_sync_scan_netbenefits as nb_tests
from tests import test_sync_scan_uploader as upload_tests
from tests.test_netbenefits_statement_pdf import PAGES as NB_PAGES
from tests.test_ssa_statement_pdf import PAGES as SSA_PAGES
from tests.test_store_provenance_stores import _form_1040, _ssa_snapshot
from views.setup._partials import _accounts as accounts_mod
from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _locked(*_args, **_kwargs):
    raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")


def _record_1040s() -> None:
    scan_ingest_mod._record_scan_result(
        PdfImportResult(form_1040_records=_form_1040(50_000.0)), recorded_at=None
    )


def test_a_1040_scan_whose_candidate_write_fails_saves_no_1040_records(store, monkeypatch):
    monkeypatch.setattr(scan_ingest_mod, "record_magi_candidates", _locked)
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _record_1040s()
    assert store.keys(TAX_PDF) == []


def test_a_corrupt_ssa_cache_also_drops_the_synced_candidate(store, monkeypatch):
    """Decision 6: "this sync was not saved" is now true of the candidate too."""
    store.put(SSA, [1, 2])  # wrong shape: save_ssa_snapshot refuses it
    session: dict = {}
    monkeypatch.setattr(accounts_mod.st, "session_state", session)
    monkeypatch.setattr(accounts_mod, "fetch_ssa_snapshot", lambda: _ssa_snapshot(1_000.0))
    warning = accounts_mod._sync_ssa_for("you", 67)
    assert warning is not None
    assert "not saved" in warning
    assert store.keys(CANDIDATE_STORE) == []
    assert store.get(SSA) == [1, 2]
    assert "ssa_snapshot_you" not in session


def _apply_ira(mock_st) -> None:
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "record_ira_balance_candidate", _locked),
    ):
        sync_scan_mod._render_ira_statement_review(
            owner_map={}, instance_owner="you", identity_set=True
        )


def test_ira_apply_saves_no_snapshot_or_link_when_the_candidate_write_fails(store):
    """The owner's PR 4 bug: the link map used to stay written after a later failure."""
    before = ira_tests._snap()
    expected = copy.deepcopy(before)
    rec = ira_tests._rec()
    mock_st, state = ira_tests._mock_st(
        state_extra={"ira_statements": [rec], "portfolio_snapshot": before},
        clicks={"ira_statement_apply_0"},
        radio_choice="you",
        link_choice="none",
    )
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _apply_ira(mock_st)
    assert store.keys(PORTFOLIO) == []
    assert store.keys(IRA_ACCOUNT_LINKS) == []
    assert state["portfolio_snapshot"] == expected
    assert state["ira_statements"] == [rec]
    mock_st.rerun.assert_not_called()


def _apply_netbenefits(mock_st) -> None:
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "load_snapshot", return_value=nb_tests._snap()),
        patch.object(sync_scan_mod, "record_ira_balance_candidate", _locked),
    ):
        sync_scan_mod._render_netbenefits_review(
            owner_map={}, instance_owner="you", identity_set=True
        )


def test_netbenefits_apply_saves_no_snapshot_when_the_candidate_write_fails(store):
    stmt = parse_netbenefits_text(NB_PAGES)
    mock_st, state = nb_tests._mock_st(
        state_extra={"netbenefits_statements": [stmt]},
        clicks={"netbenefits_apply_0"},
        radio_choice="spouse",
    )
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _apply_netbenefits(mock_st)
    assert store.keys(PORTFOLIO) == []
    assert "portfolio_snapshot" not in state
    assert state["netbenefits_statements"] == [stmt]


def _apply_ssa(mock_st) -> None:
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "save_ssa_pdf_estimates", _locked),
    ):
        sync_scan_mod._render_ssa_review(owner_map={}, instance_owner="you", identity_set=True)


def test_ssa_apply_records_no_candidate_when_the_estimates_write_fails(store):
    stmt = parse_ssa_statement_text(SSA_PAGES)
    mock_st, state = nb_tests._mock_st(
        state_extra={"ssa_statements": [stmt]},
        clicks={"ssa_apply_0"},
        radio_choice="you",
    )
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _apply_ssa(mock_st)
    assert store.keys(CANDIDATE_STORE) == []
    assert store.keys(SSA_PDF) == []
    assert state["ssa_statements"] == [stmt]


def _scan(mock_st, result: PdfImportResult) -> None:
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "save_ytd_snapshot", _locked),
    ):
        sync_scan_mod._apply_scan_result(
            result,
            ledger={"koinly": {}, "brokerage": {}},
            account_overrides={},
            owner_map={},
            instance_owner="you",
        )


def test_a_rolled_back_upload_apply_leaves_the_digests_so_a_retry_is_not_skipped(store):
    rec = ira_tests._rec(account_type="taxable", account_number="T1")
    parsed = ScanIngestResult(
        brokerage_count=1,
        form_1040_count=0,
        koinly_count=0,
        skipped_count=0,
        unrecognized_count=0,
        magi_candidates_recorded=0,
        errors=[],
        raw=PdfImportResult(brokerage_records=[rec]),
        pdf_cache={},
    )
    mock_run = MagicMock(return_value=parsed)
    mock_st, state = upload_tests._mock_st(
        session_extra={"instance_owner": "you"},
        uploaded_files=[upload_tests._FakeUploadedFile("a.pdf", data=b"synthetic-bytes")],
        button_clicks={"scan_uploaded_pdfs_btn"},
    )

    def _upload() -> None:
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch.object(sync_scan_mod, "run_uploaded_scan", mock_run),
            patch.object(sync_scan_mod, "save_ytd_snapshot", _locked),
        ):
            sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )

    for attempt in (1, 2):
        with pytest.raises(StoreUnavailableError, match="database is locked"):
            _upload()
        assert state["_pdf_scanned_digests"] == set()
        assert mock_run.call_count == attempt
    mock_st.success.assert_not_called()


def test_a_scan_whose_ytd_write_fails_saves_no_statement_or_ledger(store):
    mock_st, state = ira_tests._mock_st()
    rec = ira_tests._rec(account_type="taxable", account_number="T1")
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _scan(mock_st, PdfImportResult(brokerage_records=[rec]))
    assert store.keys(BROKERAGE_STATEMENTS) == []
    assert store.keys(PDF_LEDGER) == []
    assert "statement_by_account" not in state


def _taxable(total: float) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[AccountSummary(account_type="taxable", owner="you", total_value=total)]
    )


def test_a_bridge_import_that_fails_last_changes_no_store_and_stages_nothing(store, monkeypatch):
    save_snapshot(_taxable(1_000.0))
    before = store.get(PORTFOLIO)
    result = SimpleNamespace(
        snapshot=_taxable(2_000.0),
        ledger={"koinly": {}, "brokerage": {}},
        ytd=YTDSnapshot(),
        scalar_updates={"your_ira": 1_000},
        joint_conflicts=(),
        account_type_overrides={},
        magi_candidates={2024: 50_000.0},
        slot_captured_at={"you": "synthetic-later"},
    )
    ready = SimpleNamespace(
        bundle=None,
        plan=None,
        local=SimpleNamespace(account_type_overrides={}, slot_captured_at={}),
        selected=frozenset(),
        operator=None,
        data={"sections": {"setup_scalars": {}}},
        version="v5",
    )
    session: dict = {}
    saved_defaults: list = []
    monkeypatch.setattr(data_bridge_mod.st, "session_state", session)
    monkeypatch.setattr(data_bridge_mod, "apply_import", lambda *a, **k: result)
    monkeypatch.setattr(data_bridge_mod, "slots_with_replaced_accounts", lambda *a: [])
    monkeypatch.setattr(data_bridge_mod, "save_user_defaults", saved_defaults.append)
    monkeypatch.setattr(data_bridge_mod, "_settle_ytd", _locked)  # the LAST write fails
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        data_bridge_mod._apply_ready_import(ready, "synthetic.enc")
    assert store.get(PORTFOLIO) == before
    assert store.keys(YTD) == []
    assert store.keys(CANDIDATE_STORE) == []
    assert BUNDLE_SCALARS_KEY not in session
    assert "_pending_defaults" not in session
    assert "_slot_captured_at" not in session
    assert "portfolio_snapshot" not in session
    assert saved_defaults == []


def _kept_manual_import(monkeypatch, session: dict, warnings: list) -> tuple:
    """A v5 import whose re-derive keeps a newer hand-typed crypto figure (one warning)."""
    from models.ytd_income import ManualMark

    snap = YTDSnapshot(
        by_owner={"you": {}},
        crypto_income_ytd=999.0,
        manual_marks={"household.crypto_income_ytd": ManualMark(999.0, "2026-10-01", "typed")},
    )
    result = SimpleNamespace(
        snapshot=_taxable(2_000.0),
        ledger={
            "koinly": {
                "you": {"stcg": 1.0, "ltcg": 2.0, "income": 3.0, "captured_at": "2026-09-20"}
            },
            "brokerage": {},
        },
        ytd=None,
        scalar_updates={},
        joint_conflicts=(),
        account_type_overrides={},
        magi_candidates={},
        slot_captured_at={},
    )
    ready = SimpleNamespace(
        bundle=None,
        plan=None,
        local=SimpleNamespace(account_type_overrides={}, slot_captured_at={}),
        selected=frozenset(),
        operator=None,
        data={"sections": {"setup_scalars": {}}},
        version="v5",
    )
    session["ytd_snapshot"] = snap
    session["ytd_manual_entry"] = True
    monkeypatch.setattr(data_bridge_mod.st, "session_state", session)
    monkeypatch.setattr(data_bridge_mod.st, "warning", warnings.append)
    monkeypatch.setattr(data_bridge_mod.st, "success", lambda *a, **k: None)
    monkeypatch.setattr(data_bridge_mod.st, "rerun", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "apply_import", lambda *a, **k: result)
    monkeypatch.setattr(data_bridge_mod, "slots_with_replaced_accounts", lambda *a: [])
    monkeypatch.setattr(data_bridge_mod, "commit_import", lambda *a, **k: None)
    monkeypatch.setattr(data_bridge_mod, "_persist_slot_captured_at", lambda *a: None)
    return ready, snap


def test_a_bridge_import_whose_commit_fails_keeps_manual_entry_and_shows_no_note(
    store, monkeypatch
):
    """The re-derive's session bookkeeping (deselect, kept-manual warnings) waits for the commit."""
    session: dict = {}
    warnings: list = []
    ready, _ = _kept_manual_import(monkeypatch, session, warnings)
    monkeypatch.setattr(store.backend, "commit", _locked)  # the block runs fully; COMMIT fails
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        data_bridge_mod._apply_ready_import(ready, "synthetic.enc")
    assert session["ytd_manual_entry"] is True
    assert warnings == []
    assert session["ytd_snapshot"].snapshot_date == ""  # the rolled-back, un-dated copy
    assert session["ytd_snapshot"].crypto_stcg_ytd == 0.0
    assert store.keys(YTD) == []


def test_a_committed_bridge_import_shows_the_kept_note_and_deselects_manual_entry(
    store, monkeypatch
):
    session: dict = {}
    warnings: list = []
    captions: list = []
    ready, _ = _kept_manual_import(monkeypatch, session, warnings)
    monkeypatch.setattr(data_bridge_mod.st, "caption", captions.append)
    data_bridge_mod._apply_ready_import(ready, "synthetic.enc")
    assert session["ytd_manual_entry"] is False
    assert any("crypto income" in w for w in warnings)
    assert not any("Manual entry stayed ON" in c for c in captions)
    assert store.keys(YTD) != []


def test_a_committed_bridge_import_keeps_an_explicitly_chosen_manual_entry(store, monkeypatch):
    session: dict = {"ytd_manual_entry_explicit": True}
    warnings: list = []
    captions: list = []
    ready, _ = _kept_manual_import(monkeypatch, session, warnings)
    monkeypatch.setattr(data_bridge_mod.st, "caption", captions.append)
    data_bridge_mod._apply_ready_import(ready, "synthetic.enc")
    assert session["ytd_manual_entry"] is True
    assert any("Manual entry stayed ON" in c for c in captions)
