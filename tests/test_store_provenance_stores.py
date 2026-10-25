"""Each provenance and mapping store reads and writes its namespace (PR 3).

All values are synthetic.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from engine.data_sources.committed import (
    CorruptCommittedCacheError,
    load_committed,
    save_committed,
)
from engine.store import SINGLE_KEY
from engine.store.namespaces import COMMITTED
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
