"""audit-1018 W10: provenance and governance (F-21, F-22, F-32). Synthetic data only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed, migrate_committed, save_committed
from engine.data_sources.confirm import confirm_field
from engine.data_sources.orchestrator import resolve_for_app
from models.household import Household
from models.sourced import Provenance, Source, SourcedValue

_NOW = datetime(2026, 7, 24, 12, 0, 0)


# --- F-21: bundle-imported scalars are stamped BUNDLE, not MANUAL -------------


def test_f21_bundle_imported_scalars_are_committed_as_backup_import(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import views.setup.data_bridge as db

    committed_path = tmp_path / "committed.json"
    baseline = Household(your_ira=500_000, spouse_ira=400_000, stock_price_now=100.0)
    save_committed(committed_path, migrate_committed(baseline, _NOW))
    monkeypatch.setattr(db, "COMMITTED_PATH", committed_path, raising=False)

    result = SimpleNamespace(
        scalar_updates={"your_ira": 654_321, "stock_price": 111},
        joint_conflicts=(),
        account_type_overrides={},
        magi_candidates={},
        slot_captured_at={},
        snapshot=None,
        ytd=None,
        ledger=None,
    )
    ready = SimpleNamespace(
        bundle=None,
        plan=None,
        local=SimpleNamespace(account_type_overrides={}, slot_captured_at={}),
        selected=frozenset(),
        operator=None,
        data={"sections": {"setup_scalars": {}}},
        version="v5",
    )
    monkeypatch.setattr(db, "apply_import", lambda *a, **k: result)
    monkeypatch.setattr(db, "commit_import", lambda *a, **k: None)
    monkeypatch.setattr(db, "slots_with_replaced_accounts", lambda *a: [])
    monkeypatch.setattr(db, "discard_pdf_balance_candidates", lambda *a: None)
    monkeypatch.setattr(db, "_persist_slot_captured_at", lambda *a: None)
    monkeypatch.setattr(db, "_settle_ytd", lambda *a: False)
    monkeypatch.setattr(st, "rerun", lambda: None)
    st.session_state.pop("_pending_defaults", None)

    db._apply_ready_import(ready, "synthetic.enc")

    # Next run: the staged values are drained into the session, then reconciled.
    session_hh = Household(your_ira=654_321, spouse_ira=400_000, stock_price_now=111.0)
    committed = load_committed(committed_path)
    assert committed is not None
    res = resolve_for_app(
        session_hh, None, {}, CandidateStore(), ChoiceMap(), committed, _NOW + timedelta(minutes=1)
    )
    assert res.committed_json["your_ira"]["source"] == "BUNDLE"
    assert res.committed_json["your_ira"]["value"] == 654_321
    assert res.committed_json["stock_price_now"]["source"] == "BUNDLE"
    # Fields the bundle did not change keep their own provenance.
    assert res.committed_json["spouse_ira"]["source"] == "UNKNOWN"


# --- F-22: unconfirmed pending MAGI candidate is not "ok" / confirmable -------


def _render_badge_with_pending_magi() -> None:
    from datetime import datetime, timedelta

    import streamlit as st

    from models.household import Household
    from models.sourced import Provenance, Source, SourcedDict
    from views._shared import render_completeness_badge

    when = datetime.now() - timedelta(days=30)
    hh = Household()
    hh.prior_year_magi = SourcedDict({2019: 123_456.0}, {2019: Provenance(Source.PDF, when, "pdf")})
    st.session_state["_pending_review"] = {"prior_year_magi.2019"}
    render_completeness_badge(hh)


def test_f22_pending_magi_candidate_reads_conflict_and_has_no_confirm_button() -> None:
    at = AppTest.from_function(_render_badge_with_pending_magi)
    at.run()
    assert not at.exception
    table = "\n".join(m.value for m in at.markdown)
    magi_row = next(line for line in table.splitlines() if "Prior-year MAGI (2019)" in line)
    assert "conflict" in magi_row
    labels = [b.label for b in at.button]
    assert not any("Prior-year MAGI (2019)" in label for label in labels)


# --- F-32: governance manual override: no negatives, 0 is expressible ---------


def _pdf_candidate(value: float = 12_345.0) -> list:
    store = CandidateStore()
    store.record_candidate("your_roth", value, Provenance(Source.PDF, _NOW, "stale stmt"))
    return store.candidates_for("your_roth")


def test_f32_manual_zero_is_a_real_override_not_use_selected_source() -> None:
    from views.setup._partials._governance import _resolve_confirm_choice

    picked = _resolve_confirm_choice(_pdf_candidate(), Source.PDF, 0.0)
    assert picked == (0.0, Source.MANUAL, "manual entry")


def test_f32_no_manual_entry_uses_selected_source() -> None:
    from views.setup._partials._governance import _resolve_confirm_choice

    picked = _resolve_confirm_choice(_pdf_candidate(), Source.PDF, None)
    assert picked is not None
    assert picked[:2] == (12_345.0, Source.PDF)


def test_f32_confirm_field_rejects_negative_balance() -> None:
    with pytest.raises(ValueError, match="negative"):
        confirm_field({}, ChoiceMap(), "your_roth", -5000.0, Source.MANUAL, _NOW)


def test_f32_confirm_field_still_allows_negative_magi() -> None:
    committed = confirm_field({}, ChoiceMap(), "prior_year_magi.2023", -5000.0, Source.MANUAL, _NOW)
    assert committed["prior_year_magi"]["data"]["2023"] == -5000.0


def test_f32_confirm_click_with_negative_value_commits_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import views.setup._partials._governance as gov

    committed_path = tmp_path / "committed.json"
    monkeypatch.setattr(gov, "COMMITTED_PATH", committed_path)
    monkeypatch.setattr(gov, "TRUST_CHOICES_PATH", tmp_path / "choices.json")
    committed_json: dict = {}

    ok = gov._handle_confirm_click("your_roth", committed_json, ChoiceMap(), [], None, -5000.0)

    assert ok is False
    assert "your_roth" not in committed_json
    assert not committed_path.exists()


def _seed_pending_roth() -> None:
    from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH

    committed = {"your_roth": SourcedValue(1_000.0, Provenance(Source.UNKNOWN, _NOW)).to_json()}
    COMMITTED_PATH.write_text(json.dumps(committed))
    store = CandidateStore()
    store.record_candidate("your_roth", 12_345.0, Provenance(Source.PDF, _NOW, "stale stmt"))
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def _render_card() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup.command_center import render_command_center

    st.session_state["_pending_review"] = {"your_roth"}
    render_command_center(Household())


def test_f32_widget_has_zero_floor_and_empty_default() -> None:
    _seed_pending_roth()
    at = AppTest.from_function(_render_card)
    at.run()
    assert not at.exception
    manual = at.number_input(key="manual_your_roth")
    assert manual.min == 0
    assert manual.value is None


def test_f32_typed_zero_commits_zero_not_the_stale_candidate() -> None:
    from engine.data_sources.paths import COMMITTED_PATH

    _seed_pending_roth()
    at = AppTest.from_function(_render_card)
    at.run()
    at.number_input(key="manual_your_roth").set_value(0.0)
    at.button(key="confirm_your_roth").click().run()
    assert not at.exception
    committed = load_committed(COMMITTED_PATH)
    assert committed is not None
    assert committed["your_roth"]["value"] == 0.0
    assert committed["your_roth"]["source"] == "MANUAL"
