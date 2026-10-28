"""audit-1026 F-21: leaving demo mode restores the stored instance owner.

Demo mode's Update button sets the session ``instance_owner`` (the write itself
goes to the null store); ``exit_demo_mode`` must re-seed it from the real store.
All data is synthetic.
"""

from __future__ import annotations

import pytest

import views.setup._state as state_mod
from engine.instance_identity import CorruptInstanceOwnerError
from engine.store import StoreUnavailableError


@pytest.fixture
def session(monkeypatch):
    fake: dict = {}
    monkeypatch.setattr(state_mod.st, "session_state", fake)
    return fake


def test_exit_demo_restores_stored_owner(session, monkeypatch):
    monkeypatch.setattr(state_mod, "load_instance_owner", lambda: "you")
    session["instance_owner"] = "spouse"  # chosen in demo mode
    state_mod.exit_demo_mode()
    assert session["instance_owner"] == "you"


def test_exit_demo_with_no_stored_owner_is_none(session, monkeypatch):
    monkeypatch.setattr(state_mod, "load_instance_owner", lambda: None)
    session["instance_owner"] = "spouse"
    state_mod.exit_demo_mode()
    assert session["instance_owner"] is None


@pytest.mark.parametrize(
    "exc",
    [CorruptInstanceOwnerError("instance_owner", ValueError("bad")), StoreUnavailableError("x", "locked")],
)
def test_exit_demo_with_unreadable_owner_is_none(session, monkeypatch, exc):
    def boom() -> str | None:
        raise exc

    monkeypatch.setattr(state_mod, "load_instance_owner", boom)
    session["instance_owner"] = "spouse"
    state_mod.exit_demo_mode()
    assert session["instance_owner"] is None
