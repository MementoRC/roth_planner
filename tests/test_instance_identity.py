"""Tests for engine.instance_identity -- durable per-instance owner identity."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestInstanceOwnerRoundTrip:
    def test_save_load_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.instance_identity as mod

        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", tmp_path / ".instance_owner.json")
        mod.save_instance_owner("you")
        assert mod.load_instance_owner() == "you"

    def test_load_missing_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.instance_identity as mod

        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", tmp_path / "nope.json")
        assert mod.load_instance_owner() is None

    def test_load_corrupt_raises_and_is_not_overwritten(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.instance_identity as mod

        bad = tmp_path / ".instance_owner.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", bad)
        with pytest.raises(mod.CorruptInstanceOwnerError):
            mod.load_instance_owner()
        assert bad.read_text() == "{not json"

    def test_save_rejects_household(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.instance_identity as mod

        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", tmp_path / ".instance_owner.json")
        with pytest.raises(ValueError, match="Invalid instance owner"):
            mod.save_instance_owner("household")

    def test_save_refuses_to_clobber_corrupt_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.instance_identity as mod

        bad = tmp_path / ".instance_owner.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "INSTANCE_OWNER_PATH", bad)
        with pytest.raises(mod.CorruptInstanceOwnerError):
            mod.save_instance_owner("you")
        assert bad.read_text() == "{not json"
