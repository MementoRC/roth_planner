"""Each provenance and mapping store reads and writes its namespace (PR 3).

All values are synthetic.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from engine.data_sources.candidate_store import CandidateStore, CorruptCandidateStoreError
from engine.data_sources.committed import (
    CorruptCommittedCacheError,
    load_committed,
    save_committed,
)
from engine.data_sources.record import discard_pdf_balance_candidates, record_ira_balance_candidate
from engine.store import SINGLE_KEY
from engine.store.namespaces import CANDIDATE_STORE, COMMITTED
from models.sourced import Provenance, Source, SourcedValue

_T0 = datetime(2026, 1, 1, 12, 0, 0)


def _baseline(value: float) -> dict:
    return {"your_ira": SourcedValue(value, Provenance(Source.MANUAL, _T0)).to_json()}


class TestCommittedStore:
    def test_round_trip(self, store):
        save_committed(_baseline(1_000.0))
        assert store.get(COMMITTED) == _baseline(1_000.0)
        assert load_committed() == _baseline(1_000.0)

    def test_absent_reads_none(self):
        assert load_committed() is None

    def test_corrupt_row_raises_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(COMMITTED)
        with pytest.raises(CorruptCommittedCacheError, match="corrupt store row"):
            load_committed()
        with pytest.raises(CorruptCommittedCacheError, match="corrupt store row"):
            save_committed(_baseline(1.0))
        assert store.backend.get(COMMITTED, SINGLE_KEY).payload == payload

    def test_wrong_shape_is_corrupt_and_kept(self, store):
        store.put(COMMITTED, [1, 2])
        with pytest.raises(CorruptCommittedCacheError, match="JSON object"):
            load_committed()
        with pytest.raises(CorruptCommittedCacheError, match="JSON object"):
            save_committed(_baseline(1.0))
        assert store.get(COMMITTED) == [1, 2]


class TestCandidateStore:
    def test_round_trip(self, store):
        cands = CandidateStore()
        cands.record_candidate("your_ira", 1_000.0, Provenance(Source.MANUAL, _T0))
        cands.save()
        assert set(store.get(CANDIDATE_STORE)) == {"your_ira"}
        loaded = CandidateStore.load()
        assert loaded.candidates_for("your_ira")[0].value == 1_000.0
        assert loaded.load_corrupt is False

    def test_absent_is_empty_and_not_corrupt(self):
        loaded = CandidateStore.load()
        assert loaded.field_keys() == []
        assert loaded.load_corrupt is False

    def test_corrupt_row_loads_empty_flagged_and_refuses_a_save(self, store, corrupt_row):
        payload = corrupt_row(CANDIDATE_STORE)
        loaded = CandidateStore.load()
        assert loaded.field_keys() == []
        assert loaded.load_corrupt is True
        with pytest.raises(CorruptCandidateStoreError, match="corrupt store row"):
            loaded.save()
        assert store.backend.get(CANDIDATE_STORE, SINGLE_KEY).payload == payload

    def test_wrong_shape_is_corrupt_and_kept(self, store):
        store.put(CANDIDATE_STORE, [1, 2])
        assert CandidateStore.load().load_corrupt is True
        with pytest.raises(CorruptCandidateStoreError, match="JSON object"):
            CandidateStore().save()
        assert store.get(CANDIDATE_STORE) == [1, 2]

    def test_record_helpers_use_the_saved_store(self, store):
        assert record_ira_balance_candidate("your_ira", 1_000.4, "synthetic", _T0)
        [cand] = CandidateStore.load().candidates_for("your_ira")
        assert cand.value == 1_000.0
        assert discard_pdf_balance_candidates(["you"]) == 1
        assert CandidateStore.load().candidates_for("your_ira") == []
