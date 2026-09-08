"""Tests for ``views/setup/_partials/_household.py:render_household_partial``.

Prior to this file, ``render_household_partial`` coverage was
characterization-only (``inspect.getsource`` assertions in
``tests/test_views_setup.py``) — that style cannot prove a render-time crash.
This file adds real ``streamlit.testing.v1.AppTest`` renders (mirrors
``tests/test_setup_accounts_partial.py``'s pattern), specifically for
audit-0823 M3's uncarried FRA-age sites: ``your_fra_age``/``spouse_fra_age``
read ``value=hh.your_fra_age``/``value=hh.spouse_fra_age`` directly (not
through ``_clamp``), so a persisted value outside ``[65, 70]`` (arriving via
``engine.upload_merge.SCALAR_KEYS`` from ``.user_defaults.json`` or an
uploaded bundle) crashes the render with ``StreamlitAPIException`` and no
user interaction.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_household_your(fra_age: int) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_household_partial

    render_household_partial(Household(your_fra_age=fra_age), st, "your")


def _render_household_spouse(fra_age: int) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_household_partial

    st.session_state.setdefault("filing_status", "MFJ")
    render_household_partial(Household(spouse_fra_age=fra_age), st, "spouse")


def _fra_widget(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


# --- audit-0823 M3: unclamped FRA-age value= regression (both bounds) -------
#
# your_fra_age/spouse_fra_age widgets bound [65, 70]. These must FAIL on
# unmodified source with a StreamlitAPIException (value out of min/max).


def test_your_fra_age_clamps_above_max(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_household_your, kwargs={"fra_age": 99})
    at.run()
    assert not at.exception
    assert _fra_widget(at, "Your FRA (Full Retirement Age)").value == 70


def test_your_fra_age_clamps_below_min(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_household_your, kwargs={"fra_age": 10})
    at.run()
    assert not at.exception
    assert _fra_widget(at, "Your FRA (Full Retirement Age)").value == 65


def test_your_fra_age_in_range_value_unchanged(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_household_your, kwargs={"fra_age": 68})
    at.run()
    assert not at.exception
    assert _fra_widget(at, "Your FRA (Full Retirement Age)").value == 68


def test_spouse_fra_age_clamps_above_max(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_household_spouse, kwargs={"fra_age": 99})
    at.run()
    assert not at.exception
    assert _fra_widget(at, "Spouse FRA (Full Retirement Age)").value == 70


def test_spouse_fra_age_clamps_below_min(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_household_spouse, kwargs={"fra_age": 10})
    at.run()
    assert not at.exception
    assert _fra_widget(at, "Spouse FRA (Full Retirement Age)").value == 65


def test_spouse_fra_age_in_range_value_unchanged(clean_command_center_caches) -> None:
    at = AppTest.from_function(_render_household_spouse, kwargs={"fra_age": 66})
    at.run()
    assert not at.exception
    assert _fra_widget(at, "Spouse FRA (Full Retirement Age)").value == 66
