"""Tests for the "Clear queued statements" control in the uploaded-PDF import
section (``_sync_scan.clear_queued_statements`` and its button inside
``_render_pdf_uploader``)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _render(state: dict, *, clicks: frozenset[str] = frozenset()) -> MagicMock:
    mock_st = MagicMock()
    mock_st.session_state = state  # a real dict: get / pop / setitem all work
    mock_st.file_uploader.return_value = []
    mock_st.button.side_effect = lambda label, key=None, **kw: key in clicks
    # The clear button is drawn into an st.empty() slot; route it to the same
    # mock so _button_keys sees it.
    mock_st.empty.return_value = mock_st
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
    ):
        sync_scan_mod._render_pdf_uploader(
            instance_owner="you",
            account_overrides={},
            owner_map={},
            ledger={"koinly": {}, "brokerage": {}},
            identity_set=True,
        )
    return mock_st


def _button_keys(mock_st: MagicMock) -> list[str | None]:
    return [c.kwargs.get("key") for c in mock_st.button.call_args_list]


class TestClearQueuedStatementsHelper:
    def test_clears_queues_and_bumps_nonce_without_touching_applied_data(self) -> None:
        state = {
            "ssa_statements": [object()],
            "netbenefits_statements": [object()],
            "_pdf_scanned_digests": {"abc"},
            "_pdf_1040_scanned": {2024: object()},
            "portfolio_snapshot": "applied",
            "ytd_snapshot": "applied",
            "pdf_upload_nonce": 2,
        }
        sync_scan_mod.clear_queued_statements(state)
        for key in sync_scan_mod._QUEUED_SCAN_KEYS:
            assert key not in state
        assert state["pdf_upload_nonce"] == 3
        assert state["portfolio_snapshot"] == "applied"
        assert state["ytd_snapshot"] == "applied"

    def test_nonce_defaults_to_zero_then_one(self) -> None:
        state: dict = {}
        sync_scan_mod.clear_queued_statements(state)
        assert state["pdf_upload_nonce"] == 1


class TestClearButton:
    @pytest.mark.parametrize(
        "queued", [{"ssa_statements": [object()]}, {"_pdf_scanned_digests": {"abc"}}]
    )
    def test_click_empties_queues_and_reruns(self, queued: dict) -> None:
        state = dict(queued)
        mock_st = _render(state, clicks=frozenset({"pdf_clear_queue"}))
        for key in queued:
            assert key not in state
        assert state["pdf_upload_nonce"] == 1
        mock_st.rerun.assert_called_once()

    def test_button_shown_but_not_clicked_leaves_queue(self) -> None:
        state = {"netbenefits_statements": [object()]}
        mock_st = _render(state)
        assert "pdf_clear_queue" in _button_keys(mock_st)
        assert state["netbenefits_statements"]
        mock_st.rerun.assert_not_called()

    def test_button_hidden_when_nothing_queued(self) -> None:
        mock_st = _render({"ssa_statements": [], "_pdf_scanned_digests": set()})
        assert "pdf_clear_queue" not in _button_keys(mock_st)


class TestButtonAfterScan:
    def test_scan_that_queues_statements_shows_button_in_same_run(self) -> None:
        """Browser pass 2026-10-10: the button only appeared on the NEXT rerun
        after a scan, because it was drawn before the scan queued anything."""
        state: dict = {}
        mock_st = MagicMock()
        mock_st.session_state = state
        upload = MagicMock()
        upload.name = "statement.pdf"
        upload.getvalue.return_value = b"%PDF-synthetic"
        mock_st.file_uploader.return_value = [upload]
        mock_st.button.side_effect = lambda label, key=None, **kw: key == "scan_uploaded_pdfs_btn"
        mock_st.empty.return_value = mock_st

        def _fake_apply(combined, *, ledger, **kw):
            state["ssa_statements"] = [object()]
            return ledger

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch.object(
                sync_scan_mod,
                "run_uploaded_scan",
                return_value=MagicMock(raw=sync_scan_mod.PdfImportResult()),
            ),
            patch.object(sync_scan_mod, "_apply_scan_result", side_effect=_fake_apply),
            patch.object(sync_scan_mod, "is_pyodide", return_value=False),
        ):
            sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )
        assert "pdf_clear_queue" in _button_keys(mock_st)


class TestUploaderKey:
    def test_key_changes_after_nonce_bump(self) -> None:
        state: dict = {}
        first = _render(state).file_uploader.call_args.kwargs["key"]
        sync_scan_mod.clear_queued_statements(state)
        second = _render(state).file_uploader.call_args.kwargs["key"]
        assert first == "pdf_upload_0"
        assert second == "pdf_upload_1"
