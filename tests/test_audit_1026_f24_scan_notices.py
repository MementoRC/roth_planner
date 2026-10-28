"""audit-1026 F-24: scan-apply notices are drawn only after the transaction commits."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from engine.pdf_import import PdfImportResult
from engine.store import StoreUnavailableError
from tests import test_sync_scan_ira_statements as ira_tests
from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _locked(*_args, **_kwargs):
    raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")


def _apply(mock_st, *, save_ytd=None) -> None:
    rec = ira_tests._rec(account_type="taxable", account_number="T1")
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "_apply_ubs_correction", return_value=["synthetic UBS note"]),
        patch.object(sync_scan_mod, "save_ytd_snapshot", save_ytd or (lambda *_a, **_k: None)),
    ):
        sync_scan_mod._apply_scan_result(
            PdfImportResult(brokerage_records=[rec]),
            ledger={"koinly": {}, "brokerage": {}},
            account_overrides={},
            owner_map={},
            instance_owner="you",
        )


def test_a_rolled_back_scan_apply_draws_no_ubs_warning(store):
    mock_st, _state = ira_tests._mock_st()
    with pytest.raises(StoreUnavailableError, match="database is locked"):
        _apply(mock_st, save_ytd=_locked)
    mock_st.warning.assert_not_called()


def test_a_committed_scan_apply_draws_the_ubs_warning(store):
    mock_st, _state = ira_tests._mock_st()
    _apply(mock_st)
    mock_st.warning.assert_any_call("synthetic UBS note")
