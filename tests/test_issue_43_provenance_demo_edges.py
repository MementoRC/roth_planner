"""Issue #43: provenance and demo-mode edge cases left by audit-1018 W9/W9b/W10. Synthetic data only."""

from __future__ import annotations

from datetime import datetime

import pytest

import engine.data_bridge_keys as keys_mod
import engine.demo_mode as demo_mod
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import migrate_committed
from engine.data_sources.orchestrator import resolve_for_app
from engine.data_status import compute_data_completeness
from models.household import Household
from models.sourced import Provenance, Source, SourcedValue

T0 = datetime(2026, 7, 24, 12, 0, 0)
T1 = datetime(2026, 7, 25, 12, 0, 0)


def _resolve(session: Household, committed: dict | None, **kwargs):
    return resolve_for_app(
        session, None, {}, CandidateStore(), ChoiceMap(), committed, T1, **kwargs
    )


# --- item 1: first-load bundle values are stamped BUNDLE ---------------------


def test_first_load_bundle_value_is_stamped_bundle_not_unknown() -> None:
    session = Household()
    session.your_ira = session.your_ira + 1234.0  # not equal to the seed default
    out = _resolve(session, None, bundle_scalars={"your_ira": session.your_ira})

    assert out.migrated is True
    assert out.committed_json["your_ira"]["source"] == "BUNDLE"
    assert out.result.household.your_ira.prov.source is Source.BUNDLE
    assert out.committed_json["spouse_ira"]["source"] == "DEFAULT"


def test_first_load_bundle_value_equal_to_seed_default_still_wins_over_default() -> None:
    session = Household()
    out = _resolve(session, None, bundle_scalars={"your_ira": session.your_ira})

    assert out.committed_json["your_ira"]["source"] == "BUNDLE"


def test_first_load_maps_stock_price_session_key_and_ignores_other_values() -> None:
    session = Household()
    session.stock_price_now = 123.0
    out = _resolve(session, None, bundle_scalars={"stock_price": 123, "your_ira": 1.0e9})

    assert out.committed_json["stock_price_now"]["source"] == "BUNDLE"
    assert out.committed_json["your_ira"]["source"] != "BUNDLE"  # value did not survive migration


# --- item 2: broader conflict badge is pinned --------------------------------


def test_committed_field_with_pending_candidate_reads_conflict() -> None:
    hh = Household()
    migrate_committed(hh, T0)
    hh.your_ira = SourcedValue(500_000.0, Provenance(Source.MANUAL, T1, "manual entry"))

    result = compute_data_completeness(hh, ["your_ira"], {"your_ira"}, now=T1)

    assert {i.field: i.severity for i in result.items} == {"your_ira": "conflict"}


# --- item 3: an untouched seed does not overwrite the committed baseline -----


def _committed_with_ira(value: float, source: Source) -> dict:
    hh = Household()
    hh.your_ira = value
    committed = migrate_committed(hh, T0)
    committed["your_ira"]["source"] = source.name
    return committed


def test_unedited_seed_does_not_overwrite_committed_baseline_as_manual() -> None:
    committed = _committed_with_ira(777_000.0, Source.BUNDLE)
    fresh = Household()  # fresh session: seeded with the default, differs from baseline
    seed = {"your_ira": fresh.your_ira}

    out = _resolve(fresh, committed, unedited_seed=seed)

    assert out.committed_json["your_ira"]["source"] == "BUNDLE"
    assert out.committed_json["your_ira"]["value"] == 777_000.0
    assert out.result.household.your_ira == 777_000.0


def test_genuine_edit_away_from_seed_is_still_manual() -> None:
    committed = _committed_with_ira(777_000.0, Source.BUNDLE)
    edited = Household()
    seed = {"your_ira": edited.your_ira}
    edited.your_ira = edited.your_ira + 5000.0

    out = _resolve(edited, committed, unedited_seed=seed)

    assert out.committed_json["your_ira"]["source"] == "MANUAL"


def test_without_seed_a_differing_session_value_is_manual() -> None:
    committed = _committed_with_ira(777_000.0, Source.BUNDLE)

    out = _resolve(Household(), committed)

    assert out.committed_json["your_ira"]["source"] == "MANUAL"


# --- item 4: data-bridge keypair is not written in demo mode -----------------


@pytest.fixture
def key_paths(tmp_path, monkeypatch: pytest.MonkeyPatch):
    pub, priv = tmp_path / "keys" / "data-bridge.pub", tmp_path / "keys" / "data-bridge.priv"
    monkeypatch.setattr(keys_mod, "PUBKEY_PATH", pub)
    monkeypatch.setattr(keys_mod, "PRIVKEY_PATH", priv)
    return pub, priv


def test_write_keypair_writes_nothing_in_demo_mode(
    tmp_path, monkeypatch: pytest.MonkeyPatch, key_paths
) -> None:
    marker = tmp_path / ".demo_mode"
    marker.write_text("demo\n")
    monkeypatch.setattr(demo_mod, "DEMO_MODE_PATH", marker)

    keys_mod.write_keypair(b"p" * 32, b"s" * 32)

    pub, _priv = key_paths
    assert not pub.parent.exists()


def test_write_keypair_still_writes_outside_demo_mode(
    tmp_path, monkeypatch: pytest.MonkeyPatch, key_paths
) -> None:
    monkeypatch.setattr(demo_mod, "DEMO_MODE_PATH", tmp_path / ".no_demo_marker")

    keys_mod.write_keypair(b"p" * 32, b"s" * 32)

    pub, priv = key_paths
    assert pub.is_file()
    assert priv.is_file()
