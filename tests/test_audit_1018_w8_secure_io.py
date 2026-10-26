"""Audit 2026-10-18, wave W8: secure I/O defects.

F-20  read_pii_json let UnicodeDecodeError escape the (JSONDecodeError, OSError)
      handlers of its callers.
F-23  write_pii_json was not atomic (writer deleted: the stores live in the database).
F-42  a non-UTF-8 committed baseline / candidate store / choice map crashed.
F-43  save_snapshot / save_ssa_snapshot crashed on a valid-JSON non-object cache.

All data is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import engine.portfolio_sync.portfolio as portfolio_mod
import engine.portfolio_sync.social_security as ssa_mod
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import (
    CorruptCommittedCacheError,
    load_committed,
    save_committed,
)
from engine.portfolio_sync.shapes import PortfolioSnapshot, SSASnapshot
from engine.secure_io import read_pii_json
from engine.store import SINGLE_KEY
from engine.store.namespaces import CANDIDATE_STORE, COMMITTED, PORTFOLIO, SSA, TRUST_CHOICES

BAD_BYTES = b"\xff\xfe\x00not utf-8 \xc3\x28"


class TestF20ReadDecodeError:
    def test_read_pii_json_raises_jsondecodeerror_on_bad_utf8(self, tmp_path: Path) -> None:
        p = tmp_path / "c.json"
        p.write_bytes(BAD_BYTES)
        with pytest.raises(json.JSONDecodeError):
            read_pii_json(p)

    def test_load_snapshot_returns_none_on_bad_utf8(self, corrupt_row) -> None:
        corrupt_row(PORTFOLIO)
        assert portfolio_mod.load_snapshot() is None

    def test_load_ssa_returns_none_on_bad_utf8(self, corrupt_row) -> None:
        corrupt_row(SSA)
        assert ssa_mod.load_ssa_snapshot(owner="you") is None


class TestF42NonUtf8Corrupt:
    def test_load_committed_raises_corrupt_error(self, store, corrupt_row) -> None:
        payload = corrupt_row(COMMITTED)
        with pytest.raises(CorruptCommittedCacheError):
            load_committed()
        assert store.backend.get(COMMITTED, SINGLE_KEY).payload == payload

    def test_save_committed_refuses_and_preserves(self, store, corrupt_row) -> None:
        payload = corrupt_row(COMMITTED)
        with pytest.raises(CorruptCommittedCacheError):
            save_committed({})
        assert store.backend.get(COMMITTED, SINGLE_KEY).payload == payload

    def test_candidate_store_load_flags_corrupt(self, store, corrupt_row) -> None:
        payload = corrupt_row(CANDIDATE_STORE)
        assert CandidateStore.load().load_corrupt is True
        assert store.backend.get(CANDIDATE_STORE, SINGLE_KEY).payload == payload

    def test_choice_map_load_flags_corrupt(self, store, corrupt_row) -> None:
        payload = corrupt_row(TRUST_CHOICES)
        cm = ChoiceMap.load()
        assert cm.load_corrupt is True
        assert store.backend.get(TRUST_CHOICES, SINGLE_KEY).payload == payload


class TestF43NonObjectCache:
    def test_save_ssa_snapshot_over_list_cache(self, store) -> None:
        store.put(SSA, [1, 2])
        # Issue #39: a non-object cache is preserved, not replaced.
        with pytest.raises(ssa_mod.CorruptSSACacheError):
            ssa_mod.save_ssa_snapshot(SSASnapshot(), owner="you")
        assert store.get(SSA) == [1, 2]

    def test_save_snapshot_over_list_cache(self, store) -> None:
        store.put(PORTFOLIO, [1, 2])
        with pytest.raises(portfolio_mod.CorruptPortfolioCacheError, match="portfolio"):
            portfolio_mod.save_snapshot(PortfolioSnapshot())
        assert store.get(PORTFOLIO) == [1, 2]
