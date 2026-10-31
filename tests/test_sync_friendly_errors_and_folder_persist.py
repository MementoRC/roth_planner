"""Friendly sync error text + scan-folder persist-on-edit callback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import views._shared as shared_mod
import views.ytd_income._partials._sync_scan as sync_scan_mod
from views._shared import friendly_sync_error


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError("raw body text", response=resp)


class TestFriendlySyncError:
    def test_connection_error(self):
        msg = friendly_sync_error(requests.ConnectionError("Max retries 127.0.0.1"))
        assert msg == "FinExtract isn't reachable — is it running?"

    def test_timeout(self):
        assert friendly_sync_error(requests.Timeout("slow")) == "FinExtract timed out."

    def test_connect_timeout_reads_as_timeout(self):
        assert friendly_sync_error(requests.ConnectTimeout("slow")) == "FinExtract timed out."

    def test_http_error_with_response(self):
        assert friendly_sync_error(_http_error(503)) == "FinExtract returned HTTP 503."

    def test_http_error_without_response_falls_back(self):
        assert friendly_sync_error(requests.HTTPError("x")) == "HTTPError (see details)"

    def test_other(self):
        assert friendly_sync_error(RuntimeError("secret detail")) == "RuntimeError (see details)"


class TestSyncLegsHideRawText:
    def test_ss_leg_stores_friendly_text(self):
        mock_st = MagicMock()
        mock_st.session_state = {"filing_status": "Single", "your_fra_age": 67}
        boom = requests.ConnectionError("raw-conn-detail")
        with (
            patch.object(shared_mod, "st", mock_st),
            patch("views.setup._partials._sync_ssa_for", side_effect=boom),
        ):
            summary = shared_mod._sync_ss_source()
        joined = " ".join(summary.warnings)
        assert "raw-conn-detail" not in joined
        assert "FinExtract isn't reachable" in joined

    def test_portfolio_leg_stores_friendly_text(self):
        boom = requests.Timeout("raw-timeout-detail")
        with patch("views.setup.portfolio.sync_portfolio_from_finextract", side_effect=boom):
            summary = shared_mod._sync_portfolio_source(MagicMock())
        assert summary.error == "FinExtract timed out."

    def test_quote_leg_hides_raw_text(self):
        boom = RuntimeError("raw-quote-detail")
        with patch(
            "views.option_exercise._partials._helpers.handle_stock_quote_fetch",
            side_effect=boom,
        ):
            summary = shared_mod._sync_market_quote_source()
        assert summary.error == "RuntimeError (see details)"


def test_command_center_summary_shows_friendly_text_only():
    from views.setup.command_center import _format_sync_everything_summary

    mock_st = MagicMock()
    mock_st.session_state = {"filing_status": "Single", "your_fra_age": 67}
    with (
        patch.object(shared_mod, "st", mock_st),
        patch(
            "views.setup.portfolio.sync_portfolio_from_finextract",
            side_effect=requests.ConnectionError("raw-conn-detail"),
        ),
    ):
        portfolio = shared_mod._sync_portfolio_source(MagicMock())
    summary = MagicMock()
    summary.portfolio = portfolio
    summary.ss.candidates_recorded = 0
    summary.ss.warnings = []
    summary.scan.result = None
    summary.scan.error = "no folder configured"
    summary.market_quote.candidates_recorded = 0
    summary.market_quote.error = None
    text = _format_sync_everything_summary(summary)
    assert "FinExtract isn't reachable" in text
    assert "raw-conn-detail" not in text


class TestFolderPersistOnEdit:
    @pytest.fixture
    def folder(self, tmp_path, monkeypatch) -> Path:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        d = tmp_path / "Statements"
        d.mkdir()
        return d

    def _run(self, text: str, saved: str | None):
        mock_st = MagicMock()
        mock_st.session_state = {"statement_folder_path": text}
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=saved),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path") as mock_save,
        ):
            sync_scan_mod._persist_statement_folder_on_edit()
        mock_st.error.assert_not_called()
        return mock_save

    def test_valid_new_path_saved(self, folder):
        mock_save = self._run(str(folder), saved=None)
        mock_save.assert_called_once_with(str(folder.resolve()))

    def test_same_path_not_resaved(self, folder):
        mock_save = self._run(str(folder), saved=str(folder.resolve()))
        mock_save.assert_not_called()

    def test_invalid_path_not_saved(self, folder):
        assert not self._run("   ", saved=None).called
        assert not self._run("\x00/bad", saved=None).called
        assert not self._run(str(folder / "missing" / ".."), saved=None).called
