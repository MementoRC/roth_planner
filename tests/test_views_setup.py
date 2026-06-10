"""Smoke tests for views.setup module."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from models.household import Household


def test_setup_module_imports():
    """views.setup must import without error."""
    from views import setup

    assert hasattr(setup, "render")


def test_render_signature():
    """render must accept a Household and return None."""
    from views import setup

    sig = inspect.signature(setup.render)
    params = list(sig.parameters.values())
    assert len(params) == 1, "render must take exactly one positional arg"
    assert params[0].annotation is Household or params[0].annotation == "Household"


def test_render_is_callable():
    from views import setup

    assert callable(setup.render)


class TestPyodideGating:
    """Verify the FinExtract sync block is gated behind is_pyodide()."""

    def test_fetch_portfolio_inside_pyodide_else_branch(self):
        """fetch_portfolio must appear AFTER the is_pyodide() guard in setup.py source.

        Static assertion: confirms the guard is not accidentally removed and that
        fetch_portfolio cannot be reached on Pyodide even without executing Streamlit.
        """
        import inspect

        from views import setup

        source = inspect.getsource(setup.render)
        guard_pos = source.find("is_pyodide()")
        fetch_pos = source.find("fetch_portfolio(")
        assert guard_pos != -1, "is_pyodide() guard not found in render()"
        assert fetch_pos != -1, "fetch_portfolio( call not found in render()"
        assert guard_pos < fetch_pos, (
            "fetch_portfolio() appears before is_pyodide() guard — "
            "sync block is not properly gated on Pyodide"
        )


class TestPdf1040ImportHelper:
    """Smoke tests for the _render_pdf_1040_import helper."""

    def test_helper_exists_and_is_callable(self):
        """_render_pdf_1040_import must exist and be callable."""
        from views import setup

        assert hasattr(setup, "_render_pdf_1040_import")
        assert callable(setup._render_pdf_1040_import)

    def test_pyodide_gate_present_in_source(self):
        """_render_pdf_1040_import must contain is_pyodide() guard."""
        import inspect

        from views import setup

        source = inspect.getsource(setup._render_pdf_1040_import)
        assert "is_pyodide()" in source, (
            "is_pyodide() guard missing from _render_pdf_1040_import — "
            "pdfplumber would be called in the Pyodide web build"
        )

    def test_filing_status_options_complete(self):
        """_FILING_STATUS_OPTIONS must contain all four canonical statuses."""
        from views.setup import _FILING_STATUS_OPTIONS

        assert set(_FILING_STATUS_OPTIONS) == {
            "married_filing_jointly",
            "single",
            "married_filing_separately",
            "head_of_household",
        }

    def test_pdf_1040_import_called_in_joint_sub(self):
        """_render_pdf_1040_import must be called inside render() (joint sub-tab context)."""
        import inspect

        from views import setup

        source = inspect.getsource(setup.render)
        assert "_render_pdf_1040_import()" in source, (
            "_render_pdf_1040_import() not called in render() — widget not wired up"
        )
        magi_pos = source.find("_render_prior_year_magi_anchor()")
        pdf_pos = source.find("_render_pdf_1040_import()")
        assert magi_pos != -1
        assert pdf_pos != -1
        assert magi_pos < pdf_pos, (
            "_render_pdf_1040_import() appears before _render_prior_year_magi_anchor() — "
            "expected PDF import to follow the manual number_input widget"
        )


class TestMergePdfMagi:
    """Unit tests for engine.tax_return_pdf.merge_pdf_magi."""

    def _make_record(self, year: int, magi: float) -> object:
        from engine.tax_return_pdf import Form1040Record

        return Form1040Record(
            tax_year=year,
            agi=magi - 500.0,
            tax_exempt_interest=300.0,
            taxable_ss=0.0,
            qualified_dividends=0.0,
            ordinary_dividends=0.0,
            feie=200.0,
            magi=magi,
            filing_status="married_filing_jointly",
            captured_at="2026-01-01T00:00:00+00:00",
        )

    def test_absent_year_is_filled(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {2023: self._make_record(2023, 162_000.0)}
        result = merge_pdf_magi({}, records)
        assert result[2023] == pytest.approx(162_000.0)

    def test_zero_year_is_filled(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {2023: self._make_record(2023, 162_000.0)}
        result = merge_pdf_magi({2023: 0.0}, records)
        assert result[2023] == pytest.approx(162_000.0)

    def test_nonzero_existing_is_preserved(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {2023: self._make_record(2023, 162_000.0)}
        result = merge_pdf_magi({2023: 175_000.0}, records)
        assert result[2023] == pytest.approx(175_000.0)

    def test_empty_records_returns_copy_of_existing(self):
        from engine.tax_return_pdf import merge_pdf_magi

        existing = {2023: 100_000.0, 2024: 120_000.0}
        result = merge_pdf_magi(existing, {})
        assert result == existing
        assert result is not existing  # must be a new dict

    def test_multiple_years_gap_fill(self):
        from engine.tax_return_pdf import merge_pdf_magi

        records = {
            2022: self._make_record(2022, 140_000.0),
            2023: self._make_record(2023, 162_000.0),
        }
        existing = {2023: 0.0}
        result = merge_pdf_magi(existing, records)
        assert result[2022] == pytest.approx(140_000.0)
        assert result[2023] == pytest.approx(162_000.0)


class TestPdfRecordRoundTrip:
    """Confirm save/load round-trip preserves filing_status and magi."""

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        from engine.tax_return_pdf import (
            _PDF_TAX_CACHE_PATH,
            Form1040Record,
            load_pdf_tax_records,
            save_pdf_tax_records,
        )

        # Patch the cache path to tmp_path
        original = _PDF_TAX_CACHE_PATH
        import engine.tax_return_pdf as _mod

        _mod._PDF_TAX_CACHE_PATH = tmp_path / ".tax_pdf_cache.json"
        try:
            rec = Form1040Record(
                tax_year=2023,
                agi=162_433.0,
                tax_exempt_interest=2_511.0,
                taxable_ss=0.0,
                qualified_dividends=500.0,
                ordinary_dividends=1_200.0,
                feie=3_000.0,
                magi=167_944.0,
                filing_status="married_filing_jointly",
                captured_at="2026-06-09T12:00:00+00:00",
            )
            save_pdf_tax_records({2023: rec})
            loaded = load_pdf_tax_records()
            assert 2023 in loaded
            assert loaded[2023].magi == pytest.approx(167_944.0)
            assert loaded[2023].filing_status == "married_filing_jointly"
            assert loaded[2023].tax_year == 2023
        finally:
            _mod._PDF_TAX_CACHE_PATH = original
