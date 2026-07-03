"""Regression tests for security hardening: cache file permissions (0o600)."""

from __future__ import annotations

import os
import stat

import pytest

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


class TestWritePiiJsonONofollow:
    """SEC-01: write_pii_json must include O_NOFOLLOW to close the symlink-follow gap."""

    def test_o_nofollow_flag_present_in_source(self):
        """Static assertion: O_NOFOLLOW must appear in the os.open call."""
        import inspect

        from engine import secure_io

        source = inspect.getsource(secure_io.write_pii_json)
        assert "O_NOFOLLOW" in source, (
            "write_pii_json is missing os.O_NOFOLLOW — a pre-planted symlink can redirect the write"
        )

    def test_o_nofollow_in_actual_flags(self, tmp_path):
        """Runtime assertion: write_pii_json raises OSError on a symlink target."""
        from engine.secure_io import write_pii_json

        target = tmp_path / "real_file.json"
        target.write_text("{}")
        link = tmp_path / "link.json"
        link.symlink_to(target)

        with pytest.raises(OSError, match="Too many levels|Not a directory|symlink"):
            write_pii_json(link, {"key": "value"})

    def test_normal_write_still_works(self, tmp_path):
        """write_pii_json must still succeed for a plain (non-symlink) path."""
        import json

        from engine.secure_io import write_pii_json

        cache_file = tmp_path / "cache.json"
        write_pii_json(cache_file, {"foo": 42})
        assert json.loads(cache_file.read_text()) == {"foo": 42}
        assert stat.S_IMODE(cache_file.stat().st_mode) == 0o600


class TestReadPiiBytes:
    """SEC-02: read_pii_bytes round-trips bytes and refuses symlinks."""

    def test_roundtrip_raw_bytes(self, tmp_path):
        """read_pii_bytes returns the exact bytes written to a normal file."""
        from engine.secure_io import read_pii_bytes

        target = tmp_path / "cache.json"
        payload = b'{"key": "value", "num": 42}'
        target.write_bytes(payload)
        assert read_pii_bytes(target) == payload

    def test_raises_oserror_on_symlink(self, tmp_path):
        """read_pii_bytes must raise OSError when path is a symlink (O_NOFOLLOW)."""
        from engine.secure_io import read_pii_bytes

        target = tmp_path / "real.json"
        target.write_bytes(b"{}")
        link = tmp_path / "link.json"
        os.symlink(target, link)

        with pytest.raises(OSError, match="Too many levels|Not a directory|symlink"):
            read_pii_bytes(link)
