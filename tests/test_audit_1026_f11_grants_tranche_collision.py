"""audit-1026 F-11: grant-change detection compares every tranche. Synthetic data."""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.resolver import GRANTS_KEY, resolve
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedList

T0 = datetime(2026, 10, 1, 12, 0, 0)


def _g(shares: int) -> StockGrant:
    return StockGrant(year=2020, strike=50.0, shares=shares, expiry_year=2030)


def _pending(committed: list[StockGrant], candidate: list[StockGrant]) -> bool:
    prov = Provenance(Source.MANUAL, T0, "committed")
    hh = Household(grants=SourcedList(list(committed), [prov] * len(committed)))
    store = CandidateStore()
    store.record_candidate(GRANTS_KEY, list(candidate), Provenance(Source.PDF, T0, "scan"))
    return GRANTS_KEY in resolve(hh, store, ChoiceMap()).pending_review


def test_f11_grants_tranche_dropped_first_tranche_differs():
    assert _pending([_g(1000), _g(500)], [_g(500)])


def test_f11_grants_tranche_changed_first_tranche_shares_differs():
    assert _pending([_g(1000), _g(500)], [_g(900), _g(500)])


def test_f11_grants_tranche_same_list_different_order_does_not_differ():
    assert not _pending([_g(1000), _g(500)], [_g(500), _g(1000)])


def test_f11_grants_tranche_identical_lists_do_not_differ():
    assert not _pending([_g(1000), _g(500)], [_g(1000), _g(500)])
