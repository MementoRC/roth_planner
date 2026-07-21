"""Regression test for audit-0720 finding M8.

_resolve_grants must compare candidate vs. committed grants by
order-independent StockGrant.key() sets, not positional list equality, so
FinExtract reordering doesn't spuriously re-open review (PR #369).
"""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.resolver import GRANTS_KEY, resolve
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedList

FIXED_DT = datetime(2026, 7, 16, 12, 30, 45)
FIXED_DT_2 = datetime(2026, 7, 16, 13, 0, 0)


class TestM8GrantOrderIndependentComparison:
    def test_reordered_identical_grants_do_not_pend(self) -> None:
        grant_a = StockGrant(year=2019, strike=104.0, shares=650, expiry_year=2029, grant_id="A")
        grant_b = StockGrant(year=2020, strike=130.0, shares=400, expiry_year=2030, grant_id="B")
        prov = Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)
        committed = Household()
        committed.grants = SourcedList([grant_a, grant_b], [prov, prov])
        store = CandidateStore()
        store.record_candidate(
            GRANTS_KEY, [grant_b, grant_a], Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2)
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert GRANTS_KEY not in result.pending_review
