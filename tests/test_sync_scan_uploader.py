"""Tests for the uploaded-PDF import flow on the YTD Income / Setup ▸ Data
page (``views/ytd_income/_partials/_sync_scan.py:_render_pdf_uploader`` and
its call site inside ``render_sync_scan_partial``).

Mirrors ``tests/test_scan_ingest.py``'s mocked-``st`` harness style
(``_make_mock_st``) rather than ``AppTest`` -- these tests only need to
assert which widgets got created and how ``run_uploaded_scan`` was driven,
not a full rendered widget tree.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from engine.data_sources.scan_ingest import ScanIngestResult
from engine.pdf_import import PdfImportResult
from models.household import Household
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _stub_hh(**kwargs) -> Household:
    return Household(your_age=60, spouse_age=54, your_ira=500_000, spouse_ira=500_000, **kwargs)


class _FakeUploadedFile:
    """Stand-in for Streamlit's ``UploadedFile`` -- only ``.name`` and
    ``.getvalue()`` are used by ``_render_pdf_uploader``."""

    def __init__(self, name: str, data: bytes = b"pdf-bytes") -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def _mock_st(*, session_extra=None, uploaded_files=(), button_clicks=()):
    mock_st = MagicMock()
    state: dict = {"ytd_snapshot": YTDSnapshot(), **(session_extra or {})}
    session_state = MagicMock()
    session_state.get.side_effect = lambda key, default=None: state.get(key, default)
    session_state.__setitem__.side_effect = state.__setitem__
    session_state.__contains__.side_effect = state.__contains__
    mock_st.session_state = session_state
    mock_st.file_uploader.return_value = list(uploaded_files)
    mock_st.button.side_effect = lambda label, key=None, **kw: key in button_clicks
    mock_st.checkbox.return_value = False
    mock_st.columns.side_effect = lambda n: [
        MagicMock() for _ in range(n if isinstance(n, int) else len(n))
    ]
    mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)
    return mock_st, state


class TestUploaderVsFolderInputGating:
    """The uploader renders in BOTH environments; the folder path/scan
    button (which needs a real local filesystem) renders only when NOT
    Pyodide."""

    def test_uploader_renders_under_pyodide_but_folder_input_does_not(self) -> None:
        mock_st, _state = _mock_st(session_extra={"instance_owner": "you"})
        hh = _stub_hh()

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "is_pyodide", lambda: True),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
            patch.object(sync_scan_mod, "load_owner_map", return_value={}),
            patch.object(
                sync_scan_mod, "load_ledger", return_value={"koinly": {}, "brokerage": {}}
            ),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
        ):
            sync_scan_mod.render_sync_scan_partial(hh)

        uploader_keys = [c.kwargs.get("key") for c in mock_st.file_uploader.call_args_list]
        assert "pdf_upload_0" in uploader_keys
        text_input_keys = [c.kwargs.get("key") for c in mock_st.text_input.call_args_list]
        assert "statement_folder_path" not in text_input_keys

    def test_folder_input_and_uploader_both_render_when_not_pyodide(self) -> None:
        mock_st, _state = _mock_st(session_extra={"instance_owner": "you"})
        hh = _stub_hh()

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "is_pyodide", lambda: False),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
            patch.object(sync_scan_mod, "load_owner_map", return_value={}),
            patch.object(
                sync_scan_mod, "load_ledger", return_value={"koinly": {}, "brokerage": {}}
            ),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.load_statement_records", return_value={}),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.koinly_report_pdf.load_koinly_report", return_value=None),
        ):
            sync_scan_mod.render_sync_scan_partial(hh)

        text_input_keys = [c.kwargs.get("key") for c in mock_st.text_input.call_args_list]
        assert "statement_folder_path" in text_input_keys
        uploader_keys = [c.kwargs.get("key") for c in mock_st.file_uploader.call_args_list]
        assert "pdf_upload_0" in uploader_keys


class TestBackendGate:
    """``ensure_pdf_backend()``'s three non-"ready" states each short-circuit
    the uploader before ``run_uploaded_scan`` is ever called."""

    @pytest.mark.parametrize("status", ["installing", "failed", "unavailable"])
    def test_non_ready_status_never_calls_run_uploaded_scan(self, status: str) -> None:
        mock_st, _state = _mock_st(
            uploaded_files=[_FakeUploadedFile("a.pdf")], button_clicks={"scan_uploaded_pdfs_btn"}
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value=status),
            patch.object(sync_scan_mod, "pdf_backend_error", return_value="boom"),
            patch.object(sync_scan_mod, "run_uploaded_scan") as mock_run,
        ):
            ledger = sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )

        mock_run.assert_not_called()
        assert ledger == {"koinly": {}, "brokerage": {}}


class TestOneBadDocumentDoesNotAbortScan:
    def test_bad_document_reported_good_document_still_processed(self) -> None:
        good_raw = PdfImportResult(brokerage_records=[])
        good_result = ScanIngestResult(
            brokerage_count=0,
            form_1040_count=0,
            koinly_count=0,
            skipped_count=0,
            unrecognized_count=0,
            magi_candidates_recorded=0,
            errors=[],
            raw=good_raw,
            pdf_cache={},
        )

        def _fake_run_uploaded_scan(documents):
            name = documents[0][0]
            if name == "bad.pdf":
                raise MemoryError("simulated crash")
            return good_result

        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"},
            uploaded_files=[_FakeUploadedFile("bad.pdf"), _FakeUploadedFile("good.pdf")],
            button_clicks={"scan_uploaded_pdfs_btn"},
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch.object(sync_scan_mod, "run_uploaded_scan", side_effect=_fake_run_uploaded_scan),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            ledger = sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )

        # The scan completed (returned normally, no exception escaped) and
        # the ledger -- untouched by either file -- comes back unchanged.
        assert ledger == {"koinly": {}, "brokerage": {}}

        reads = [c.args[0] for c in mock_st.write.call_args_list]
        assert any("bad.pdf" in r for r in reads), reads
        assert any("good.pdf" in r for r in reads), reads

        reported = [c.args[0] for c in mock_st.warning.call_args_list]
        assert any("bad.pdf" in w and "simulated crash" in w for w in reported), reported


class TestAlreadyScannedFileIsSkipped:
    """The user's real workflow: add one file, click Scan; add another,
    click Scan again. The second click must not re-parse a file whose
    CONTENT (not name) was already successfully scanned."""

    def test_second_scan_of_identical_content_is_skipped_not_reparsed(self) -> None:
        good_result = ScanIngestResult(
            brokerage_count=0,
            form_1040_count=0,
            koinly_count=0,
            skipped_count=0,
            unrecognized_count=0,
            magi_candidates_recorded=0,
            errors=[],
            raw=PdfImportResult(brokerage_records=[]),
            pdf_cache={},
        )
        mock_run = MagicMock(return_value=good_result)

        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"},
            uploaded_files=[_FakeUploadedFile("2024_TaxReturn.pdf", data=b"same-content")],
            button_clicks={"scan_uploaded_pdfs_btn"},
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch.object(sync_scan_mod, "run_uploaded_scan", mock_run),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )
            assert mock_run.call_count == 1

            mock_st.write.reset_mock()
            sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )

        # run_uploaded_scan was NOT called a second time for the same content.
        assert mock_run.call_count == 1

        reads = [c.args[0] for c in mock_st.write.call_args_list]
        assert any(
            "2024_TaxReturn.pdf" in r and "already scanned" in r and "skipped" in r for r in reads
        ), reads

        status_calls = mock_st.status.return_value.__enter__.return_value.update.call_args_list
        assert any("1 skipped" in c.kwargs.get("label", "") for c in status_calls), status_calls

    def test_two_identical_files_in_one_scan_are_parsed_once(self) -> None:
        good_result = ScanIngestResult(
            brokerage_count=0,
            form_1040_count=0,
            koinly_count=0,
            skipped_count=0,
            unrecognized_count=0,
            magi_candidates_recorded=0,
            errors=[],
            raw=PdfImportResult(brokerage_records=[]),
            pdf_cache={},
        )
        mock_run = MagicMock(return_value=good_result)

        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"},
            uploaded_files=[
                _FakeUploadedFile("a.pdf", data=b"dup-content"),
                _FakeUploadedFile("b.pdf", data=b"dup-content"),
            ],
            button_clicks={"scan_uploaded_pdfs_btn"},
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch.object(sync_scan_mod, "run_uploaded_scan", mock_run),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )

        assert mock_run.call_count == 1
        reads = [c.args[0] for c in mock_st.write.call_args_list]
        assert any("b.pdf" in r and "already scanned" in r for r in reads), reads


class TestProgressBarClearedOnCompletion:
    """A finished scan must not leave the ``st.progress`` bar (and its
    "file N of M" label) drawn beneath the collapsed ``st.status`` summary --
    a completed scan that still shows a live-looking progress bar reads as
    if the app is stuck."""

    def test_progress_emptied_after_successful_scan(self) -> None:
        good_result = ScanIngestResult(
            brokerage_count=0,
            form_1040_count=0,
            koinly_count=0,
            skipped_count=0,
            unrecognized_count=0,
            magi_candidates_recorded=0,
            errors=[],
            raw=PdfImportResult(brokerage_records=[]),
            pdf_cache={},
        )
        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"},
            uploaded_files=[_FakeUploadedFile("good.pdf")],
            button_clicks={"scan_uploaded_pdfs_btn"},
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch.object(sync_scan_mod, "run_uploaded_scan", return_value=good_result),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )

        # progress = st.progress(0.0) is called exactly once, so every
        # subsequent .progress(...)/.empty() call lands on the same mock.
        mock_st.progress.return_value.empty.assert_called_once()

    def test_progress_emptied_even_when_a_file_fails(self) -> None:
        def _raise(_documents):
            raise MemoryError("simulated crash")

        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"},
            uploaded_files=[_FakeUploadedFile("bad.pdf")],
            button_clicks={"scan_uploaded_pdfs_btn"},
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch.object(sync_scan_mod, "run_uploaded_scan", side_effect=_raise),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            sync_scan_mod._render_pdf_uploader(
                instance_owner="you",
                account_overrides={},
                owner_map={},
                ledger={"koinly": {}, "brokerage": {}},
                identity_set=True,
            )

        mock_st.progress.return_value.empty.assert_called_once()


class TestSyncButtonErrorIsolation:
    """Issue #490: the "Sync from FinExtract" button called
    ``fetch_ytd_snapshot``/``fetch_option_exercises`` with no error handling
    -- any exception (network error, malformed payload, non-200) crashed the
    whole page render instead of being reported like the existing
    falsy-``snapshot_date`` "FinExtract unavailable" case."""

    def test_fetch_ytd_snapshot_error_is_isolated(self) -> None:
        hh = _stub_hh()
        prior_snapshot = YTDSnapshot(wages_ytd=1.0)
        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"}, button_clicks={"ytd_sync_btn"}
        )
        mock_st.session_state.ytd_snapshot = prior_snapshot

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "is_pyodide", lambda: False),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
            patch.object(sync_scan_mod, "load_owner_map", return_value={}),
            patch.object(
                sync_scan_mod, "load_ledger", return_value={"koinly": {}, "brokerage": {}}
            ),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.load_statement_records", return_value={}),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.koinly_report_pdf.load_koinly_report", return_value=None),
            patch(
                "engine.portfolio_sync.fetch_ytd_snapshot",
                side_effect=RuntimeError("connection refused"),
            ),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch.object(sync_scan_mod, "save_ytd_snapshot") as mock_save,
        ):
            sync_scan_mod.render_ytd_finextract_sync(hh)

        # fetch_option_exercises must never run once fetch_ytd_snapshot raised.
        mock_fetch_ex.assert_not_called()
        warnings = [str(c.args[0]) for c in mock_st.warning.call_args_list]
        assert any("connection refused" in w and "sync failed" in w.lower() for w in warnings), (
            warnings
        )
        # None of the success side effects ran.
        assert mock_st.session_state.ytd_snapshot is prior_snapshot
        setitem_keys = [c.args[0] for c in mock_st.session_state.__setitem__.call_args_list]
        assert "ytd_manual_entry" not in setitem_keys
        mock_save.assert_not_called()
        mock_st.rerun.assert_not_called()

    def test_fetch_option_exercises_error_is_isolated(self) -> None:
        hh = _stub_hh()
        prior_snapshot = YTDSnapshot(wages_ytd=2.0)
        fetched_status = YTDSnapshot(tax_year=hh.base_year, snapshot_date="2026-06-12")
        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"}, button_clicks={"ytd_sync_btn"}
        )
        mock_st.session_state.ytd_snapshot = prior_snapshot

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "is_pyodide", lambda: False),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
            patch.object(sync_scan_mod, "load_owner_map", return_value={}),
            patch.object(
                sync_scan_mod, "load_ledger", return_value={"koinly": {}, "brokerage": {}}
            ),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.load_statement_records", return_value={}),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.koinly_report_pdf.load_koinly_report", return_value=None),
            patch("engine.portfolio_sync.fetch_ytd_snapshot", return_value=fetched_status),
            patch(
                "engine.portfolio_sync.fetch_option_exercises",
                side_effect=RuntimeError("malformed payload"),
            ),
            patch.object(sync_scan_mod, "save_ytd_snapshot") as mock_save,
        ):
            sync_scan_mod.render_ytd_finextract_sync(hh)

        warnings = [str(c.args[0]) for c in mock_st.warning.call_args_list]
        assert any("malformed payload" in w and "sync failed" in w.lower() for w in warnings), (
            warnings
        )
        # None of the success side effects ran.
        assert mock_st.session_state.ytd_snapshot is prior_snapshot
        setitem_keys = [c.args[0] for c in mock_st.session_state.__setitem__.call_args_list]
        assert "ytd_manual_entry" not in setitem_keys
        mock_save.assert_not_called()
        mock_st.rerun.assert_not_called()

    def test_unavailable_snapshot_date_path_still_reports_its_own_message(self) -> None:
        """Confirms the pre-existing falsy-``snapshot_date`` branch (kept
        unchanged by this fix) still reports its own distinct message --
        not a duplicate of the new exception-path coverage above."""
        hh = _stub_hh()
        failed_status = YTDSnapshot(tax_year=hh.base_year)  # snapshot_date stays falsy
        mock_st, _state = _mock_st(
            session_extra={"instance_owner": "you"}, button_clicks={"ytd_sync_btn"}
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "is_pyodide", lambda: False),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
            patch.object(sync_scan_mod, "load_owner_map", return_value={}),
            patch.object(
                sync_scan_mod, "load_ledger", return_value={"koinly": {}, "brokerage": {}}
            ),
            patch.object(sync_scan_mod, "ensure_pdf_backend", return_value="ready"),
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.load_statement_records", return_value={}),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.koinly_report_pdf.load_koinly_report", return_value=None),
            patch("engine.portfolio_sync.fetch_ytd_snapshot", return_value=failed_status),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch.object(sync_scan_mod, "save_ytd_snapshot") as mock_save,
        ):
            mock_exercises = MagicMock()
            mock_exercises.server_available = False
            mock_fetch_ex.return_value = mock_exercises

            sync_scan_mod.render_ytd_finextract_sync(hh)

        warnings = [str(c.args[0]) for c in mock_st.warning.call_args_list]
        assert any(
            w == "FinExtract unavailable — use **Add an entry by hand** on This Year's Income"
            for w in warnings
        ), warnings
        mock_save.assert_not_called()
        mock_st.rerun.assert_not_called()


class TestHolderNameMismatchRecording:
    """Issue #487: a detected mismatch is RECORDED into the fixed
    ``st.session_state["holder_name_mismatches"]`` dict, not just warned
    about -- the persistent panel (below) is what actually acts on it."""

    def test_mismatch_recorded_in_session_state(self) -> None:
        mock_st, state = _mock_st()

        with patch.object(sync_scan_mod, "st", mock_st):
            recorded = sync_scan_mod._warn_on_holder_name_mismatch(
                "Jane Doe",
                "you",
                {"jane doe": "household"},
                "1234",
                ("schwab", "1234"),
            )

        assert recorded is True
        mismatches = state["holder_name_mismatches"]
        assert len(mismatches) == 1
        entry = next(iter(mismatches.values()))
        assert entry == {
            "owner_key": "Jane Doe",
            "named_owner": "household",
            "resolved": "you",
            "account_label": "1234",
            "override_key": ("schwab", "1234"),
        }

    def test_no_owner_key_records_nothing(self) -> None:
        mock_st, state = _mock_st()

        with patch.object(sync_scan_mod, "st", mock_st):
            recorded = sync_scan_mod._warn_on_holder_name_mismatch(
                None, "you", {}, "1234", ("ibkr", "1234")
            )

        assert recorded is False
        assert "holder_name_mismatches" not in state

    def test_agreeing_names_record_nothing(self) -> None:
        mock_st, state = _mock_st()

        with patch.object(sync_scan_mod, "st", mock_st):
            recorded = sync_scan_mod._warn_on_holder_name_mismatch(
                "Jane Doe",
                "household",
                {"jane doe": "household"},
                "1234",
                ("schwab", "1234"),
            )

        assert recorded is False
        assert "holder_name_mismatches" not in state


class TestHolderNameMismatchPanel:
    """``_render_holder_name_mismatches`` -- the persistent panel that
    re-checks and resolves entries recorded above."""

    def _entry(self, **overrides) -> dict:
        base = {
            "owner_key": "Jane Doe",
            "named_owner": "household",
            "resolved": "you",
            "account_label": "1234",
            "override_key": ("schwab", "1234"),
        }
        base.update(overrides)
        return base

    def test_renders_row_for_still_mismatching_entry_without_naming_a_page(self) -> None:
        mock_st, state = _mock_st(session_extra={"holder_name_mismatches": {"k1": self._entry()}})

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "load_owner_map", return_value={"jane doe": "household"}),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
        ):
            sync_scan_mod._render_holder_name_mismatches("you")

        warnings = [str(c.args[0]) for c in mock_st.warning.call_args_list]
        assert any("household" in w and "you" in w for w in warnings), warnings
        assert all("Command Center" not in w for w in warnings), warnings
        assert "k1" in state["holder_name_mismatches"]

    def test_drops_entry_that_no_longer_mismatches(self) -> None:
        mock_st, state = _mock_st(session_extra={"holder_name_mismatches": {"k1": self._entry()}})

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            # Owner map corrected externally since the entry was recorded --
            # "jane doe" now maps to "you", matching the resolved owner.
            patch.object(sync_scan_mod, "load_owner_map", return_value={"jane doe": "you"}),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
        ):
            sync_scan_mod._render_holder_name_mismatches("you")

        assert state["holder_name_mismatches"] == {}
        mock_st.warning.assert_not_called()

    def test_holder_name_button_saves_owner_map_and_removes_entry(self) -> None:
        mock_st, state = _mock_st(
            session_extra={"holder_name_mismatches": {"k1": self._entry()}},
            button_clicks={"holder_mismatch_name_k1"},
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "load_owner_map", return_value={"jane doe": "household"}),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
            patch.object(sync_scan_mod, "save_owner_map") as mock_save_map,
        ):
            sync_scan_mod._render_holder_name_mismatches("you")

        mock_save_map.assert_called_once_with({"jane doe": "you"})
        assert state["holder_name_mismatches"] == {}
        mock_st.rerun.assert_called_once()

    def test_account_override_button_saves_override_and_removes_entry(self) -> None:
        mock_st, state = _mock_st(
            session_extra={"holder_name_mismatches": {"k1": self._entry()}},
            button_clicks={"holder_mismatch_account_k1"},
        )

        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "load_owner_map", return_value={"jane doe": "household"}),
            patch.object(sync_scan_mod, "load_account_overrides", return_value={}),
            patch("engine.account_attribution.save_account_override") as mock_save_override,
        ):
            sync_scan_mod._render_holder_name_mismatches("you")

        mock_save_override.assert_called_once_with("schwab", "1234", "household")
        assert state["holder_name_mismatches"] == {}
        mock_st.rerun.assert_called_once()

    def test_no_entries_renders_nothing(self) -> None:
        mock_st, _state = _mock_st()

        with patch.object(sync_scan_mod, "st", mock_st):
            sync_scan_mod._render_holder_name_mismatches("you")

        mock_st.warning.assert_not_called()
        mock_st.button.assert_not_called()
