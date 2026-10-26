"""Regression tests for security hardening of the PII read helpers."""

from __future__ import annotations

import os

import pytest


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
