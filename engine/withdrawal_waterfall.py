"""Pure IRA-withdrawal-waterfall solver.

Purpose
-------
When after-tax income does not cover living expenses, funds must be drawn
from accounts in a fixed order: brokerage first, then traditional IRA
(penalty-free balances before penalty-exposed balances), then Roth. A
traditional-IRA draw is itself ordinary income, so it raises the tax that
created the shortfall -- a fixed point (draw -> tax -> larger draw).
``solve_waterfall`` resolves that fixed point via successive substitution.

This module is PURE: no Streamlit, no ``engine.scenario`` import, no I/O.

Non-convergence
----------------
``tax_of`` may be discontinuous (e.g. an ACA subsidy cliff), so successive
substitution can enter a perpetual oscillation instead of converging. When
``max_iterations`` is reached without the change dropping below
``tolerance``, ``solve_waterfall`` sets ``converged=False`` and reports the
CONSERVATIVE (larger-draw) side of the oscillation -- it never silently
picks the cheaper branch, since underestimating the draw would leave the
household short of cash.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Accounts:
    brokerage: float
    brokerage_basis_fraction: float
    your_ira: float
    spouse_ira: float
    your_roth: float
    spouse_roth: float


@dataclass
class WaterfallDraw:
    brokerage_draw: float
    realized_gain: float
    your_ira_draw: float
    spouse_ira_draw: float
    early_withdrawal_penalty: float
    roth_draw: float
    unfunded: float
    iterations: int
    converged: bool


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def _ira_order(your_age: float, spouse_age: float, penalty_age: float) -> list[str]:
    """Rank the two IRA owners: penalty-free before penalty-exposed.

    Ties (both free or both exposed) resolve to 'your' first.
    """
    your_free = your_age >= penalty_age
    spouse_free = spouse_age >= penalty_age
    if your_free == spouse_free or your_free:
        return ["your", "spouse"]
    return ["spouse", "your"]


def solve_waterfall(
    need: float,
    accounts: Accounts,
    tax_of: Callable[[float], float],
    your_age: float,
    spouse_age: float,
    penalty_age: float = 59.5,
    penalty_rate: float = 0.10,
    max_iterations: int = 50,
    tolerance: float = 1.0,
) -> WaterfallDraw:
    if need <= 0:
        return WaterfallDraw(
            brokerage_draw=0.0,
            realized_gain=0.0,
            your_ira_draw=0.0,
            spouse_ira_draw=0.0,
            early_withdrawal_penalty=0.0,
            roth_draw=0.0,
            unfunded=0.0,
            iterations=0,
            converged=True,
        )

    # 1. Brokerage first -- not ordinary income, not passed to tax_of.
    brokerage_draw = _clamp(need, 0.0, accounts.brokerage)
    realized_gain = brokerage_draw * (1 - accounts.brokerage_basis_fraction)
    remaining_need = need - brokerage_draw

    order = _ira_order(your_age, spouse_age, penalty_age)
    balances = {"your": accounts.your_ira, "spouse": accounts.spouse_ira}
    free = {"your": your_age >= penalty_age, "spouse": spouse_age >= penalty_age}
    total_ira_available = accounts.your_ira + accounts.spouse_ira

    def allocate(draw: float) -> dict[str, float]:
        remaining = draw
        result = {}
        for owner in order:
            take = _clamp(remaining, 0.0, balances[owner])
            result[owner] = take
            remaining -= take
        return result

    def exposed_amount(alloc: dict[str, float]) -> float:
        return sum(amt for owner, amt in alloc.items() if not free[owner])

    iterations = 0
    converged = True
    ira_draw = 0.0
    your_ira_draw = 0.0
    spouse_ira_draw = 0.0
    penalty = 0.0

    if remaining_need > 0:
        d = _clamp(remaining_need, 0.0, total_ira_available)
        prev_d = d
        converged = False
        for i in range(1, max_iterations + 1):
            iterations = i
            alloc = allocate(d)
            exposed = exposed_amount(alloc)
            tax = tax_of(d)
            pen = penalty_rate * exposed
            candidate = _clamp(remaining_need + tax + pen, 0.0, total_ira_available)
            if abs(candidate - d) < tolerance:
                d = candidate
                converged = True
                break
            prev_d = d
            d = candidate
        if not converged:
            d = max(d, prev_d)

        final_alloc = allocate(d)
        exposed = exposed_amount(final_alloc)
        ira_draw = d
        penalty = penalty_rate * exposed
        your_ira_draw = final_alloc["your"]
        spouse_ira_draw = final_alloc["spouse"]

    tax_on_draw = tax_of(ira_draw) if remaining_need > 0 else 0.0
    net_spendable = ira_draw - tax_on_draw - penalty
    remaining_after_ira = max(0.0, remaining_need - net_spendable)

    roth_available = accounts.your_roth + accounts.spouse_roth
    roth_draw = _clamp(remaining_after_ira, 0.0, roth_available)
    unfunded = max(0.0, remaining_after_ira - roth_draw)

    return WaterfallDraw(
        brokerage_draw=brokerage_draw,
        realized_gain=realized_gain,
        your_ira_draw=your_ira_draw,
        spouse_ira_draw=spouse_ira_draw,
        early_withdrawal_penalty=penalty,
        roth_draw=roth_draw,
        unfunded=unfunded,
        iterations=iterations,
        converged=converged,
    )
