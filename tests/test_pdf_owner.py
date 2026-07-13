"""Tests for engine.pdf_owner -- owner role vocabulary and learned name->owner map."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.pdf_owner import (
    OWNER_ROLES,
    OwnerRole,
    learn_owner,
    load_owner_map,
    normalize_owner_key,
    resolve_owner,
    save_owner_map,
)


class TestOwnerRoles:
    def test_owner_roles_frozenset(self) -> None:
        assert frozenset({"you", "spouse", "household"}) == OWNER_ROLES

    def test_owner_role_constants(self) -> None:
        assert OwnerRole.YOU == "you"
        assert OwnerRole.SPOUSE == "spouse"
        assert OwnerRole.HOUSEHOLD == "household"


class TestNormalizeOwnerKey:
    def test_lowercases_and_strips(self) -> None:
        assert normalize_owner_key("  Claude R Cirba  ") == "claude r cirba"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_owner_key("Claude   R\tCirba") == "claude r cirba"

    def test_none_passthrough(self) -> None:
        assert normalize_owner_key(None) is None

    def test_empty_string_becomes_none(self) -> None:
        assert normalize_owner_key("   ") is None


class TestOwnerMapRoundTrip:
    def test_save_load_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_owner as mod

        monkeypatch.setattr(mod, "_OWNER_MAP_PATH", tmp_path / ".pdf_owner_map.json")
        save_owner_map({"claude r cirba": "you"})
        assert load_owner_map() == {"claude r cirba": "you"}

    def test_load_missing_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_owner as mod

        monkeypatch.setattr(mod, "_OWNER_MAP_PATH", tmp_path / "nope.json")
        assert load_owner_map() == {}

    def test_load_corrupt_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import engine.pdf_owner as mod

        bad = tmp_path / ".pdf_owner_map.json"
        bad.write_text("{not json")
        monkeypatch.setattr(mod, "_OWNER_MAP_PATH", bad)
        assert load_owner_map() == {}


class TestResolveOwner:
    def test_resolve_hit(self) -> None:
        assert resolve_owner("claude r cirba", {"claude r cirba": "you"}) == "you"

    def test_resolve_miss_returns_none(self) -> None:
        assert resolve_owner("jane doe", {"claude r cirba": "you"}) is None

    def test_resolve_none_key_returns_none(self) -> None:
        assert resolve_owner(None, {"claude r cirba": "you"}) is None

    def test_resolve_normalizes_before_lookup(self) -> None:
        assert resolve_owner("  Claude R Cirba  ", {"claude r cirba": "you"}) == "you"


class TestLearnOwner:
    def test_learn_adds_entry(self) -> None:
        existing: dict[str, str] = {}
        updated = learn_owner("Jane R Cirba", "spouse", existing)
        assert updated == {"jane r cirba": "spouse"}
        assert existing == {}  # pure -- does not mutate the input

    def test_learn_overwrites_existing_entry(self) -> None:
        existing = {"claude r cirba": "spouse"}
        updated = learn_owner("Claude R Cirba", "you", existing)
        assert updated == {"claude r cirba": "you"}

    def test_learn_rejects_invalid_role(self) -> None:
        with pytest.raises(ValueError, match="Invalid owner role"):
            learn_owner("Claude R Cirba", "cousin", {})

    def test_learn_none_key_raises(self) -> None:
        with pytest.raises(ValueError, match="owner key"):
            learn_owner(None, "you", {})
