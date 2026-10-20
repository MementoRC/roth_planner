"""Issue #40 (Sweet Spot prices the brokerage-sale gain like run_scenario) and
issue #22 (the marginal chart's Fed Tax trace is split into the conversion's own
tax, the tax-funding draw and the 10% penalty gross-up).

Synthetic households only.
"""

from __future__ import annotations

from typing import Any

import pytest

import engine.sweet_spot_compute as ssc
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan, YearResult
from engine.sweet_spot_compute import (
    STEP,
    BaseIncome,
    ConversionResult,
    all_in_at_conversion,
    base_income_for_year,
    compute_marginal_costs,
    compute_multi_year_summary,
    estimate_ltcg_eligible,
    fill_conversions_for_year,
    forced_draw_fills,
    sweet_spot_ltcg_eligible,
    zero_conversion_gain_fractions,
    zero_conversion_sale_gains,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import _NO_EXTRA, _draw_solver, sweep_all_in
from models.household import Household
from tests.test_issue_1_room_matches_fill import BASE_YEAR, _hh


def _sale_hh() -> Household:
    """Spending is funded by selling low-basis brokerage (80% gain): no IRA draw."""
    return _hh(brokerage_start=200_000.0, brokerage_start_basis=40_000.0)


def _young_hh() -> Household:
    """Both under 59 1/2, no brokerage: every IRA draw is penalty-exposed."""
    return Household(
        your_age=50,
        spouse_age=48,
        base_year=BASE_YEAR,
        cpi_assumption=0.0,
        grants=[],
        brokerage_start=0.0,
        living_expenses=60_000.0,
        your_ira=900_000.0,
        spouse_ira=700_000.0,
        your_ss_fra=0.0,
        spouse_ss_fra=0.0,
    )


def _setup(
    hh: Household,
) -> tuple[dict[int, YearResult], dict[int, tuple[float, float]], BaseIncome]:
    years = zero_conversion_years(hh)
    draws = {y: yr.forced_your_ira_draw + yr.forced_spouse_ira_draw for y, yr in years.items()}
    gains = zero_conversion_sale_gains(hh, years)
    fills = forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws=draws, sale_gains=gains)
    base = base_income_for_year(
        hh,
        BASE_YEAR,
        ira_draw=draws[BASE_YEAR],
        sale_gain=gains[BASE_YEAR],
        sale_gain_fraction=zero_conversion_gain_fractions(hh, years)[BASE_YEAR],
    )
    return years, fills, base


def _sweep(hh: Household, convs: list[float], ltcg_eligible: float = 0.0) -> list[ConversionResult]:
    years, fills, base = _setup(hh)
    return sweep_all_in(
        hh,
        base,
        convs,
        net_inv_income=0.0,
        ltcg_eligible=ltcg_eligible,
        zero_year=years[BASE_YEAR],
        fills=fills,
    )


def _scenario_year(hh: Household, conv: float) -> YearResult:
    plan = ConversionPlan(your_conversions={BASE_YEAR: conv}, spouse_conversions={BASE_YEAR: 0.0})
    return run_scenario(hh, plan, "oracle", end_age=95).years[0]


# --------------------------------------------------------------------------
# Issue #40 (1): the conversion-forced brokerage sale
# --------------------------------------------------------------------------


def test_precondition_sale_year_conversion_sells_more_brokerage() -> None:
    hh = _sale_hh()
    zero = _scenario_year(hh, 0.0)
    conv = _scenario_year(hh, 100_000.0)
    assert zero.forced_your_ira_draw + zero.forced_spouse_ira_draw == 0.0
    assert conv.forced_your_ira_draw + conv.forced_spouse_ira_draw == 0.0
    assert conv.forced_brokerage_draw > zero.forced_brokerage_draw + 5_000.0


@pytest.mark.parametrize("conv", [60_000.0, 100_000.0, 150_000.0])
def test_sale_year_curve_magi_matches_run_scenario(conv: float) -> None:
    hh = _sale_hh()
    _years, _fills, base = _setup(hh)
    aware = _sweep(hh, [0.0, conv])[1]
    oracle = _scenario_year(hh, conv)
    assert aware.magi == pytest.approx(oracle.magi, abs=25.0)
    # The draw-blind price misses the gain on the extra sale.
    blind = all_in_at_conversion(hh, base, conv, 0.0)
    assert aware.magi > blind.magi + 1_000.0


def test_sale_year_curve_prices_the_gain_as_preferential() -> None:
    hh = _sale_hh()
    _years, _fills, base = _setup(hh)
    conv = 100_000.0
    aware = _sweep(hh, [0.0, conv])[1]
    blind = all_in_at_conversion(hh, base, conv, 0.0)
    # No Social Security: the gain cannot reach ordinary taxable income.
    assert aware.taxable_inc == pytest.approx(blind.taxable_inc, abs=0.01)
    assert aware.conv_tax == pytest.approx(blind.conv_tax, abs=0.01)


def test_sale_year_ltcg_delta_matches_run_scenario_gain_tax() -> None:
    hh = _sale_hh()
    base = _setup(hh)[2]
    le = sweet_spot_ltcg_eligible(hh, base, None)
    conv = 150_000.0
    aware = _sweep(hh, [0.0, conv], le)[1]
    with_conv = _scenario_year(hh, conv).brokerage_gain_tax
    oracle = with_conv - _scenario_year(hh, 0.0).brokerage_gain_tax
    assert oracle > 0.0
    assert aware.ltcg_delta == pytest.approx(oracle, abs=25.0)


def test_full_basis_sale_year_stays_the_plain_sweep() -> None:
    hh = _hh(brokerage_start=2_000_000.0)  # sells at full basis: no gain
    years, fills, base = _setup(hh)
    assert _draw_solver(hh, base, 0.0, 0.0, years[BASE_YEAR], fills) is None


# --------------------------------------------------------------------------
# Issue #40 (2): the sale gain stacks with the year's LTCG
# --------------------------------------------------------------------------


def test_ltcg_eligible_includes_the_sale_gain() -> None:
    hh = _sale_hh()
    _years, _fills, base = _setup(hh)
    assert base.sale_gain > 0.0
    got = sweet_spot_ltcg_eligible(hh, base, None)
    assert got == pytest.approx(estimate_ltcg_eligible(hh, BASE_YEAR) + base.sale_gain)


def test_summary_ltcg_stacking_prices_the_sale_gain() -> None:
    hh = _sale_hh()
    base = _setup(hh)[2]
    row = compute_multi_year_summary(hh, include_ltcg_stacking=True)[0]
    forecast = estimate_ltcg_eligible(hh, BASE_YEAR)
    curve = _sweep(hh, [row.fill_22], forecast + base.sale_gain)[0]
    without_gain = _sweep(hh, [row.fill_22], forecast)[0]
    assert row.cost_22 == pytest.approx(curve.all_in, abs=1.0)
    assert curve.ltcg_delta > without_gain.ltcg_delta + 100.0


# --------------------------------------------------------------------------
# Issue #40 (3): the fill-provoked gate uses the cards' base
# --------------------------------------------------------------------------


def test_fill_provoked_gate_sizes_fills_on_the_sale_gain_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hh = _sale_hh()
    years = zero_conversion_years(hh)
    draws = {y: yr.forced_your_ira_draw + yr.forced_spouse_ira_draw for y, yr in years.items()}
    gains = zero_conversion_sale_gains(hh, years)
    assert gains[BASE_YEAR] > 0.0
    bases: list[BaseIncome] = []
    plans: list[ConversionPlan] = []
    real_base, real_run = ssc.base_income_for_year, ssc.run_scenario

    def _spy_base(*args: Any, **kwargs: Any) -> BaseIncome:
        bases.append(real_base(*args, **kwargs))
        return bases[-1]

    def _spy_run(h: Household, plan: ConversionPlan, *args: Any, **kwargs: Any) -> Any:
        plans.append(plan)
        return real_run(h, plan, *args, **kwargs)

    monkeypatch.setattr(ssc, "base_income_for_year", _spy_base)
    monkeypatch.setattr(ssc, "run_scenario", _spy_run)
    ssc._fill_provoked_draw_years(hh, None, 0.0, draws, gains)
    by_year = {b.year: b for b in bases}
    for year, gain in gains.items():
        assert by_year[year].sale_gain == gain
    # The gate's base is the cards' base, so its fill is the cards' fill.
    cards = real_base(hh, BASE_YEAR, ira_draw=draws[BASE_YEAR], sale_gain=gains[BASE_YEAR])
    assert by_year[BASE_YEAR].base_magi == pytest.approx(cards.base_magi)
    expected = fill_conversions_for_year(hh, cards)[1]
    assert plans[0].your_conversions[BASE_YEAR] == pytest.approx(expected)


# --------------------------------------------------------------------------
# Issue #22: Fed Tax / Tax-Funding Draw / 10% Penalty Gross-Up
# --------------------------------------------------------------------------

CONVS = [float(c) for c in range(0, 300_000 + STEP, STEP)]


def test_young_forced_draw_precondition() -> None:
    yr = zero_conversion_years(_young_hh())[BASE_YEAR]
    assert yr.forced_your_ira_draw + yr.forced_spouse_ira_draw > 0.0
    assert yr.forced_early_withdrawal_penalty > 0.0


def test_12_bracket_split_own_tax_funding_draw_and_real_penalty() -> None:
    # Draw D = rC / (0.9 - r): at r = 12%, D = 15.38% of C.
    m = compute_marginal_costs(_sweep(_young_hh(), CONVS))
    i = CONVS.index(10_000.0)
    draw_per_1k = 1000 * 0.12 / (0.9 - 0.12)
    assert m.marginal_tax[i] == pytest.approx(120.0, abs=0.5)
    assert m.marginal_funding_draw[i] == pytest.approx(0.12 * draw_per_1k, abs=0.5)  # 1.85%
    # Fed tax + funding draw is the old Fed Tax trace, rate / (1 - rate / 0.9).
    old_fed = m.marginal_tax[i] + m.marginal_funding_draw[i]
    assert old_fed == pytest.approx(120.0 / (1 - 0.12 / 0.9), abs=0.5)  # 13.85%
    # The penalty is the real 10% of the draw.
    assert m.marginal_penalty[i] == pytest.approx(0.1 * draw_per_1k, abs=0.5)  # 1.54%
    assert m.marginals[i] == pytest.approx(draw_per_1k, abs=0.5)  # 15.38% == D / C


def test_components_sum_to_the_new_total_which_adds_the_penalty() -> None:
    hh = _young_hh()
    results = _sweep(hh, CONVS)
    m = compute_marginal_costs(results)
    for i in range(1, len(results)):
        cur, prev = results[i], results[i - 1]
        fed = (cur.conv_tax - prev.conv_tax) / STEP * 1000
        assert m.marginal_tax[i] + m.marginal_funding_draw[i] == pytest.approx(fed, abs=1e-6)
        parts = fed + m.marginal_penalty[i] + m.marginal_irmaa[i] + m.marginal_aca[i]
        assert parts + m.marginal_niit[i] + m.marginal_ltcg[i] == pytest.approx(
            m.marginals[i], abs=1e-6
        )
    # New total - old (penalty-free) total == the penalty, at every point.
    years, fills, base = _setup(hh)
    solve = _draw_solver(hh, base, 0.0, 0.0, years[BASE_YEAR], fills)
    assert solve is not None
    for r in results[1::25]:
        priced, extra = solve(r.conv, _NO_EXTRA)
        old = all_in_at_conversion(hh, base, r.conv + extra.draw, 0.0, extra_gain=extra.gain)
        assert extra.penalty > 0.0
        assert priced.all_in - old.all_in == pytest.approx(extra.penalty, abs=1e-6)
        assert r.penalty_tax == pytest.approx(extra.penalty, abs=1.0)
        assert r.all_in == pytest.approx(priced.all_in, abs=1.0)


def test_no_draw_year_has_no_split_traces() -> None:
    m = compute_marginal_costs(_sweep(_hh(brokerage_start=2_000_000.0), CONVS))
    assert not any(m.marginal_funding_draw)
    assert not any(m.marginal_penalty)


def test_penalty_free_draw_has_no_penalty_trace() -> None:
    # 61 / 55: the waterfall draws the penalty-free (61) IRA first.
    m = compute_marginal_costs(_sweep(_hh(), CONVS[:40]))
    assert any(v > 0 for v in m.marginal_funding_draw)
    assert not any(m.marginal_penalty)
