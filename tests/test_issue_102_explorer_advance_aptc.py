"""Issue #102: the ACA + IRMAA Explorer takes an advance-APTC what-if.

audit-1101 W4 (F-03) added ``aca_allowed_ptc()``: a household that ends the
year below 100% FPL after receiving advance APTC is still allowed a credit
(Treas. Reg. §1.36B-2(b)(6)) instead of repaying the whole advance. The
Explorer priced every point with ``aca_net_cost`` and no advance input, so
that case never showed. ``compute_cost_curves`` now takes ``advance_aptc``
and prices the credit, net cost, loss and the Form 8962 reconciliation with
``aca_allowed_ptc``. Zero advance reproduces the old curves exactly.

Synthetic figures only.
"""

from __future__ import annotations

import dataclasses

import pytest
from streamlit.testing.v1 import AppTest

from engine.aca import aca_allowed_ptc, aca_net_cost, aca_subsidy, fpl_for
from engine.aca_irmaa_compute import compute_cost_curves
from models.household import Household

YEAR = 2026
CPI = 0.025
BENCHMARK = 21_600.0


def _aca_hh(**overrides: object) -> Household:
    kw: dict = {
        "your_age": 60,
        "spouse_age": 60,
        "your_aca_enrolled": True,
        "spouse_aca_enrolled": True,
        "aca_benchmark_premium_annual": BENCHMARK,
        "aca_enhanced_subsidies_active": False,
        "grants": [],
    }
    kw.update(overrides)
    return Household(**kw)


def _points() -> list[float]:
    fpl = fpl_for("MFJ", YEAR, CPI)
    return [0.5 * fpl, 0.8 * fpl, 1.0 * fpl, 1.5 * fpl, 3.0 * fpl, 4.5 * fpl]


def _curves(hh: Household, base_magi: float, **kw: object):
    return compute_cost_curves(_points(), base_magi, 0.0, hh, year=YEAR, cpi=CPI, **kw)


def test_zero_advance_reproduces_the_curves_without_the_input() -> None:
    hh = _aca_hh()
    base = 0.8 * fpl_for("MFJ", YEAR, CPI)
    omitted = _curves(hh, base)
    zero = _curves(hh, base, advance_aptc=0.0)
    assert dataclasses.asdict(omitted) == dataclasses.asdict(zero)
    # The pre-#102 formula, point by point (no SS, so ACA MAGI == swept MAGI).
    for magi, sub, net in zip(
        _points(), zero.aca_subsidy_vals, zero.aca_net_cost_vals, strict=True
    ):
        assert sub == aca_subsidy(magi, BENCHMARK, False, "MFJ", year=YEAR, cpi=CPI)
        assert net == aca_net_cost(magi, BENCHMARK, False, "MFJ", year=YEAR, cpi=CPI)
    assert zero.aca_repayment_vals == [0.0] * len(_points())


def test_below_fpl_with_advance_is_allowed_a_credit() -> None:
    hh = _aca_hh()
    advance = 15_000.0
    fpl = fpl_for("MFJ", YEAR, CPI)
    curves = _curves(hh, 0.8 * fpl, advance_aptc=advance)
    i = _points().index(0.8 * fpl)
    allowed = aca_allowed_ptc(
        0.8 * fpl, BENCHMARK, False, "MFJ", year=YEAR, cpi=CPI, advance_aptc_paid=True
    )
    assert allowed > 0
    assert curves.aca_subsidy_vals[i] == pytest.approx(allowed)
    # Before #102 the Explorer said the full premium was owed here.
    assert curves.aca_net_cost_vals[i] == pytest.approx(BENCHMARK - allowed)
    assert curves.aca_net_cost_vals[i] < BENCHMARK
    assert curves.aca_repayment_vals[i] == pytest.approx(advance - allowed)


def test_at_or_above_fpl_advance_changes_only_the_reconciliation() -> None:
    hh = _aca_hh()
    base = 1.5 * fpl_for("MFJ", YEAR, CPI)
    advance = 5_000.0
    without = _curves(hh, base)
    withadv = _curves(hh, base, advance_aptc=advance)
    for i, magi in enumerate(_points()):
        if magi < fpl_for("MFJ", YEAR, CPI):
            continue
        assert withadv.aca_subsidy_vals[i] == pytest.approx(without.aca_subsidy_vals[i])
        assert withadv.aca_net_cost_vals[i] == pytest.approx(without.aca_net_cost_vals[i])
        # Positive = repaid at filing (no cap for TY 2026+), negative = extra credit.
        assert withadv.aca_repayment_vals[i] == pytest.approx(advance - without.aca_subsidy_vals[i])
    # Above the 400% cliff the whole advance is repaid.
    assert withadv.aca_repayment_vals[-1] == pytest.approx(advance)


def test_subsidy_loss_measures_from_the_allowed_base_credit() -> None:
    hh = _aca_hh()
    fpl = fpl_for("MFJ", YEAR, CPI)
    base = 0.8 * fpl
    advance = 15_000.0
    curves = _curves(hh, base, advance_aptc=advance)
    base_allowed = aca_allowed_ptc(
        base, BENCHMARK, False, "MFJ", year=YEAR, cpi=CPI, advance_aptc_paid=True
    )
    i = _points().index(3.0 * fpl)
    point_sub = aca_subsidy(3.0 * fpl, BENCHMARK, False, "MFJ", year=YEAR, cpi=CPI)
    assert curves.aca_subsidy_loss_vals[i] == pytest.approx(max(base_allowed - point_sub, 0.0))
    assert curves.total_hidden_cost_vals[i] >= curves.aca_subsidy_loss_vals[i]


def test_negative_advance_is_clamped_to_zero() -> None:
    hh = _aca_hh()
    base = 0.8 * fpl_for("MFJ", YEAR, CPI)
    assert dataclasses.asdict(_curves(hh, base, advance_aptc=-5_000.0)) == dataclasses.asdict(
        _curves(hh, base)
    )


def test_nobody_on_aca_has_no_reconciliation() -> None:
    hh = _aca_hh(your_aca_enrolled=False, spouse_aca_enrolled=False)
    curves = _curves(hh, 50_000.0, advance_aptc=10_000.0)
    assert curves.aca_repayment_vals == [0.0] * len(_points())
    assert curves.aca_net_cost_vals == [0.0] * len(_points())


# --------------------------------------------------------------------------- view


def _render(kw: dict, session: dict | None = None) -> AppTest:
    def _script(kw: dict, session: dict) -> None:
        import streamlit as st

        import views.aca_irmaa as page
        from models.household import Household

        for k, v in session.items():
            st.session_state.setdefault(k, v)
        page.render(Household(**kw))

    at = AppTest.from_function(_script, args=(kw, session or {}), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


_VIEW_KW = {
    "your_age": 60,
    "spouse_age": 60,
    "your_aca_enrolled": True,
    "spouse_aca_enrolled": True,
    "grants": [],
}


def _aptc_box(at: AppTest):
    return next(n for n in at.number_input if "advance aptc" in n.label.lower())


def test_view_advance_input_defaults_to_household_value_and_floors_at_zero() -> None:
    at = _render({**_VIEW_KW, "advance_aptc_annual": 6_000.0})
    box = _aptc_box(at)
    assert box.value == 6_000
    assert box.min == 0


def test_view_explains_advance_aptc() -> None:
    at = _render(_VIEW_KW)
    text = " ".join(c.value for c in at.caption)
    assert "advance APTC" in text
    assert "Form 8962" in text


def test_view_advance_input_does_not_write_the_profile_value() -> None:
    """A what-if: Setup › Assumptions stays the one home of the saved value."""
    at = _render(_VIEW_KW, {"advance_aptc_annual": 0})
    _aptc_box(at).set_value(12_000).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["advance_aptc_annual"] == 0


def test_view_hides_the_advance_input_when_nobody_is_on_aca() -> None:
    at = _render({**_VIEW_KW, "your_aca_enrolled": False, "spouse_aca_enrolled": False})
    assert not [n for n in at.number_input if "advance aptc" in n.label.lower()]
