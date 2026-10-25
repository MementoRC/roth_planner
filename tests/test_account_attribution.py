"""Tests for engine.account_attribution -- per-account owner overrides."""

from __future__ import annotations

import pytest

from engine.store import SINGLE_KEY
from engine.store.namespaces import ACCOUNT_ATTRIBUTION


class TestSaveOverrideRejectsAmbiguousBroker:
    def test_broker_containing_delimiter_is_rejected(self) -> None:
        import engine.account_attribution as mod

        with pytest.raises(ValueError, match=r"\|"):
            mod.save_account_override("schwab|evil", "****-*123", "spouse")


class TestOverridesRoundTrip:
    def test_save_load_round_trip(self) -> None:
        import engine.account_attribution as mod

        mod.save_account_override("schwab", "****-*123", "spouse")
        assert mod.load_account_overrides() == {("schwab", "****-*123"): "spouse"}

    def test_delete_removes_entry(self) -> None:
        import engine.account_attribution as mod

        mod.save_account_override("schwab", "****-*123", "spouse")
        mod.delete_account_override("schwab", "****-*123")
        assert mod.load_account_overrides() == {}

    def test_load_missing_returns_empty(self) -> None:
        import engine.account_attribution as mod

        assert mod.load_account_overrides() == {}

    def test_load_corrupt_returns_empty(self, corrupt_row) -> None:
        import engine.account_attribution as mod

        corrupt_row(ACCOUNT_ATTRIBUTION)
        assert mod.load_account_overrides() == {}

    def test_save_two_different_keys_both_persist(self) -> None:
        import engine.account_attribution as mod

        mod.save_account_override("schwab", "****-*123", "spouse")
        mod.save_account_override("vanguard", "****-*456", "you")
        assert mod.load_account_overrides() == {
            ("schwab", "****-*123"): "spouse",
            ("vanguard", "****-*456"): "you",
        }

    def test_save_invalid_owner_role_raises(self) -> None:
        import engine.account_attribution as mod

        with pytest.raises(ValueError, match="Invalid owner role"):
            mod.save_account_override("schwab", "****-*123", "bogus")

    def test_load_drops_entries_with_unknown_owner_role(self, store) -> None:
        """An owner value outside OWNER_ROLES must not survive the read.

        save_account_override validates its owner argument, but an imported
        legacy file, a truncated legacy entry, or a future role rename can all
        put an unknown value in the row. Consumers treat the loaded vocabulary
        as closed (views/setup/command_center.py indexes the resolved owner
        into a fixed three-item list), so an unknown role must be dropped here
        rather than handed onward.
        """
        import engine.account_attribution as mod

        store.put(
            ACCOUNT_ATTRIBUTION,
            {
                "version": 1,
                "overrides": {
                    "schwab|****-*123": "joint",
                    "vanguard|****-*456": "you",
                },
            },
        )
        assert mod.load_account_overrides() == {("vanguard", "****-*456"): "you"}

    def test_dropped_override_falls_back_to_instance_owner(self, store) -> None:
        """Dropping a bad override degrades to the instance owner, not a crash.

        This is the behaviour the tolerant read path already promises for a
        corrupt row, extended to a corrupt VALUE: the account is attributed to
        the instance owner exactly as if no override had been recorded.
        """
        import engine.account_attribution as mod

        store.put(ACCOUNT_ATTRIBUTION, {"version": 1, "overrides": {"schwab|****-*123": "joint"}})
        overrides = mod.load_account_overrides()
        resolved = mod.resolve_account_owner("schwab", "****-*123", overrides, "you")
        assert resolved == "you"
        assert resolved in mod.OWNER_ROLES


class TestRefusesToClobberCorruptStore:
    def test_save_refuses_to_clobber_corrupt_row(self, store, corrupt_row) -> None:
        import engine.account_attribution as mod

        payload = corrupt_row(ACCOUNT_ATTRIBUTION)
        with pytest.raises(mod.CorruptAccountAttributionError):
            mod.save_account_override("schwab", "****-*123", "spouse")
        assert store.backend.get(ACCOUNT_ATTRIBUTION, SINGLE_KEY).payload == payload

    def test_delete_refuses_to_clobber_corrupt_row(self, store, corrupt_row) -> None:
        import engine.account_attribution as mod

        payload = corrupt_row(ACCOUNT_ATTRIBUTION)
        with pytest.raises(mod.CorruptAccountAttributionError):
            mod.delete_account_override("schwab", "****-*123")
        assert store.backend.get(ACCOUNT_ATTRIBUTION, SINGLE_KEY).payload == payload


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
