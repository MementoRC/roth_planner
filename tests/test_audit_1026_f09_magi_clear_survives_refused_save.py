"""audit-1026 F-09: a cleared prior-year MAGI flag survives a refused autosave.

get_household() used to pop the one-shot flags (MAGI_CLEARED_KEY, SEED_VALUES_KEY,
BUNDLE_SCALARS_KEY) before the autosave. When the save was refused (database
locked, read-only store) the committed baseline kept the old anchor, the flag
was gone, and the "Leave 0" clear was silently lost on the next render. The
flags may only be consumed once the save actually commits. Synthetic figures.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.data_sources.committed as committed_mod
import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from engine.data_sources.committed import load_committed
from engine.data_sources.orchestrator import BUNDLE_SCALARS_KEY, MAGI_CLEARED_KEY, SEED_VALUES_KEY
from engine.store import StoreUnavailableError

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _committed_magi() -> dict:
    return dict((load_committed() or {}).get("prior_year_magi", {}).get("data", {}))


def _committed_app() -> AppTest:
    """Run once so the baseline is committed with a 2025 anchor."""
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "overview"
    at.session_state["prior_year_magi"] = {2025: 250_000.0}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _committed_magi() == {"2025": 250_000.0}
    return at


def _user_clears_2025(at: AppTest) -> None:
    at.session_state["prior_year_magi"] = {}
    at.session_state[MAGI_CLEARED_KEY] = [2025]


def test_refused_save_keeps_flag_and_next_commit_deletes_anchor(monkeypatch):
    at = _committed_app()
    real_save = committed_mod.save_committed
    refuse = {"on": True}

    def maybe_refused(*args, **kwargs):
        if refuse["on"]:
            raise StoreUnavailableError("/synthetic/.roth_planner.db", "database is locked")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(committed_mod, "save_committed", maybe_refused)
    _user_clears_2025(at)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _committed_magi() == {"2025": 250_000.0}  # nothing committed
    assert at.session_state[MAGI_CLEARED_KEY] == [2025]  # flag survives

    refuse["on"] = False
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _committed_magi() == {}
    assert dict(at.session_state["prior_year_magi"]) == {}
    assert MAGI_CLEARED_KEY not in at.session_state


def test_successful_save_consumes_the_flags():
    at = _committed_app()
    _user_clears_2025(at)
    at.session_state[SEED_VALUES_KEY] = {}
    at.session_state[BUNDLE_SCALARS_KEY] = {}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _committed_magi() == {}
    assert MAGI_CLEARED_KEY not in at.session_state
    assert SEED_VALUES_KEY not in at.session_state
    assert BUNDLE_SCALARS_KEY not in at.session_state
