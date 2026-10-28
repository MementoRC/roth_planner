"""audit-1026 F-23: the Manual-entry auto-deselect runs only after a committed apply."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from engine.pdf_import import PdfImportResult
from engine.store import StoreUnavailableError
from tests import test_sync_scan_ira_statements as ira_tests
from tests import test_ubs_csv_uploader as ubs_tests
from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _locked(*_args, **_kwargs):
    raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")


def _ok(*_args, **_kwargs):
    return None


def _scan(mock_st, save_ytd) -> None:
    rec = ira_tests._rec(account_type="taxable", account_number="T1")
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "save_ytd_snapshot", save_ytd),
    ):
        sync_scan_mod._apply_scan_result(
            PdfImportResult(brokerage_records=[rec]),
            ledger={"koinly": {}, "brokerage": {}},
            account_overrides={},
            owner_map={},
            instance_owner="you",
        )


def test_a_rolled_back_scan_apply_keeps_manual_entry_on(store):
    mock_st, state = ira_tests._mock_st(state_extra={"ytd_manual_entry": True})
    with pytest.raises(StoreUnavailableError):
        _scan(mock_st, _locked)
    assert state["ytd_manual_entry"] is True


def test_a_committed_scan_apply_turns_manual_entry_off(store):
    mock_st, state = ira_tests._mock_st(state_extra={"ytd_manual_entry": True})
    _scan(mock_st, _ok)
    assert state["ytd_manual_entry"] is False


def test_a_committed_scan_apply_keeps_an_explicit_choice_and_says_so(store):
    mock_st, state = ira_tests._mock_st(
        state_extra={"ytd_manual_entry": True, "ytd_manual_entry_explicit": True}
    )
    _scan(mock_st, _ok)
    assert state["ytd_manual_entry"] is True
    assert any("Manual entry stayed ON" in c.args[0] for c in mock_st.caption.call_args_list)


def _ubs_st() -> tuple:
    mock_st, state = ubs_tests._mock_st(
        session_extra={"instance_owner": "you", "ytd_manual_entry": True},
        uploaded_files=[ubs_tests._FakeUploadedFile("o.csv", ubs_tests._order_csv("FA-NEW"))],
        button_clicks={"import_ubs_csv_btn"},
    )
    mock_st.rerun.side_effect = ira_tests._RerunError
    return mock_st, state


def _ubs_import(mock_st, save_ytd) -> None:
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "load_ubs_orders", return_value=[]),
        patch.object(sync_scan_mod, "save_ubs_orders"),
        patch.object(sync_scan_mod, "save_ytd_snapshot", save_ytd),
        patch.object(sync_scan_mod, "_apply_ubs_correction", return_value=["note"]),
    ):
        sync_scan_mod._render_ubs_csv_uploader(
            ledger={"koinly": {}, "brokerage": {}}, identity_set=True
        )


def test_a_rolled_back_ubs_import_keeps_manual_entry_on(store):
    mock_st, state = _ubs_st()
    with pytest.raises(StoreUnavailableError):
        _ubs_import(mock_st, _locked)
    assert state["ytd_manual_entry"] is True


def test_a_committed_ubs_import_turns_manual_entry_off(store):
    mock_st, state = _ubs_st()
    _ubs_import(mock_st, _ok)  # a UBS warning is returned, so no rerun fires
    assert state["ytd_manual_entry"] is False


def _apply_to_ytd(mock_st, save_ytd) -> None:
    rec = ira_tests._rec(account_type="taxable", account_number="T1")
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "save_ledger"),
        patch.object(sync_scan_mod, "save_ytd_snapshot", save_ytd),
        patch.object(sync_scan_mod, "_apply_ubs_correction", return_value=["note"]),
    ):
        sync_scan_mod._render_scan_review(
            {"T1": rec}, {"koinly": {}, "brokerage": {}}, {}, {}, "you", True
        )


def test_a_rolled_back_apply_to_ytd_keeps_manual_entry_on(store):
    mock_st, state = ira_tests._mock_st(
        state_extra={"ytd_manual_entry": True}, clicks={"apply_statements_btn"}
    )
    with pytest.raises(StoreUnavailableError):
        _apply_to_ytd(mock_st, _locked)
    assert state["ytd_manual_entry"] is True


def test_a_committed_apply_to_ytd_turns_manual_entry_off(store):
    mock_st, state = ira_tests._mock_st(
        state_extra={"ytd_manual_entry": True}, clicks={"apply_statements_btn"}
    )
    _apply_to_ytd(mock_st, _ok)
    assert state["ytd_manual_entry"] is False
