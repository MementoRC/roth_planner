"""audit-1026 F-12: the Confirm card preselects the source the planner is using.

The radio had no index=, so it preselected the first-RECORDED candidate while the
resolver used the user's trusted choice, else the DEFAULT_LADDER winner; clicking
Confirm untouched committed the wrong source. Synthetic figures.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.record import record_magi_candidates
from engine.store import get_store
from engine.store.namespaces import COMMITTED
from models.sourced import Provenance, Source, SourcedValue

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
T0 = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _review_app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "review"
    return at


def _run(at: AppTest) -> None:
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def _seed_ira_candidates(order: list[tuple[Source, float]]) -> None:
    get_store().put(
        COMMITTED,
        {"your_ira": SourcedValue(1_600_000.0, Provenance(Source.UNKNOWN, T0)).to_json()},
    )
    store = CandidateStore()
    for source, value in order:
        store.record_candidate("your_ira", value, Provenance(source, T0, "synthetic"))
    store.save()


def test_f12_confirm_default_magi_preselects_the_resolver_winner():
    record_magi_candidates({2024: 140_000.0}, Source.BUNDLE, "bundle", T0)
    record_magi_candidates({2024: 150_000.0}, Source.PDF, "1040 scan", T0)
    at = _review_app()
    at.session_state["prior_year_magi"] = {}
    _run(at)
    radio = at.radio(key="trust_prior_year_magi.2024")
    assert str(radio.value) == str(Source.PDF)
    at.button(key="confirm_prior_year_magi.2024").click().run()
    assert not at.exception
    entry = (load_committed() or {})["prior_year_magi"]
    assert entry["data"]["2024"] == 150_000.0
    assert entry["prov"]["2024"]["source"] == "PDF"


def test_f12_confirm_default_scalar_preselects_the_resolver_winner():
    _seed_ira_candidates([(Source.BUNDLE, 1_700_000.0), (Source.PDF, 1_800_000.0)])
    at = _review_app()
    at.session_state["your_ira"] = 1_600_000
    _run(at)
    assert str(at.radio(key="trust_your_ira").value) == str(Source.PDF)
    at.button(key="confirm_your_ira").click().run()
    assert not at.exception
    committed = (load_committed() or {})["your_ira"]
    assert (committed["value"], committed["source"]) == (1_800_000.0, "PDF")


def test_f12_confirm_default_prefers_the_users_trusted_choice():
    _seed_ira_candidates(
        [
            (Source.BUNDLE, 1_700_000.0),
            (Source.PDF, 1_800_000.0),
            (Source.FINEXTRACT_LIVE, 1_900_000.0),
        ]
    )
    choices = ChoiceMap()
    choices.set_choice("your_ira", Source.FINEXTRACT_LIVE, T0)
    choices.save()
    at = _review_app()
    at.session_state["your_ira"] = 1_600_000
    _run(at)
    assert str(at.radio(key="trust_your_ira").value) == str(Source.FINEXTRACT_LIVE)
