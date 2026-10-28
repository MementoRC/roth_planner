"""audit-1026 F-10: a Setup edit equal to a LOSING candidate is a manual edit.

app.py writes back only the resolved winner; reconcile used to skip any edit
matching ANY source's candidate, so the writeback overwrote the user's value.
All figures are synthetic.
"""

from __future__ import annotations

from datetime import datetime

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.orchestrator import resolve_for_app
from engine.data_sources.record import record_magi_candidates
from engine.data_sources.resolver import magi_field_key
from models.household import Household
from models.sourced import Source

T0 = datetime(2026, 10, 1, 12, 0, 0)


class _App:
    def __init__(self, session_magi: dict[int, float]):
        self.session_magi = dict(session_magi)
        self.committed: dict | None = None

    def rerun(self):
        hh = Household(prior_year_magi=dict(self.session_magi))
        cands = CandidateStore.load()
        res = resolve_for_app(hh, None, {}, cands, ChoiceMap(), self.committed, recorded_at=T0)
        cands.save()
        self.committed = res.committed_json
        resolved = res.result.household.prior_year_magi
        self.session_magi = {int(y): float(v) for y, v in dict(resolved).items()}
        return res

    def magi(self) -> dict:
        return dict((self.committed or {}).get("prior_year_magi", {}).get("data", {}))

    def prov(self, year: int) -> str | None:
        prov = (self.committed or {}).get("prior_year_magi", {}).get("prov", {})
        entry = prov.get(str(year))
        return entry["source"] if entry else None


def _app_with_two_candidates(store) -> _App:
    app = _App({2023: 111_000.0})
    app.rerun()  # migration
    record_magi_candidates({2024: 140_000.0}, Source.BUNDLE, "bundle", T0)
    record_magi_candidates({2024: 150_000.0}, Source.PDF, "1040 scan", T0)
    app.rerun()  # PDF winner written back to session
    assert app.session_magi[2024] == 150_000.0
    return app


def test_f10_manual_edit_equal_to_losing_candidate_is_committed(store):
    app = _app_with_two_candidates(store)
    app.session_magi[2024] = 140_000.0  # user types the BUNDLE (losing) value
    app.rerun()
    assert app.magi().get("2024") == 140_000.0
    assert app.prov(2024) == "MANUAL"


def test_f10_manual_edit_between_candidates_still_commits(store):
    app = _app_with_two_candidates(store)
    app.session_magi[2024] = 145_000.0
    app.rerun()
    assert app.magi()["2024"] == 145_000.0
    assert app.prov(2024) == "MANUAL"


def test_f10_manual_edit_control_winner_value_is_not_an_edit(store):
    app = _app_with_two_candidates(store)
    res = app.rerun()  # session still holds the written-back winner
    assert "2024" not in app.magi()
    assert magi_field_key(2024) in res.result.pending_review
