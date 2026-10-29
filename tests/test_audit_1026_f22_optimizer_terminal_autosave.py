"""audit-1026 F-22: terminal IRA tax-rate edits on the optimizer panel are saved.

Synthetic values only.
"""

from __future__ import annotations

import pytest

import views.optimizer_panel as panel


@pytest.fixture
def session(monkeypatch):
    fake: dict = {}
    monkeypatch.setattr(panel.st, "session_state", fake)
    return fake


@pytest.fixture
def saves(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(
        panel,
        "autosave_user_defaults",
        lambda: calls.append(panel.st.session_state["terminal_ira_tax_rate"]),
    )
    return calls


def test_changed_terminal_rate_is_saved_after_copy(session, saves):
    session.update({"terminal_ira_tax_rate": 24.0, "w": 30.0})
    panel._copy_terminal_rate("w")
    assert session["terminal_ira_tax_rate"] == 30.0
    assert saves == [30.0]  # saved with the new value already in the store key


def test_unchanged_terminal_rate_is_not_saved(session, saves):
    session.update({"terminal_ira_tax_rate": 24.0, "w": 24.0})
    panel._copy_terminal_rate("w")
    assert saves == []


def test_discount_rate_edit_stays_session_only(session, saves):
    session.update({"_auto_opt_discount_pct": 0.0, "d": 3.0})
    panel._copy_to_store("d", panel.DISCOUNT_STORE_KEY)
    assert saves == []
