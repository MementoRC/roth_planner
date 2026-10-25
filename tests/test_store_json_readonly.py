"""JsonReadOnlyBackend: a failed import's namespaces are read from their JSON files."""

from __future__ import annotations

import json

import pytest

from engine.store.core import SINGLE_KEY, Store
from engine.store.errors import CorruptStoreError, StoreUnavailableError
from engine.store.json_readonly import JsonReadOnlyBackend
from engine.store.legacy_files import LegacyFile, per_entry_reader, single_document_reader

ONE = LegacyFile("t_one", ".t_one.json", single_document_reader)
KEYED = LegacyFile("t_keyed", ".t_keyed.json", per_entry_reader)


def _store(tmp_path) -> Store:
    return Store(JsonReadOnlyBackend(tmp_path, [ONE, KEYED], reason="importing .t_one.json failed"))


def test_reads_the_legacy_document(tmp_path):
    (tmp_path / ".t_one.json").write_text(json.dumps({"a": 1}))
    store = _store(tmp_path)
    assert store.get("t_one") == {"a": 1}
    assert store.keys("t_one") == [SINGLE_KEY]


def test_reads_each_entry_of_a_keyed_file(tmp_path):
    (tmp_path / ".t_keyed.json").write_text(json.dumps({"k2": 2, "k1": 1}))
    store = _store(tmp_path)
    assert store.keys("t_keyed") == ["k1", "k2"]
    assert store.get("t_keyed", key="k2") == 2
    assert store.get("t_keyed", key="nope") is None


def test_reads_follow_the_file_as_it_is_now(tmp_path):
    path = tmp_path / ".t_one.json"
    path.write_text(json.dumps({"a": 1}))
    store = _store(tmp_path)
    assert store.get("t_one") == {"a": 1}
    path.write_text(json.dumps({"a": 2}))
    assert store.get("t_one") == {"a": 2}


def test_missing_file_and_unknown_namespace_read_absent(tmp_path):
    store = _store(tmp_path)
    assert store.get("t_one") is None
    assert store.keys("t_one") == []
    assert store.get("not_routed") is None


def test_invalid_json_is_a_corrupt_row(tmp_path):
    (tmp_path / ".t_one.json").write_text("{not json")
    with pytest.raises(CorruptStoreError):
        _store(tmp_path).get("t_one")


def test_a_symlinked_file_is_unavailable_not_followed(tmp_path):
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"a": 1}))
    (tmp_path / ".t_one.json").symlink_to(target)
    with pytest.raises(StoreUnavailableError):
        _store(tmp_path).get("t_one")


def test_writes_are_refused_and_the_file_is_untouched(tmp_path):
    path = tmp_path / ".t_one.json"
    path.write_text(json.dumps({"a": 1}))
    store = _store(tmp_path)
    with pytest.raises(StoreUnavailableError, match="saving is disabled"):
        store.put("t_one", {"a": 2})
    with pytest.raises(StoreUnavailableError, match="saving is disabled"):
        store.delete("t_one")
    assert json.loads(path.read_text()) == {"a": 1}
