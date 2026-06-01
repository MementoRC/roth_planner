"""Tests for the synthetic-defaults gate."""
from __future__ import annotations

from config.defaults import DEFAULTS
from config.loader import load_defaults


class TestSyntheticDefaults:
    def test_defaults_have_expected_shape(self):
        required_keys = {
            "your_age", "spouse_age",
            "your_ira", "spouse_ira",
            "your_ss_fra", "spouse_ss_fra",
            "living_expenses",
            "employer_name", "stock_ticker",
            "stock_price_now", "stock_price_late",
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
        (tmp_path / ".user_defaults.py").write_text(
            "OVERRIDES = {'your_age': 50}\n"
        )
        env_file = tmp_path / "env_overrides.py"
        env_file.write_text("OVERRIDES = {'your_age': 70}\n")
        monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(env_file))
        result = load_defaults()
        assert result["your_age"] == 70

    def test_partial_override_merges(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
        (tmp_path / ".user_defaults.py").write_text(
            "OVERRIDES = {'your_ira': 1_000_000}\n"
        )
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
