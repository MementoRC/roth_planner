"""Issue #21: in a forced-draw year EVERY bracket guide-line (and the MAGI lines)
sits where the draw-aware cost curve actually steps.

Issue #4 made only the 12% / 22% lines draw-aware. The curve prices conversion C
at C + D(C), D being the extra IRA draw C's tax forces, so it reaches each higher
bracket earlier than the draw-blind ``bracket_boundary_conversion``.

Synthetic households only.
"""

from __future__ import annotations

import pytest

from engine.sweet_spot_compute import (
    STEP,
    base_income_for_year,
    bracket_vline_positions,
    forced_draw_fills,
    zero_conversion_ira_draws,
    zero_conversion_years,
)
from engine.sweet_spot_draw_curve import (
    draw_aware_boundary,
    draw_aware_bracket_vlines,
    sweep_all_in,
)
from engine.tax import BRACKETS_MFJ
from engine.tax_indexing import index_bracket_list
from models.household import Household
from tests.test_issue_1_room_matches_fill import (
    BASE_YEAR,
    _forced_draw_hh,
    _no_draw_hh,
)

CONVS = list(range(0, 800_000 + STEP, STEP))


def _setup(hh: Household):  # type: ignore[no-untyped-def]
    draws = zero_conversion_ira_draws(hh)
    years = zero_conversion_years(hh)
    fills = forced_draw_fills(hh, ytd=None, net_inv_income=0.0, ira_draws=draws)
    base = base_income_for_year(hh, BASE_YEAR, ira_draw=draws[BASE_YEAR])
    brackets = index_bracket_list(BRACKETS_MFJ, BASE_YEAR, hh.cpi_assumption)
    kwargs = {"net_inv_income": 0.0, "ltcg_eligible": 0.0, "zero_year": years[BASE_YEAR]}
    return base, brackets, fills, kwargs


def _last_conv_within(results, measure, target: float) -> int:  # type: ignore[no-untyped-def]
    """Largest swept conversion whose curve point is still at or below target."""
    return max(r.conv for r in results if measure(r) <= target)


def test_every_bracket_line_sits_at_the_curve_step() -> None:
    hh = _forced_draw_hh()
    base, brackets, fills, kw = _setup(hh)
    curve = sweep_all_in(hh, base, CONVS, fills=fills, **kw)
    lines = draw_aware_bracket_vlines(hh, base, brackets, fills, **kw)
    ceilings = {rate: ceil for ceil, rate in brackets[:-1]}
    assert {rate for _, rate in lines} >= {0.24, 0.32, 0.35}
    for conv, rate in (line for line in lines if line[1] >= 0.24):
        step = _last_conv_within(curve, lambda r: r.taxable_inc, ceilings[rate])
        assert abs(conv - step) <= STEP, (rate, conv, step)


def test_draw_blind_lines_missed_the_curve_step() -> None:
    """Precondition: the old draw-blind placement is off for the higher brackets."""
    hh = _forced_draw_hh()
    base, brackets, fills, kw = _setup(hh)
    curve = sweep_all_in(hh, base, CONVS, fills=fills, **kw)
    ceilings = {rate: ceil for ceil, rate in brackets[:-1]}
    blind = {r: c for c, r in bracket_vline_positions(hh, base, brackets, fills)}
    step = _last_conv_within(curve, lambda r: r.taxable_inc, ceilings[0.24])
    assert blind[0.24] - step > STEP


def test_card_brackets_unchanged() -> None:
    hh = _forced_draw_hh()
    base, brackets, fills, kw = _setup(hh)
    before = {r: c for c, r in bracket_vline_positions(hh, base, brackets, fills)}
    after = {r: c for c, r in draw_aware_bracket_vlines(hh, base, brackets, fills, **kw)}
    assert after[0.12] == before[0.12]
    assert after[0.22] == before[0.22]


def test_magi_boundary_sits_at_the_curve_magi_crossing() -> None:
    hh = _forced_draw_hh()
    base, _brackets, fills, kw = _setup(hh)
    curve = sweep_all_in(hh, base, CONVS, fills=fills, **kw)
    threshold = curve[200].magi  # a MAGI the curve reaches at ~200k converted
    blind = 400_000.0
    conv = draw_aware_boundary(
        hh,
        base,
        blind_boundary=blind,
        target=threshold,
        measure=lambda r: r.magi,
        fills=fills,
        **kw,
    )
    step = _last_conv_within(curve, lambda r: r.magi, threshold)
    assert conv == pytest.approx(step, abs=STEP)


def test_no_draw_year_lines_unchanged() -> None:
    hh = _no_draw_hh()
    base, brackets, fills, kw = _setup(hh)
    assert draw_aware_bracket_vlines(hh, base, brackets, fills, **kw) == bracket_vline_positions(
        hh, base, brackets, fills
    )
    assert (
        draw_aware_boundary(
            hh,
            base,
            blind_boundary=123_456.0,
            target=1.0,
            measure=lambda r: r.magi,
            fills=fills,
            **kw,
        )
        == 123_456.0
    )
