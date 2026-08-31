"""Tests for engine.account_attribution -- per-account owner overrides."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSaveOverrideRejectsAmbiguousBroker:
    def test_broker_containing_delimiter_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        with pytest.raises(ValueError, match=r"\|"):
            mod.save_account_override("schwab|evil", "****-*123", "spouse")


class TestOverridesRoundTrip:
    def test_save_load_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        mod.save_account_override("schwab", "****-*123", "spouse")
        assert mod.load_account_overrides() == {("schwab", "****-*123"): "spouse"}

    def test_delete_removes_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json")
        mod.save_account_override("schwab", "****-*123", "spouse")
        mod.delete_account_override("schwab", "****-*123")
        assert mod.load_account_overrides() == {}

    def test_load_missing_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / "nope.json")
        assert mod.load_account_overrides() == {}

    def test_load_corrupt_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        bad = tmp_path / ".account_attribution.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", bad)
        assert mod.load_account_overrides() == {}


class TestResolveAccountOwner:
    def test_resolves_to_instance_owner_when_no_override(self) -> None:
        import engine.account_attribution as mod

        assert mod.resolve_account_owner("schwab", "****-*123", {}, "you") == "you"

    def test_override_wins_over_instance_owner(self) -> None:
        import engine.account_attribution as mod

        overrides = {("schwab", "****-*123"): "spouse"}
        assert mod.resolve_account_owner("schwab", "****-*123", overrides, "you") == "spouse"

    def test_never_returns_none(self) -> None:
        import engine.account_attribution as mod

        assert mod.resolve_account_owner("vanguard", "9999", {}, "spouse") is not None
