"""Persisted keys renamed to neutral stock_* names still load from old saves.

Every load boundary accepts the pre-rename names (stored as SHA-256 digests in
``models.legacy_keys.LEGACY_KEY_DIGESTS``) and reads them under the current
names; every save/export writes only the current names. The old names are
rebuilt at test time from the current ones (prefix assembled from code points)
rather than spelled out here, and each is checked against the digest map.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
import streamlit as st

import config.loader as loader
import engine.portfolio_sync.portfolio as portfolio_mod
import views.setup._state as state_mod
from config.scalar_registry import scalar_default, scalar_default_by_config_key
from engine.bridge_bundle import build_bundle_v5, parse_v5
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import apply_committed, extract_committed, load_committed
from engine.portfolio_sync.shapes import PortfolioSnapshot, portfolio_snapshot_from_dict
from engine.upload_merge import build_user_defaults_session_updates
from models.household import Household
from models.legacy_keys import (
    LEGACY_KEY_DIGESTS,
    current_key,
    is_legacy_key,
    key_digest,
    migrate_legacy_keys,
)
from models.sourced import Provenance, Source, SourcedValue

_LEGACY_PREFIX = "".join(map(chr, (116, 120, 110)))
OLD = {
    new: new.replace("stock", _LEGACY_PREFIX, 1) for new in sorted(set(LEGACY_KEY_DIGESTS.values()))
}
_WHEN = datetime(2026, 1, 15, 12, 0, 0)


def _has_legacy_key(text: str) -> bool:
    return any(f'"{old}"' in text for old in OLD.values())


# --- the map itself ---------------------------------------------------------


def test_reconstructed_legacy_names_match_the_digest_map() -> None:
    assert {key_digest(old): new for new, old in OLD.items()} == LEGACY_KEY_DIGESTS
    assert all(is_legacy_key(old) and current_key(old) == new for new, old in OLD.items())
    assert not any(is_legacy_key(new) for new in OLD)
    assert current_key("stock_price") == "stock_price"


def test_old_key_moves_to_new_key() -> None:
    assert migrate_legacy_keys({OLD["stock_price_growth_rate"]: 4.0}) == {
        "stock_price_growth_rate": 4.0
    }


def test_new_key_wins_when_both_present() -> None:
    raw = {OLD["stock_price_now"]: 1.0, "stock_price_now": 2.0}
    assert migrate_legacy_keys(raw) == {"stock_price_now": 2.0}


def test_input_is_not_mutated() -> None:
    raw = {OLD["stock_shares_held"]: 3}
    migrate_legacy_keys(raw)
    assert raw == {OLD["stock_shares_held"]: 3}


def test_every_legacy_name_maps_to_a_stock_name() -> None:
    assert all(new.startswith("stock_") for new in LEGACY_KEY_DIGESTS.values())


# --- scalar registry lookups ------------------------------------------------


def test_scalar_registry_resolves_legacy_names() -> None:
    assert scalar_default(OLD["stock_price"]) == scalar_default("stock_price")
    assert scalar_default(OLD["stock_price_growth_rate"]) == 7.0
    assert scalar_default_by_config_key(OLD["stock_price_growth_rate"]) == 7.0


# --- .user_defaults.json (session restore + autosave) -----------------------


@pytest.fixture
def user_defaults_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    path = tmp_path / ".user_defaults.json"
    monkeypatch.setattr(loader, "_USER_DEFAULTS_PATH", path)
    return path


def test_user_defaults_with_old_key_load_under_new_key(user_defaults_file) -> None:
    user_defaults_file.write_text(json.dumps({OLD["stock_price_growth_rate"]: 9.0}))
    assert loader.load_defaults()["stock_price_growth_rate"] == 9.0
    raw = loader.load_raw_user_defaults()
    assert raw == {"stock_price_growth_rate": 9.0}


def test_autosave_rewrites_old_key_as_new(user_defaults_file) -> None:
    user_defaults_file.write_text(
        json.dumps({OLD["stock_price_growth_rate"]: 9.0, "living_expenses": 1})
    )
    loader.save_user_defaults({"living_expenses": 2})
    text = user_defaults_file.read_text()
    assert not _has_legacy_key(text)
    assert json.loads(text) == {"stock_price_growth_rate": 9.0, "living_expenses": 2}


def test_session_payload_writes_only_new_keys(monkeypatch) -> None:
    monkeypatch.setattr(st, "session_state", {"stock_price": 150, "stock_price_growth_rate": 6.0})
    payload = state_mod._user_defaults_from_session()
    assert payload["stock_price_now"] == 150
    assert payload["stock_price_growth_rate"] == 6.0
    assert not _has_legacy_key(json.dumps(payload))


# --- settings import / v4 bundle setup_scalars -----------------------------


def test_settings_import_reads_old_keys() -> None:
    updates = build_user_defaults_session_updates(
        {OLD["stock_price_growth_rate"]: 4.0, "stock_price_now": 150}, as_spouse=False
    )
    assert updates["stock_price_growth_rate"] == 4.0
    assert updates["stock_price"] == 150
    assert not any(is_legacy_key(k) for k in updates)


def test_spouse_import_sees_receivers_old_key_as_set() -> None:
    updates = build_user_defaults_session_updates(
        {"stock_price_growth_rate": 4.0},
        as_spouse=True,
        receiver_persisted={OLD["stock_price_growth_rate"]: 9.0},
    )
    assert "stock_price_growth_rate" not in updates
    assert updates["_bundle_joint_conflicts"] == [
        {"field": "stock_price_growth_rate", "mine": 9.0, "theirs": 4.0}
    ]


# --- v5 bundle --------------------------------------------------------------


def _v5_bundle() -> dict:
    return build_bundle_v5(
        setup_scalars={"stock_price_now": 120.0, "stock_price_growth_rate": 5.5},
        snapshot=None,
        ledger=None,
        ytd=None,
        grants=None,
        exporter="you",
        slot_captured_at=None,
        now="2026-01-15T12:00:00",
    )


def test_v5_export_writes_only_new_keys() -> None:
    bundle = _v5_bundle()
    assert bundle["joint"]["stock_price_growth_rate"] == 5.5
    assert not _has_legacy_key(json.dumps(bundle))


def test_v5_bundle_with_old_keys_imports_into_new_keys() -> None:
    bundle = _v5_bundle()
    joint = bundle["joint"]
    joint[OLD["stock_price_growth_rate"]] = joint.pop("stock_price_growth_rate")
    parsed = parse_v5(bundle)
    assert parsed.joint["stock_price_growth_rate"] == 5.5
    assert parsed.joint["stock_price_now"] == 120.0
    assert parsed.warnings == ()


# --- committed baseline -----------------------------------------------------


def test_committed_baseline_with_old_key_loads_into_new_attr(tmp_path) -> None:
    path = tmp_path / "committed.json"
    entry = SourcedValue(210.5, Provenance(Source.PDF, _WHEN, "statement")).to_json()
    path.write_text(json.dumps({OLD["stock_price_now"]: entry}))
    committed = load_committed(path)
    assert committed is not None
    assert set(committed) == {"stock_price_now"}
    hh = Household()
    apply_committed(hh, committed)
    assert hh.stock_price_now == 210.5
    assert set(extract_committed(hh)) == {"stock_price_now"}


# --- candidate store and trust choices --------------------------------------


def test_candidate_store_with_old_field_key_loads_under_new_key(tmp_path) -> None:
    store = CandidateStore()
    store.record_candidate("stock_price_now", 199.5, Provenance(Source.MARKET_QUOTE, _WHEN, "q"))
    raw = store.to_json()
    raw[OLD["stock_price_now"]] = raw.pop("stock_price_now")
    path = tmp_path / "cands.json"
    path.write_text(json.dumps(raw))
    loaded = CandidateStore.load(path)
    assert not loaded.load_corrupt
    assert [c.value for c in loaded.candidates_for("stock_price_now")] == [199.5]
    loaded.save(path)
    assert not _has_legacy_key(path.read_text())


def test_trust_choices_with_old_field_key_load_under_new_key(tmp_path) -> None:
    choices = ChoiceMap()
    choices.set_choice("stock_price_now", Source.MARKET_QUOTE, _WHEN)
    raw = choices.to_json()
    raw[OLD["stock_price_now"]] = raw.pop("stock_price_now")
    path = tmp_path / "choices.json"
    path.write_text(json.dumps(raw))
    loaded = ChoiceMap.load(path)
    choice = loaded.get("stock_price_now")
    assert choice is not None
    assert choice.source == Source.MARKET_QUOTE
    loaded.save(path)
    assert not _has_legacy_key(path.read_text())


# --- portfolio cache --------------------------------------------------------


def test_portfolio_cache_with_old_keys_loads_and_saves_new_keys(tmp_path, monkeypatch) -> None:
    path = tmp_path / ".portfolio_cache.json"
    monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", path)
    path.write_text(
        json.dumps({"accounts": [], OLD["stock_shares_held"]: 5, OLD["stock_shares_value"]: 500.0})
    )
    snap = portfolio_mod.load_snapshot()
    assert snap is not None
    assert (snap.stock_shares_held, snap.stock_shares_value) == (5, 500.0)
    portfolio_mod.save_snapshot(snap, allow_empty=True)
    text = path.read_text()
    assert not _has_legacy_key(text)
    assert json.loads(text)["stock_shares_held"] == 5


def test_snapshot_dict_with_old_keys_loads_under_new_names() -> None:
    snap = portfolio_snapshot_from_dict({OLD["stock_shares_held"]: 7})
    assert snap.stock_shares_held == 7
    assert PortfolioSnapshot().stock_shares_value == 0.0
