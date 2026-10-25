"""audit-1008 W12 — prior-year MAGI anchor governance.

F-46: an unconfirmed MAGI candidate for a year with no committed value was
written back into session by app.py, then read on the next rerun by
``reconcile_manual_edits`` as a manual edit and committed as Source.MANUAL —
silently skipping the Command Center confirm.

F-64: "Leave 0 to use projected MAGI" popped the year from session, but
reconcile never deletes committed years, so the anchor snapped back.

W11 follow-up: ``save_committed`` only checked that the saved baseline was
valid JSON, so a valid-JSON-but-wrong-shape baseline (which
``load_committed`` / ``apply_committed`` cannot use) was silently replaced.

All figures are synthetic.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import (
    CorruptCommittedCacheError,
    load_committed,
    save_committed,
)
from engine.data_sources.orchestrator import resolve_for_app
from engine.data_sources.record import record_magi_candidates
from engine.data_sources.resolver import magi_field_key
from engine.store.namespaces import CANDIDATE_STORE, COMMITTED
from models.household import Household
from models.sourced import Source

T0 = datetime(2026, 10, 1, 12, 0, 0)


class _App:
    """Mimic app.py get_household(): session -> Household -> resolve -> writeback."""

    def __init__(self, store, session_magi: dict[int, float]):
        self.doc_store = store
        self.session_magi = dict(session_magi)
        self.committed: dict | None = None
        self.cleared: set[int] = set()

    def rerun(self):
        hh = Household(prior_year_magi=dict(self.session_magi))
        cands = CandidateStore.load()
        res = resolve_for_app(
            hh,
            None,
            {},
            cands,
            ChoiceMap(),
            self.committed,
            recorded_at=T0,
            cleared_magi_years=set(self.cleared),
        )
        if self.doc_store.has(CANDIDATE_STORE) or cands.field_keys():
            cands.save()
        self.cleared.clear()
        self.committed = res.committed_json
        resolved = res.result.household.prior_year_magi
        self.session_magi = {int(y): float(v) for y, v in dict(resolved).items()}
        return res

    def committed_magi(self) -> dict:
        return dict((self.committed or {}).get("prior_year_magi", {}).get("data", {}))

    def committed_prov(self, year: int) -> str | None:
        prov = (self.committed or {}).get("prior_year_magi", {}).get("prov", {})
        entry = prov.get(str(year))
        return entry["source"] if entry else None


# ---------------------------------------------------------------- F-46


def test_unconfirmed_new_year_candidate_stays_pending_across_reruns(store):
    app = _App(store, {2024: 111_000.0})
    app.rerun()  # migration
    app.rerun()
    record_magi_candidates({2025: 150_000.0}, Source.PDF, "1040 scan", T0)

    for _ in range(4):
        res = app.rerun()
        assert magi_field_key(2025) in res.result.pending_review
        assert "2025" not in app.committed_magi()
        assert app.committed_prov(2025) is None


def test_genuine_manual_entry_for_new_year_is_still_committed(store):
    app = _App(store, {2024: 111_000.0})
    app.rerun()
    app.session_magi[2025] = 99_000.0  # user types a new year in Setup
    app.rerun()
    assert app.committed_magi()["2025"] == 99_000.0
    assert app.committed_prov(2025) == "MANUAL"


def test_manual_value_differing_from_pending_candidate_is_committed(store):
    app = _App(store, {2024: 111_000.0})
    app.rerun()
    record_magi_candidates({2025: 150_000.0}, Source.PDF, "1040 scan", T0)
    app.rerun()  # candidate written back to session
    app.session_magi[2025] = 140_000.0  # user overrides it
    app.rerun()
    assert app.committed_magi()["2025"] == 140_000.0
    assert app.committed_prov(2025) == "MANUAL"


# ---------------------------------------------------------------- F-64


def test_leave_zero_clears_a_committed_anchor(store):
    app = _App(store, {})
    app.rerun()  # migration with nothing
    app.session_magi[2024] = 2_500_000.0  # typo
    app.rerun()
    assert app.committed_magi() == {"2024": 2_500_000.0}

    # user sets the field back to 0: the partial pops it and flags the clear
    app.session_magi.pop(2024)
    app.cleared.add(2024)
    res = app.rerun()
    assert app.committed_magi() == {}
    assert 2024 not in dict(res.result.household.prior_year_magi)
    assert res.committed_changed

    for _ in range(3):
        res = app.rerun()
        assert 2024 not in dict(res.result.household.prior_year_magi)


def test_clearing_one_year_keeps_the_other(store):
    app = _App(store, {})
    app.rerun()
    app.session_magi.update({2024: 2_500_000.0, 2025: 150_000.0})
    app.rerun()
    app.session_magi.pop(2024)
    app.cleared.add(2024)
    res = app.rerun()
    assert app.committed_magi() == {"2025": 150_000.0}
    assert dict(res.result.household.prior_year_magi) == {2025: 150_000.0}


def test_clear_also_drops_that_years_candidates(store):
    app = _App(store, {})
    app.rerun()
    record_magi_candidates({2024: 150_000.0}, Source.PDF, "1040 scan", T0)
    app.rerun()
    assert 2024 in app.session_magi
    app.session_magi.pop(2024)
    app.cleared.add(2024)
    res = app.rerun()
    assert 2024 not in dict(res.result.household.prior_year_magi)
    assert magi_field_key(2024) not in res.result.pending_review
    assert not CandidateStore.load().has_candidates(magi_field_key(2024))


def test_empty_session_without_clear_flag_does_not_wipe_committed(store):
    """A fresh browser session seeds prior_year_magi={} — not a clear."""
    app = _App(store, {2024: 111_000.0})
    app.rerun()
    app.session_magi = {}
    app.rerun()
    assert app.committed_magi() == {"2024": 111_000.0}


# ---------------------------------------------------------------- save_committed


@pytest.mark.parametrize(
    "content",
    [
        [1, 2, 3],
        "just a string",
        {"your_ira": "not-a-payload"},
        {"prior_year_magi": {"data": {"2024": 1.0}}},
    ],
)
def test_save_committed_refuses_wrong_shape_row(store, content):
    store.put(COMMITTED, content)
    with pytest.raises(CorruptCommittedCacheError):
        save_committed({})
    assert store.get(COMMITTED) == content


@pytest.mark.parametrize(
    "content",
    [
        [1, 2, 3],
        {"your_ira": "not-a-payload"},
    ],
)
def test_load_committed_flags_wrong_shape_row(store, content):
    store.put(COMMITTED, content)
    with pytest.raises(CorruptCommittedCacheError):
        load_committed()


def test_save_committed_overwrites_valid_baseline(store):
    app = _App(store, {2024: 111_000.0})
    app.rerun()
    save_committed(app.committed)
    save_committed(app.committed)
    assert load_committed() == app.committed
