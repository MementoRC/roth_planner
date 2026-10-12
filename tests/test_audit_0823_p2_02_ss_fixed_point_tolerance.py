"""Regression: audit-0823 finding claims/P2-02 — `_nontaxable_ss`'s fixed-point
solve used a FIXED 50-iteration loop with no convergence check, and its own
docstring's claim of "sub-cent precision" was false.

`_nontaxable_ss` solves ``x = other_income - taxable_ss(combined_ss, x)`` by
naive fixed-point iteration. `taxable_ss` is piecewise-linear in `x` with
slopes 0 / 0.5 / 0.85 (the IRC §86 inclusion tiers), so the iteration map
``f(x) = other_income - taxable_ss(combined_ss, x)`` is a contraction with
Lipschitz constant <= 0.85 (the steepest, 85%, tier). Each iteration therefore
shrinks the error by at most 15% — not enough to reach sub-cent precision in
a fixed 50 steps when the initial error is large.

The initial guess is ``x_0 = other_income``, and the true fixed point is
``x* = other_income - taxable``, so the initial error equals the taxable-SS
amount itself — up to ``0.85 * combined_ss`` in the worst case (deep in the
85% tier, uncapped). For a household with $40,000 combined SS sitting in the
phase-in band, that is an initial error on the order of $15,000-$30,000+.
Since 0.85**50 ~= 2.96e-4, a fixed 50 iterations from a ~$34,000 initial
error still leaves an error on the order of $10 — nowhere near sub-cent.
Reaching sub-cent precision (error < $0.01) from that starting point requires
solving 0.85**n * 34_000 < 0.01, i.e. n > log(34_000 / 0.01) / log(1 / 0.85)
~= 97 iterations — roughly double the fixed budget.

This test measures the solver's OWN fixed-point residual from outside the
function: it recomputes `combined_ss` with the same public helpers the
function uses, calls `_nontaxable_ss`, derives the implied `taxable` amount,
and checks whether re-running `taxable_ss` at the implied non-SS income
reproduces that `taxable` amount to the cent. Pre-fix, it does not.
"""

from engine.aca_irmaa_compute import _nontaxable_ss
from engine.ira import ss_benefit_at_age, ss_with_cola
from engine.tax import taxable_ss
from models.household import Household

# Combined SS ~= $40,000/yr (one high earner claiming at 62, spouse not
# claiming) -- the same order of magnitude used in the function's own
# docstring example. 40_000 / 8.4 = the monthly-FRA benefit that produces
# ~$40,000 annual at a 62 claim age (reduction factor 0.7, *12 months).
_YOUR_SS_FRA_MONTHLY = 40_000 / 8.4
_YA = 62
_OTHER_INCOME = 60_000.0
_FILING_STATUS = "MFJ"


def _build_household() -> Household:
    return Household(
        your_age=_YA,
        spouse_age=_YA - 4,
        your_ss_start_age=_YA,
        your_ss_fra=_YOUR_SS_FRA_MONTHLY,
        your_fra_age=67,
        ss_cola=0.025,
        your_aca_enrolled=True,
    )


def _combined_ss(hh: Household) -> float:
    """Recompute combined SS the same way `_nontaxable_ss` does internally
    (lines 85-102 of engine/aca_irmaa_compute.py), using the same public
    helpers, so the test's expectation is derived independently of any
    hardcoded value that could drift from the fixture."""
    your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
    # spouse passes sa=None to _nontaxable_ss below, so spouse contributes 0
    return ss_with_cola(your_ss_base, _YA - hh.your_ss_start_age, hh.ss_cola)


def test_ss_fixed_point_residual_is_subcent() -> None:
    """The solver's own fixed point must be accurate to the cent.

    Pre-fix (fixed 50 iterations, no convergence check), this fails: the
    measured residual is several dollars, not sub-cent, because the initial
    error (~$0.85 * combined_ss) only shrinks by 15% per step.
    """
    hh = _build_household()
    combined_ss = _combined_ss(hh)

    nts = _nontaxable_ss(
        hh,
        _YA,
        None,  # spouse not claiming -- keeps this a single-earner fixed point
        other_income=_OTHER_INCOME,
        filing_status=_FILING_STATUS,
    )

    # Guard against the vacuous early-return path (combined_ss <= 0 -> 0.0),
    # which would make the residual check trivially pass without exercising
    # the iteration at all.
    assert nts > 0.0, (
        f"fixture produced nts={nts!r} <= 0 -- combined_ss={combined_ss!r} is "
        "not actually being drawn; the fixed-point loop was never exercised"
    )

    taxable = combined_ss - nts
    residual = (
        taxable_ss(combined_ss, _OTHER_INCOME - taxable, filing_status=_FILING_STATUS) - taxable
    )

    assert abs(residual) < 0.01, (
        f"fixed-point residual {residual!r} is not sub-cent "
        f"(combined_ss={combined_ss!r}, other_income={_OTHER_INCOME!r}, "
        f"nts={nts!r}, taxable={taxable!r}) -- audit-0823 claims/P2-02: the "
        "fixed 50-iteration loop does not converge to sub-cent precision "
        "from this starting point"
    )


def test_ss_fixed_point_result_is_bounded() -> None:
    """Sanity: non-taxable SS can never be negative or exceed combined SS,
    regardless of solver precision."""
    hh = _build_household()
    combined_ss = _combined_ss(hh)

    nts = _nontaxable_ss(
        hh,
        _YA,
        None,
        other_income=_OTHER_INCOME,
        filing_status=_FILING_STATUS,
    )

    assert 0.0 <= nts <= combined_ss, f"nts={nts!r} out of bounds for combined_ss={combined_ss!r}"
