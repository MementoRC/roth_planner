"""Cross-store flows (spec §2, store PR 4): a failure part-way through a flow
that writes several stores leaves every store, and the session keys the flow
touched, as they were.

Reuses the mocked-``st`` harnesses of the per-flow test modules (imported as
modules, so their tests are not collected twice). Synthetic values only.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock, patch

import pytest

from engine.data_sources.scan_ingest import ScanIngestResult
from engine.netbenefits_statement_pdf import parse_netbenefits_text
from engine.pdf_import import PdfImportResult
from engine.ssa_statement_pdf import parse_ssa_statement_text
from engine.store import StoreUnavailableError
from engine.store.namespaces import (
    BROKERAGE_STATEMENTS,
    CANDIDATE_STORE,
    IRA_ACCOUNT_LINKS,
    PDF_LEDGER,
    PORTFOLIO,
    SSA_PDF,
)
from tests import test_sync_scan_ira_statements as ira_tests
from tests import test_sync_scan_netbenefits as nb_tests
from tests import test_sync_scan_uploader as upload_tests
from tests.test_netbenefits_statement_pdf import PAGES as NB_PAGES
from tests.test_ssa_statement_pdf import PAGES as SSA_PAGES
from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _locked(*_args, **_kwargs):
    raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")


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
