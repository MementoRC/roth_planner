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
    return Household(your_age=61, spouse_age=55, your_ira=500_000, spouse_ira=500_000, **kwargs)


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
        assert "pdf_upload" in uploader_keys
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
        assert "pdf_upload" in uploader_keys


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
