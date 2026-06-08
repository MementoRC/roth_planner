"""Tests for the synthetic-defaults gate and JSON loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.defaults import DEFAULTS
from config.loader import load_defaults


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
        result = load_defaults()
        assert result == DEFAULTS

    def test_cwd_override_file_wins(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
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
        (tmp_path / ".user_defaults.py").write_text("OVERRIDES = {'your_age': 50}\n")
        env_file = tmp_path / "env_overrides.py"
        env_file.write_text("OVERRIDES = {'your_age': 70}\n")
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(env_file))
        result = load_defaults()
        assert result["your_age"] == 70

    def test_partial_override_merges(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.py").write_text("OVERRIDES = {'your_ira': 1_000_000}\n")
        result = load_defaults()
        assert result["your_ira"] == 1_000_000
        assert result["spouse_ira"] == DEFAULTS["spouse_ira"]
        assert result["employer_name"] == DEFAULTS["employer_name"]

    def test_missing_overrides_attr_falls_through(self, tmp_path, monkeypatch):
        """If the file exists but has no OVERRIDES, fall through to DEFAULTS."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.py").write_text("# empty\n")
        result = load_defaults()
        assert result == DEFAULTS


class TestJsonOverrideLoader:
    """load_defaults picks up .user_defaults.json and merges correctly."""

    def test_json_file_overrides_scalar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
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
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
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
        json_file = tmp_path / "my_overrides.json"
        json_file.write_text(json.dumps({"your_age": 70, "living_expenses": 80_000}))
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(json_file))
        result = load_defaults()
        assert result["your_age"] == 70
        assert result["living_expenses"] == 80_000

    def test_json_preferred_over_py(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When both .user_defaults.json and .user_defaults.py exist, JSON wins."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.json").write_text(json.dumps({"your_age": 99}))
        (tmp_path / ".user_defaults.py").write_text("OVERRIDES = {'your_age': 1}\n")
        result = load_defaults()
        assert result["your_age"] == 99

    def test_invalid_json_falls_through_to_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.json").write_text("not valid json{{")
        result = load_defaults()
        assert result["your_age"] == DEFAULTS["your_age"]


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
