"""Regression tests for audit-0721 W4 findings: exercise-schedule/grant
share-count invariants (C11, C22, C21+C12 collision cluster, C23, C24)."""

from __future__ import annotations

import copy
import pickle
from datetime import datetime

import pytest

from engine.exercise_grid import normalize_grid_edits
from engine.exercise_optimizer import _build_candidate_schedule
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedDict, SourcedList


def approx(expected, tol=1e-6):
    return pytest.approx(expected, abs=tol)


class TestC11GridAggregateCap:
    """normalize_grid_edits must cap the SUM of scheduled shares per grant,
    not just each cell individually."""

    def test_sum_across_years_is_capped_to_grant_shares(self):
        grant = StockGrant(2019, 104.0, 150, 2029)
        years = [2026, 2027, 2028]
        raw = {grant.key(): {2026: 100, 2027: 100, 2028: 100}}

        norm = normalize_grid_edits([grant], years, raw)

        assert sum(norm.shares_by_key[grant.key()].values()) == 150
        assert norm.shares_by_key[grant.key()] == {2026: 100, 2027: 50}
        assert norm.remaining_by_key[grant.key()] == 0

    def test_under_budget_entries_pass_through_unclamped(self):
        grant = StockGrant(2019, 104.0, 650, 2029)
        years = [2026, 2027]
        raw = {grant.key(): {2026: 200, 2027: 50}}

        norm = normalize_grid_edits([grant], years, raw)

        assert norm.shares_by_key[grant.key()] == {2026: 200, 2027: 50}
        assert norm.remaining_by_key[grant.key()] == 400


class TestC22CumulativeClamp:
    """income_for must clamp CUMULATIVELY across years for a grant, not
    independently per cell."""

    def test_income_for_clamps_cumulatively_not_per_cell(self):
        grant = StockGrant(2019, 100.0, 1000, 2030, grant_id="g1")
        sched = ExerciseSchedule()
        sched.set_shares("g1", 2028, 700)
        sched.set_shares("g1", 2029, 700)
        sched.set_price(2028, 150.0)
        sched.set_price(2029, 150.0)

        income_2028 = sched.income_for(2028, [grant])
        income_2029 = sched.income_for(2029, [grant])

        # per-share spread = 50; true cap is 1000 shares * $50 = $50,000 total.
        assert income_2028 == approx(700 * 50.0)
        assert income_2029 == approx(300 * 50.0)
        assert income_2028 + income_2029 == approx(50_000.0)


class TestC21C12CollisionCluster:
    """Colliding grants (empty grant_id, same year/strike/expiry_year) must
    be treated as one aggregated lot, not last-write-wins or double-counted."""

    def test_colliding_grants_share_a_key(self):
        g1 = StockGrant(2019, 150.0, 500, 2029)
        g2 = StockGrant(2019, 150.0, 300, 2029)
        assert g1.key() == g2.key()

    def test_default_at_expiry_sums_colliding_grant_shares(self):
        g1 = StockGrant(2019, 150.0, 500, 2029)
        g2 = StockGrant(2019, 150.0, 300, 2029)

        sched = ExerciseSchedule.default_at_expiry([g1, g2], base_year=2026, price_now=200.0)

        assert sched.shares(g1.key(), 2029) == 800
        assert sched.total_exercised(g1.key()) == 800

    def test_income_for_single_counts_colliding_grants_shared_cell(self):
        g1 = StockGrant(2019, 150.0, 500, 2029)
        g2 = StockGrant(2019, 150.0, 300, 2029)

        sched = ExerciseSchedule.default_at_expiry([g1, g2], base_year=2026, price_now=200.0)

        # 800 aggregated shares * $50 spread = $40,000 -- NOT double-counted
        # to $80,000 by iterating g1 and g2 separately.
        assert sched.income_for(2029, [g1, g2]) == approx(40_000.0)

    def test_build_candidate_schedule_aggregates_remaining_shares(self):
        g1 = StockGrant(2028, 100.0, 100, 2028)
        g2 = StockGrant(2028, 100.0, 300, 2028)

        schedule, over = _build_candidate_schedule(
            [g1, g2],
            base_year=2026,
            ceiling_income_by_year={2026: 1e9, 2027: 1e9, 2028: 1e9},
            base_ex_option_by_year={2026: 0.0, 2027: 0.0, 2028: 0.0},
            price_for_year=lambda _y: 200.0,
        )

        # Both 100 and 300 shares survive scheduling (aggregated to 400),
        # not just the last-processed grant's 300 (dict comprehension
        # last-write-wins bug).
        assert schedule.total_exercised(g1.key()) == 400
        assert over == []


class TestC23HouseholdGrantsIndependence:
    """Household()'s default grants must not be shared by identity across
    instances."""

    def test_default_grants_are_independent_objects(self):
        hh1 = Household()
        hh2 = Household()

        assert hh1.grants[0] is not hh2.grants[0]

    def test_mutating_one_households_grant_does_not_leak(self):
        hh1 = Household()
        hh2 = Household()
        original_shares = hh2.grants[0].shares

        hh1.grants[0].shares = 999_999

        assert hh2.grants[0].shares == original_shares


class TestC24SourcedMutationGuards:
    """SourcedList/SourcedDict must not silently desync prov on mutation."""

    def test_sourced_list_append_raises(self):
        prov = [Provenance(Source.MANUAL, datetime.now())] * 3
        sl = SourcedList([1, 2, 3], prov)

        with pytest.raises(TypeError):
            sl.append(4)

        # Original data/prov untouched by the failed mutation attempt.
        assert list(sl) == [1, 2, 3]
        assert len(sl.prov) == 3

    def test_sourced_list_setitem_raises(self):
        prov = [Provenance(Source.MANUAL, datetime.now())]
        sl = SourcedList([1], prov)

        with pytest.raises(TypeError):
            sl[0] = 2

    def test_sourced_dict_setitem_raises(self):
        prov = {1: Provenance(Source.MANUAL, datetime.now())}
        sd = SourcedDict({1: 10.0}, prov)

        with pytest.raises(TypeError):
            sd[2] = 20.0

    def test_sourced_dict_update_raises(self):
        prov = {1: Provenance(Source.MANUAL, datetime.now())}
        sd = SourcedDict({1: 10.0}, prov)

        with pytest.raises(TypeError):
            sd.update({2: 20.0})

    def test_construction_still_works_after_mutation_guards(self):
        # __init__ must not route through the overridden mutators.
        prov = [Provenance(Source.MANUAL, datetime.now())] * 2
        sl = SourcedList([1, 2], prov)
        assert list(sl) == [1, 2]
        assert sl.to_json()["data"] == [1, 2]

    # -- copy/deepcopy/pickle must reconstruct via __init__, not the raising
    # -- mutators CPython normally uses to rebuild list/dict subclasses.

    def test_sourced_list_deepcopy_preserves_data_and_prov(self):
        prov = [Provenance(Source.MANUAL, datetime.now()), Provenance(Source.PDF, datetime.now())]
        sl = SourcedList([1, 2], prov)

        cp = copy.deepcopy(sl)

        assert type(cp) is SourcedList
        assert list(cp) == [1, 2]
        assert cp.prov == prov
        assert cp is not sl

    def test_sourced_list_copy_preserves_data_and_prov(self):
        prov = [Provenance(Source.MANUAL, datetime.now()), Provenance(Source.PDF, datetime.now())]
        sl = SourcedList([1, 2], prov)

        cp = copy.copy(sl)

        assert type(cp) is SourcedList
        assert list(cp) == [1, 2]
        assert cp.prov == prov

    def test_sourced_list_pickle_roundtrip_preserves_data_and_prov(self):
        prov = [Provenance(Source.MANUAL, datetime.now()), Provenance(Source.PDF, datetime.now())]
        sl = SourcedList([1, 2], prov)

        rt = pickle.loads(pickle.dumps(sl))

        assert type(rt) is SourcedList
        assert list(rt) == [1, 2]
        assert rt.prov == prov

    def test_sourced_list_copies_still_raise_on_mutation(self):
        prov = [Provenance(Source.MANUAL, datetime.now())]
        sl = SourcedList([1], prov)

        with pytest.raises(TypeError):
            copy.deepcopy(sl).append(2)

    def test_sourced_dict_deepcopy_preserves_data_and_prov(self):
        prov = {1: Provenance(Source.MANUAL, datetime.now())}
        sd = SourcedDict({1: 10.0}, prov)

        cp = copy.deepcopy(sd)

        assert type(cp) is SourcedDict
        assert dict(cp) == {1: 10.0}
        assert cp.prov == prov
        assert cp is not sd

    def test_sourced_dict_copy_preserves_data_and_prov(self):
        prov = {1: Provenance(Source.MANUAL, datetime.now())}
        sd = SourcedDict({1: 10.0}, prov)

        cp = copy.copy(sd)

        assert type(cp) is SourcedDict
        assert dict(cp) == {1: 10.0}
        assert cp.prov == prov

    def test_sourced_dict_pickle_roundtrip_preserves_data_and_prov(self):
        prov = {1: Provenance(Source.MANUAL, datetime.now())}
        sd = SourcedDict({1: 10.0}, prov)

        rt = pickle.loads(pickle.dumps(sd))

        assert type(rt) is SourcedDict
        assert dict(rt) == {1: 10.0}
        assert rt.prov == prov

    def test_sourced_dict_copies_still_raise_on_mutation(self):
        prov = {1: Provenance(Source.MANUAL, datetime.now())}
        sd = SourcedDict({1: 10.0}, prov)

        with pytest.raises(TypeError):
            copy.deepcopy(sd)[2] = 20.0
