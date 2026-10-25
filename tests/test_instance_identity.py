"""Tests for engine.instance_identity -- durable per-instance owner identity."""

from __future__ import annotations

import pytest

from engine.store import SINGLE_KEY
from engine.store.namespaces import INSTANCE_OWNER
from tests.conftest import CORRUPT_PAYLOAD


class TestInstanceOwnerRoundTrip:
    def test_save_load_round_trip(self) -> None:
        import engine.instance_identity as mod

        mod.save_instance_owner("you")
        assert mod.load_instance_owner() == "you"

    def test_load_missing_returns_none(self) -> None:
        import engine.instance_identity as mod

        assert mod.load_instance_owner() is None

    def test_load_corrupt_raises_and_is_not_overwritten(self, store, corrupt_row) -> None:
        import engine.instance_identity as mod

        corrupt_row(INSTANCE_OWNER)
        with pytest.raises(mod.CorruptInstanceOwnerError):
            mod.load_instance_owner()
        assert store.backend.get(INSTANCE_OWNER, SINGLE_KEY).payload == CORRUPT_PAYLOAD

    def test_save_rejects_household(self) -> None:
        import engine.instance_identity as mod

        with pytest.raises(ValueError, match="Invalid instance owner"):
            mod.save_instance_owner("household")

    def test_save_refuses_to_clobber_corrupt_row(self, store, corrupt_row) -> None:
        import engine.instance_identity as mod

        corrupt_row(INSTANCE_OWNER)
        with pytest.raises(mod.CorruptInstanceOwnerError):
            mod.save_instance_owner("you")
        assert store.backend.get(INSTANCE_OWNER, SINGLE_KEY).payload == CORRUPT_PAYLOAD
