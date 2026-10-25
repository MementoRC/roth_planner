"""Issue #39: follow-ups from audit-1018 W8 (#25) and W11 (#30).

Corrupt caches must be preserved, not overwritten; NaN / negative inputs must be
sanitized at their sources. Synthetic figures only; every file lives in tmp_path.
"""

from __future__ import annotations

import json

import pytest
from streamlit.testing.v1 import AppTest

import config.loader as loader
import engine.instance_identity as identity
import engine.portfolio_sync.portfolio as portfolio_mod
import engine.portfolio_sync.social_security as ssa_mod
from engine.portfolio_sync.shapes import PortfolioSnapshot, SSASnapshot
from engine.store import SINGLE_KEY
from engine.store.namespaces import PORTFOLIO
from engine.upload_merge import build_user_defaults_session_updates, sanitize_living_expenses
from models.exercise_schedule import ExerciseSchedule
from models.grants import StockGrant

BAD_BYTES = b"\xff\xfe not json at all"


@pytest.fixture(
    params=[BAD_BYTES, b"{not json", b"[1, 2, 3]"], ids=["non-utf8", "malformed", "list"]
)
def bad_bytes(request: pytest.FixtureRequest) -> bytes:
    return request.param


# --- save_snapshot / save_ssa_snapshot preserve an unreadable cache ------------


def test_save_snapshot_refuses_corrupt_row_and_preserves_bytes(store, corrupt_row):
    payload = corrupt_row(PORTFOLIO)
    with pytest.raises(portfolio_mod.CorruptPortfolioCacheError, match="portfolio"):
        portfolio_mod.save_snapshot(PortfolioSnapshot(server_available=True))
    assert store.backend.get(PORTFOLIO, SINGLE_KEY).payload == payload


def test_save_snapshot_refuses_non_object_document(store):
    store.put(PORTFOLIO, [1, 2, 3])
    with pytest.raises(portfolio_mod.CorruptPortfolioCacheError, match="portfolio"):
        portfolio_mod.save_snapshot(PortfolioSnapshot(server_available=True))
    assert store.get(PORTFOLIO) == [1, 2, 3]


def test_save_snapshot_missing_row_still_writes(store):
    portfolio_mod.save_snapshot(PortfolioSnapshot(server_available=True))
    assert store.get(PORTFOLIO)["server_available"] is True


def test_save_ssa_snapshot_refuses_corrupt_cache_and_preserves_bytes(
    tmp_path, monkeypatch, bad_bytes
):
    path = tmp_path / ".ssa_cache.json"
    path.write_bytes(bad_bytes)
    monkeypatch.setattr(ssa_mod, "_SSA_CACHE_PATH", path)
    with pytest.raises(ssa_mod.CorruptSSACacheError):
        ssa_mod.save_ssa_snapshot(SSASnapshot(), owner="you")
    assert path.read_bytes() == bad_bytes


def test_save_ssa_snapshot_keeps_other_owner(tmp_path, monkeypatch):
    path = tmp_path / ".ssa_cache.json"
    monkeypatch.setattr(ssa_mod, "_SSA_CACHE_PATH", path)
    ssa_mod.save_ssa_snapshot(SSASnapshot(), owner="you")
    ssa_mod.save_ssa_snapshot(SSASnapshot(), owner="spouse")
    assert set(json.loads(path.read_text())) == {"you", "spouse"}


# --- non-UTF-8 read_text gaps ---------------------------------------------------


def test_load_instance_owner_non_utf8_is_corrupt_not_crash(tmp_path, monkeypatch):
    path = tmp_path / ".instance_owner.json"
    path.write_bytes(BAD_BYTES)
    monkeypatch.setattr(identity, "INSTANCE_OWNER_PATH", path)
    with pytest.raises(identity.CorruptInstanceOwnerError):
        identity.load_instance_owner()


def test_user_defaults_non_utf8_reads_as_empty(tmp_path):
    path = tmp_path / ".user_defaults.json"
    path.write_bytes(BAD_BYTES)
    assert loader._load_overrides_from_json(path) == {}


def test_save_user_defaults_survives_non_utf8_existing_file(tmp_path, monkeypatch):
    path = tmp_path / ".user_defaults.json"
    path.write_bytes(BAD_BYTES)
    monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(path))
    loader.save_user_defaults({"growth_rate": 6.0})
    assert json.loads(path.read_text()) == {"growth_rate": 6.0}


# --- living_expenses sanitized at its sources ----------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (float("nan"), 60_000),
        (float("inf"), 60_000),
        (-5_000, 0),
        (42_000, 42_000),
        (42_000.5, 42_000.5),
    ],
)
def test_sanitize_living_expenses(raw, expected):
    assert sanitize_living_expenses(raw) == expected


@pytest.mark.parametrize("as_spouse", [False, True])
def test_upload_merge_sanitizes_living_expenses(as_spouse):
    for raw, expected in ((float("nan"), 60_000), (-1, 0)):
        updates = build_user_defaults_session_updates(
            {"living_expenses": raw}, as_spouse=as_spouse, receiver_persisted={}
        )
        assert updates["living_expenses"] == expected


@pytest.mark.parametrize(("raw", "expected"), [(float("nan"), 60_000), (-5_000, 0)])
def test_app_seed_sanitizes_living_expenses(tmp_path, monkeypatch, raw, expected):
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps({"living_expenses": raw}))
    monkeypatch.setenv("ROTH_PLANNER_DEFAULTS", str(path))
    at = AppTest.from_file("app.py", default_timeout=60)
    at.run()
    assert not at.exception
    assert at.session_state["living_expenses"] == expected


# --- grants / schedule non-finite price ----------------------------------------


@pytest.mark.parametrize("price", [float("nan"), float("inf"), float("-inf")])
def test_per_share_spread_non_finite_price_is_zero(price):
    grant = StockGrant(2020, 100.0, 50, 2030)
    assert grant.per_share_spread(price) == 0.0
    assert grant.spread(price) == 0.0


def test_per_share_spread_finite_price_unchanged():
    assert StockGrant(2020, 100.0, 50, 2030).per_share_spread(130.0) == 30.0


def test_validate_flags_non_finite_price():
    schedule = ExerciseSchedule(price_by_year={2027: float("nan"), 2028: float("inf")})
    messages = schedule.validate([], 2026)
    assert sum("non-finite" in m for m in messages) == 2


def test_validate_still_flags_negative_price():
    messages = ExerciseSchedule(price_by_year={2027: -1.0}).validate([], 2026)
    assert any("negative price" in m for m in messages)


# --- inherited IRA NaN balance in session state --------------------------------


def _render_assumptions() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_assumptions_partial

    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", 60_000)
    st.session_state.setdefault("_pending_review", set())
    render_assumptions_partial(Household(), st)


def test_inherited_ira_nan_balance_does_not_crash():
    at = AppTest.from_function(_render_assumptions)
    at.session_state["inherited_iras"] = [
        {"balance": float("nan"), "inherited_year": 2030, "owner": "you", "growth_rate": 0.07}
    ]
    at.run()
    assert not at.exception, str(at.exception[0].value)[:300]
    widget = next(w for w in at.number_input if w.key == "iira_balance_0")
    assert widget.value == 0


def test_living_expenses_widget_nan_does_not_crash():
    at = AppTest.from_function(_render_assumptions)
    at.session_state["living_expenses"] = float("nan")
    at.run()
    assert not at.exception
