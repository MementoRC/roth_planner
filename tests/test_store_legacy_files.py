"""The legacy JSON registry that PR 2's migrate_json walks."""

from __future__ import annotations

import json

import pytest

from engine.store.core import SINGLE_KEY
from engine.store.legacy_files import (
    LegacyFile,
    declare_store,
    per_entry_reader,
    register_legacy_file,
    registered,
    single_document_reader,
)


def test_registry_is_empty_in_pr1():
    assert registered() == ()


def test_registered_entries_come_back_sorted_by_namespace():
    b = LegacyFile("b_ns", ".b.json", single_document_reader)
    a = LegacyFile("a_ns", ".a.json", single_document_reader)
    register_legacy_file(b)
    register_legacy_file(a)
    register_legacy_file(a)  # identical re-registration is harmless (module re-import)
    entries = registered()
    names = [entry.namespace for entry in entries]
    assert names == sorted(names)
    assert a in entries
    assert b in entries


def test_declare_store_registers_the_namespace_and_its_file():
    from engine.store import core

    assert declare_store("t_decl", ".t_decl.json") == "t_decl"
    assert "t_decl" in core._NAMESPACES
    assert LegacyFile("t_decl", ".t_decl.json", single_document_reader) in registered()
    with pytest.raises(ValueError, match="t_decl"):
        declare_store("t_decl", ".other.json")


def test_per_entry_reader_maps_each_entry_to_a_row(tmp_path):
    path = tmp_path / ".x.json"
    path.write_text(json.dumps({"k1": {"v": 1}, "k2": 2}))
    assert per_entry_reader(path) == {"k1": {"v": 1}, "k2": 2}


def test_per_entry_reader_rejects_a_non_object(tmp_path):
    path = tmp_path / ".x.json"
    path.write_text("[1, 2]")
    with pytest.raises(ValueError, match="expected a JSON object"):
        per_entry_reader(path)


def test_conflicting_registration_is_refused():
    register_legacy_file(LegacyFile("ns", ".one.json", single_document_reader))
    with pytest.raises(ValueError, match="ns"):
        register_legacy_file(LegacyFile("ns", ".two.json", single_document_reader))


def test_single_document_reader_maps_the_file_to_the_default_key(tmp_path):
    path = tmp_path / ".x.json"
    path.write_text(json.dumps({"a": 1}))
    assert single_document_reader(path) == {SINGLE_KEY: {"a": 1}}
