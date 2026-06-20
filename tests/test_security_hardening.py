"""Regression tests for security hardening: cache file permissions (0o600)."""

from __future__ import annotations

import stat

from engine.portfolio_sync.portfolio import save_snapshot
from engine.portfolio_sync.shapes import PortfolioSnapshot, TaxReturnSnapshot
from engine.portfolio_sync.tax_return import save_tax_snapshot
from engine.tax_return_pdf import save_pdf_tax_records


class TestCacheFilePermissions:
    """All three PII cache files must be written with mode 0o600."""

    def test_portfolio_cache_mode_600(self, monkeypatch, tmp_path):
        cache_file = tmp_path / ".portfolio_cache.json"
        monkeypatch.setattr("engine.portfolio_sync.portfolio._CACHE_PATH", cache_file)
        save_snapshot(PortfolioSnapshot())
        assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600

    def test_tax_return_cache_mode_600(self, monkeypatch, tmp_path):
        cache_file = tmp_path / ".tax_return_cache.json"
        monkeypatch.setattr("engine.portfolio_sync.tax_return._TAX_CACHE_PATH", cache_file)
        save_tax_snapshot(TaxReturnSnapshot())
        assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600

    def test_pdf_tax_cache_mode_600(self, monkeypatch, tmp_path):
        cache_file = tmp_path / ".tax_pdf_cache.json"
        monkeypatch.setattr("engine.tax_return_pdf._PDF_TAX_CACHE_PATH", cache_file)
        save_pdf_tax_records({})
        assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600
