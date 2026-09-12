"""Tests for engine.account_attribution -- per-account owner overrides."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestSaveOverrideRejectsAmbiguousBroker:
    def test_broker_containing_delimiter_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(
            mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json"
        )
        with pytest.raises(ValueError, match=r"\|"):
            mod.save_account_override("schwab|evil", "****-*123", "spouse")


class TestOverridesRoundTrip:
    def test_save_load_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(
            mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json"
        )
        mod.save_account_override("schwab", "****-*123", "spouse")
        assert mod.load_account_overrides() == {("schwab", "****-*123"): "spouse"}

    def test_delete_removes_entry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(
            mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json"
        )
        mod.save_account_override("schwab", "****-*123", "spouse")
        mod.delete_account_override("schwab", "****-*123")
        assert mod.load_account_overrides() == {}

    def test_load_missing_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / "nope.json")
        assert mod.load_account_overrides() == {}

    def test_load_corrupt_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        bad = tmp_path / ".account_attribution.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", bad)
        assert mod.load_account_overrides() == {}

    def test_save_two_different_keys_both_persist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(
            mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json"
        )
        mod.save_account_override("schwab", "****-*123", "spouse")
        mod.save_account_override("vanguard", "****-*456", "you")
        assert mod.load_account_overrides() == {
            ("schwab", "****-*123"): "spouse",
            ("vanguard", "****-*456"): "you",
        }

    def test_save_invalid_owner_role_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        monkeypatch.setattr(
            mod, "_ACCOUNT_ATTRIBUTION_PATH", tmp_path / ".account_attribution.json"
        )
        with pytest.raises(ValueError, match="Invalid owner role"):
            mod.save_account_override("schwab", "****-*123", "bogus")

    def test_load_drops_entries_with_unknown_owner_role(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An owner value outside OWNER_ROLES must not survive the read.

        save_account_override validates its owner argument, but the store is
        plaintext JSON on disk -- a hand edit, a truncated legacy entry, or a
        future role rename can all put an unknown value there. Consumers treat
        the loaded vocabulary as closed (views/setup/command_center.py indexes
        the resolved owner into a fixed three-item list), so an unknown role
        must be dropped here rather than handed onward.
        """
        import json

        import engine.account_attribution as mod

        store = tmp_path / ".account_attribution.json"
        store.write_text(
            json.dumps(
                {
                    "version": 1,
                    "overrides": {
                        "schwab|****-*123": "joint",
                        "vanguard|****-*456": "you",
                    },
                }
            )
        )
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", store)
        assert mod.load_account_overrides() == {("vanguard", "****-*456"): "you"}

    def test_dropped_override_falls_back_to_instance_owner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dropping a bad override degrades to the instance owner, not a crash.

        This is the behaviour the tolerant read path already promises for a
        corrupt file, extended to a corrupt VALUE: the account is attributed to
        the instance owner exactly as if no override had been recorded.
        """
        import json

        import engine.account_attribution as mod

        store = tmp_path / ".account_attribution.json"
        store.write_text(json.dumps({"version": 1, "overrides": {"schwab|****-*123": "joint"}}))
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", store)
        overrides = mod.load_account_overrides()
        resolved = mod.resolve_account_owner("schwab", "****-*123", overrides, "you")
        assert resolved == "you"
        assert resolved in mod.OWNER_ROLES


class TestRefusesToClobberCorruptStore:
    def test_save_refuses_to_clobber_corrupt_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        bad = tmp_path / ".account_attribution.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", bad)
        with pytest.raises(mod.CorruptAccountAttributionError):
            mod.save_account_override("schwab", "****-*123", "spouse")
        assert bad.read_text() == "{not json"

    def test_delete_refuses_to_clobber_corrupt_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.account_attribution as mod

        bad = tmp_path / ".account_attribution.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_ACCOUNT_ATTRIBUTION_PATH", bad)
        with pytest.raises(mod.CorruptAccountAttributionError):
            mod.delete_account_override("schwab", "****-*123")
        assert bad.read_text() == "{not json"


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


class TestAccountAttributionPathIsGloballyRedirected:
    def test_no_local_monkeypatch_still_avoids_the_real_repo_file(self) -> None:
        from engine.account_attribution import save_account_override

        save_account_override("schwab", "****-*123", "spouse")
