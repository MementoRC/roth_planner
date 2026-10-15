"""Draw-aware Sweet Spot cost curve (issue #10).

``base_income_for_year(ira_draw=d0)`` prices every swept conversion on the
ZERO-conversion IRA draw. In a year where the waterfall forces a draw, the
conversion's own tax raises the cash need and so forces an extra draw, which is
ordinary income exactly like a conversion. The 12% / 22% cards and guide-lines
already account for it (``scenario_autofill._solve_year``); this module makes
the cost curve do so too, so its bracket steps land where the cards say.

Simplification: the realized gain on the brokerage leg of the waterfall is
preferential income, not ordinary, and is ignored here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from engine.scenario import waterfall_accounts
from engine.scenario_types import YearResult
from engine.sweet_spot_compute import BaseIncome, ConversionResult, all_in_at_conversion
from engine.withdrawal_waterfall import Accounts, solve_waterfall
from models.household import Household

_MAX_PASSES = 10
_TOLERANCE = 0.5


def _no_marginal_tax(_extra_ordinary: float) -> float:
    return 0.0


def _ira_draw(need: float, accounts: Accounts, zero_year: YearResult) -> float:
    """IRA legs the waterfall takes to fund ``need`` (slack and penalty gross-up
    handled by the solver; its marginal tax is zero as in the auto-fill)."""
    draw = solve_waterfall(
        need, accounts, _no_marginal_tax, zero_year.your_age, zero_year.spouse_age
    )
    return draw.your_ira_draw + draw.spouse_ira_draw


def _extra_need(result: ConversionResult) -> float:
    """Cash the conversion adds to the year's need. Excludes LTCG tax (paid from
    the brokerage) and IRMAA (2-year lookback), as the waterfall does."""
    return result.conv_tax + result.niit_delta + result.aca_loss


def sweep_all_in(
    hh: Household,
    base: BaseIncome,
    convs: Sequence[int],
    *,
    net_inv_income: float,
    ltcg_eligible: float,
    zero_year: YearResult,
    fills: Mapping[int, tuple[float, float]],
) -> list[ConversionResult]:
    """All-in cost at each conversion in ``convs``, counting the extra IRA draw
    each conversion's tax forces.

    ``base`` carries the zero-conversion draw. A year with no such draw that the
    Fill does not provoke one in (``fills`` lacks it) is the plain sweep. Else,
    per conversion C, the extra draw D is the fixed point of
    ``D = draw(need0 + cost(C + D)) - draw(need0)`` (warm-started from the
    previous C), and the point is priced at ``C + D`` -- a draw and a conversion
    enter every income aggregate identically -- but reported at ``conv=C`` so
    deltas stay relative to zero conversion.
    """

    def _price(amount: float) -> ConversionResult:
        return all_in_at_conversion(hh, base, amount, net_inv_income, ltcg_eligible=ltcg_eligible)

    zero_draw = zero_year.forced_your_ira_draw + zero_year.forced_spouse_ira_draw
    if zero_draw <= 0.0 and base.year not in fills:
        return [_price(c) for c in convs]

    accounts = waterfall_accounts(zero_year, zero_year.brokerage_balance)
    # The unclamped need (income_needed floors at 0 and splits slack into
    # excess_rmd), net of the penalty the solver grosses up for itself.
    need0 = (
        zero_year.income_needed - zero_year.excess_rmd - zero_year.forced_early_withdrawal_penalty
    )
    draw0 = _ira_draw(need0, accounts, zero_year)

    results: list[ConversionResult] = []
    extra_draw = 0.0
    for conv in convs:
        priced = _price(conv + extra_draw)
        if conv > 0:
            for _ in range(_MAX_PASSES):
                need = need0 + _extra_need(priced)
                new_draw = max(_ira_draw(need, accounts, zero_year) - draw0, 0.0)
                settled = abs(new_draw - extra_draw) < _TOLERANCE
                extra_draw = new_draw
                priced = _price(conv + extra_draw)
                if settled:
                    break
        else:
            extra_draw = 0.0
            priced = _price(0.0)
        results.append(replace(priced, conv=conv))
    return results
