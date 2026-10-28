"""audit-1026 F-05: clearing a prior-year MAGI anchor must stick across reruns and restarts.

app.py re-records the on-disk 1040 PDF cache as a candidate on every rerun. The
clear used to drop the candidate with a plain dict removal, so the next ordinary
rerun re-recorded it and the anchor came back. The clear now records a persisted
dismissal of the (year, source, value) candidate; a different PDF value or a
manual entry is still honoured. Synthetic figures.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.committed import load_committed
from engine.data_sources.orchestrator import MAGI_CLEARED_KEY
from models.sourced import Source

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FIELD = "prior_year_magi.2024"


def _pdf_records(agi: float) -> dict:
    return {2024: SimpleNamespace(agi=agi, tax_exempt_interest=0.0, feie=0.0)}


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: _pdf_records(150_000.0))


def _new_app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "overview"
    return at


def _run(at: AppTest) -> None:
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def _magi(at: AppTest) -> dict:
    return {int(k): float(v) for k, v in dict(at.session_state["prior_year_magi"]).items()}


def _cleared_app() -> AppTest:
    at = _new_app()
    _run(at)
    assert _magi(at) == {2024: 150_000.0}  # the PDF candidate is offered
    at.session_state["prior_year_magi"] = {}
    at.session_state[MAGI_CLEARED_KEY] = [2024]
    _run(at)
    assert _magi(at) == {}
    return at


def test_clear_sticks_across_ordinary_reruns():
    at = _cleared_app()
    for _ in range(2):
        _run(at)
        assert _magi(at) == {}
        assert FIELD not in at.session_state["_pending_review"]


def test_clear_survives_a_restart():
    _cleared_app()
    fresh = _new_app()  # new session, same store
    _run(fresh)
    assert _magi(fresh) == {}
    assert FIELD not in fresh.session_state["_pending_review"]


def test_a_different_pdf_value_is_still_offered(monkeypatch):
    at = _cleared_app()
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: _pdf_records(175_000.0))
    _run(at)
    assert _magi(at) == {2024: 175_000.0}
    assert FIELD in at.session_state["_pending_review"]


def test_a_manual_entry_after_a_clear_is_honoured_and_lifts_the_dismissal():
    at = _cleared_app()
    assert CandidateStore.load().is_dismissed(FIELD, Source.PDF, 150_000.0)
    at.session_state["prior_year_magi"] = {2024: 200_000.0}
    _run(at)
    committed = (load_committed() or {})["prior_year_magi"]["data"]
    assert committed == {"2024": 200_000.0}
    assert not CandidateStore.load().is_dismissed(FIELD, Source.PDF, 150_000.0)
