"""Draw-aware Sweet Spot cost curve (issue #10).

``base_income_for_year(ira_draw=d0)`` prices every swept conversion on the
ZERO-conversion IRA draw. In a year where the waterfall forces a draw, the
conversion's own tax raises the cash need and so forces an extra draw, which is
ordinary income exactly like a conversion. The 12% / 22% cards and guide-lines
already account for it (``scenario_autofill._solve_year``); this module makes
the cost curve do so too, so its bracket steps land where the cards say.

The gain the zero-conversion waterfall realizes by selling brokerage is already
in ``base`` (``BaseIncome.sale_gain``, audit-1018 F-04). Issue #40: the EXTRA
brokerage sale a conversion's tax forces (brokerage is sold before any IRA) is
solved here too, and its gain priced as preferential income
(``all_in_at_conversion(extra_gain=...)``), as engine.scenario folds it into
realized_gains.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from engine.scenario import waterfall_accounts
from engine.scenario_types import YearResult
from engine.sweet_spot_compute import (
    STEP,
    BaseIncome,
    ConversionResult,
    all_in_at_conversion,
    bracket_vline_positions,
    irmaa_safe_max,
)
from engine.withdrawal_waterfall import Accounts, solve_waterfall
from models.household import Household

_MAX_PASSES = 10
_TOLERANCE = 0.5


@dataclass(frozen=True)
class _Legs:
    """Waterfall legs that are income or cost: the IRA draw, the gain realized
    by the brokerage sale and the early-withdrawal penalty on the IRA draw."""

    draw: float = 0.0
    gain: float = 0.0
    penalty: float = 0.0


_NO_EXTRA = _Legs()


def _no_marginal_tax(_extra_ordinary: float) -> float:
    return 0.0


def _waterfall_legs(need: float, accounts: Accounts, zero_year: YearResult) -> _Legs:
    """Legs the waterfall takes to fund ``need`` (slack and penalty gross-up
    handled by the solver; its marginal tax is zero as in the auto-fill)."""
    draw = solve_waterfall(
        need, accounts, _no_marginal_tax, zero_year.your_age, zero_year.spouse_age
    )
    return _Legs(
        draw.your_ira_draw + draw.spouse_ira_draw,
        draw.realized_gain,
        draw.early_withdrawal_penalty,
    )


def _extra_need(result: ConversionResult) -> float:
    """Cash the conversion adds to the year's need. Excludes LTCG tax (paid from
    the brokerage) and IRMAA (2-year lookback), as the waterfall does."""
    return result.conv_tax + result.niit_delta + result.aca_loss


def _split_draw_tax(priced: ConversionResult, own_tax: float, extra: _Legs) -> ConversionResult:
    """Issue #22: split the tax the extra draw adds (conv_tax over the
    conversion's ``own_tax``) by what the draw pays for -- the penalty's share
    of the draw is ``penalty_tax``, the rest ``funding_draw_tax``."""
    draw_tax = priced.conv_tax - own_tax
    penalty_share = extra.penalty / extra.draw if extra.draw > 0.0 else 0.0
    penalty_tax = draw_tax * penalty_share
    return replace(priced, funding_draw_tax=draw_tax - penalty_tax, penalty_tax=penalty_tax)


def sweep_all_in(
    hh: Household,
    base: BaseIncome,
    convs: Sequence[float],
    *,
    net_inv_income: float,
    ltcg_eligible: float,
    zero_year: YearResult,
    fills: Mapping[int, tuple[float, float]],
) -> list[ConversionResult]:
    """All-in cost at each conversion in ``convs``, counting the extra IRA draw
    and brokerage sale each conversion's tax forces.

    ``base`` carries the zero-conversion draw. A year with no such draw or
    gain-realizing sale that the Fill does not provoke a draw in (``fills``
    lacks it) is the plain sweep. Else, per conversion C, the extra draw D (and
    sale gain G) is the fixed point of ``D = draw(need0 + cost(C + D, G)) -
    draw(need0)`` (warm-started from the previous C), and the point is priced at
    ``C + D`` -- a draw and a conversion enter every income aggregate
    identically -- with G as preferential income, but reported at ``conv=C`` so
    deltas stay relative to zero conversion. Issue #22: the tax D adds is
    reported on ``funding_draw_tax`` / ``penalty_tax`` (parts of conv_tax).
    """

    def _price(amount: float) -> ConversionResult:
        return all_in_at_conversion(hh, base, amount, net_inv_income, ltcg_eligible=ltcg_eligible)

    solve = _draw_solver(hh, base, net_inv_income, ltcg_eligible, zero_year, fills)
    if solve is None:
        return [_price(c) for c in convs]

    results: list[ConversionResult] = []
    extra = _NO_EXTRA
    for conv in convs:
        if conv > 0:
            priced, extra = solve(conv, extra)
            priced = _split_draw_tax(priced, _price(conv).conv_tax, extra)
        else:
            extra = _NO_EXTRA
            priced = _price(0.0)
        results.append(replace(priced, conv=conv))
    return results


_DrawSolver = Callable[[float, _Legs], tuple[ConversionResult, _Legs]]


def _draw_solver(
    hh: Household,
    base: BaseIncome,
    net_inv_income: float,
    ltcg_eligible: float,
    zero_year: YearResult,
    fills: Mapping[int, tuple[float, float]],
) -> _DrawSolver | None:
    """``solve(conv, extra_guess) -> (priced at conv + draw, extra legs)``, or
    None when the year has no forced draw, no gain-realizing sale and the Fill
    provokes no draw."""
    zero_draw = zero_year.forced_your_ira_draw + zero_year.forced_spouse_ira_draw
    sells_at_gain = zero_year.forced_brokerage_draw > 0.0 and base.sale_gain_fraction > 0.0
    if zero_draw <= 0.0 and not sells_at_gain and base.year not in fills:
        return None

    basis = zero_year.brokerage_balance * (1.0 - base.sale_gain_fraction)
    accounts = waterfall_accounts(zero_year, basis)
    # The unclamped need (income_needed floors at 0 and splits slack into
    # excess_rmd), net of the penalty the solver grosses up for itself.
    need0 = (
        zero_year.income_needed - zero_year.excess_rmd - zero_year.forced_early_withdrawal_penalty
    )
    legs0 = _waterfall_legs(need0, accounts, zero_year)

    def _price(amount: float, gain: float) -> ConversionResult:
        return all_in_at_conversion(
            hh, base, amount, net_inv_income, ltcg_eligible=ltcg_eligible, extra_gain=gain
        )

    def _extra(need: float) -> _Legs:
        legs = _waterfall_legs(need, accounts, zero_year)
        return _Legs(
            max(legs.draw - legs0.draw, 0.0),
            max(legs.gain - legs0.gain, 0.0),
            max(legs.penalty - legs0.penalty, 0.0),
        )

    def solve(conv: float, extra: _Legs) -> tuple[ConversionResult, _Legs]:
        priced = _price(conv + extra.draw, extra.gain)
        for _ in range(_MAX_PASSES):
            new = _extra(need0 + _extra_need(priced))
            settled = (
                abs(new.draw - extra.draw) < _TOLERANCE and abs(new.gain - extra.gain) < _TOLERANCE
            )
            extra = new
            priced = _price(conv + extra.draw, extra.gain)
            if settled:
                break
        return priced, extra

    return solve


_CARD_RATES = (0.12, 0.22)


def draw_aware_bracket_vlines(
    hh: Household,
    base: BaseIncome,
    indexed_brackets: Sequence[tuple[float, float]],
    fills: Mapping[int, tuple[float, float]],
    *,
    net_inv_income: float,
    ltcg_eligible: float,
    zero_year: YearResult,
) -> list[tuple[float, float]]:
    """Issue #21: :func:`bracket_vline_positions` with every bracket above the
    card brackets (12% / 22%, already the cards' draw-aware fills) moved to
    where the draw-aware curve reaches its ceiling. Unchanged in a no-draw year.
    """
    blind = bracket_vline_positions(hh, base, indexed_brackets, fills)
    positions: list[tuple[float, float]] = []
    for (ceil, _), (conv, rate) in zip(indexed_brackets, blind, strict=False):
        if not any(abs(rate - r) < 1e-9 for r in _CARD_RATES):
            conv = draw_aware_boundary(
                hh,
                base,
                blind_boundary=conv,
                target=ceil,
                measure=lambda r: r.taxable_inc,
                net_inv_income=net_inv_income,
                ltcg_eligible=ltcg_eligible,
                zero_year=zero_year,
                fills=fills,
            )
        positions.append((conv, rate))
    return positions


def draw_aware_irmaa_safe_max(
    hh: Household,
    base: BaseIncome,
    irmaa_tier1_threshold: float,
    net_inv_income: float,
    ltcg_eligible: float,
    *,
    zero_year: YearResult,
    fills: Mapping[int, tuple[float, float]],
) -> float:
    """audit-1018 F-05: :func:`irmaa_safe_max` with the extra IRA draw the
    conversion's own tax forces -- the largest STEP-aligned C whose draw-aware
    MAGI (priced at C + D(C), the curve's own pricing) stays at or under the
    tier. The draw-blind answer is the upper bound (the draw only adds MAGI) and
    the answer when the year has no forced draw.
    """
    blind = irmaa_safe_max(hh, base, irmaa_tier1_threshold, net_inv_income, ltcg_eligible)
    solve = _draw_solver(hh, base, net_inv_income, ltcg_eligible, zero_year, fills)
    if solve is None or blind <= 0.0:
        return blind
    lo, hi = 0, int(blind // STEP)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if solve(float(mid * STEP), _NO_EXTRA)[0].magi <= irmaa_tier1_threshold:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return float(best * STEP)


def draw_aware_boundary(
    hh: Household,
    base: BaseIncome,
    *,
    blind_boundary: float,
    target: float,
    measure: Callable[[ConversionResult], float],
    net_inv_income: float,
    ltcg_eligible: float,
    zero_year: YearResult,
    fills: Mapping[int, tuple[float, float]],
) -> float:
    """Issue #21: the conversion at which the draw-aware curve's ``measure``
    (taxable income, MAGI, ...) reaches ``target``.

    The curve prices conversion C at C + D(C), D being the extra IRA draw C's tax
    forces, so it reaches every threshold earlier than the draw-blind
    ``blind_boundary`` (which is therefore an upper bound and the answer when the
    year has no draw). Bisects on C with the curve's own pricing.
    """
    solve = _draw_solver(hh, base, net_inv_income, ltcg_eligible, zero_year, fills)
    if solve is None or blind_boundary <= 0.0:
        return blind_boundary
    lo, hi = 0.0, blind_boundary
    for _ in range(40):
        mid = (lo + hi) / 2
        if measure(solve(mid, _NO_EXTRA)[0]) <= target:
            lo = mid
        else:
            hi = mid
    return lo
