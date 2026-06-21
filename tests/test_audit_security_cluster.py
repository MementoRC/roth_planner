"""Tests for AUDIT_2026-06-20 security cluster (F12/F35/F36/F38/F57).

Each class corresponds to one finding; tests are minimal and focused on the
invariant enforced by the fix.
"""

from __future__ import annotations

import os
import stat
import warnings

import pytest

# ---------------------------------------------------------------------------
# F12/F35 — PII JSON savers create files with mode 0o600 (no TOCTOU window)
# ---------------------------------------------------------------------------


class TestPiiJsonSaverModes:
    """Each saver must produce a file with exactly mode 0o600."""

    def test_save_ytd_snapshot_mode_600(self, tmp_path, monkeypatch):
        from engine.portfolio_sync import ytd as ytd_mod
        from models.ytd_income import YTDSnapshot

        cache_path = tmp_path / ".ytd_cache.json"
        monkeypatch.setattr(ytd_mod, "_YTD_CACHE_PATH", cache_path)

        ytd_mod.save_ytd_snapshot(YTDSnapshot())

        assert cache_path.exists()
        assert stat.S_IMODE(os.stat(cache_path).st_mode) == 0o600

    def test_save_snapshot_portfolio_mode_600(self, tmp_path, monkeypatch):
        from engine.portfolio_sync import portfolio as portfolio_mod
        from engine.portfolio_sync.shapes import PortfolioSnapshot

        cache_path = tmp_path / ".portfolio_cache.json"
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache_path)

        portfolio_mod.save_snapshot(PortfolioSnapshot())

        assert cache_path.exists()
        assert stat.S_IMODE(os.stat(cache_path).st_mode) == 0o600

    def test_save_tax_snapshot_mode_600(self, tmp_path, monkeypatch):
        from engine.portfolio_sync import tax_return as tr_mod
        from engine.portfolio_sync.shapes import TaxReturnSnapshot

        cache_path = tmp_path / ".tax_return_cache.json"
        monkeypatch.setattr(tr_mod, "_TAX_CACHE_PATH", cache_path)

        tr_mod.save_tax_snapshot(TaxReturnSnapshot())

        assert cache_path.exists()
        assert stat.S_IMODE(os.stat(cache_path).st_mode) == 0o600

    def test_save_pdf_tax_records_mode_600(self, tmp_path, monkeypatch):
        import engine.tax_return_pdf as pdf_mod

        cache_path = tmp_path / ".tax_pdf_cache.json"
        monkeypatch.setattr(pdf_mod, "_PDF_TAX_CACHE_PATH", cache_path)

        pdf_mod.save_pdf_tax_records({})

        assert cache_path.exists()
        assert stat.S_IMODE(os.stat(cache_path).st_mode) == 0o600

    def test_overwrite_preserves_mode_600(self, tmp_path, monkeypatch):
        """Re-writing a pre-existing 0o644 file must tighten to 0o600."""
        from engine.portfolio_sync import ytd as ytd_mod
        from models.ytd_income import YTDSnapshot

        cache_path = tmp_path / ".ytd_cache.json"
        # Pre-create with loose permissions to simulate the old pattern
        cache_path.write_text("{}")
        cache_path.chmod(0o644)

        monkeypatch.setattr(ytd_mod, "_YTD_CACHE_PATH", cache_path)
        ytd_mod.save_ytd_snapshot(YTDSnapshot())

        assert stat.S_IMODE(os.stat(cache_path).st_mode) == 0o600


# ---------------------------------------------------------------------------
# F36 — _PDF_TAX_CACHE_PATH must be absolute and land at project root
# ---------------------------------------------------------------------------


class TestPdfTaxCachePath:
    def test_is_absolute(self):
        from engine.tax_return_pdf import _PDF_TAX_CACHE_PATH

        assert _PDF_TAX_CACHE_PATH.is_absolute()

    def test_filename_correct(self):
        from engine.tax_return_pdf import _PDF_TAX_CACHE_PATH

        assert _PDF_TAX_CACHE_PATH.name == ".tax_pdf_cache.json"

    def test_lands_at_project_root(self):
        """Parent directory must be the repo root (same level as engine/)."""
        import engine.tax_return_pdf as _m
        from engine.tax_return_pdf import _PDF_TAX_CACHE_PATH

        # engine/tax_return_pdf.py → engine/ → project root
        expected_parent = _m.__file__ and (
            __import__("pathlib").Path(_m.__file__).resolve().parent.parent
        )
        assert _PDF_TAX_CACHE_PATH.parent == expected_parent


# ---------------------------------------------------------------------------
# F38 — BROWSER_PRIVKEY_LS_KEY must not exist on engine.data_bridge_browser
# ---------------------------------------------------------------------------


class TestBrowserPrivkeyLsKeyRemoved:
    def test_constant_not_exported(self):
        import engine.data_bridge_browser as mod

        assert not hasattr(mod, "BROWSER_PRIVKEY_LS_KEY")


# ---------------------------------------------------------------------------
# F57 — _try_load splits OSError (silent) vs ValueError (RuntimeWarning)
# ---------------------------------------------------------------------------


class TestTryLoadWarning:
    def test_missing_file_returns_none_no_warning(self, tmp_path):
        from engine.data_bridge_keys import _try_load

        missing = tmp_path / "no_such_file.key"
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning → failure
            result = _try_load("NONEXISTENT_ENV_VAR_XYZ", missing)

        assert result is None

    def test_malformed_file_warns_and_returns_none(self, tmp_path):
        from engine.data_bridge_keys import _try_load

        bad_key = tmp_path / "bad.key"
        bad_key.write_text("not-valid-key-material\n")

        with pytest.warns(RuntimeWarning, match="key file unreadable"):
            result = _try_load("NONEXISTENT_ENV_VAR_XYZ", bad_key)

        assert result is None
