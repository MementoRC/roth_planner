"""audit-1026 F-18: a derived RMD start age follows the birth year.

A stored 73 used to be read as an explicit choice, and the Setup selectbox wrote
the derived 73 back into session_state on every render, so it froze: correcting
ages 70/68 -> 60/58 left 73/73 instead of 75/75. Only a value the user picked
(marked by ``<person>_rmd_age_chosen``) may persist.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from config.scalar_registry import scalar_default
from engine.upload_merge import SCALAR_KEYS, build_user_defaults_session_updates
from models.household import Household, rmd_age_kwargs

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
BASE = 2026


def _household(state: dict) -> Household:
    """Session state -> Household, the way app.get_household() builds it."""
    return Household(
        your_age=state["your_age"],
        spouse_age=state["spouse_age"],
        base_year=BASE,
        **rmd_age_kwargs(state, "your"),
        **rmd_age_kwargs(state, "spouse"),
    )


def _setup_write_back(state: dict, hh: Household) -> None:
    """What the Setup selectboxes do on render when the user touches nothing."""
    from views.setup._partials._household import rmd_choice_write_back

    for who in ("your", "spouse"):
        rmd_choice_write_back(state, who, None)


def test_age_correction_rederives_rmd_start_age() -> None:
    state = {"your_age": 70, "spouse_age": 68}
    hh = _household(state)
    assert (hh.your_rmd_start_age, hh.spouse_rmd_start_age) == (73, 73)
    _setup_write_back(state, hh)

    state.update(your_age=60, spouse_age=58)
    hh = _household(state)
    assert (hh.your_rmd_start_age, hh.spouse_rmd_start_age) == (75, 75)


def test_legacy_persisted_73_loads_as_auto() -> None:
    # Old file: 73 stored, no *_chosen marker, person born 1966.
    state = {"your_age": 60, "spouse_age": 58, "your_rmd_start_age": 73, "spouse_rmd_start_age": 73}
    hh = _household(state)
    assert (hh.your_rmd_start_age, hh.spouse_rmd_start_age) == (75, 75)


def test_explicit_choice_persists_across_rebuild() -> None:
    state = {"your_age": 71, "spouse_age": 60}  # born 1955: derived 73
    from views.setup._partials._household import rmd_choice_write_back

    rmd_choice_write_back(state, "your", 75)
    rmd_choice_write_back(state, "spouse", 73)  # born 1966, explicit 73
    for _ in range(2):
        hh = _household(state)
        assert (hh.your_rmd_start_age, hh.spouse_rmd_start_age) == (75, 73)
    # Picking Auto again returns to the birth-year rule.
    rmd_choice_write_back(state, "your", None)
    assert _household(state).your_rmd_start_age == 73


def test_household_defaults_unchanged() -> None:
    assert Household(your_age=60).your_rmd_start_age == 75
    assert Household(your_age=70).your_rmd_start_age == 73


def test_chosen_marker_in_registry_and_export() -> None:
    assert scalar_default("your_rmd_age_chosen") is False
    assert "spouse_rmd_age_chosen" in SCALAR_KEYS
    updates = build_user_defaults_session_updates({"your_rmd_age_chosen": True}, as_spouse=True)
    assert updates["spouse_rmd_age_chosen"] is True


def test_setup_page_age_change_does_not_freeze_rmd_age() -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    at.session_state["_suppress_snapshot_autoload"] = True
    at.session_state["your_age"] = 70
    at.session_state["spouse_age"] = 68
    at.run()
    assert not at.exception
    at.session_state["your_age"] = 60
    at.session_state["spouse_age"] = 58
    at.run()
    assert not at.exception
    assert at.session_state["your_rmd_age_chosen"] is False
    assert at.session_state["spouse_rmd_age_chosen"] is False
    assert at.session_state["your_rmd_start_age"] != 73
