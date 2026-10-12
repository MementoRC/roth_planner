"""The exercise optimizer's terminal IRA tax rate is a household setting.

audit-1008 F-07 / W19 follow-up: the rate used to be an engine constant
(engine.exercise_optimizer.DEFAULT_TERMINAL_IRA_TAX_RATE) that only a
session-only optimizer-panel input could change. It is now the persisted
scalar ``terminal_ira_tax_rate`` (percent): edited under Setup › Assumptions,
seeded/saved/exported like every other scalar, clamped to 0-50%, and the
default for the optimizer panels' input.
"""

import pytest
from streamlit.testing.v1 import AppTest

from config.scalar_registry import (
    SCALAR_REGISTRY,
    TERMINAL_IRA_TAX_RATE_BOUNDS,
    joint_keys,
    scalar_default,
    scalar_keys,
)
from engine.exercise_optimizer import DEFAULT_TERMINAL_IRA_TAX_RATE

_KEY = "terminal_ira_tax_rate"
_INPUT_LABEL = "Tax rate on IRA money left at the end (%)"


# ── registry ────────────────────────────────────────────────────────────────


def test_registered_as_joint_persisted_scalar() -> None:
    assert scalar_default(_KEY) == 24.0
    assert _KEY in scalar_keys()  # exported / imported / seeded
    assert _KEY in joint_keys()  # household-wide, like growth_rate


def test_default_matches_engine_default() -> None:
    assert scalar_default(_KEY) == pytest.approx(DEFAULT_TERMINAL_IRA_TAX_RATE * 100)


def test_bounds() -> None:
    assert TERMINAL_IRA_TAX_RATE_BOUNDS == (0.0, 50.0)
    assert sum(1 for s in SCALAR_REGISTRY if s.session_key == _KEY) == 1


# ── optimizer_settings reads the setting, clamped ───────────────────────────


def _settings_probe() -> None:
    import streamlit as st

    from views.optimizer_panel import optimizer_settings

    st.session_state["_probe"] = optimizer_settings()


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (30.0, 0.30),
        (10, 0.10),
        (80.0, 0.50),  # above the bound -> clamped
        (-5.0, 0.0),  # below the bound -> clamped
        ("junk", 0.24),  # unparseable -> default
        (None, 0.24),  # unset -> default
    ],
)
def test_optimizer_settings_uses_setting(stored, expected) -> None:
    at = AppTest.from_function(_settings_probe)
    at.session_state[_KEY] = stored
    at.run()
    assert not at.exception
    _discount, terminal = at.session_state["_probe"]
    assert terminal == pytest.approx(expected)


# ── panel input defaults to the setting and edits it ────────────────────────


def _render_both_halves() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.optimizer_panel import render_conversion_half, render_exercise_half

    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        your_ira=1_000_000,
        spouse_ira=1_000_000,
        stock_price_now=200.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=5000, expiry_year=2030, grant_id="big")],
    )
    render_conversion_half(hh)
    render_exercise_half(hh)


def _panel_app(rate) -> AppTest:
    at = AppTest.from_function(_render_both_halves, default_timeout=60)
    at.session_state[_KEY] = rate
    return at


def _terminal_inputs(at: AppTest) -> list:
    return [n for n in at.number_input if n.label == _INPUT_LABEL]


def test_panel_inputs_default_to_setting() -> None:
    at = _panel_app(31.0)
    at.run()
    assert not at.exception
    inputs = _terminal_inputs(at)
    assert len(inputs) == 2  # one per half
    assert all(n.value == pytest.approx(31.0) for n in inputs)


def test_panel_out_of_range_setting_is_clamped_not_an_error() -> None:
    at = _panel_app(95.0)
    at.run()
    assert not at.exception
    assert all(n.value == pytest.approx(50.0) for n in _terminal_inputs(at))


def test_panel_edit_changes_the_setting() -> None:
    at = _panel_app(24.0)
    at.run()
    _terminal_inputs(at)[0].set_value(35.0).run()
    assert not at.exception
    assert at.session_state[_KEY] == pytest.approx(35.0)


# ── the setting reaches the optimizer's terminal-tax term ───────────────────


def _baseline_terminal(rate: float) -> float:
    at = _panel_app(rate)
    at.run()
    [b for b in at.button if b.label == "Run optimizer"][0].click()
    at.run()
    assert not at.exception
    result = at.session_state["_auto_opt_result"]
    [baseline] = [c for c in result.candidates if c.ceiling_label == "current"]
    return baseline.terminal_deferred_tax


def test_setting_scales_terminal_deferred_tax() -> None:
    # The baseline candidate's plan does not depend on the rate, so its
    # terminal deferred tax is (pre-tax balance left) x rate: 40% is 4x 10%.
    low = _baseline_terminal(10.0)
    high = _baseline_terminal(40.0)
    assert low > 0
    assert high == pytest.approx(4 * low, rel=1e-9)


# ── Setup › Assumptions is the setting's home ───────────────────────────────


def _render_assumptions() -> None:
    import streamlit as st

    from config.scalar_registry import SCALAR_REGISTRY, scalar_default
    from models.household import Household
    from views.setup._partials._assumptions import render_assumptions_partial

    for spec in SCALAR_REGISTRY:
        st.session_state.setdefault(spec.session_key, scalar_default(spec.session_key))
    st.session_state.setdefault("survivor", None)
    st.session_state.setdefault("inherited_iras", [])
    render_assumptions_partial(Household(your_age=60, spouse_age=54, base_year=2026), st)


def _assumptions_app(rate) -> AppTest:
    at = AppTest.from_function(_render_assumptions, default_timeout=30)
    at.session_state[_KEY] = rate
    return at


def test_assumptions_input_shows_setting() -> None:
    at = _assumptions_app(27.0)
    at.run()
    assert not at.exception
    [inp] = _terminal_inputs(at)
    assert inp.value == pytest.approx(27.0)


def test_assumptions_input_writes_setting() -> None:
    at = _assumptions_app(24.0)
    at.run()
    _terminal_inputs(at)[0].set_value(18.0).run()
    assert not at.exception
    assert at.session_state[_KEY] == pytest.approx(18.0)


def test_assumptions_clamps_imported_out_of_range_value() -> None:
    at = _assumptions_app(120.0)
    at.run()
    assert not at.exception
    [inp] = _terminal_inputs(at)
    assert inp.value == pytest.approx(50.0)
    assert at.session_state[_KEY] == pytest.approx(50.0)
