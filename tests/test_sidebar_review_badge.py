"""Sidebar "awaiting review" badge reflects candidates the page body recorded (#602)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import views.navigation as navigation_mod
from engine.data_sources.record import record_magi_candidates
from models.sourced import Source

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _badges(at: AppTest) -> list[str]:
    return [w.value for w in at.sidebar.warning if "awaiting review" in w.value]


def _run(monkeypatch: pytest.MonkeyPatch, *, record: bool, stop: bool = False) -> AppTest:
    def _body(_hh: object) -> None:
        if record:
            record_magi_candidates(
                {2031: 123456.0},
                Source.PDF,
                "synthetic",
                datetime(2026, 1, 1),
            )
        if stop:
            import streamlit as st

            st.stop()

    monkeypatch.setattr(navigation_mod, "route_target", lambda _page: _body)
    at = AppTest.from_file(str(APP_PATH))
    at.session_state["_suppress_snapshot_autoload"] = True
    at.run()
    assert not at.exception
    return at


def test_badge_counts_candidate_recorded_by_page_body_same_run(monkeypatch) -> None:
    at = _run(monkeypatch, record=True)
    badges = _badges(at)
    assert len(badges) == 1
    assert "1 data field(s) awaiting review" in badges[0]
    assert len(at.session_state["_pending_review"]) == 1


def test_badge_still_drawn_when_page_body_stops(monkeypatch) -> None:
    record_magi_candidates(
        {2031: 123456.0},
        Source.PDF,
        "synthetic",
        datetime(2026, 1, 1),
    )
    at = _run(monkeypatch, record=False, stop=True)
    assert len(_badges(at)) == 1


def test_no_badge_when_nothing_pending(monkeypatch) -> None:
    at = _run(monkeypatch, record=False)
    assert _badges(at) == []
