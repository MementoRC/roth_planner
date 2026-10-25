"""Tests for AUDIT_2026-06-20 security cluster (F12/F35/F36/F38/F57).

Each class corresponds to one finding; tests are minimal and focused on the
invariant enforced by the fix.
"""

from __future__ import annotations

import warnings

import pytest

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
