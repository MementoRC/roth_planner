"""Tests for models/sourced.py — provenance-carrying value/dict/list wrappers.

Pure model tests: no streamlit, no engine imports, no pytest fixtures requiring
external services. All datetimes are fixed literals (never datetime.now()).
"""

from __future__ import annotations

import copy
import dataclasses
import pickle
from datetime import datetime
from pathlib import Path

import pytest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap, TrustChoice
from engine.data_sources.committed import (
    COMMITTED_FIELD_ATTRS,
    apply_committed,
    extract_committed,
    load_committed,
    migrate_committed,
    save_committed,
)
from engine.data_sources.confirm import confirm_field
from engine.data_sources.ingest import is_valid_field_key
from engine.data_sources.ingest import record_candidate as ingest_record_candidate
from engine.data_sources.orchestrator import reconcile_manual_edits, resolve_for_app
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.data_sources.record import (
    record_magi_candidates,
    record_ss_fra_candidate,
    record_txn_quote_candidate,
)
from engine.data_sources.resolver import (
    GRANTS_KEY,
    HOUSEHOLD_SCALAR_FIELDS,
    confirm,
    magi_field_key,
    resolve,
)
from engine.data_sources.snapshot_ingest import (
    _merge_snapshot_grants,
    apply_snapshot_overwrite,
    record_snapshot_candidates,
)
from engine.portfolio_sync import AccountSummary, EquityGrant, PortfolioSnapshot
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedDict, SourcedList, SourcedValue

FIXED_DT = datetime(2026, 7, 16, 12, 30, 45, 123456)
FIXED_DT_2 = datetime(2026, 7, 16, 13, 0, 0)
FIXED_DT_3 = datetime(2026, 7, 16, 14, 0, 0)


class TestSourcedModel:
    # -- Provenance -------------------------------------------------------

    def test_provenance_to_json_from_json_round_trip(self) -> None:
        prov = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="user entered")
        payload = prov.to_json()
        assert payload == {
            "source": "MANUAL",
            "recorded_at": FIXED_DT.isoformat(),
            "detail": "user entered",
        }
        restored = Provenance.from_json(payload)
        assert restored == prov
        assert restored.source == Source.MANUAL
        assert restored.recorded_at == FIXED_DT
        assert restored.detail == "user entered"

    def test_provenance_is_frozen(self) -> None:
        prov = Provenance(source=Source.PDF, recorded_at=FIXED_DT)
        assert dataclasses.fields(prov)  # sanity: it is a dataclass
        with pytest.raises(dataclasses.FrozenInstanceError):
            prov.source = Source.MANUAL  # type: ignore[misc]

    def test_provenance_default_detail_is_empty_string(self) -> None:
        prov = Provenance(source=Source.ESTIMATE, recorded_at=FIXED_DT)
        assert prov.detail == ""

    # -- SourcedValue -------------------------------------------------------

    def test_sourced_value_arithmetic_returns_plain_float(self) -> None:
        prov = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT)
        sv = SourcedValue(100.0, prov)

        product = sv * 1.07
        assert type(product) is float
        assert product == pytest.approx(107.0)

        added = sv + 5.0
        assert type(added) is float
        assert added == pytest.approx(105.0)

        subtracted = sv - 5.0
        assert type(subtracted) is float
        assert subtracted == pytest.approx(95.0)

        divided = sv / 4.0
        assert type(divided) is float
        assert divided == pytest.approx(25.0)

    def test_sourced_value_float_conversion_and_prov_access(self) -> None:
        prov = Provenance(source=Source.FINEXTRACT_LIVE, recorded_at=FIXED_DT, detail="synced")
        sv = SourcedValue(285000.0, prov)
        assert float(sv) == 285000.0
        assert sv.prov is prov
        assert sv.prov.source == Source.FINEXTRACT_LIVE
        assert sv.prov.detail == "synced"

    def test_sourced_value_round_trip_with_microseconds(self) -> None:
        prov = Provenance(source=Source.BUNDLE, recorded_at=FIXED_DT, detail="imported")
        sv = SourcedValue(42.5, prov)
        payload = sv.to_json()
        assert payload == {
            "value": 42.5,
            "source": "BUNDLE",
            "recorded_at": FIXED_DT.isoformat(),
            "detail": "imported",
        }
        restored = SourcedValue.from_json(payload)
        assert float(restored) == 42.5
        assert restored.prov.source == Source.BUNDLE
        assert restored.prov.detail == "imported"
        assert restored.prov.recorded_at == FIXED_DT
        assert restored.prov.recorded_at.microsecond == 123456

    # -- SourcedDict -------------------------------------------------------

    def test_sourced_dict_per_key_provenance_and_get(self) -> None:
        prov_2024 = Provenance(source=Source.PDF, recorded_at=FIXED_DT, detail="1040 2024")
        prov_2025 = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="estimate 2025")
        data = {2024: 285000.0, 2025: 290000.0}
        prov = {2024: prov_2024, 2025: prov_2025}
        sd = SourcedDict(data, prov)

        assert sd.get(2024) == 285000.0
        assert sd[2025] == 290000.0
        assert sd.prov[2024].source == Source.PDF
        assert sd.prov[2025].source == Source.MANUAL
        assert sd.prov[2024].detail == "1040 2024"

    def test_sourced_dict_round_trip_preserves_int_keys_and_prov(self) -> None:
        prov_2024 = Provenance(source=Source.PDF, recorded_at=FIXED_DT, detail="1040 2024")
        prov_2025 = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="estimate 2025")
        sd = SourcedDict(
            {2024: 285000.0, 2025: 290000.0},
            {2024: prov_2024, 2025: prov_2025},
        )
        payload = sd.to_json()
        restored = SourcedDict.from_json(payload, key_type=int)

        assert set(restored.keys()) == {2024, 2025}
        assert all(isinstance(k, int) for k in restored)
        assert restored[2024] == 285000.0
        assert restored[2025] == 290000.0
        assert restored.prov[2024].source == Source.PDF
        assert restored.prov[2025].source == Source.MANUAL
        assert restored.prov[2024].detail == "1040 2024"
        assert restored.prov[2025].recorded_at == FIXED_DT

    # -- SourcedList -------------------------------------------------------

    def test_sourced_list_per_element_provenance_aligned_by_index(self) -> None:
        prov_a = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="first")
        prov_b = Provenance(source=Source.ESTIMATE, recorded_at=FIXED_DT, detail="second")
        sl = SourcedList([100.0, 200.0], [prov_a, prov_b])

        assert sl[0] == 100.0
        assert sl[1] == 200.0
        assert sl.prov[0].source == Source.MANUAL
        assert sl.prov[1].source == Source.ESTIMATE
        assert sl.prov[0].detail == "first"
        assert sl.prov[1].detail == "second"

    def test_sourced_list_round_trip_preserves_alignment(self) -> None:
        prov_a = Provenance(source=Source.DEFAULT, recorded_at=FIXED_DT)
        prov_b = Provenance(source=Source.UNKNOWN, recorded_at=FIXED_DT, detail="fallback")
        sl = SourcedList([1.5, 2.5, 3.5], [prov_a, prov_b, prov_b])
        payload = sl.to_json()
        restored = SourcedList.from_json(payload)

        assert list(restored) == [1.5, 2.5, 3.5]
        assert len(restored.prov) == 3
        assert restored.prov[0].source == Source.DEFAULT
        assert restored.prov[1].source == Source.UNKNOWN
        assert restored.prov[1].detail == "fallback"
        assert restored.prov[2].source == Source.UNKNOWN


class TestSourcedDeepcopy:
    """Regression: __new__/__init__ require ``prov``, which the default
    reduction protocol omits, crashing copy.deepcopy/pickle on any Household
    carrying a committed sourced field (optimizer/scenario-compare deepcopy
    households)."""

    def test_sourced_value_deepcopy_preserves_value_and_prov(self) -> None:
        prov = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="entered")
        sv = SourcedValue(100.0, prov)

        copied = copy.deepcopy(sv)

        assert copied == 100.0
        assert isinstance(copied, SourcedValue)
        assert copied.prov.source == Source.MANUAL
        assert copied.prov.detail == "entered"
        assert copied.prov.recorded_at == FIXED_DT

    def test_sourced_dict_deepcopy_preserves_data_and_per_key_prov(self) -> None:
        prov_2024 = Provenance(source=Source.PDF, recorded_at=FIXED_DT, detail="1040 2024")
        prov_2025 = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT_2, detail="est 2025")
        sd = SourcedDict(
            {2024: 285000.0, 2025: 290000.0},
            {2024: prov_2024, 2025: prov_2025},
        )

        copied = copy.deepcopy(sd)

        assert isinstance(copied, SourcedDict)
        assert dict(copied) == {2024: 285000.0, 2025: 290000.0}
        assert copied.prov[2024].source == Source.PDF
        assert copied.prov[2025].source == Source.MANUAL
        assert copied.prov[2024].detail == "1040 2024"

    def test_sourced_list_deepcopy_preserves_data_and_per_element_prov(self) -> None:
        prov_a = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT, detail="first")
        prov_b = Provenance(source=Source.ESTIMATE, recorded_at=FIXED_DT_2, detail="second")
        sl = SourcedList([100.0, 200.0], [prov_a, prov_b])

        copied = copy.deepcopy(sl)

        assert isinstance(copied, SourcedList)
        assert list(copied) == [100.0, 200.0]
        assert copied.prov[0].source == Source.MANUAL
        assert copied.prov[1].source == Source.ESTIMATE
        assert copied.prov[1].detail == "second"

    def test_sourced_value_pickle_round_trip(self) -> None:
        prov = Provenance(source=Source.BUNDLE, recorded_at=FIXED_DT, detail="imported")
        sv = SourcedValue(42.5, prov)

        restored = pickle.loads(pickle.dumps(sv))

        assert restored == 42.5
        assert isinstance(restored, SourcedValue)
        assert restored.prov.source == Source.BUNDLE
        assert restored.prov.detail == "imported"
        assert restored.prov.recorded_at == FIXED_DT

    def test_sourced_dict_pickle_round_trip(self) -> None:
        prov_2024 = Provenance(source=Source.PDF, recorded_at=FIXED_DT, detail="1040 2024")
        sd = SourcedDict({2024: 285000.0}, {2024: prov_2024})

        restored = pickle.loads(pickle.dumps(sd))

        assert isinstance(restored, SourcedDict)
        assert dict(restored) == {2024: 285000.0}
        assert restored.prov[2024].source == Source.PDF
        assert restored.prov[2024].detail == "1040 2024"

    def test_sourced_list_pickle_round_trip(self) -> None:
        prov_a = Provenance(source=Source.DEFAULT, recorded_at=FIXED_DT)
        sl = SourcedList([1.5, 2.5], [prov_a, prov_a])

        restored = pickle.loads(pickle.dumps(sl))

        assert isinstance(restored, SourcedList)
        assert list(restored) == [1.5, 2.5]
        assert restored.prov[0].source == Source.DEFAULT

    def test_household_with_committed_sourced_value_deepcopies_clean(self) -> None:
        """Exact scenario that would crash engine/exercise_optimizer.py and
        engine/scenario_compare.py, both of which deepcopy Household."""
        hh = Household()
        hh.your_ira = SourcedValue(
            1_700_000.0,
            Provenance(Source.UNKNOWN, datetime(2026, 7, 16, 12, 0, 0), "pre-migration"),
        )

        hh2 = copy.deepcopy(hh)

        assert hh2.your_ira == 1_700_000.0
        assert isinstance(hh2.your_ira, SourcedValue)
        assert hh2.your_ira.prov.source == Source.UNKNOWN
        assert hh2.your_ira.prov.detail == "pre-migration"


class TestCandidateStore:
    def test_record_candidate_and_candidates_for_key(self) -> None:
        store = CandidateStore()
        prov = Provenance(source=Source.MANUAL, recorded_at=FIXED_DT)
        store.record_candidate("your_ira", 1_700_000.0, prov)

        candidates = store.candidates_for("your_ira")
        assert len(candidates) == 1
        assert candidates[0].value == 1_700_000.0
        assert candidates[0].prov.source == Source.MANUAL

    def test_record_candidate_keeps_latest_per_source(self) -> None:
        store = CandidateStore()
        prov1 = Provenance(source=Source.PDF, recorded_at=FIXED_DT, detail="first")
        prov2 = Provenance(source=Source.PDF, recorded_at=FIXED_DT_2, detail="second")
        store.record_candidate("your_ira", 100.0, prov1)
        store.record_candidate("your_ira", 200.0, prov2)

        candidates = store.candidates_for("your_ira")
        assert len(candidates) == 1
        assert candidates[0].value == 200.0
        assert candidates[0].prov.detail == "second"

    def test_record_candidate_keeps_one_per_distinct_source(self) -> None:
        store = CandidateStore()
        prov_pdf = Provenance(source=Source.PDF, recorded_at=FIXED_DT)
        prov_live = Provenance(source=Source.FINEXTRACT_LIVE, recorded_at=FIXED_DT)
        store.record_candidate("your_ira", 100.0, prov_pdf)
        store.record_candidate("your_ira", 200.0, prov_live)

        candidates = store.candidates_for("your_ira")
        assert len(candidates) == 2
        sources = {c.prov.source for c in candidates}
        assert sources == {Source.PDF, Source.FINEXTRACT_LIVE}

    def test_has_candidates_and_field_keys(self) -> None:
        store = CandidateStore()
        assert store.has_candidates("your_ira") is False
        assert store.field_keys() == []

        store.record_candidate("your_ira", 100.0, Provenance(Source.MANUAL, FIXED_DT))
        assert store.has_candidates("your_ira") is True
        assert store.field_keys() == ["your_ira"]
        assert store.has_candidates("spouse_ira") is False

    def test_to_json_from_json_round_trip(self) -> None:
        store = CandidateStore()
        store.record_candidate(
            "your_ira", 1_700_000.0, Provenance(Source.MANUAL, FIXED_DT, detail="entered")
        )
        store.record_candidate(
            "spouse_ira", 900_000.0, Provenance(Source.PDF, FIXED_DT_2, detail="1040")
        )

        payload = store.to_json()
        restored = CandidateStore.from_json(payload)

        assert restored.field_keys() == store.field_keys()
        your_ira_candidates = restored.candidates_for("your_ira")
        assert len(your_ira_candidates) == 1
        assert your_ira_candidates[0].value == 1_700_000.0
        assert your_ira_candidates[0].prov.source == Source.MANUAL
        assert your_ira_candidates[0].prov.detail == "entered"

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        store = CandidateStore()
        store.record_candidate("your_ira", 100.0, Provenance(Source.MANUAL, FIXED_DT))
        path = tmp_path / "candidates.json"
        store.save(path)

        restored = CandidateStore.load(path)
        assert restored.candidates_for("your_ira")[0].value == 100.0

    def test_load_missing_file_returns_empty_store_no_raise(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.json"
        store = CandidateStore.load(missing)
        assert store.field_keys() == []

    def test_load_corrupt_file_returns_empty_store_no_raise(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not valid json!!")
        store = CandidateStore.load(corrupt)
        assert store.field_keys() == []


class TestChoiceMap:
    def test_set_choice_and_get(self) -> None:
        cm = ChoiceMap()
        cm.set_choice("your_ira", Source.FINEXTRACT_LIVE, FIXED_DT)

        choice = cm.get("your_ira")
        assert choice is not None
        assert choice.source == Source.FINEXTRACT_LIVE
        assert choice.locked_at == FIXED_DT

    def test_get_missing_returns_none(self) -> None:
        cm = ChoiceMap()
        assert cm.get("your_ira") is None

    def test_clear_removes_choice(self) -> None:
        cm = ChoiceMap()
        cm.set_choice("your_ira", Source.MANUAL, FIXED_DT)
        cm.clear("your_ira")
        assert cm.get("your_ira") is None

    def test_clear_missing_key_is_noop(self) -> None:
        cm = ChoiceMap()
        cm.clear("your_ira")  # must not raise
        assert cm.get("your_ira") is None

    def test_trust_choice_json_round_trip(self) -> None:
        choice = TrustChoice(source=Source.PDF, locked_at=FIXED_DT)
        payload = choice.to_json()
        restored = TrustChoice.from_json(payload)
        assert restored == choice

    def test_to_json_from_json_round_trip(self) -> None:
        cm = ChoiceMap()
        cm.set_choice("your_ira", Source.MANUAL, FIXED_DT)
        cm.set_choice("spouse_ira", Source.PDF, FIXED_DT_2)

        payload = cm.to_json()
        restored = ChoiceMap.from_json(payload)

        assert restored.get("your_ira") == TrustChoice(Source.MANUAL, FIXED_DT)
        assert restored.get("spouse_ira") == TrustChoice(Source.PDF, FIXED_DT_2)

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        cm = ChoiceMap()
        cm.set_choice("your_ira", Source.FINEXTRACT_LIVE, FIXED_DT)
        path = tmp_path / "choices.json"
        cm.save(path)

        restored = ChoiceMap.load(path)
        assert restored.get("your_ira") == TrustChoice(Source.FINEXTRACT_LIVE, FIXED_DT)

    def test_load_missing_file_returns_empty_map_no_raise(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.json"
        cm = ChoiceMap.load(missing)
        assert cm.get("your_ira") is None

    def test_load_corrupt_file_returns_empty_map_no_raise(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("not json at all {{{")
        cm = ChoiceMap.load(corrupt)
        assert cm.get("your_ira") is None


class TestResolver:
    def test_household_scalar_fields_constant_matches_expected(self) -> None:
        assert set(HOUSEHOLD_SCALAR_FIELDS) == {
            "your_ira",
            "spouse_ira",
            "your_roth",
            "spouse_roth",
            "txn_price_now",
            "your_ss_fra",
            "spouse_ss_fra",
        }
        for field_key in HOUSEHOLD_SCALAR_FIELDS:
            assert hasattr(Household(), field_key)

    def test_freeze_invariant_committed_value_survives_differing_candidate(self) -> None:
        """Regression: loading a fresh candidate must never clobber a committed value."""
        committed = Household()
        committed.your_ira = SourcedValue(
            1_700_000.0, Provenance(Source.UNKNOWN, FIXED_DT, detail="baseline")
        )
        store = CandidateStore()
        store.record_candidate(
            "your_ira", 1_750_000.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2, detail="synced")
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert result.household.your_ira == 1_700_000.0
        assert "your_ira" in result.pending_review

    def test_committed_value_with_no_differing_candidate_is_not_pending(self) -> None:
        committed = Household()
        committed.your_ira = SourcedValue(1_700_000.0, Provenance(Source.MANUAL, FIXED_DT))
        store = CandidateStore()
        store.record_candidate(
            "your_ira", 1_700_000.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2)
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert result.household.your_ira == 1_700_000.0
        assert "your_ira" not in result.pending_review

    def test_committed_with_choice_not_repending_from_non_trusted_source(self) -> None:
        """Regression: confirming txn_price_now=284.02 trusting MARKET_QUOTE must
        not re-pend when every FinExtract sync keeps recording a stale 192.84
        candidate — only the trusted source's own candidates can re-open review."""
        committed = Household()
        committed.txn_price_now = SourcedValue(
            284.02, Provenance(Source.MARKET_QUOTE, FIXED_DT, detail="confirmed")
        )
        store = CandidateStore()
        store.record_candidate(
            "txn_price_now", 192.84, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2, "sync")
        )
        choices = ChoiceMap()
        choices.set_choice("txn_price_now", Source.MARKET_QUOTE, FIXED_DT)

        result = resolve(committed, store, choices)

        assert result.household.txn_price_now == 284.02
        assert "txn_price_now" not in result.pending_review
        candidate_sources = {c.prov.source for c in store.candidates_for("txn_price_now")}
        assert Source.FINEXTRACT_LIVE in candidate_sources

    def test_committed_with_choice_repends_when_trusted_source_changes(self) -> None:
        committed = Household()
        committed.txn_price_now = SourcedValue(
            284.02, Provenance(Source.MARKET_QUOTE, FIXED_DT, detail="confirmed")
        )
        store = CandidateStore()
        store.record_candidate(
            "txn_price_now", 290.0, Provenance(Source.MARKET_QUOTE, FIXED_DT_2, "new quote")
        )
        choices = ChoiceMap()
        choices.set_choice("txn_price_now", Source.MARKET_QUOTE, FIXED_DT)

        result = resolve(committed, store, choices)

        assert result.household.txn_price_now == 284.02
        assert "txn_price_now" in result.pending_review

    def test_committed_no_choice_still_pends_on_any_differing_candidate(self) -> None:
        """Unchanged behavior: with no trust choice yet, any differing
        candidate (from any source) still nudges a committed field pending."""
        committed = Household()
        committed.txn_price_now = SourcedValue(
            284.02, Provenance(Source.MARKET_QUOTE, FIXED_DT, detail="confirmed")
        )
        store = CandidateStore()
        store.record_candidate(
            "txn_price_now", 192.84, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2, "sync")
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert result.household.txn_price_now == 284.02
        assert "txn_price_now" in result.pending_review

    def test_choice_wins_over_ladder(self) -> None:
        committed = Household()  # your_ira not yet committed (plain float default)
        store = CandidateStore()
        store.record_candidate("your_ira", 500_000.0, Provenance(Source.MANUAL, FIXED_DT))
        store.record_candidate(
            "your_ira", 600_000.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2)
        )
        choices = ChoiceMap()
        choices.set_choice("your_ira", Source.FINEXTRACT_LIVE, FIXED_DT_3)

        result = resolve(committed, store, choices)

        assert result.household.your_ira == 600_000.0
        assert isinstance(result.household.your_ira, SourcedValue)
        assert result.household.your_ira.prov.source == Source.FINEXTRACT_LIVE
        assert "your_ira" in result.pending_review

    def test_ladder_fallback_live_beats_default(self) -> None:
        committed = Household()
        store = CandidateStore()
        store.record_candidate("your_ira", 400_000.0, Provenance(Source.DEFAULT, FIXED_DT))
        store.record_candidate(
            "your_ira", 450_000.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2)
        )
        choices = ChoiceMap()  # no explicit choice -> ladder decides

        result = resolve(committed, store, choices)

        assert result.household.your_ira == 450_000.0
        assert result.household.your_ira.prov.source == Source.FINEXTRACT_LIVE
        assert "your_ira" in result.pending_review

    def test_first_run_empty_leaves_plain_default_not_pending(self) -> None:
        committed = Household()
        store = CandidateStore()
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert result.household.your_ira == committed.your_ira
        assert not isinstance(result.household.your_ira, SourcedValue)
        assert "your_ira" not in result.pending_review

    def test_resolve_does_not_mutate_committed(self) -> None:
        committed = Household()
        original_value = committed.your_ira
        store = CandidateStore()
        store.record_candidate(
            "your_ira", 999_999.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)
        )
        choices = ChoiceMap()

        resolve(committed, store, choices)

        assert committed.your_ira == original_value
        assert not isinstance(committed.your_ira, SourcedValue)

    def test_magi_flip_committed_year_survives_differing_pdf_candidate(self) -> None:
        committed = Household()
        committed.prior_year_magi = SourcedDict(
            {2024: 285_000.0},
            {2024: Provenance(Source.FINEXTRACT_LIVE, FIXED_DT, detail="live")},
        )
        store = CandidateStore()
        store.record_candidate(
            magi_field_key(2024), 288_000.0, Provenance(Source.PDF, FIXED_DT_2, detail="1040 pdf")
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert result.household.prior_year_magi[2024] == 285_000.0
        assert magi_field_key(2024) in result.pending_review

    def test_magi_uncommitted_year_resolves_via_ladder_and_is_pending(self) -> None:
        committed = Household()  # prior_year_magi empty
        store = CandidateStore()
        store.record_candidate(
            magi_field_key(2025), 290_000.0, Provenance(Source.ESTIMATE, FIXED_DT)
        )

        result = resolve(committed, store, ChoiceMap())

        assert result.household.prior_year_magi[2025] == 290_000.0
        assert magi_field_key(2025) in result.pending_review

    def test_confirm_locks_in_pdf_value_for_magi_year(self) -> None:
        committed = Household()
        store = CandidateStore()
        store.record_candidate(
            magi_field_key(2024), 288_000.0, Provenance(Source.PDF, FIXED_DT, detail="1040 pdf")
        )
        choices = ChoiceMap()

        updated = confirm(
            magi_field_key(2024), Source.PDF, committed, store, choices, now=FIXED_DT_2
        )

        assert updated.prior_year_magi[2024] == 288_000.0
        assert updated.prior_year_magi.prov[2024].source == Source.PDF
        assert choices.get(magi_field_key(2024)) == TrustChoice(Source.PDF, FIXED_DT_2)

        # Re-resolving now shows the field committed & NOT pending, even
        # though the same PDF candidate is still sitting in the store.
        result = resolve(updated, store, choices)
        assert result.household.prior_year_magi[2024] == 288_000.0
        assert magi_field_key(2024) not in result.pending_review

    def test_confirm_with_override_value_uses_manual_provenance(self) -> None:
        committed = Household()
        store = CandidateStore()
        choices = ChoiceMap()

        updated = confirm(
            "your_ira",
            Source.MANUAL,
            committed,
            store,
            choices,
            now=FIXED_DT,
            override_value=1_234_567.0,
        )

        assert updated.your_ira == 1_234_567.0
        assert isinstance(updated.your_ira, SourcedValue)
        assert updated.your_ira.prov.source == Source.MANUAL
        assert updated.your_ira.prov.detail == "manual entry"

    def test_confirm_does_not_mutate_committed(self) -> None:
        committed = Household()
        store = CandidateStore()
        store.record_candidate("your_ira", 111.0, Provenance(Source.MANUAL, FIXED_DT))
        choices = ChoiceMap()

        confirm("your_ira", Source.MANUAL, committed, store, choices, now=FIXED_DT_2)

        assert not isinstance(committed.your_ira, SourcedValue)


class TestIngest:
    def test_is_valid_field_key_scalar(self) -> None:
        assert is_valid_field_key("your_ira") is True

    def test_is_valid_field_key_grants(self) -> None:
        assert is_valid_field_key(GRANTS_KEY) is True

    def test_is_valid_field_key_magi(self) -> None:
        assert is_valid_field_key(magi_field_key(2024)) is True

    def test_is_valid_field_key_bogus_is_false(self) -> None:
        assert is_valid_field_key("bogus") is False

    def test_record_candidate_valid_stores_and_returns_true(self) -> None:
        store = CandidateStore()

        ok = ingest_record_candidate(
            store, "your_ira", 1_700_000.0, Source.MANUAL, "entered", FIXED_DT
        )

        assert ok is True
        candidates = store.candidates_for("your_ira")
        assert len(candidates) == 1
        assert candidates[0].value == 1_700_000.0
        assert candidates[0].prov.source == Source.MANUAL
        assert candidates[0].prov.detail == "entered"
        assert candidates[0].prov.recorded_at == FIXED_DT

    def test_record_candidate_invalid_field_key_returns_false_no_raise(self) -> None:
        store = CandidateStore()

        ok = ingest_record_candidate(store, "bogus", 1.0, Source.MANUAL, "x", FIXED_DT)

        assert ok is False
        assert store.field_keys() == []


class TestCommitted:
    def test_extract_apply_round_trip_scalars(self) -> None:
        hh = Household()
        hh.your_ira = SourcedValue(1_700_000.0, Provenance(Source.MANUAL, FIXED_DT, "entered"))
        hh.txn_price_now = SourcedValue(210.5, Provenance(Source.PDF, FIXED_DT_2, "1099"))

        committed_json = extract_committed(hh)
        assert set(committed_json.keys()) == {"your_ira", "txn_price_now"}

        hh2 = Household()
        apply_committed(hh2, committed_json)

        assert hh2.your_ira == 1_700_000.0
        assert isinstance(hh2.your_ira, SourcedValue)
        assert hh2.your_ira.prov.source == Source.MANUAL
        assert hh2.txn_price_now == 210.5
        assert hh2.txn_price_now.prov.detail == "1099"

    def test_extract_apply_round_trip_magi(self) -> None:
        hh = Household()
        hh.prior_year_magi = SourcedDict(
            {2024: 285_000.0}, {2024: Provenance(Source.PDF, FIXED_DT, "1040")}
        )

        committed_json = extract_committed(hh)
        hh2 = Household()
        apply_committed(hh2, committed_json)

        assert hh2.prior_year_magi[2024] == 285_000.0
        assert hh2.prior_year_magi.prov[2024].source == Source.PDF

    def test_extract_apply_round_trip_grants(self) -> None:
        hh = Household()
        grants = [StockGrant(year=2019, strike=104.0, shares=650, expiry_year=2029, grant_id="G1")]
        hh.grants = SourcedList(grants, [Provenance(Source.FINEXTRACT_LIVE, FIXED_DT, "sync")])

        committed_json = extract_committed(hh)
        hh2 = Household()
        apply_committed(hh2, committed_json)

        assert len(hh2.grants) == 1
        assert hh2.grants[0] == grants[0]
        assert hh2.grants.prov[0].source == Source.FINEXTRACT_LIVE

    def test_uncommitted_attrs_are_skipped_by_extract(self) -> None:
        hh = Household()
        assert extract_committed(hh) == {}

    def test_apply_committed_missing_keys_left_as_is(self) -> None:
        hh = Household()
        original_ira = hh.your_ira

        apply_committed(hh, {})

        assert hh.your_ira == original_ira
        assert not isinstance(hh.your_ira, SourcedValue)

    def test_migrate_committed_wraps_plain_household_identical_values(self) -> None:
        hh = Household()
        original_your_ira = hh.your_ira
        original_spouse_roth = hh.spouse_roth
        original_magi = dict(hh.prior_year_magi)
        original_grants = list(hh.grants)

        committed_json = migrate_committed(hh, FIXED_DT)

        assert set(committed_json.keys()) == set(COMMITTED_FIELD_ATTRS)
        for attr in HOUSEHOLD_SCALAR_FIELDS:
            value = getattr(hh, attr)
            assert isinstance(value, SourcedValue)
            assert value.prov.source == Source.UNKNOWN
            assert value.prov.detail == "pre-migration"
        assert hh.your_ira == original_your_ira
        assert hh.spouse_roth == original_spouse_roth
        assert isinstance(hh.prior_year_magi, SourcedDict)
        assert dict(hh.prior_year_magi) == original_magi
        assert isinstance(hh.grants, SourcedList)
        assert list(hh.grants) == original_grants

    def test_grants_round_trip_via_save_load(self, tmp_path: Path) -> None:
        hh = Household()
        grants = [
            StockGrant(year=2019, strike=104.0, shares=650, expiry_year=2029, grant_id="G1"),
            StockGrant(year=2020, strike=130.0, shares=400, expiry_year=2030, grant_id="G2"),
        ]
        hh.grants = SourcedList(
            grants,
            [
                Provenance(Source.FINEXTRACT_LIVE, FIXED_DT, "sync"),
                Provenance(Source.FINEXTRACT_LIVE, FIXED_DT, "sync"),
            ],
        )
        committed_json = extract_committed(hh)
        path = tmp_path / "committed.json"
        save_committed(path, committed_json)

        loaded = load_committed(path)
        assert loaded is not None
        hh2 = Household()
        apply_committed(hh2, loaded)

        assert list(hh2.grants) == grants
        assert hh2.grants.prov[0].source == Source.FINEXTRACT_LIVE
        assert hh2.grants.prov[1].detail == "sync"

    def test_load_committed_missing_file_returns_none_no_raise(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.json"
        assert load_committed(missing) is None

    def test_load_committed_corrupt_file_returns_none_no_raise(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "corrupt.json"
        corrupt.write_text("{not valid json")
        assert load_committed(corrupt) is None


class TestSnapshotIngest:
    def _make_snap(self) -> PortfolioSnapshot:
        accounts = [
            AccountSummary(account_type="trad_ira", owner="you", total_value=500_000.0),
            AccountSummary(account_type="trad_ira", owner="spouse", total_value=300_000.0),
            AccountSummary(account_type="roth_ira", owner="you", total_value=100_000.0),
        ]
        equity_grants = [
            EquityGrant(
                grant_id="G1",
                grant_type="NQO",
                grant_date="2019-01-01",
                shares_granted=1000,
                outstanding=1000,
                current_value=50_000.0,
            ),
            EquityGrant(
                grant_id="G2",
                grant_type="NQO",
                grant_date="2020-01-01",
                shares_granted=500,
                outstanding=0,  # fully exercised -> dropped silently
                current_value=0.0,
            ),
            EquityGrant(
                grant_id="G3",
                grant_type="NQO",
                grant_date="2021-06-01",
                shares_granted=200,
                outstanding=200,  # no configured strike -> dropped + reported
                current_value=30_000.0,
            ),
        ]
        return PortfolioSnapshot(
            accounts=accounts,
            equity_grants=equity_grants,
            txn_shares_held=1000,
            txn_shares_value=150_000.0,
            server_available=True,
        )

    def test_merge_snapshot_grants_drops_exhausted_and_missing_strike(self) -> None:
        snap = self._make_snap()
        strikes = {"2019": 104.0}

        merged, dropped = _merge_snapshot_grants(snap, strikes)

        assert merged == [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="G1")
        ]
        assert dropped == [(2021, 200)]

    def test_apply_snapshot_overwrite_matches_derivations(self) -> None:
        snap = self._make_snap()
        strikes = {"2019": 104.0}
        hh = Household()

        apply_snapshot_overwrite(hh, snap, strikes)

        assert hh.your_ira == 500_000.0
        assert not isinstance(hh.your_ira, SourcedValue)
        assert hh.spouse_ira == 300_000.0
        assert hh.your_roth == 100_000.0
        assert hh.txn_price_now == 150.0
        assert hh.grants == [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="G1")
        ]

    def test_record_snapshot_candidates_records_finextract_live(self) -> None:
        snap = self._make_snap()
        strikes = {"2019": 104.0}
        store = CandidateStore()

        dropped = record_snapshot_candidates(store, snap, strikes, FIXED_DT)

        assert dropped == [(2021, 200)]

        your_ira_candidates = store.candidates_for("your_ira")
        assert len(your_ira_candidates) == 1
        assert your_ira_candidates[0].value == 500_000.0
        assert your_ira_candidates[0].prov.source == Source.FINEXTRACT_LIVE
        assert your_ira_candidates[0].prov.detail == "FinExtract live"

        assert store.candidates_for("spouse_ira")[0].value == 300_000.0
        assert store.candidates_for("your_roth")[0].value == 100_000.0
        assert store.candidates_for("txn_price_now")[0].value == 150.0

        grants_candidates = store.candidates_for("grants")
        assert len(grants_candidates) == 1
        assert grants_candidates[0].value == [
            StockGrant(year=2019, strike=104.0, shares=1000, expiry_year=2029, grant_id="G1")
        ]
        assert grants_candidates[0].prov.source == Source.FINEXTRACT_LIVE


class TestOrchestrator:
    def test_migration_identity_no_snap_no_candidates(self) -> None:
        session_hh = Household()
        session_hh.your_ira = 1_700_000.0
        store = CandidateStore()
        choices = ChoiceMap()

        outcome = resolve_for_app(session_hh, None, {}, store, choices, None, FIXED_DT)

        assert outcome.migrated is True
        assert outcome.result.household.your_ira == 1_700_000.0
        assert isinstance(outcome.result.household.your_ira, SourcedValue)
        assert outcome.result.household.your_ira.prov.source == Source.UNKNOWN
        assert outcome.result.pending_review == set()
        assert outcome.dropped_missing_strike == []

    def test_freeze_committed_baseline_survives_newer_finextract_candidate(self) -> None:
        """Clobber-bug regression at the orchestrator level: a fresh
        FinExtract sync (recorded as a candidate, never applied directly)
        must never silently overwrite an already-committed value."""
        session_hh1 = Household()
        session_hh1.your_ira = 1_700_000.0
        first = resolve_for_app(
            session_hh1, None, {}, CandidateStore(), ChoiceMap(), None, FIXED_DT
        )
        committed_json = first.committed_json

        session_hh2 = Household()
        session_hh2.your_ira = 1_700_000.0
        store2 = CandidateStore()
        store2.record_candidate(
            "your_ira", 2_000_000.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2, "sync")
        )

        second = resolve_for_app(
            session_hh2, None, {}, store2, ChoiceMap(), committed_json, FIXED_DT_2
        )

        assert second.migrated is False
        assert second.result.household.your_ira == 1_700_000.0
        assert "your_ira" in second.result.pending_review


class TestReconcileManualEdits:
    def test_session_edit_promotes_committed_field_to_manual(self) -> None:
        session_hh = Household()
        session_hh.your_ira = 2_000_000.0
        committed_json = {
            "your_ira": SourcedValue(
                1_700_000.0, Provenance(Source.UNKNOWN, FIXED_DT)
            ).to_json()
        }

        result_json, changed = reconcile_manual_edits(session_hh, committed_json, FIXED_DT_2)

        assert changed is True
        assert result_json["your_ira"]["value"] == 2_000_000.0
        assert result_json["your_ira"]["source"] == "MANUAL"

        committed_hh = Household()
        apply_committed(committed_hh, result_json)
        resolved = resolve(committed_hh, CandidateStore(), ChoiceMap())

        assert resolved.household.your_ira == 2_000_000.0
        assert "your_ira" not in resolved.pending_review

    def test_session_matching_committed_leaves_provenance_untouched(self) -> None:
        session_hh = Household()
        session_hh.your_ira = 1_700_000.0
        original_payload = SourcedValue(
            1_700_000.0, Provenance(Source.UNKNOWN, FIXED_DT)
        ).to_json()
        committed_json = {"your_ira": dict(original_payload)}

        result_json, changed = reconcile_manual_edits(session_hh, committed_json, FIXED_DT_2)

        assert changed is False
        assert result_json["your_ira"] == original_payload

    def test_magi_year_edit_promotes_that_year_to_manual(self) -> None:
        session_hh = Household()
        session_hh.prior_year_magi = {2024: 290_000.0}
        committed_json = {
            "prior_year_magi": SourcedDict(
                {2024: 285_000.0}, {2024: Provenance(Source.UNKNOWN, FIXED_DT)}
            ).to_json()
        }

        result_json, changed = reconcile_manual_edits(session_hh, committed_json, FIXED_DT_2)

        assert changed is True
        assert result_json["prior_year_magi"]["data"]["2024"] == 290_000.0
        assert result_json["prior_year_magi"]["prov"]["2024"]["source"] == "MANUAL"

    def test_int_widget_rounding_drift_does_not_promote_fractional_committed_value(
        self,
    ) -> None:
        # Setup balance widgets are integer (format=%d): session_hh holds the
        # rounded int mirror while a FinExtract-derived committed value keeps
        # fractional cents. Rounding drift alone must not relabel MANUAL.
        session_hh = Household()
        session_hh.your_ira = 1_700_001  # int, rounded widget/writeback value
        committed_json = {
            "your_ira": SourcedValue(
                1_700_000.53, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)
            ).to_json()
        }

        result_json, changed = reconcile_manual_edits(session_hh, committed_json, FIXED_DT_2)

        assert changed is False
        assert result_json["your_ira"]["source"] == "FINEXTRACT_LIVE"
        assert result_json["your_ira"]["value"] == 1_700_000.53

    def test_magi_int_widget_rounding_drift_does_not_promote_fractional_committed_value(
        self,
    ) -> None:
        session_hh = Household()
        session_hh.prior_year_magi = {2024: 285_000}  # int rounding of 285_000.40
        committed_json = {
            "prior_year_magi": SourcedDict(
                {2024: 285_000.40}, {2024: Provenance(Source.PDF, FIXED_DT)}
            ).to_json()
        }

        result_json, changed = reconcile_manual_edits(session_hh, committed_json, FIXED_DT_2)

        assert changed is False
        assert result_json["prior_year_magi"]["data"]["2024"] == 285_000.40
        assert result_json["prior_year_magi"]["prov"]["2024"]["source"] == "PDF"

    def test_genuine_edit_still_detected_despite_rounding_tolerance(self) -> None:
        session_hh = Household()
        session_hh.your_ira = 1_800_000  # differs from committed by far more than $1
        committed_json = {
            "your_ira": SourcedValue(
                1_700_000.53, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)
            ).to_json()
        }

        result_json, changed = reconcile_manual_edits(session_hh, committed_json, FIXED_DT_2)

        assert changed is True
        assert result_json["your_ira"]["source"] == "MANUAL"
        assert result_json["your_ira"]["value"] == 1_800_000.0


class TestPaths:
    def test_cache_path_constants_match_app_py_expected_locations(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        assert repo_root / ".candidate_store.json" == CANDIDATE_STORE_PATH
        assert repo_root / ".trust_choices.json" == TRUST_CHOICES_PATH
        assert repo_root / ".committed_household.json" == COMMITTED_PATH


class TestConfirmField:
    def test_scalar_confirm_updates_committed_and_choice(self) -> None:
        committed_json: dict = {}
        choices = ChoiceMap()

        result = confirm_field(
            committed_json,
            choices,
            "your_ira",
            2_000_000.0,
            Source.FINEXTRACT_LIVE,
            FIXED_DT,
            detail="live sync",
        )

        assert result is committed_json
        assert committed_json["your_ira"]["value"] == 2_000_000.0
        assert committed_json["your_ira"]["source"] == "FINEXTRACT_LIVE"
        assert committed_json["your_ira"]["detail"] == "live sync"
        assert choices.get("your_ira") == TrustChoice(Source.FINEXTRACT_LIVE, FIXED_DT)

        hh = Household()
        apply_committed(hh, committed_json)
        assert hh.your_ira == 2_000_000.0
        assert isinstance(hh.your_ira, SourcedValue)

    def test_magi_year_confirm_updates_only_that_year(self) -> None:
        committed_json = {
            "prior_year_magi": SourcedDict(
                {2023: 270_000.0}, {2023: Provenance(Source.UNKNOWN, FIXED_DT)}
            ).to_json()
        }
        choices = ChoiceMap()

        confirm_field(
            committed_json,
            choices,
            magi_field_key(2024),
            288_000.0,
            Source.PDF,
            FIXED_DT_2,
            detail="1040 pdf",
        )

        assert committed_json["prior_year_magi"]["data"]["2023"] == 270_000.0
        assert committed_json["prior_year_magi"]["data"]["2024"] == 288_000.0
        assert committed_json["prior_year_magi"]["prov"]["2024"]["source"] == "PDF"
        assert choices.get(magi_field_key(2024)) == TrustChoice(Source.PDF, FIXED_DT_2)

        hh = Household()
        apply_committed(hh, committed_json)
        assert hh.prior_year_magi[2023] == 270_000.0
        assert hh.prior_year_magi[2024] == 288_000.0

    def test_grants_confirm_serializes_grant_list(self) -> None:
        committed_json: dict = {}
        choices = ChoiceMap()
        grants = [StockGrant(year=2019, strike=104.0, shares=650, expiry_year=2029, grant_id="G1")]

        confirm_field(
            committed_json, choices, GRANTS_KEY, grants, Source.FINEXTRACT_LIVE, FIXED_DT
        )

        hh = Household()
        apply_committed(hh, committed_json)
        assert list(hh.grants) == grants
        assert hh.grants.prov[0].source == Source.FINEXTRACT_LIVE

    def test_confirm_field_then_resolve_shows_committed_not_pending(self) -> None:
        committed_json: dict = {}
        choices = ChoiceMap()
        store = CandidateStore()
        store.record_candidate(
            "your_ira", 2_000_000.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT)
        )

        confirm_field(
            committed_json, choices, "your_ira", 2_000_000.0, Source.FINEXTRACT_LIVE, FIXED_DT_2
        )

        hh = Household()
        apply_committed(hh, committed_json)
        result = resolve(hh, store, choices)

        assert result.household.your_ira == 2_000_000.0
        assert "your_ira" not in result.pending_review

    def test_unknown_field_key_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown or unsupported field"):
            confirm_field({}, ChoiceMap(), "not_a_real_field", 1.0, Source.MANUAL, FIXED_DT)


class TestRecordMagiCandidates:
    """engine.data_sources.record.record_magi_candidates — Wave 5.

    Reusable helper each external MAGI producer (FinExtract sync, PDF 1040
    scan, Data Bridge bundle import) calls instead of gap-filling / full-
    replacing st.session_state["prior_year_magi"] directly (audit defect #2).
    """

    def test_records_one_candidate_per_year(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        count = record_magi_candidates(
            {2023: 270_000.0, 2024: 288_000.0},
            Source.PDF,
            "Form 1040 PDF",
            FIXED_DT,
            store_path=store_path,
        )

        assert count == 2
        store = CandidateStore.load(store_path)
        c2023 = store.candidates_for(magi_field_key(2023))
        c2024 = store.candidates_for(magi_field_key(2024))
        assert len(c2023) == 1
        assert c2023[0].value == 270_000.0
        assert c2023[0].prov.source == Source.PDF
        assert c2023[0].prov.detail == "Form 1040 PDF"
        assert c2023[0].prov.recorded_at == FIXED_DT
        assert len(c2024) == 1
        assert c2024[0].value == 288_000.0

    def test_skips_falsy_and_none_values(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        count = record_magi_candidates(
            {2022: 0.0, 2023: None, 2024: 150_000.0},
            Source.FINEXTRACT_LIVE,
            "FinExtract tax return",
            FIXED_DT,
            store_path=store_path,
        )

        assert count == 1
        store = CandidateStore.load(store_path)
        assert store.candidates_for(magi_field_key(2022)) == []
        assert store.candidates_for(magi_field_key(2023)) == []
        assert len(store.candidates_for(magi_field_key(2024))) == 1

    def test_round_trips_through_store_file(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"
        record_magi_candidates(
            {2025: 300_000.0}, Source.BUNDLE, "Data bridge import", FIXED_DT, store_path=store_path
        )

        # Fresh load (simulating a later app run) sees the persisted candidate.
        reloaded = CandidateStore.load(store_path)
        candidates = reloaded.candidates_for(magi_field_key(2025))
        assert len(candidates) == 1
        assert candidates[0].value == 300_000.0
        assert candidates[0].prov.source == Source.BUNDLE

    def test_coerces_string_year_and_int_value(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        count = record_magi_candidates(
            {"2024": 275_000}, Source.PDF, "Form 1040 PDF", FIXED_DT, store_path=store_path
        )

        assert count == 1
        store = CandidateStore.load(store_path)
        candidates = store.candidates_for(magi_field_key(2024))
        assert candidates[0].value == 275_000.0
        assert isinstance(candidates[0].value, float)

    def test_store_path_param_is_isolated_from_real_candidate_store(self, tmp_path: Path) -> None:
        """Explicit store_path writes to the tmp file, never the real CANDIDATE_STORE_PATH."""
        store_path = tmp_path / "isolated_candidate_store.json"
        assert store_path != CANDIDATE_STORE_PATH

        record_magi_candidates(
            {2024: 200_000.0}, Source.PDF, "Form 1040 PDF", FIXED_DT, store_path=store_path
        )

        assert store_path.exists()


class TestSsFraCharacterization:
    """C0: pin current SS-FRA-consuming numbers before Part C sourced-field
    wiring. SourcedValue is a transparent float subclass, so wrapping
    your_ss_fra/spouse_ss_fra must never move these numbers."""

    def test_your_ss_at_70_golden(self) -> None:
        hh = Household(your_ss_fra=2_500.0, your_ss_start_age=70, your_fra_age=67)
        assert hh.your_ss_at_70() == pytest.approx(37_200.0)

    def test_spouse_ss_at_70_golden(self) -> None:
        hh = Household(spouse_ss_fra=1_800.0, spouse_ss_start_age=62, spouse_fra_age=67)
        assert hh.spouse_ss_at_70() == pytest.approx(15_120.0)

    def test_migration_baseline_preserves_ss_fra_value(self) -> None:
        """First-load migration: a plain session your_ss_fra/spouse_ss_fra
        survives byte-identical as the committed baseline."""
        session_hh = Household()
        session_hh.your_ss_fra = 2_500.0
        session_hh.spouse_ss_fra = 1_800.0

        outcome = resolve_for_app(session_hh, None, {}, CandidateStore(), ChoiceMap(), None, FIXED_DT)

        assert outcome.migrated is True
        assert outcome.result.household.your_ss_fra == 2_500.0
        assert outcome.result.household.spouse_ss_fra == 1_800.0
        assert isinstance(outcome.result.household.your_ss_fra, SourcedValue)
        assert outcome.result.household.your_ss_fra.prov.source == Source.UNKNOWN
        assert outcome.result.household.your_ss_at_70() == pytest.approx(37_200.0)


class TestSsFraSourcedField:
    """C1: your_ss_fra/spouse_ss_fra are in the closed sourced-field set —
    record_candidate -> resolver resolves it; round-trips through
    store->load JSON with .prov preserved."""

    def test_ss_fra_fields_in_household_scalar_fields(self) -> None:
        assert "your_ss_fra" in HOUSEHOLD_SCALAR_FIELDS
        assert "spouse_ss_fra" in HOUSEHOLD_SCALAR_FIELDS
        for field_key in ("your_ss_fra", "spouse_ss_fra"):
            assert hasattr(Household(), field_key)

    def test_record_candidate_and_resolve(self) -> None:
        committed = Household()  # your_ss_fra not yet committed (plain float default)
        store = CandidateStore()
        ingest_record_candidate(
            store, "your_ss_fra", 2_600.0, Source.FINEXTRACT_LIVE, "SSA statement", FIXED_DT
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert result.household.your_ss_fra == 2_600.0
        assert isinstance(result.household.your_ss_fra, SourcedValue)
        assert result.household.your_ss_fra.prov.source == Source.FINEXTRACT_LIVE
        assert "your_ss_fra" in result.pending_review

    def test_freeze_invariant_committed_value_survives_differing_candidate(self) -> None:
        committed = Household()
        committed.your_ss_fra = SourcedValue(2_500.0, Provenance(Source.MANUAL, FIXED_DT))
        store = CandidateStore()
        store.record_candidate(
            "your_ss_fra", 2_700.0, Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2)
        )
        choices = ChoiceMap()

        result = resolve(committed, store, choices)

        assert result.household.your_ss_fra == 2_500.0
        assert "your_ss_fra" in result.pending_review

    def test_store_round_trips_through_json_with_provenance(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"
        store = CandidateStore()
        ingest_record_candidate(
            store, "spouse_ss_fra", 1_900.0, Source.FINEXTRACT_LIVE, "SSA statement", FIXED_DT
        )
        store.save(store_path)

        reloaded = CandidateStore.load(store_path)
        candidates = reloaded.candidates_for("spouse_ss_fra")
        assert len(candidates) == 1
        assert candidates[0].value == 1_900.0
        assert candidates[0].prov.source == Source.FINEXTRACT_LIVE
        assert candidates[0].prov.recorded_at == FIXED_DT
        assert candidates[0].prov.detail == "SSA statement"


class TestRecordSsFraCandidate:
    """engine.data_sources.record.record_ss_fra_candidate — Wave 2 Part C.

    Replaces the SS sync button's direct st.session_state[...] write; the
    synced value now sits pending in the CandidateStore until confirmed via
    the Command Center review gate (mirrors record_magi_candidates).
    """

    def test_records_finextract_live_candidate(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        recorded = record_ss_fra_candidate(
            "your_ss_fra",
            2_500.6,
            Source.FINEXTRACT_LIVE,
            "SSA statement",
            FIXED_DT,
            store_path=store_path,
        )

        assert recorded is True
        store = CandidateStore.load(store_path)
        candidates = store.candidates_for("your_ss_fra")
        assert len(candidates) == 1
        assert candidates[0].value == 2_501.0  # rounded, matches int-typed widget
        assert isinstance(candidates[0].value, float)
        assert candidates[0].prov.source == Source.FINEXTRACT_LIVE
        assert candidates[0].prov.detail == "SSA statement"
        assert candidates[0].prov.recorded_at == FIXED_DT

    def test_records_spouse_field(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        record_ss_fra_candidate(
            "spouse_ss_fra",
            1_800.0,
            Source.FINEXTRACT_LIVE,
            "SSA statement",
            FIXED_DT,
            store_path=store_path,
        )

        store = CandidateStore.load(store_path)
        assert store.candidates_for("spouse_ss_fra")[0].value == 1_800.0

    def test_rejects_unrecognized_field_key(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        recorded = record_ss_fra_candidate(
            "not_a_real_field",
            2_500.0,
            Source.FINEXTRACT_LIVE,
            "detail",
            FIXED_DT,
            store_path=store_path,
        )

        assert recorded is False
        assert not store_path.exists()

    def test_confirm_then_resolve_matches_c0_golden(self, tmp_path: Path) -> None:
        """End-to-end: sync (candidate) -> confirm -> resolved number equals
        the C0 golden — freeze-until-confirm changes the mechanism, not the
        final number."""
        store_path = tmp_path / "candidate_store.json"
        record_ss_fra_candidate(
            "your_ss_fra",
            2_500.0,
            Source.FINEXTRACT_LIVE,
            "SSA statement",
            FIXED_DT,
            store_path=store_path,
        )
        store = CandidateStore.load(store_path)
        choices = ChoiceMap()

        committed = Household()  # not yet committed
        confirmed = confirm(
            "your_ss_fra", Source.FINEXTRACT_LIVE, committed, store, choices, FIXED_DT_2
        )
        confirmed.your_ss_start_age = 70
        confirmed.your_fra_age = 67

        assert confirmed.your_ss_fra == 2_500.0
        assert confirmed.your_ss_fra.prov.source == Source.FINEXTRACT_LIVE
        assert confirmed.your_ss_at_70() == pytest.approx(37_200.0)


class TestRecordTxnQuoteCandidate:
    """engine.data_sources.record.record_txn_quote_candidate — live-quote P1.

    Mirrors record_ss_fra_candidate: a fetched TXN quote lands as a pending
    txn_price_now candidate from Source.MARKET_QUOTE, never writing directly
    to Household/session state.
    """

    def test_records_market_quote_candidate(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        recorded = record_txn_quote_candidate(
            202.357, recorded_at=FIXED_DT, store_path=store_path
        )

        assert recorded is True
        store = CandidateStore.load(store_path)
        candidates = store.candidates_for("txn_price_now")
        assert len(candidates) == 1
        assert candidates[0].value == 202.36  # rounded to the nearest cent
        assert candidates[0].prov.source == Source.MARKET_QUOTE
        assert candidates[0].prov.detail == "Yahoo Finance quote"
        assert candidates[0].prov.recorded_at == FIXED_DT

    def test_custom_detail(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"

        record_txn_quote_candidate(
            150.0, detail="Yahoo Finance AAPL", recorded_at=FIXED_DT, store_path=store_path
        )

        store = CandidateStore.load(store_path)
        assert store.candidates_for("txn_price_now")[0].prov.detail == "Yahoo Finance AAPL"

    def test_makes_txn_price_now_pending(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"
        record_txn_quote_candidate(210.0, recorded_at=FIXED_DT, store_path=store_path)
        store = CandidateStore.load(store_path)
        choices = ChoiceMap()

        result = resolve(Household(), store, choices)

        assert "txn_price_now" in result.pending_review
        assert result.household.txn_price_now == 210.0
        assert result.household.txn_price_now.prov.source == Source.MARKET_QUOTE

    def test_round_trips_through_store_save_and_load(self, tmp_path: Path) -> None:
        store_path = tmp_path / "candidate_store.json"
        record_txn_quote_candidate(199.99, recorded_at=FIXED_DT, store_path=store_path)

        reloaded = CandidateStore.load(store_path)
        candidate = reloaded.candidates_for("txn_price_now")[0]
        assert candidate.value == 199.99
        assert candidate.prov.source == Source.MARKET_QUOTE
        assert candidate.prov.recorded_at == FIXED_DT
        assert candidate.prov.to_json()["recorded_at"] == FIXED_DT.isoformat()


class TestSsFraMigration:
    """C3: legacy session your_ss_fra/spouse_ss_fra migrate to the committed
    baseline tagged Source.UNKNOWN 'pre-migration' — first-load numbers are
    byte-identical to C0 (matches the your_ira precedent)."""

    def test_migrate_committed_tags_ss_fra_pre_migration(self) -> None:
        hh = Household()
        hh.your_ss_fra = 2_500.0
        hh.spouse_ss_fra = 1_800.0

        committed_json = migrate_committed(hh, FIXED_DT)

        assert committed_json["your_ss_fra"]["value"] == 2_500.0
        assert committed_json["your_ss_fra"]["source"] == "UNKNOWN"
        assert committed_json["your_ss_fra"]["detail"] == "pre-migration"
        assert committed_json["spouse_ss_fra"]["value"] == 1_800.0
        assert committed_json["spouse_ss_fra"]["source"] == "UNKNOWN"

        assert hh.your_ss_fra == 2_500.0
        assert isinstance(hh.your_ss_fra, SourcedValue)

    def test_apply_committed_round_trip_matches_golden(self) -> None:
        source_hh = Household()
        source_hh.your_ss_fra = 2_500.0
        committed_json = migrate_committed(source_hh, FIXED_DT)

        fresh_hh = Household(your_ss_start_age=70, your_fra_age=67)
        apply_committed(fresh_hh, committed_json)

        assert fresh_hh.your_ss_fra == 2_500.0
        assert fresh_hh.your_ss_at_70() == pytest.approx(37_200.0)


class TestMagiLadderFlip:
    """PDF supersedes FinExtract for an uncommitted MAGI year (default ladder)."""

    def test_pdf_beats_finextract_for_uncommitted_year(self) -> None:
        committed = Household()  # prior_year_magi empty -> year is uncommitted
        store = CandidateStore()
        store.record_candidate(
            magi_field_key(2024), 290_000.0, Provenance(Source.PDF, FIXED_DT, detail="1040 pdf")
        )
        store.record_candidate(
            magi_field_key(2024),
            285_000.0,
            Provenance(Source.FINEXTRACT_LIVE, FIXED_DT_2, detail="live"),
        )

        result = resolve(committed, store, ChoiceMap())

        assert result.household.prior_year_magi[2024] == 290_000.0
        assert result.household.prior_year_magi.prov[2024].source == Source.PDF
        assert magi_field_key(2024) in result.pending_review
