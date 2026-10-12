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

SATURATION is a SEPARATE, ROUTINE condition and is reported separately, on
``ira_leg_saturated``. The iterate is clamped to the total available IRA
balance, so once the required draw exceeds what the IRAs hold, successive
candidates are pinned at that ceiling and stop changing. That is not a
numerical failure -- it simply means the IRA leg is spent and the waterfall
moves on to the Roth, which is the ordinary behaviour of a waterfall, not an
error. Conflating it with ``converged`` made a fully-funded year (IRAs
exhausted, Roth covering the rest, ``unfunded == 0``) report failure.

The one case where saturation DOES defeat convergence is when the ceiling
bound AND no later account could make up the difference: the tolerance test
would otherwise declare success at a draw that never satisfied the need.
Saturation is therefore judged on the UNCLAMPED candidate, and only
downgrades ``converged`` when the shortfall survives every account (i.e.
``unfunded > 0``).

Flag meanings, precisely:

* ``converged == False`` -- the gross-up iteration did not settle (it
  oscillated or hit ``max_iterations``), OR it settled only by pinning
  against the IRA ceiling while leaving the household genuinely short. It
  does NOT fire merely because the IRAs ran out.
* ``ira_leg_saturated == True`` -- the IRA ceiling bound. INFORMATIONAL:
  routine in any year funded partly from the Roth, and not a failure.
* ``unfunded > 0`` -- the economic failure signal. See below.

Reported shortfall
------------------
``unfunded > 0`` means the household genuinely could not raise the cash --
every account is exhausted. This, NOT ``converged``, is the signal a
consumer should test to detect a plan failure. It does NOT fire for the
sub-dollar residue the
``tolerance`` test leaves behind: that residue is swept by drawing slightly
more from whatever balance remains (see ``_MAX_TOPUP_PASSES``), so the
dollars really do leave an account rather than being clamped out of the
arithmetic. Consumers may therefore treat ``unfunded > 0`` as a true
plan-failure signal.
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
    # The IRA ceiling bound during the gross-up. Routine and informational --
    # any year funded partly from the Roth saturates the IRA leg by
    # construction. Kept distinct from `converged` so a successful
    # multi-account year is not mistaken for a solver failure.
    ira_leg_saturated: bool = False


# Half a cent: below this a residual is arithmetic noise from `tolerance`, not
# money the household actually failed to raise.
_SWEEP_EPSILON = 0.005
# Each top-up pass costs a `tax_of` call, which re-runs a whole projection
# year, so the sweep is bounded. Three passes take a sub-dollar residue to
# well under a cent.
_MAX_TOPUP_PASSES = 5


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


def allocate_ira_draw(
    draw: float,
    your_ira: float,
    spouse_ira: float,
    your_age: float,
    spouse_age: float,
    penalty_age: float = 59.5,
) -> tuple[float, float]:
    """Split a total traditional-IRA draw across the two owners.

    Penalty-free balances are drawn before penalty-exposed ones (ties go to
    "your"), and each leg clamps to its own balance.

    Exported because any caller that must PREDICT the split has to use this
    exact rule. In particular the ``tax_of`` probe in ``engine.scenario``
    previously attributed a whole speculative draw to "your" IRA; once that
    probe was clamped to the balance it truncated whenever "your" IRA was the
    smaller one, so the probe's tax no longer described the draw that would
    actually be taken -- and swapping the two households changed which IRA was
    smaller, breaking me/spouse symmetry. One ordering rule, one copy: a
    second implementation is free to drift from this one, and did.
    """
    your_free = your_age >= penalty_age
    spouse_free = spouse_age >= penalty_age

    if your_free == spouse_free:
        # Genuine tie: both penalty-free, or both exposed. Sequencing by LABEL
        # here ("your" first) would make the result depend on which person is
        # called "you" -- two households identical but for the labels would
        # drain different IRAs, and since the two accounts carry different
        # growth rates the combined balances then diverge. That breaks the
        # me/spouse parity invariants. Split pro-rata by balance instead: it is
        # label-independent, so swapping the two people allocates identically.
        total = your_ira + spouse_ira
        if total <= 0:
            return 0.0, 0.0
        taken = _clamp(draw, 0.0, total)
        return taken * (your_ira / total), taken * (spouse_ira / total)

    # Not a tie: draw the penalty-free balance first. Driven by AGE, not by
    # label, so it is already symmetric under a swap.
    order = _ira_order(your_age, spouse_age, penalty_age)
    balances = {"your": your_ira, "spouse": spouse_ira}
    remaining = draw
    result = {"your": 0.0, "spouse": 0.0}
    for owner in order:
        take = _clamp(remaining, 0.0, balances[owner])
        result[owner] = take
        remaining -= take
    return result["your"], result["spouse"]


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

    free = {"your": your_age >= penalty_age, "spouse": spouse_age >= penalty_age}
    total_ira_available = accounts.your_ira + accounts.spouse_ira

    def allocate(draw: float) -> dict[str, float]:
        your_take, spouse_take = allocate_ira_draw(
            draw,
            accounts.your_ira,
            accounts.spouse_ira,
            your_age,
            spouse_age,
            penalty_age,
        )
        return {"your": your_take, "spouse": spouse_take}

    def exposed_amount(alloc: dict[str, float]) -> float:
        return sum(amt for owner, amt in alloc.items() if not free[owner])

    iterations = 0
    converged = True
    ira_leg_saturated = False
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
            candidate_raw = remaining_need + tax + pen
            candidate = _clamp(candidate_raw, 0.0, total_ira_available)
            if abs(candidate - d) < tolerance:
                d = candidate
                # The iteration settled. Whether it settled on a genuine
                # fixed point or merely pinned against the IRA ceiling is a
                # SEPARATE question, recorded on `ira_leg_saturated`: a
                # candidate at the ceiling stops changing because it cannot
                # grow, not because a fixed point was found.
                #
                # Saturation alone is ROUTINE -- it just means the IRA leg is
                # spent and the Roth funds the remainder. Only if the
                # shortfall survives every later account too (checked after
                # the Roth draw below) does it defeat convergence.
                converged = True
                ira_leg_saturated = candidate_raw > total_ira_available
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

    # Top-up sweep. The fixed point above only converges to within `tolerance`
    # (a dollar), so the solved draw can leave a sub-dollar shortfall even when
    # the IRA still holds plenty. Reporting that as `unfunded` is wrong: any
    # consumer testing `unfunded > 0` reads it as a funding FAILURE, and a
    # seven-cent rounding residue is not one.
    #
    # The residue is swept by actually drawing MORE -- never by clamping the
    # number to zero and calling the need met. Each pass re-grosses for the tax
    # and penalty on the extra dollars, so a few passes drive the shortfall
    # under a cent. The loop is bounded because every `tax_of` call re-runs a
    # full projection year.
    if remaining_need > 0 and remaining_after_ira > _SWEEP_EPSILON:
        for _ in range(_MAX_TOPUP_PASSES):
            headroom = total_ira_available - ira_draw
            if remaining_after_ira <= _SWEEP_EPSILON or headroom <= _SWEEP_EPSILON:
                break
            ira_draw = _clamp(ira_draw + remaining_after_ira, 0.0, total_ira_available)
            final_alloc = allocate(ira_draw)
            penalty = penalty_rate * exposed_amount(final_alloc)
            your_ira_draw = final_alloc["your"]
            spouse_ira_draw = final_alloc["spouse"]
            tax_on_draw = tax_of(ira_draw)
            net_spendable = ira_draw - tax_on_draw - penalty
            remaining_after_ira = max(0.0, remaining_need - net_spendable)

    roth_available = accounts.your_roth + accounts.spouse_roth
    roth_draw = _clamp(remaining_after_ira, 0.0, roth_available)
    unfunded = max(0.0, remaining_after_ira - roth_draw)
    # `unfunded > 0` must IMPLY every account is exhausted. Sub-cent residue
    # left when balances remain is arithmetic noise from the tolerance, not a
    # funding failure, so it is not reported as one.
    if unfunded <= _SWEEP_EPSILON:
        unfunded = 0.0

    # Saturation that no later account could rescue is the one case where
    # pinning against the ceiling really does defeat convergence: the
    # tolerance test would otherwise declare success at a draw that never
    # satisfied the need. Saturation WITH the Roth covering the remainder is
    # a fully funded year and stays converged.
    if ira_leg_saturated and unfunded > 0:
        converged = False

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
        ira_leg_saturated=ira_leg_saturated,
    )
