"""Friendly sync error text + scan-folder persist-on-edit callback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import views._shared as shared_mod
import views.ytd_income._partials._sync_scan as sync_scan_mod
from views._shared import friendly_sync_error, friendly_sync_error_text


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


_UNREACH = "FinExtract isn't reachable — is it running?"


class TestFriendlySyncErrorText:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("HTTPConnectionPool(host='x', port=1): Read timed out. (read timeout=10)", "timeout"),
            ("ConnectTimeout: boom", "timeout"),
            (
                "HTTPConnectionPool(host='127.0.0.1', port=7890): Max retries exceeded with "
                "url: /status (Caused by NewConnectionError(...Connection refused))",
                "unreach",
            ),
            ("Connection aborted.", "unreach"),
            ("NameResolutionError: nope", "unreach"),
            (
                "holdings fetch failed: Unexpected redirect (302) from /query/brokerage; "
                "refusing to follow.",
                "FinExtract returned an unexpected redirect (HTTP 302).",
            ),
            (
                "500 Server Error: Internal Server Error for url: http://127.0.0.1:7890/status",
                "FinExtract returned HTTP 500.",
            ),
            ("holdings fetch failed: HTTP 500", "FinExtract returned HTTP 500."),
            ("404 Client Error: Not Found", "FinExtract returned HTTP 404."),
            (
                "holdings fetch failed: Expecting value: line 1 column 1 (char 0)",
                "FinExtract sent an unreadable response.",
            ),
            ("JSONDecodeError", "FinExtract sent an unreadable response."),
            ("Extra data: line 2", "FinExtract sent an unreadable response."),
            (None, "FinExtract sync failed."),
            ("", "FinExtract sync failed."),
        ],
    )
    def test_patterns(self, raw, expected):
        expected = {
            "timeout": "FinExtract timed out.",
            "unreach": _UNREACH,
        }.get(expected, expected)
        assert friendly_sync_error_text(raw) == expected

    def test_fallback_never_echoes_raw(self):
        out = friendly_sync_error_text("something weird http://h/x")
        assert out == "FinExtract sync failed (see details)."
        assert "weird" not in out

    def test_custom_source(self):
        assert friendly_sync_error_text("timed out", "Yahoo Finance") == "Yahoo Finance timed out."


class TestSyncLegsHideRawText:
    def test_portfolio_leg_hides_raw_snapshot_error(self):
        outcome = MagicMock()
        outcome.snap.server_available = False
        outcome.snap.error = "500 Server Error: x for url: http://127.0.0.1:7890/status"
        with patch("views.setup.portfolio.sync_portfolio_from_finextract", return_value=outcome):
            summary = shared_mod._sync_portfolio_source(MagicMock())
        assert summary.error == "FinExtract returned HTTP 500."
        assert "127.0.0.1" not in summary.error

    def test_quote_leg_hides_raw_result_error(self):
        result = MagicMock(ok=False, error="Read timed out for url: http://q.example/x")
        with patch(
            "views.option_exercise._partials._helpers.handle_stock_quote_fetch",
            return_value=result,
        ):
            summary = shared_mod._sync_market_quote_source()
        assert summary.error == "Yahoo Finance timed out."

    def test_ssa_warning_hides_raw_url(self):
        from views.setup import _partials

        snap = MagicMock(error="500 Server Error: x for url: http://127.0.0.1:7890/ssa")
        with patch("views.setup._partials._accounts.fetch_ssa_snapshot", return_value=snap):
            warning = _partials._sync_ssa_for("you", 67)
        assert warning == "SSA sync: FinExtract returned HTTP 500."

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


def test_ss_leg_dedupes_identical_warnings_keeps_distinct():
    mock_st = MagicMock()
    mock_st.session_state = {"filing_status": "MFJ", "your_fra_age": 67, "spouse_fra_age": 67}
    same = "SSA sync: FinExtract returned HTTP 404."
    with (
        patch.object(shared_mod, "st", mock_st),
        patch("views.setup._partials._sync_ssa_for", return_value=same),
    ):
        summary = shared_mod._sync_ss_source()
    assert summary.warnings == [same]

    with (
        patch.object(shared_mod, "st", mock_st),
        patch("views.setup._partials._sync_ssa_for", side_effect=[same, "other"]),
    ):
        summary = shared_mod._sync_ss_source()
    assert summary.warnings == [same, "other"]


def _legs(*, portfolio_ok=False, ss=0, scan_ok=False, quote_ok=False):
    summary = MagicMock()
    summary.portfolio.server_available = portfolio_ok
    summary.portfolio.error = None if portfolio_ok else "FinExtract sync failed."
    summary.ss.candidates_recorded = ss
    summary.scan.result = object() if scan_ok else None
    summary.scan.error = None if scan_ok else "no folder configured"
    summary.market_quote.error = None if quote_ok else "Yahoo Finance sync failed."
    return summary


class TestAnyLegSucceeded:
    def test_all_failed(self):
        from views.setup.command_center import _any_sync_leg_succeeded

        assert _any_sync_leg_succeeded(_legs()) is False

    @pytest.mark.parametrize(
        "kwargs",
        [{"portfolio_ok": True}, {"ss": 1}, {"scan_ok": True}, {"quote_ok": True}],
    )
    def test_any_one_leg_succeeding(self, kwargs):
        from views.setup.command_center import _any_sync_leg_succeeded

        assert _any_sync_leg_succeeded(_legs(**kwargs)) is True


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
