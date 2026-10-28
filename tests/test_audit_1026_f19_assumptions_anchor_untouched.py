"""audit-1026 F-19: merely rendering Profile > Assumptions must not alter a MAGI anchor.

The prior-year MAGI inputs used min_value=0 and int() seeding, so a confirmed
negative anchor was shown as 0 and then read as the user's "Leave 0" clear
(deleted everywhere), and a fractional anchor was truncated and re-stamped MANUAL.
Only a user changing a non-zero anchor to 0 is a clear. Synthetic figures.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod
from engine.data_sources.committed import load_committed, save_committed
from engine.data_sources.orchestrator import MAGI_CLEARED_KEY

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
LABEL = "2024 filed MAGI"


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


def _commit_pdf_anchor(value: float) -> None:
    save_committed(
        {
            "prior_year_magi": {
                "data": {"2024": value},
                "prov": {
                    "2024": {
                        "source": "PDF",
                        "recorded_at": "2026-01-01T00:00:00",
                        "detail": "synthetic",
                    }
                },
            }
        }
    )


def _committed_anchor() -> tuple[float, str]:
    entry = (load_committed() or {})["prior_year_magi"]
    return entry["data"]["2024"], entry["prov"]["2024"]["source"]


def _assumptions_app() -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["nav_page"] = "assumptions"
    return at


def _run(at: AppTest) -> None:
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def _anchor_widget(at: AppTest):
    return next(w for w in at.number_input if w.label == LABEL)


@pytest.mark.parametrize("value", [-20_000.0, 80_500.6])
def test_rendering_assumptions_keeps_the_anchor_exactly(value):
    _commit_pdf_anchor(value)
    at = _assumptions_app()
    _run(at)
    _run(at)
    assert dict(at.session_state["prior_year_magi"]) == {2024: value}
    assert MAGI_CLEARED_KEY not in at.session_state
    assert _committed_anchor() == (value, "PDF")


def test_user_setting_a_nonzero_anchor_to_zero_still_clears_it():
    _commit_pdf_anchor(90_000.0)
    at = _assumptions_app()
    _run(at)
    assert dict(at.session_state["prior_year_magi"]) == {2024: 90_000.0}
    _anchor_widget(at).set_value(0).run()
    assert not at.exception, [e.value for e in at.exception]
    assert dict(at.session_state["prior_year_magi"]) == {}
    committed = (load_committed() or {}).get("prior_year_magi", {})
    assert not committed.get("data")
