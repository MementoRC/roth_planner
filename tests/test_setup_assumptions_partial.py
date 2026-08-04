"""Tests for ``views/setup/_partials/_assumptions.py:render_assumptions_partial``
— Task 7 of the ui-shell-theme-toggle plan.

Growth rate, living expenses, ACA benchmark premium / enhanced-subsidies
toggle / advance APTC, Medicare Part B base premium, CPI projection rate,
the prior-year filed-MAGI IRMAA-lookback anchor (+ its own inline
trust/manual/confirm governance card), the survivor-scenario expander, and
the inherited-IRAs expander, extracted out of
``views/setup/parameters.py``'s Joint sub-tab. Uses
``streamlit.testing.v1.AppTest.from_function`` (mirrors
``tests/test_setup_options_partial.py``'s pattern).

Unkeyed-widget safety net (Owner decision 5): the 7 top-level widgets plus
the prior-year-MAGI anchor's two number_inputs are UNKEYED "controlled"
widgets, so a typo'd ``session_state.<attr>`` name during the move would
silently create a NEW session_state attribute rather than raising —
sentinel round-trip tests below catch that. The survivor-scenario and
inherited-IRAs expander widgets ARE explicitly keyed, but their write-through
into the non-widget-bound ``session_state["survivor"]``/
``session_state["inherited_iras"]`` shapes is itself move-prone logic, so
this file's round-trip tests cover those too per the plan's Task 7 item.
"""

from __future__ import annotations

import json
from datetime import datetime

from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from models.sourced import Provenance, Source

_RECORDED_AT = datetime(2026, 7, 24, 12, 0, 0)


def _seed_pending_prior_year_magi_2024() -> None:
    """Committed 2024 MAGI=$200k/UNKNOWN + a Source.PDF $290k candidate."""
    committed_json = {
        "prior_year_magi": {
            "data": {"2024": 200_000.0},
            "prov": {"2024": Provenance(Source.UNKNOWN, _RECORDED_AT).to_json()},
        }
    }
    COMMITTED_PATH.write_text(json.dumps(committed_json))

    store = CandidateStore()
    store.record_candidate(
        "prior_year_magi.2024",
        290_000.0,
        Provenance(Source.PDF, _RECORDED_AT, "Form 1040 PDF"),
    )
    store.save(CANDIDATE_STORE_PATH)
    ChoiceMap().save(TRUST_CHOICES_PATH)


def _render_assumptions_with_pending(pending: set[str]) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_assumptions_partial

    # growth_rate/living_expenses are read via bare session_state attribute
    # access (no .get() fallback) by render_assumptions_partial.
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", 60_000)
    st.session_state["_pending_review"] = pending
    render_assumptions_partial(Household(), st)


def _number_input_by_label(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


def _checkbox_by_label(at: AppTest, label: str):
    return next(w for w in at.checkbox if w.label == label)


def _slider_by_label(at: AppTest, label: str):
    return next(w for w in at.slider if w.label == label)


def test_assumptions_partial_renders_without_exception_when_nothing_pending(
    clean_command_center_caches,
) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception


def test_assumptions_partial_shows_pending_prior_year_magi_candidate(
    clean_command_center_caches,
) -> None:
    _seed_pending_prior_year_magi_2024()

    at = AppTest.from_function(
        _render_assumptions_with_pending, kwargs={"pending": {"prior_year_magi.2024"}}
    )
    at.run()

    assert not at.exception
    rendered_text = "\n".join(m.value for m in at.markdown) + "\n".join(
        c.value for c in at.caption
    )
    assert "290,000" in rendered_text  # the Source.PDF candidate value
    assert "200,000" in rendered_text  # the currently-committed value


def test_assumptions_partial_confirm_prior_year_magi_syncs_session_state(
    clean_command_center_caches,
) -> None:
    """Confirming prior_year_magi.2024 must update BOTH the on-disk committed
    JSON and st.session_state["prior_year_magi"] (int-keyed dict), and clear
    the field from _pending_review — same shape ``_apply_confirm_to_session``
    already guarantees for the other governed fields.
    """
    _seed_pending_prior_year_magi_2024()

    at = AppTest.from_function(
        _render_assumptions_with_pending, kwargs={"pending": {"prior_year_magi.2024"}}
    )
    at.run()
    assert not at.exception

    at.button(key="confirm_prior_year_magi.2024").click().run()

    assert not at.exception
    assert at.session_state["prior_year_magi"][2024] == 290_000.0
    assert "prior_year_magi.2024" not in at.session_state["_pending_review"]

    committed_json = load_committed(COMMITTED_PATH)
    assert committed_json is not None
    assert committed_json["prior_year_magi"]["data"]["2024"] == 290_000.0
    assert committed_json["prior_year_magi"]["prov"]["2024"]["source"] == "PDF"


# --- Unkeyed-widget sentinel round-trip tests (Owner decision 5) ------------


def test_growth_rate_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _slider_by_label(at, "Growth Rate %").set_value(9.5).run()
    assert at.session_state["growth_rate"] == 9.5


def test_living_expenses_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _number_input_by_label(at, "Annual Living Expenses").set_value(72_000).run()
    assert at.session_state["living_expenses"] == 72_000


def test_aca_benchmark_premium_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _number_input_by_label(at, "ACA Benchmark Premium ($/yr)").set_value(18_400).run()
    assert at.session_state["aca_benchmark_premium_annual"] == 18_400


def test_aca_enhanced_subsidies_active_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _checkbox_by_label(at, "ACA enhanced subsidies active (ARP/IRA-style)").set_value(True).run()
    assert at.session_state["aca_enhanced_subsidies_active"] is True


def test_advance_aptc_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _number_input_by_label(at, "Advance APTC ($/yr)").set_value(4_200).run()
    assert at.session_state["advance_aptc_annual"] == 4_200


def test_medicare_part_b_base_monthly_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _number_input_by_label(at, "Medicare Part B Base Premium ($/mo)").set_value(215.75).run()
    assert at.session_state["medicare_part_b_base_monthly"] == 215.75


def test_cpi_assumption_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _number_input_by_label(at, "Annual CPI Projection Rate (0.025 = 2.5%)").set_value(0.031).run()
    assert at.session_state["cpi_assumption"] == 0.031


def test_prior_year_magi_anchor_round_trip(clean_command_center_caches) -> None:
    """Household() defaults base_year=2026, so the anchor's two number_inputs
    are labeled "2024 filed MAGI" / "2025 filed MAGI"."""
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    _number_input_by_label(at, "2024 filed MAGI").set_value(210_000).run()
    _number_input_by_label(at, "2025 filed MAGI").set_value(225_000).run()

    assert at.session_state["prior_year_magi"] == {2024: 210_000.0, 2025: 225_000.0}


# --- Survivor scenario / Inherited IRAs round-trip tests ---------------------


def test_survivor_scenario_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    at.checkbox(key="_survivor_enabled").set_value(True).run()
    at.radio(key="_survivor_who_dies").set_value("Spouse").run()
    at.number_input(key="_survivor_death_year").set_value(2044).run()

    assert at.session_state["survivor"] == {"who_dies": "spouse", "death_year": 2044}


def test_survivor_scenario_disable_clears_scenario(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    at.checkbox(key="_survivor_enabled").set_value(True).run()
    at.checkbox(key="_survivor_enabled").set_value(False).run()

    assert at.session_state["survivor"] is None


def test_inherited_iras_add_and_edit_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    at.button(key="iira_add").click().run()
    assert not at.exception
    assert len(at.session_state["inherited_iras"]) == 1

    at.number_input(key="iira_balance_0").set_value(55_000).run()
    at.number_input(key="iira_year_0").set_value(2030).run()
    at.number_input(key="iira_rate_0").set_value(4.5).run()
    at.radio(key="iira_owner_0").set_value("Spouse").run()

    entry = at.session_state["inherited_iras"][0]
    assert entry["balance"] == 55_000.0
    assert entry["inherited_year"] == 2030
    assert entry["growth_rate"] == 0.045
    assert entry["owner"] == "spouse"


def test_inherited_iras_remove_round_trip(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    at.button(key="iira_add").click().run()
    assert len(at.session_state["inherited_iras"]) == 1

    at.button(key="iira_remove_0").click().run()
    assert not at.exception
    assert at.session_state["inherited_iras"] == []


def test_inherited_iras_remove_preserves_survivor_values(clean_command_center_caches) -> None:
    """audit-0802 F6: removing an early inherited-IRA row must not corrupt the
    surviving rows with the removed row's stale position-keyed widget values."""
    at = AppTest.from_function(_render_assumptions_with_pending, kwargs={"pending": set()})
    at.run()
    assert not at.exception

    at.button(key="iira_add").click().run()
    at.button(key="iira_add").click().run()
    at.button(key="iira_add").click().run()
    assert len(at.session_state["inherited_iras"]) == 3

    at.number_input(key="iira_balance_0").set_value(100_000).run()
    at.number_input(key="iira_balance_1").set_value(250_000).run()
    at.number_input(key="iira_balance_2").set_value(300_000).run()
    assert [e["balance"] for e in at.session_state["inherited_iras"]] == [
        100_000.0,
        250_000.0,
        300_000.0,
    ]

    # remove the FIRST row
    at.button(key="iira_remove_0").click().run()
    assert not at.exception

    assert [e["balance"] for e in at.session_state["inherited_iras"]] == [
        250_000.0,
        300_000.0,
    ], "removing row 0 corrupted survivor balances via stale position-keyed widget state"
