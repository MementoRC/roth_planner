"""Tests for the W2 Part A scan/ingest unification (kills the
``_pdf_1040_scanned`` session dual-writer).

- A0: characterization — pins the CURRENT ``views/ytd_income.py`` "Scan folder"
  handler's outcome (golden) for a fixed ``PdfImportResult`` containing one
  Form 1040 record + one brokerage record: the MAGI candidate it records via
  ``record_magi_candidates`` and the pdf-tax cache it persists via
  ``save_pdf_tax_records``. Must pass unmodified before/after the refactor.
- A1: the new pure ``engine.data_sources.scan_ingest.scan_and_record`` helper
  reproduces the identical golden, in isolation (no streamlit).
- A2: once ``views/ytd_income.py`` is rewired to call
  ``views._shared.run_folder_scan``, the SAME fixture through the full view
  render reproduces the golden AND writes ``_pdf_1040_scanned`` exactly once.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import views.ytd_income as ytd_income_mod
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.paths import CANDIDATE_STORE_PATH
from engine.data_sources.scan_ingest import ScanIngestResult, scan_and_record
from engine.pdf_import import PdfImportResult
from engine.tax_return_pdf import Form1040Record
from models.household import Household
from models.sourced import Source
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials import _analysis as analysis_mod
from views.ytd_income._partials import _event_log as event_log_mod
from views.ytd_income._partials import _manual_entry as manual_entry_mod
from views.ytd_income._partials import _sync_scan as sync_scan_mod

_RECORDED_AT = datetime(2026, 7, 17, 9, 0, 0)
_GOLDEN_MAGI = 290_000.0
_GOLDEN_YEAR = 2024

# audit HIGH fix: the prior_year_magi CANDIDATE recorded by scan_and_record is
# compute_irmaa_magi(agi, tax_exempt_interest) = 280_000.0 + 1_000.0, NOT the
# record's own (FEIE-inclusive, Roth/ACA-flavor) .magi field above — feie=0.0
# in this fixture so this only differs from the old (buggy) 290_000.0 answer
# because .magi was deliberately set as an independent sentinel, not derived
# from agi + tax_exempt_interest + feie.
_GOLDEN_IRMAA_MAGI = 281_000.0

_FORM_1040 = Form1040Record(
    tax_year=_GOLDEN_YEAR,
    agi=280_000.0,
    tax_exempt_interest=1_000.0,
    taxable_ss=0.0,
    qualified_dividends=0.0,
    ordinary_dividends=0.0,
    feie=0.0,
    magi=_GOLDEN_MAGI,
    filing_status=None,
    captured_at="2026-07-17T00:00:00+00:00",
)

_BROKERAGE_REC = BrokerageStatementRecord(
    account_number="XXXX1234",
    broker="vanguard",
    account_type="taxable",
    statement_period_end="2026-06-30",
    interest_taxable_ytd=100.0,
    interest_tax_exempt_ytd=0.0,
    dividends_taxable_ytd=200.0,
    dividends_tax_exempt_ytd=0.0,
    stcg_net_ytd=0.0,
    ltcg_net_ytd=0.0,
    captured_at="2026-07-10T00:00:00+00:00",
)


def _fixed_result() -> PdfImportResult:
    return PdfImportResult(
        brokerage_records=[_BROKERAGE_REC],
        form_1040_records={_GOLDEN_YEAR: _FORM_1040},
    )


def _stub_hh(**kwargs) -> Household:
    return Household(your_age=61, spouse_age=55, your_ira=500_000, spouse_ira=500_000, **kwargs)


def _make_mock_st(ytd: YTDSnapshot) -> MagicMock:
    """Mirrors tests/test_views_ytd_income.py::_make_mock_st."""
    mock_st = MagicMock()
    session_state = MagicMock()
    _state: dict = {"ytd_snapshot": ytd, "apply_ytd_to_projection": False}
    session_state.get.side_effect = lambda key, default=None: _state.get(key, default)
    mock_st.session_state = session_state
    mock_st.number_input.return_value = 0
    mock_st.checkbox.return_value = False

    def _columns_side_effect(arg):
        n = arg if isinstance(arg, int) else len(arg)
        return [MagicMock() for _ in range(n)]

    mock_st.columns.side_effect = _columns_side_effect
    mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)
    mock_st.form.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_st.form.return_value.__exit__ = MagicMock(return_value=False)
    mock_st.form_submit_button.return_value = False
    mock_st.button.return_value = False
    return mock_st


@pytest.fixture
def clean_candidate_store():
    """CANDIDATE_STORE_PATH is repo-root-anchored (engine.data_sources.paths) --
    same isolation approach as tests/test_pdf_magi_candidate_flow.py. Cleans up
    BEFORE (not just after) to guard against a leftover file from a prior
    interrupted suite run (mirrors tests/conftest.py's
    clean_command_center_caches pattern)."""
    CANDIDATE_STORE_PATH.unlink(missing_ok=True)
    yield
    CANDIDATE_STORE_PATH.unlink(missing_ok=True)


def _run_ytd_scan(tmp_path, monkeypatch) -> tuple[MagicMock, dict]:
    """Drives views/ytd_income.py's "Scan folder" handler against the fixed
    scan_pdf_folder result. Returns (mock_st, persisted pdf cache dict)."""
    import engine.pdf_ledger as ledger_mod
    import engine.pdf_owner as owner_mod
    import engine.tax_return_pdf as tax_return_pdf_mod

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pdf_cache_path = tmp_path / ".tax_pdf_cache.json"
    monkeypatch.setattr(tax_return_pdf_mod, "_PDF_TAX_CACHE_PATH", pdf_cache_path)

    hh = _stub_hh()
    ytd = YTDSnapshot()
    mock_st = _make_mock_st(ytd)
    mock_st.checkbox.return_value = False
    mock_st.text_input.return_value = str(tmp_path)
    mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"
    mock_st.selectbox.return_value = "household"  # no owner_key -> manual confirm

    import views._shared as shared_mod

    with (
        patch.object(ytd_income_mod, "st", mock_st),
        # run_folder_scan's _pdf_1040_scanned write lives in views/_shared.py,
        # which has its own `import streamlit as st` binding -- must be
        # patched separately from ytd_income_mod.st (post-rewire; the ORIGINAL
        # handler wrote it inline via ytd_income_mod.st only).
        patch.object(shared_mod, "st", mock_st),
        # views/ytd_income/_partials/_sync_scan.py holds the actual Scan-folder
        # handler post-Task-3 extraction -- has its own `import streamlit as st`
        # binding, so must be patched separately too.
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(manual_entry_mod, "st", mock_st),
        patch.object(event_log_mod, "st", mock_st),
        patch.object(analysis_mod, "st", mock_st),
        patch("engine.pdf_import.scan_pdf_folder", return_value=_fixed_result()),
        patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
        patch("engine.brokerage_statement_pdf.save_statement_folder_path"),
        patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
        patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
        patch.object(ytd_income_mod, "save_ytd_snapshot"),
        patch.object(sync_scan_mod, "save_ytd_snapshot"),
        patch.object(ledger_mod, "_LEDGER_PATH", tmp_path / ".pdf_import_ledger.json"),
        patch.object(owner_mod, "_OWNER_MAP_PATH", tmp_path / ".pdf_owner_map.json"),
    ):
        # recorded_at is real datetime.now() here -- not asserted (A0/A2 only
        # golden the value/source/detail, matching record_magi_candidates'
        # contract; the timestamp naturally differs run to run).
        mock_fetch_ex.return_value = MagicMock(server_available=False)
        ytd_income_mod.render(hh)

    from engine.tax_return_pdf import load_pdf_tax_records

    persisted = load_pdf_tax_records()
    return mock_st, persisted


class TestA0CurrentScanHandlerGolden:
    """Pins the CURRENT (pre-refactor) ytd_income.py scan handler's outcome."""

    def test_records_magi_candidate_for_scanned_1040(self, tmp_path, monkeypatch, clean_candidate_store):
        _run_ytd_scan(tmp_path, monkeypatch)

        # audit-0805 W1: re-import at test-run time (not the module-level
        # binding frozen at collection) to see tests/conftest.py's per-test
        # cache-path redirect.
        from engine.data_sources.paths import CANDIDATE_STORE_PATH

        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        candidates = store.candidates_for(f"prior_year_magi.{_GOLDEN_YEAR}")
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.value == _GOLDEN_IRMAA_MAGI
        assert candidate.prov.source == Source.PDF
        assert candidate.prov.detail == "Form 1040 PDF"

    def test_persists_merged_pdf_tax_cache(self, tmp_path, monkeypatch, clean_candidate_store):
        _mock_st, persisted = _run_ytd_scan(tmp_path, monkeypatch)

        assert set(persisted) == {_GOLDEN_YEAR}
        assert persisted[_GOLDEN_YEAR].magi == _GOLDEN_MAGI
        assert persisted[_GOLDEN_YEAR].tax_year == _GOLDEN_YEAR

    def test_writes_pdf_1040_scanned_session_key(self, tmp_path, monkeypatch, clean_candidate_store):
        mock_st, _persisted = _run_ytd_scan(tmp_path, monkeypatch)

        setitem_calls = [
            call
            for call in mock_st.session_state.__setitem__.call_args_list
            if call[0][0] == "_pdf_1040_scanned"
        ]
        assert setitem_calls, "Expected _pdf_1040_scanned to be written after a scan with a Form 1040"
        written = setitem_calls[-1][0][1]
        assert set(written) == {_GOLDEN_YEAR}
        assert written[_GOLDEN_YEAR].magi == _GOLDEN_MAGI


class TestA1ScanAndRecordPureHelper:
    """engine.data_sources.scan_ingest.scan_and_record reproduces the A0 golden."""

    def test_no_streamlit_import(self):
        import engine.data_sources.scan_ingest as mod

        assert "streamlit" not in getattr(mod, "__dict__", {})
        source = Path(mod.__file__).read_text()
        assert "import streamlit" not in source

    def test_records_same_magi_candidate(self, tmp_path, clean_candidate_store):
        with patch("engine.pdf_import.scan_pdf_folder", return_value=_fixed_result()):
            result = scan_and_record(tmp_path, recorded_at=_RECORDED_AT)

        assert isinstance(result, ScanIngestResult)
        assert result.magi_candidates_recorded == 1

        from engine.data_sources.paths import CANDIDATE_STORE_PATH

        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        candidates = store.candidates_for(f"prior_year_magi.{_GOLDEN_YEAR}")
        assert len(candidates) == 1
        assert candidates[0].value == _GOLDEN_IRMAA_MAGI
        assert candidates[0].prov.source == Source.PDF
        assert candidates[0].prov.detail == "Form 1040 PDF"

    def test_persists_same_merged_pdf_cache(self, tmp_path):
        cache_path = tmp_path / "scan_ingest_cache.json"
        store_path = tmp_path / "scan_ingest_candidates.json"
        with patch("engine.pdf_import.scan_pdf_folder", return_value=_fixed_result()):
            result = scan_and_record(
                tmp_path,
                store_path=store_path,
                pdf_cache_path=cache_path,
                recorded_at=_RECORDED_AT,
            )

        assert set(result.pdf_cache) == {_GOLDEN_YEAR}
        assert result.pdf_cache[_GOLDEN_YEAR].magi == _GOLDEN_MAGI

        import json

        raw = json.loads(cache_path.read_text())
        assert set(raw) == {str(_GOLDEN_YEAR)}
        assert Form1040Record.from_dict(raw[str(_GOLDEN_YEAR)]).magi == _GOLDEN_MAGI

    def test_counts_and_raw_result(self, tmp_path, clean_candidate_store):
        with patch("engine.pdf_import.scan_pdf_folder", return_value=_fixed_result()):
            result = scan_and_record(tmp_path, recorded_at=_RECORDED_AT)

        assert result.brokerage_count == 1
        assert result.form_1040_count == 1
        assert result.koinly_count == 0
        assert result.skipped_count == 0
        assert result.unrecognized_count == 0
        assert result.errors == []
        assert result.raw.form_1040_records == {_GOLDEN_YEAR: _FORM_1040}
        assert result.files_scanned == 2

    def test_no_form_1040_records_records_nothing(self, tmp_path, clean_candidate_store):
        empty_result = PdfImportResult(brokerage_records=[_BROKERAGE_REC])
        with patch("engine.pdf_import.scan_pdf_folder", return_value=empty_result):
            result = scan_and_record(tmp_path, recorded_at=_RECORDED_AT)

        assert result.magi_candidates_recorded == 0
        assert result.pdf_cache == {}
        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        assert not store.has_candidates(f"prior_year_magi.{_GOLDEN_YEAR}")


class TestAuditHighIrmaaFeieScope:
    """Audit HIGH: prior_year_magi (IRMAA-scoped) must not receive the
    FEIE-inclusive Roth/ACA-flavor MAGI. End-to-end through scan_and_record
    -> resolver -> irmaa_surcharge for the audit's concrete AGI=$200,000 +
    FEIE=$20,000 case (pre-fix: fabricated a $2,296.80/year surcharge).
    """

    _FEIE_YEAR = 2025
    _FEIE_FORM_1040 = Form1040Record(
        tax_year=_FEIE_YEAR,
        agi=200_000.0,
        tax_exempt_interest=0.0,
        taxable_ss=0.0,
        qualified_dividends=0.0,
        ordinary_dividends=0.0,
        feie=20_000.0,
        magi=220_000.0,  # FEIE-inclusive Roth/ACA-flavor (compute_magi output)
        filing_status=None,
        captured_at="2026-07-17T00:00:00+00:00",
    )

    def test_recorded_candidate_excludes_feie(self, tmp_path, clean_candidate_store):
        feie_result = PdfImportResult(form_1040_records={self._FEIE_YEAR: self._FEIE_FORM_1040})
        with patch("engine.pdf_import.scan_pdf_folder", return_value=feie_result):
            result = scan_and_record(tmp_path, recorded_at=_RECORDED_AT)

        assert result.magi_candidates_recorded == 1
        from engine.data_sources.paths import CANDIDATE_STORE_PATH

        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        candidates = store.candidates_for(f"prior_year_magi.{self._FEIE_YEAR}")
        assert len(candidates) == 1
        # Correct IRMAA MAGI = AGI + tax_exempt_interest only ($200,000), NOT
        # the FEIE-inclusive $220,000 that the record's own .magi field carries.
        assert candidates[0].value == 200_000.0

    def test_resolved_household_irmaa_surcharge_matches_correct_magi(
        self, tmp_path, clean_candidate_store
    ):
        from engine.data_sources.choices import ChoiceMap
        from engine.data_sources.resolver import resolve
        from engine.irmaa import irmaa_surcharge

        feie_result = PdfImportResult(form_1040_records={self._FEIE_YEAR: self._FEIE_FORM_1040})
        with patch("engine.pdf_import.scan_pdf_folder", return_value=feie_result):
            scan_and_record(tmp_path, recorded_at=_RECORDED_AT)

        from engine.data_sources.paths import CANDIDATE_STORE_PATH

        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        result = resolve(Household(), store, ChoiceMap())
        resolved_magi = result.household.prior_year_magi[self._FEIE_YEAR]

        assert resolved_magi == 200_000.0
        # Fixed: no surcharge (below the 2026 Tier-1 $218,000 MFJ threshold).
        assert irmaa_surcharge(resolved_magi) == 0.0
        # Pre-fix (FEIE-inclusive $220,000) would have fabricated this exact
        # $2,296.80/year surcharge -- confirms the discrepancy the audit found.
        assert irmaa_surcharge(220_000.0) == pytest.approx(2_296.80, abs=0.01)


class TestA2RewiredYtdIncomeView:
    """views/ytd_income.py's "Scan folder" now routes through run_folder_scan --
    reruns the exact same fixture + mock_st harness as TestA0 (proving the
    golden is unchanged post-refactor) and additionally proves the scan is a
    SINGLE call to scan_pdf_folder (single entry point)."""

    def test_golden_unchanged_after_rewire(self, tmp_path, monkeypatch, clean_candidate_store):
        """Same assertions as TestA0 -- run against the (now rewired) view."""
        mock_st, persisted = _run_ytd_scan(tmp_path, monkeypatch)

        from engine.data_sources.paths import CANDIDATE_STORE_PATH

        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        candidates = store.candidates_for(f"prior_year_magi.{_GOLDEN_YEAR}")
        assert len(candidates) == 1
        assert candidates[0].value == _GOLDEN_IRMAA_MAGI
        assert candidates[0].prov.source == Source.PDF
        assert candidates[0].prov.detail == "Form 1040 PDF"

        assert set(persisted) == {_GOLDEN_YEAR}
        assert persisted[_GOLDEN_YEAR].magi == _GOLDEN_MAGI

        setitem_calls = [
            call
            for call in mock_st.session_state.__setitem__.call_args_list
            if call[0][0] == "_pdf_1040_scanned"
        ]
        assert len(setitem_calls) == 1, "run_folder_scan must be the ONLY _pdf_1040_scanned writer"

    def test_single_scan_pdf_folder_call(self, tmp_path, monkeypatch, clean_candidate_store):
        import engine.pdf_ledger as ledger_mod
        import engine.pdf_owner as owner_mod
        import engine.tax_return_pdf as tax_return_pdf_mod

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(tax_return_pdf_mod, "_PDF_TAX_CACHE_PATH", tmp_path / ".tax_pdf_cache.json")

        hh = _stub_hh()
        mock_st = _make_mock_st(YTDSnapshot())
        mock_st.checkbox.return_value = False
        mock_st.text_input.return_value = str(tmp_path)
        mock_st.button.side_effect = lambda label, **kw: label == "Scan folder"
        mock_st.selectbox.return_value = "household"

        import views._shared as shared_mod

        with (
            patch.object(ytd_income_mod, "st", mock_st),
            patch.object(shared_mod, "st", mock_st),
            patch.object(sync_scan_mod, "st", mock_st),
        patch.object(manual_entry_mod, "st", mock_st),
        patch.object(event_log_mod, "st", mock_st),
        patch.object(analysis_mod, "st", mock_st),
            patch("engine.pdf_import.scan_pdf_folder", return_value=_fixed_result()) as mock_scan,
            patch("engine.brokerage_statement_pdf.load_statement_folder_path", return_value=None),
            patch("engine.brokerage_statement_pdf.save_statement_folder_path"),
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.portfolio_sync.fetch_option_exercises") as mock_fetch_ex,
            patch.object(ytd_income_mod, "save_ytd_snapshot"),
            patch.object(sync_scan_mod, "save_ytd_snapshot"),
            patch.object(ledger_mod, "_LEDGER_PATH", tmp_path / ".pdf_import_ledger.json"),
            patch.object(owner_mod, "_OWNER_MAP_PATH", tmp_path / ".pdf_owner_map.json"),
        ):
            mock_fetch_ex.return_value = MagicMock(server_available=False)
            ytd_income_mod.render(hh)

        assert mock_scan.call_count == 1


class TestA3ParametersDuplicateScanRemoved:
    """views/setup/parameters.py no longer has its own folder/scan/writer --
    only the shared _pdf_1040_scanned-reading confirm loop remains."""

    def test_no_duplicate_folder_input_or_scan_button_in_source(self):
        import inspect

        import views.setup.parameters as parameters_mod

        source = inspect.getsource(parameters_mod._render_pdf_1040_import)
        assert "tax_1040_folder_path" not in source
        assert "Scan for 1040 PDFs" not in source
        assert "scan_1040_folder" not in source

    def test_save_1040_record_flow_still_works(self, tmp_path, monkeypatch):
        """Pre-seeded _pdf_1040_scanned (as if run_folder_scan already wrote it)
        + a 'Save 1040 record' click still persists the record via
        save_pdf_tax_records, exactly as before."""
        import views.setup.parameters as parameters_mod

        cache_path = tmp_path / ".tax_pdf_cache.json"
        import engine.tax_return_pdf as tax_return_pdf_mod

        monkeypatch.setattr(tax_return_pdf_mod, "_PDF_TAX_CACHE_PATH", cache_path)

        import dataclasses

        # _render_pdf_1040_import mutates rec.filing_status in place -- use a
        # private copy so this test never contaminates the shared _FORM_1040
        # fixture used by other tests in this module.
        _own_form_1040 = dataclasses.replace(_FORM_1040)

        mock_st = MagicMock()
        session_state = MagicMock()
        _state = {"_pdf_1040_scanned": {_GOLDEN_YEAR: _own_form_1040}}
        session_state.get.side_effect = lambda key, default=None: _state.get(key, default)
        session_state.__setitem__.side_effect = lambda key, value: _state.__setitem__(key, value)
        mock_st.session_state = session_state
        mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)
        mock_st.spinner.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_st.spinner.return_value.__exit__ = MagicMock(return_value=False)
        mock_st.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.selectbox.return_value = "married_filing_jointly"
        mock_st.button.side_effect = lambda label, **kw: label == "Save 1040 record"

        with (
            patch.object(parameters_mod, "st", mock_st),
            patch.object(parameters_mod, "is_pyodide", return_value=False),
        ):
            parameters_mod._render_pdf_1040_import()

        from engine.tax_return_pdf import load_pdf_tax_records

        persisted = load_pdf_tax_records()
        assert set(persisted) == {_GOLDEN_YEAR}
        assert persisted[_GOLDEN_YEAR].magi == _GOLDEN_MAGI
        assert persisted[_GOLDEN_YEAR].filing_status == "married_filing_jointly"
