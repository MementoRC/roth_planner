"""audit-1008 W19 (F-07): the exercise optimizer ranked candidates on the
undiscounted nominal sum of yearly tax and ignored the tax still owed on IRA
money left at the end of the projection. A plan that simply never converted
looked cheapest even when it left millions of pre-tax dollars -- and their
deferred tax -- to the survivor or heirs.

User decision D2: rank on lifetime tax PLUS the deferred tax on terminal
pre-tax balances, with an optional discount rate that defaults to 0.

All households here are synthetic.
"""

from __future__ import annotations

import copy

import pytest

from engine.exercise_optimizer import (
    DEFAULT_TERMINAL_IRA_TAX_RATE,
    _lifetime_total_cost,
    _ranking_score,
    _terminal_deferred_tax,
    optimize_exercises,
)
from engine.scenario import run_scenario
from engine.scenario_autofill import auto_fill_22
from engine.scenario_types import ConversionPlan, YearResult
from models.household import Household


def _no_conversions(hh: Household, ytd: object, net_inv_income: float = 0.0) -> ConversionPlan:
    return ConversionPlan()


_CEILINGS = [
    ("never-convert", "top-of-12", _no_conversions),
    ("fill-22", "top-of-22", auto_fill_22),
]


def _household() -> Household:
    """Large pre-tax balances, enough brokerage to live on, no grants."""
    return Household(
        your_age=62,
        spouse_age=60,
        your_ira=1_200_000,
        spouse_ira=1_200_000,
        brokerage_start=1_500_000,
        living_expenses=60_000,
        grants=[],
    )


def _by_label(result, label):
    return next(c for c in result.candidates if c.ceiling_label == label)


# --- the reported failure ----------------------------------------------------


def test_never_converting_no_longer_wins_by_deferring_tax_past_the_horizon() -> None:
    """With a horizon that ends before RMDs, never converting pays almost no
    tax inside the projection and so had the lowest nominal lifetime cost --
    while leaving the whole pre-tax balance untaxed at the end."""
    result = optimize_exercises(_household(), end_age=72, ceilings=_CEILINGS)
    never = _by_label(result, "never-convert")
    fill = _by_label(result, "fill-22")

    # The old objective (nominal lifetime cost) prefers never converting ...
    assert never.lifetime_all_in < fill.lifetime_all_in
    # ... but it leaves far more deferred tax behind ...
    assert never.terminal_deferred_tax > fill.terminal_deferred_tax + never.lifetime_all_in
    # ... so the optimizer now picks the converting plan.
    assert result.best.ceiling_label == "fill-22"


def test_terminal_deferred_tax_matches_the_final_pre_tax_balances() -> None:
    hh = _household()
    result = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS)
    never = _by_label(result, "never-convert")
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = never.schedule
    final = run_scenario(hh_copy, never.conversions, end_age=72).years[-1]
    pre_tax = (
        final.your_ira_end
        + final.spouse_ira_end
        + final.your_inherited_balance_end
        + final.spouse_inherited_balance_end
    )
    assert pre_tax > 0
    assert never.terminal_deferred_tax == pytest.approx(pre_tax * DEFAULT_TERMINAL_IRA_TAX_RATE)


def test_terminal_tax_rate_is_configurable() -> None:
    hh = _household()
    low = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS, terminal_tax_rate=0.10)
    high = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS, terminal_tax_rate=0.37)
    assert _by_label(high, "never-convert").terminal_deferred_tax == pytest.approx(
        _by_label(low, "never-convert").terminal_deferred_tax * 3.7
    )
    # A zero rate reproduces the old ranking: never converting wins again.
    zero = optimize_exercises(hh, end_age=72, ceilings=_CEILINGS, terminal_tax_rate=0.0)
    assert zero.best.ceiling_label in ("never-convert", "current")


# --- discount rate -----------------------------------------------------------


def test_zero_discount_score_is_the_nominal_sum_plus_terminal_tax() -> None:
    result = optimize_exercises(_household(), end_age=80, ceilings=_CEILINGS)
    for c in result.candidates:
        assert c.score == pytest.approx(c.lifetime_all_in + c.terminal_deferred_tax)
    assert result.baseline_score == pytest.approx(
        _by_label(result, "current").lifetime_all_in
        + _by_label(result, "current").terminal_deferred_tax
    )
    assert result.best.score == min(c.score for c in result.candidates)


def test_positive_discount_rate_discounts_each_year_to_the_base_year() -> None:
    hh = _household()
    rate = 0.06
    result = optimize_exercises(hh, end_age=80, ceilings=_CEILINGS, discount_rate=rate)
    current = _by_label(result, "current")
    hh_copy = copy.deepcopy(hh)
    hh_copy.exercise_schedule = current.schedule
    years = run_scenario(hh_copy, current.conversions, end_age=80).years
    expected = sum(
        (
            yr.federal_tax_amt
            + yr.irmaa_cost
            + yr.aca_loss
            + yr.niit_cost
            + yr.conversion_ltcg_cost
            + yr.forced_early_withdrawal_penalty
        )
        / (1 + rate) ** (yr.year - hh.base_year)
        for yr in years
    ) + current.terminal_deferred_tax / (1 + rate) ** (years[-1].year + 1 - hh.base_year)
    assert current.score == pytest.approx(expected)
    assert result.baseline_score == pytest.approx(expected)
    # Discounting never raises a score; the nominal figures are unchanged.
    nominal = optimize_exercises(hh, end_age=80, ceilings=_CEILINGS)
    for c in result.candidates:
        n = _by_label(nominal, c.ceiling_label)
        assert c.score < n.score
        assert c.lifetime_all_in == pytest.approx(n.lifetime_all_in)
    assert result.best.score == min(c.score for c in result.candidates)


def test_discounting_can_flip_the_ranking_toward_later_costs() -> None:
    """Paying $100 now vs $150 in 30 years: undiscounted the early payment is
    cheaper; at 5% the later one is (150 / 1.05**30 ~= 34.7)."""
    early = [YearResult(year=2026, your_age=60, spouse_age=60, phase="clean", federal_tax_amt=100)]
    early += [
        YearResult(year=y, your_age=60, spouse_age=60, phase="clean") for y in range(2027, 2057)
    ]
    late = [
        YearResult(year=y, your_age=60, spouse_age=60, phase="clean") for y in range(2026, 2056)
    ]
    late.append(YearResult(year=2056, your_age=90, spouse_age=90, phase="rmd", federal_tax_amt=150))

    assert _ranking_score(early, 2026, 0.0, 0.0) < _ranking_score(late, 2026, 0.0, 0.0)
    assert _ranking_score(early, 2026, 0.05, 0.0) > _ranking_score(late, 2026, 0.05, 0.0)
    assert _ranking_score(late, 2026, 0.05, 0.0) == pytest.approx(150 / 1.05**30)


def test_terminal_tax_is_discounted_from_the_year_after_the_horizon() -> None:
    years = [YearResult(year=y, your_age=60, spouse_age=60, phase="clean") for y in (2026, 2027)]
    assert _ranking_score(years, 2026, 0.10, 121.0) == pytest.approx(121.0 / 1.1**2)
    assert _ranking_score([], 2026, 0.10, 50.0) == 50.0


def test_discount_rate_at_or_below_minus_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="discount_rate"):
        optimize_exercises(_household(), end_age=70, ceilings=_CEILINGS, discount_rate=-1.0)


# --- cost terms --------------------------------------------------------------


def test_early_withdrawal_penalty_counts_as_a_cost() -> None:
    yr = YearResult(
        year=2026,
        your_age=55,
        spouse_age=55,
        phase="clean",
        federal_tax_amt=1_000,
        forced_early_withdrawal_penalty=250,
    )
    assert _lifetime_total_cost([yr]) == 1_250


def test_terminal_deferred_tax_counts_inherited_balances_but_not_roth() -> None:
    final = YearResult(
        year=2060,
        your_age=90,
        spouse_age=90,
        phase="rmd",
        your_ira_end=100_000,
        spouse_ira_end=50_000,
        your_inherited_balance_end=20_000,
        spouse_inherited_balance_end=30_000,
        your_roth_end=1_000_000,
        spouse_roth_end=1_000_000,
    )
    assert _terminal_deferred_tax([final], 0.24) == pytest.approx(200_000 * 0.24)
    assert _terminal_deferred_tax([], 0.24) == 0.0


# --- Auto-Optimizer panel ----------------------------------------------------


def _render_both_halves() -> None:
    from models.grants import StockGrant
    from models.household import Household
    from views.optimizer_panel import render_conversion_half, render_exercise_half

    hh = Household(
        your_age=60,
        spouse_age=54,
        base_year=2026,
        your_ira=400_000,
        spouse_ira=400_000,
        stock_price_now=150.0,
        grants=[StockGrant(year=2019, strike=100.0, shares=500, expiry_year=2028, grant_id="g1")],
    )
    render_conversion_half(hh)
    render_exercise_half(hh)


def _panel_app():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_function(_render_both_halves, default_timeout=60)


def _spy(monkeypatch) -> list[dict]:
    import views.optimizer_panel as panel

    calls: list[dict] = []
    real = panel.optimize_exercises

    def spy(hh, **kwargs):
        calls.append(kwargs)
        return real(hh, **kwargs)

    monkeypatch.setattr("views.optimizer_panel.optimize_exercises", spy)
    return calls


def test_panel_passes_default_settings_to_the_optimizer(monkeypatch) -> None:
    calls = _spy(monkeypatch)
    at = _panel_app()
    at.run()
    [b for b in at.button if b.label == "Run optimizer"][0].click()
    at.run()
    assert not at.exception
    assert calls[-1]["discount_rate"] == 0.0
    assert calls[-1]["terminal_tax_rate"] == pytest.approx(DEFAULT_TERMINAL_IRA_TAX_RATE)
    headers = list(at.dataframe[0].value.columns)
    assert "Tax left in IRAs at end" in headers
    assert "Ranking score" in headers


def test_panel_settings_are_shared_and_passed_as_fractions(monkeypatch) -> None:
    calls = _spy(monkeypatch)
    at = _panel_app()
    at.run()
    discount_inputs = [n for n in at.number_input if n.label == "Discount rate (%/yr)"]
    assert len(discount_inputs) == 2
    discount_inputs[0].set_value(5.0)
    at.run()
    [n for n in at.number_input if n.label.startswith("Tax rate on IRA")][1].set_value(30.0)
    at.run()
    assert not at.exception
    # Both halves show the shared setting.
    assert [n.value for n in at.number_input if n.label == "Discount rate (%/yr)"] == [5.0, 5.0]
    [b for b in at.button if b.label == "Run optimizer"][1].click()
    at.run()
    assert calls[-1]["discount_rate"] == pytest.approx(0.05)
    assert calls[-1]["terminal_tax_rate"] == pytest.approx(0.30)


def test_changing_a_setting_marks_the_result_stale() -> None:
    at = _panel_app()
    at.run()
    [b for b in at.button if b.label == "Run optimizer"][0].click()
    at.run()
    assert [b for b in at.button if b.label.startswith("Apply")]
    [n for n in at.number_input if n.label == "Discount rate (%/yr)"][0].set_value(3.0)
    at.run()
    assert not at.exception
    # The cached result was computed at 0%, so it is no longer shown or Applied.
    assert not [b for b in at.button if b.label.startswith("Apply")]
