"""Tests for the synthetic-defaults gate and JSON loader."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import config.loader as _loader_mod
from config.defaults import DEFAULTS
from config.loader import load_defaults, save_user_defaults


class TestSyntheticDefaults:
    def test_defaults_have_expected_shape(self):
        required_keys = {
            "your_age",
            "spouse_age",
            "your_ira",
            "spouse_ira",
            "your_ss_fra",
            "spouse_ss_fra",
            "living_expenses",
            "employer_name",
            "stock_ticker",
            "stock_price_now",
            "stock_price_late",
            "grants",
        }
        assert required_keys <= set(DEFAULTS)

    def test_defaults_have_no_personal_data(self):
        """The synthetic defaults must not leak household-specific values."""
        # Sentinel values that would indicate personal data leaked into the repo
        forbidden_ira = {1_700_000, 1_700_000.0}
        forbidden_employer = {"TXN", "Texas Instruments"}
        forbidden_ss = {3_800, 3_800.0}

        assert DEFAULTS["your_ira"] not in forbidden_ira
        assert DEFAULTS["spouse_ira"] not in forbidden_ira
        assert DEFAULTS["your_age"] != 61
        assert DEFAULTS["employer_name"] not in forbidden_employer
        assert DEFAULTS["stock_ticker"] not in forbidden_employer
        assert DEFAULTS["your_ss_fra"] not in forbidden_ss


class TestOverrideLoader:
    def test_no_override_returns_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # no .user_defaults.py in cwd
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        result = load_defaults()
        assert result == DEFAULTS

    def test_cwd_override_file_wins(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.py").write_text(
            "OVERRIDES = {'your_age': 99, 'your_ira': 9_000_000}\n"
        )
        result = load_defaults()
        assert result["your_age"] == 99
        assert result["your_ira"] == 9_000_000
        # Unspecified keys still come from DEFAULTS
        assert result["spouse_age"] == DEFAULTS["spouse_age"]

    def test_env_var_overrides_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.py").write_text("OVERRIDES = {'your_age': 50}\n")
        env_file = tmp_path / "env_overrides.py"
        env_file.write_text("OVERRIDES = {'your_age': 70}\n")
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(env_file))
        result = load_defaults()
        assert result["your_age"] == 70

    def test_partial_override_merges(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.py").write_text("OVERRIDES = {'your_ira': 1_000_000}\n")
        result = load_defaults()
        assert result["your_ira"] == 1_000_000
        assert result["spouse_ira"] == DEFAULTS["spouse_ira"]
        assert result["employer_name"] == DEFAULTS["employer_name"]

    def test_missing_overrides_attr_falls_through(self, tmp_path, monkeypatch):
        """If the file exists but has no OVERRIDES, fall through to DEFAULTS."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.py").write_text("# empty\n")
        result = load_defaults()
        assert result == DEFAULTS


class TestJsonOverrideLoader:
    """load_defaults picks up .user_defaults.json and merges correctly."""

    def test_json_file_overrides_scalar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(_loader_mod, "_USER_DEFAULTS_PATH", tmp_path / ".user_defaults.json")
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.json").write_text(
            json.dumps({"your_age": 63, "spouse_age": 57})
        )
        result = load_defaults()
        assert result["your_age"] == 63
        assert result["spouse_age"] == 57
        # Non-overridden keys still come from DEFAULTS
        assert result["your_ira"] == DEFAULTS["your_ira"]

    def test_grant_strikes_passed_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(_loader_mod, "_USER_DEFAULTS_PATH", tmp_path / ".user_defaults.json")
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        strikes = {"2019": 104.41, "2020": 130.52, "2021": 169.23}
        (tmp_path / ".user_defaults.json").write_text(json.dumps({"grant_strikes": strikes}))
        result = load_defaults()
        assert result["grant_strikes"] == strikes
        # Full grants list from DEFAULTS still present
        assert "grants" in result

    def test_env_var_pointing_at_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        json_file = tmp_path / "my_overrides.json"
        json_file.write_text(json.dumps({"your_age": 70, "living_expenses": 80_000}))
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(json_file))
        result = load_defaults()
        assert result["your_age"] == 70
        assert result["living_expenses"] == 80_000

    def test_json_preferred_over_py(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both .user_defaults.json and .user_defaults.py exist, JSON wins."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(_loader_mod, "_USER_DEFAULTS_PATH", tmp_path / ".user_defaults.json")
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.json").write_text(json.dumps({"your_age": 99}))
        (tmp_path / ".user_defaults.py").write_text("OVERRIDES = {'your_age': 1}\n")
        result = load_defaults()
        assert result["your_age"] == 99

    def test_invalid_json_falls_through_to_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.json").write_text("not valid json{{")
        result = load_defaults()
        assert result["your_age"] == DEFAULTS["your_age"]


class TestPyOverrideIsTrusted:
    """SEC-04: _py_override_is_trusted must reject group/world-writable .py overrides."""

    def test_group_writable_py_override_is_not_exec(self, tmp_path, monkeypatch):
        """A group-writable .py override must not be exec'd (returns {})."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        override = tmp_path / ".user_defaults.py"
        override.write_text("OVERRIDES = {'your_age': 99}\n")
        os.chmod(override, 0o664)  # group-writable

        from config.loader import _load_overrides_from_py

        result = _load_overrides_from_py(override)
        assert result == {}

    def test_world_writable_py_override_is_not_exec(self, tmp_path, monkeypatch):
        """A world-writable .py override must not be exec'd (returns {})."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        override = tmp_path / ".user_defaults.py"
        override.write_text("OVERRIDES = {'your_age': 88}\n")
        os.chmod(override, 0o646)  # world-writable

        from config.loader import _load_overrides_from_py

        result = _load_overrides_from_py(override)
        assert result == {}

    def test_secure_600_py_override_still_applied(self, tmp_path, monkeypatch):
        """A 0o600 owner-owned .py override must still be exec'd normally."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        override = tmp_path / ".user_defaults.py"
        override.write_text("OVERRIDES = {'your_age': 77}\n")
        os.chmod(override, 0o600)

        from config.loader import _load_overrides_from_py

        result = _load_overrides_from_py(override)
        assert result == {"your_age": 77}

    @pytest.mark.skipif(not hasattr(os, "getuid"), reason="uid check not available on Windows")
    def test_warns_on_group_writable(self, tmp_path, caplog):
        """_py_override_is_trusted logs a warning for group/world-writable files."""
        import logging

        from config.loader import _py_override_is_trusted

        p = tmp_path / "override.py"
        p.write_text("OVERRIDES = {}\n")
        os.chmod(p, 0o664)

        with caplog.at_level(logging.WARNING, logger="config.loader"):
            result = _py_override_is_trusted(p)
        assert result is False
        assert any("chmod" in r.message for r in caplog.records)


class TestSaveUserDefaults:
    """save_user_defaults is the write-side counterpart to load_defaults."""

    def test_round_trips_through_load_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        save_user_defaults({"your_age": 63, "spouse_age": 57})
        result = load_defaults()
        assert result["your_age"] == 63
        assert result["spouse_age"] == 57
        # Non-overridden keys still come from DEFAULTS
        assert result["your_ira"] == DEFAULTS["your_ira"]

    def test_writes_file_with_restrictive_permissions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(_loader_mod, "_USER_DEFAULTS_PATH", tmp_path / ".user_defaults.json")
        save_user_defaults({"your_age": 63})
        path = tmp_path / ".user_defaults.json"
        assert path.exists()
        assert path.stat().st_mode & 0o777 == 0o600

    def test_failed_write_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A save into a nonexistent directory must not raise (best-effort)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "config.loader.Path",
            lambda *_a, **_kw: tmp_path / "missing_dir" / ".user_defaults.json",
        )
        save_user_defaults({"your_age": 63})  # must not raise


class TestClearUserDefaults:
    """clear_user_defaults deletes the on-disk personal file so a Reset-to-demo
    is not silently undone on the next startup (audit-0802 F3).

    save_user_defaults merges incoming data ON TOP of whatever is on disk, so
    session-only clearing cannot neutralise a stale file — the file itself must
    be removed.
    """

    def test_deletes_existing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from config.loader import clear_user_defaults

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(_loader_mod, "_USER_DEFAULTS_PATH", tmp_path / ".user_defaults.json")
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        save_user_defaults({"your_ira": 1_700_000})
        assert (tmp_path / ".user_defaults.json").exists()

        clear_user_defaults()

        assert not (tmp_path / ".user_defaults.json").exists()
        # load_defaults now falls through to demo DEFAULTS
        assert load_defaults()["your_ira"] == DEFAULTS["your_ira"]

    def test_missing_file_is_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deleting when no file exists must be a silent no-op, not an error."""
        from config.loader import clear_user_defaults

        monkeypatch.chdir(tmp_path)
        clear_user_defaults()  # must not raise


class TestSyntheticGrantStrikes:
    """DEFAULTS exposes grant_strikes mirroring the synthetic grants."""

    def test_grant_strikes_present_in_defaults(self) -> None:
        assert "grant_strikes" in DEFAULTS

    def test_grant_strikes_mirror_synthetic_grants(self) -> None:
        strikes = DEFAULTS["grant_strikes"]
        for grant in DEFAULTS["grants"]:
            year_str = str(grant.year)
            assert year_str in strikes, f"Missing strike for year {grant.year}"
            assert strikes[year_str] == grant.strike
