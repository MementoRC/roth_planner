"""Issue #10: the Sweet Spot cost curve and Cost Jump Points are draw-aware.

The cards / vlines already size the 12% and 22% fills against the IRA draw the
waterfall forces (issue #1, #3, #4). The curve used to price every conversion on
the zero-conversion draw, so its bracket steps sat to the right of those fills.
``sweep_all_in`` adds the draw each conversion's own tax provokes.

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.sweet_spot_compute import (
    STEP,
    BaseIncome,
    ConversionResult,
    all_in_at_conversion,
    base_income_for_year,
    compute_marginal_costs,
    fills_for_year,
    find_sweet_spots,
    forced_draw_fills,
    zero_conversion_ira_draws,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import sweep_all_in
from models.household import Household
from tests.test_issue_1_room_matches_fill import (
    BASE_YEAR,
    _fill_provoked_draw_hh,
    _forced_draw_hh,
    _no_draw_hh,
)

CONVS = list(range(0, 400_000 + STEP, STEP))


def _curve(
    hh: Household,
) -> tuple[list[ConversionResult], list[ConversionResult], tuple[float, float], BaseIncome]:
    """(draw-aware sweep, plain sweep, (card fill_12, card fill_22), base)."""
    draws = zero_conversion_ira_draws(hh)
    years = zero_conversion_years(hh)
    fills = forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws=draws)
    base = base_income_for_year(hh, BASE_YEAR, ira_draw=draws[BASE_YEAR])
    aware = sweep_all_in(
        hh,
        base,
        CONVS,
        net_inv_income=0.0,
        ltcg_eligible=0.0,
        zero_year=years[BASE_YEAR],
        fills=fills,
    )
    plain = [all_in_at_conversion(hh, base, c, 0.0, ltcg_eligible=0.0) for c in CONVS]
    return aware, plain, fills_for_year(hh, base, fills), base


def _step_near(results: list[ConversionResult], target: float) -> bool:
    """True when find_sweet_spots reports a jump within one step of ``target``."""
    return any(abs(s.conv - target) <= STEP for s in find_sweet_spots(results))


def _largest_rise_conv(results: list[ConversionResult], lo: float, hi: float) -> float:
    """Conversion of the biggest marginal-cost rise inside (lo, hi)."""
    m = compute_marginal_costs(results).marginals
    rises = [(m[i] - m[i - 1], CONVS[i - 1]) for i in range(2, len(m)) if lo < CONVS[i - 1] < hi]
    return max(rises)[1]


def _first_rise_conv(results: list[ConversionResult], after: float) -> float:
    """Conversion where the marginal cost first rises (> $5 per $1K) past ``after``."""
    m = compute_marginal_costs(results).marginals
    return next(
        CONVS[i - 1] for i in range(2, len(m)) if CONVS[i - 1] > after and m[i] - m[i - 1] > 5
    )


@pytest.mark.parametrize("make_hh", [_forced_draw_hh, _fill_provoked_draw_hh])
def test_curve_steps_sit_at_the_card_fills(make_hh) -> None:  # type: ignore[no-untyped-def]
    hh = make_hh()
    aware, plain, (fill_12, fill_22), _base = _curve(hh)
    assert 0 < fill_12 < fill_22
    # Jump points: 12 -> 22 reported at the 12% fill.
    assert _step_near(aware, fill_12)
    assert _step_near(aware, fill_22)
    # Both bracket steps sit within one step of the card fills on the curve.
    assert abs(_largest_rise_conv(aware, 0.0, (fill_12 + fill_22) / 2) - fill_12) <= STEP
    mid = (fill_12 + fill_22) / 2
    assert abs(_first_rise_conv(aware, mid) - fill_22) <= STEP
    # The old, draw-blind curve put the 22% step to the right of the card.
    assert abs(_first_rise_conv(plain, mid) - fill_22) > STEP


def test_no_draw_year_curve_is_the_plain_list() -> None:
    hh = _no_draw_hh()
    aware, plain, _cards, _base = _curve(hh)
    assert aware == plain


@pytest.mark.parametrize("make_hh", [_forced_draw_hh, _fill_provoked_draw_hh, _no_draw_hh])
def test_deltas_stay_relative_to_zero_conversion(make_hh) -> None:  # type: ignore[no-untyped-def]
    aware, _plain, _cards, _base = _curve(make_hh())
    assert aware[0].conv == 0
    assert aware[0].all_in == 0.0
    assert [r.conv for r in aware] == CONVS
