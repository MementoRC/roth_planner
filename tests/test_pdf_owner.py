"""Tests for engine.pdf_owner -- owner role vocabulary and learned name->owner map."""

from __future__ import annotations

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
from engine.store import SINGLE_KEY, CorruptStoreError
from engine.store.namespaces import PDF_OWNER_MAP


class TestOwnerRoles:
    def test_owner_roles_frozenset(self) -> None:
        assert frozenset({"you", "spouse", "household"}) == OWNER_ROLES

    def test_owner_role_constants(self) -> None:
        assert OwnerRole.YOU == "you"
        assert OwnerRole.SPOUSE == "spouse"
        assert OwnerRole.HOUSEHOLD == "household"


class TestNormalizeOwnerKey:
    def test_lowercases_and_strips(self) -> None:
        assert normalize_owner_key("  John Q Sample  ") == "john q sample"

    def test_collapses_internal_whitespace(self) -> None:
        assert normalize_owner_key("John   Q\tSample") == "john q sample"

    def test_none_passthrough(self) -> None:
        assert normalize_owner_key(None) is None

    def test_empty_string_becomes_none(self) -> None:
        assert normalize_owner_key("   ") is None


class TestOwnerMapRoundTrip:
    def test_save_load_round_trip(self) -> None:
        save_owner_map({"john q sample": "you"})
        assert load_owner_map() == {"john q sample": "you"}

    def test_load_missing_returns_empty(self) -> None:
        assert load_owner_map() == {}

    def test_load_corrupt_returns_empty(self, corrupt_row) -> None:
        corrupt_row(PDF_OWNER_MAP)
        assert load_owner_map() == {}

    def test_save_refuses_a_corrupt_row(self, store, corrupt_row) -> None:
        payload = corrupt_row(PDF_OWNER_MAP)
        with pytest.raises(CorruptStoreError, match="pdf_owner_map"):
            save_owner_map({"john q sample": "you"})
        assert store.backend.get(PDF_OWNER_MAP, SINGLE_KEY).payload == payload


class TestResolveOwner:
    def test_resolve_hit(self) -> None:
        assert resolve_owner("john q sample", {"john q sample": "you"}) == "you"

    def test_resolve_miss_returns_none(self) -> None:
        assert resolve_owner("jane doe", {"john q sample": "you"}) is None

    def test_resolve_none_key_returns_none(self) -> None:
        assert resolve_owner(None, {"john q sample": "you"}) is None

    def test_resolve_normalizes_before_lookup(self) -> None:
        assert resolve_owner("  John Q Sample  ", {"john q sample": "you"}) == "you"


class TestLearnOwner:
    def test_learn_adds_entry(self) -> None:
        existing: dict[str, str] = {}
        updated = learn_owner("Jane R Sample", "spouse", existing)
        assert updated == {"jane r sample": "spouse"}
        assert existing == {}  # pure -- does not mutate the input

    def test_learn_overwrites_existing_entry(self) -> None:
        existing = {"john q sample": "spouse"}
        updated = learn_owner("John Q Sample", "you", existing)
        assert updated == {"john q sample": "you"}

    def test_learn_rejects_invalid_role(self) -> None:
        with pytest.raises(ValueError, match="Invalid owner role"):
            learn_owner("John Q Sample", "cousin", {})

    def test_learn_none_key_raises(self) -> None:
        with pytest.raises(ValueError, match="owner key"):
            learn_owner(None, "you", {})
