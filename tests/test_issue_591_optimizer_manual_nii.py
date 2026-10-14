"""Issue #591: the exercise optimizer's own scenario runs ignored the manual
"Additional NII" estimate (Setup › Assumptions). The planning pages price NIIT
on it (audit-1008 F-78), so the optimizer understated NIIT -- and with it each
candidate's cost -- for any household with a manual NII estimate.

All households here are synthetic.
"""

from __future__ import annotations

import copy

import pytest
from streamlit.testing.v1 import AppTest

from engine.exercise_optimizer import _lifetime_total_cost, optimize_exercises
from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_24
from engine.scenario_types import ConversionPlan
from models.household import Household

_NII = 20_000.0


def _no_conversions(hh: Household, ytd: object, net_inv_income: float = 0.0) -> ConversionPlan:
    return ConversionPlan()


# fill-24 pushes MAGI well past the $250k MFJ NIIT threshold, so NII is taxed.
_CEILINGS = [
    ("never-convert", "top-of-12", _no_conversions),
    ("fill-24", "top-of-24", auto_fill_24),
]


def _household() -> Household:
    return Household(
        your_age=62,
        spouse_age=60,
        your_ira=1_200_000,
        spouse_ira=1_200_000,
        brokerage_start=1_500_000,
        living_expenses=60_000,
        grants=[],
    )


def _page_run(hh: Household, candidate, net_inv_income: float):
    """The scenario a planning page runs for this candidate's plan."""
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = candidate.schedule
    return run_scenario(hh_copy, candidate.conversions, end_age=72, net_inv_income=net_inv_income)


def test_candidates_are_priced_with_the_manual_nii_estimate() -> None:
    hh = _household()
    result = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS, net_inv_income=_NII)
    fill = next(c for c in result.candidates if c.ceiling_label == "fill-24")

    page = _page_run(hh, fill, _NII)
    assert sum(yr.niit_cost for yr in page.years) > 0  # the estimate is actually taxed
    # The optimizer's cost for the candidate equals the page scenario's cost.
    assert fill.lifetime_all_in == pytest.approx(_lifetime_total_cost(page.years))


def test_baseline_is_priced_with_the_manual_nii_estimate() -> None:
    hh = _household()
    current = ConversionPlan(your_conversions={hh.base_year: 300_000.0})
    result = optimize_exercises(
        hh, current_plan=current, end_age=72, ceilings=_CEILINGS, net_inv_income=_NII
    )
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = hh.effective_schedule()
    page = run_scenario(hh_copy, current, end_age=72, net_inv_income=_NII)
    assert result.baseline_cost == pytest.approx(_lifetime_total_cost(page.years))


def test_the_estimate_raises_cost_versus_no_estimate() -> None:
    hh = _household()
    with_nii = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS, net_inv_income=_NII)
    without = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS)
    by_label = {c.ceiling_label: c for c in without.candidates}
    fill_with = next(c for c in with_nii.candidates if c.ceiling_label == "fill-24")
    assert fill_with.lifetime_all_in > by_label["fill-24"].lifetime_all_in


def test_default_is_unchanged_for_direct_callers() -> None:
    """No estimate passed: same answer as an explicit 0."""
    hh = _household()
    a = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS)
    b = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS, net_inv_income=0.0)
    assert [c.lifetime_all_in for c in a.candidates] == [c.lifetime_all_in for c in b.candidates]


# --- issue #7: the auto-fill that sizes each candidate gets the estimate too ---


def test_i7_autofill_receives_the_manual_nii_estimate() -> None:
    seen: list[float] = []

    def spy(hh: Household, ytd: object, net_inv_income: float = 0.0) -> ConversionPlan:
        seen.append(net_inv_income)
        return ConversionPlan()

    optimize_exercises(
        _household(), end_age=72, ceilings=[("spy", "top-of-12", spy)], net_inv_income=_NII
    )
    assert seen
    assert all(n == pytest.approx(_NII) for n in seen)


# --- the panel passes the estimate and treats it as an input -----------------


def _render_panel() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.optimizer_panel import render_conversion_half

    render_conversion_half(
        Household(
            your_age=60,
            spouse_age=54,
            base_year=2026,
            your_ira=1_000_000,
            spouse_ira=1_000_000,
            stock_price_now=200.0,
            grants=[
                StockGrant(year=2019, strike=100.0, shares=500, expiry_year=2028, grant_id="g")
            ],
        )
    )


def test_panel_runs_the_optimizer_on_the_manual_nii(monkeypatch) -> None:
    import views.optimizer_panel as panel

    seen: dict[str, float] = {}
    real = panel.optimize_exercises

    def spy(*args, **kwargs):
        seen["nii"] = kwargs.get("net_inv_income", 0.0)
        return real(*args, **kwargs)

    monkeypatch.setattr(panel, "optimize_exercises", spy)
    at = AppTest.from_function(_render_panel, default_timeout=60)
    at.session_state["net_inv_income"] = 20_000
    at.run()
    [b for b in at.button if b.label == "Run optimizer"][0].click()
    at.run()
    assert not at.exception
    assert seen["nii"] == pytest.approx(20_000)


def test_changing_the_estimate_makes_the_cached_result_stale() -> None:
    at = AppTest.from_function(_render_panel, default_timeout=60)
    at.session_state["net_inv_income"] = 0
    at.run()
    [b for b in at.button if b.label == "Run optimizer"][0].click()
    at.run()
    assert "Apply conversions" in [b.label for b in at.button]

    at.session_state["net_inv_income"] = 20_000
    at.run()
    assert not at.exception
    assert "Apply conversions" not in [b.label for b in at.button]
