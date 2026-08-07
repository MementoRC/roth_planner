"""Pinning tests for brokerage cost-basis bookkeeping (audit-0805 C8,
engine/scenario.py:1010-1015, :884-913).

There is NO bug here -- engine/scenario.py:1011-1015 already correctly
resolves:

    brokerage_basis = (
        hh.brokerage_start_basis
        if hh.brokerage_start_basis is not None
        else hh.brokerage_start
    )

The point of this file is that BOTH branches were previously untested, and
the None-vs-0.0 distinction is load-bearing: a careless
`hh.brokerage_start_basis or hh.brokerage_start` would silently treat an
explicit $0 basis (100% unrealized gain, e.g. gifted/inherited-with-
carryover-basis-of-zero brokerage holdings) the same as "basis unknown,
assume full basis" (no gain at all) -- a real and easy-to-reintroduce
regression that pure type-checking would not catch (0.0 is a valid float in
both branches).
"""

from __future__ import annotations

import pytest

from engine.scenario import ConversionPlan, run_scenario
from models.household import Household


def approx(expected: float, tol: float = 0.01) -> object:
    return pytest.approx(expected, abs=tol)


def _bare_household(**overrides: object) -> Household:
    """MFJ household mirroring tests/test_audit_0805_c8_expense_debit.py's
    fixture style: every income source zeroed by default (no RMDs -- age 61
    is below RMD start -- no grants, no Social Security) and
    growth_rate=0.0 / brok_turnover=0.0 / expense_inflation=0.0 so brokerage
    growth, forecast dividends, and realized LTCG are all exactly 0.0 unless
    a test explicitly opts back in via overrides.
    """
    base: dict[str, object] = {
        "grants": [],
        "your_age": 61,
        "spouse_age": 61,
        "your_ira": 1_000_000.0,
        "spouse_ira": 0.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "filing_status": "MFJ",
        "base_year": 2026,
        "growth_rate": 0.0,
        "brok_turnover": 0.0,
        "expense_inflation": 0.0,
        "brokerage_start": 0.0,
        "brokerage_start_basis": None,
        "living_expenses": 0.0,
    }
    base.update(overrides)
    return Household(**base)  # type: ignore[arg-type]


class TestNoneResolvesToFullBasis:
    """brokerage_start_basis=None means 'basis unknown' -- resolve to the
    full starting balance (no unrealized gain assumed)."""

    def test_none_basis_resolves_to_full_brokerage_start(self) -> None:
        hh = _bare_household(brokerage_start=100_000.0, brokerage_start_basis=None)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "basis-none", end_age=62)

        yr0 = result.years[0]
        assert yr0.brokerage_basis == approx(100_000.0)


class TestExplicitZeroBasisIsHonored:
    """brokerage_start_basis=0.0 is an EXPLICIT claim of zero cost basis --
    distinct from None ("unknown"). This is exactly the case a naive
    `hh.brokerage_start_basis or hh.brokerage_start` resolution would
    silently break: 0.0 is falsy in Python, so `or` would fall through to
    the full brokerage_start and erase the explicit zero-basis claim. This
    test exists specifically to catch that falsy-zero `or`-style regression
    -- it is the reason this file exists.
    """

    def test_explicit_zero_basis_stays_zero_not_full_balance(self) -> None:
        hh = _bare_household(brokerage_start=100_000.0, brokerage_start_basis=0.0)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "basis-zero", end_age=62)

        yr0 = result.years[0]
        assert yr0.brokerage_basis == approx(0.0), (
            f"Expected explicit brokerage_start_basis=0.0 to be honored as "
            f"zero cost basis, got {yr0.brokerage_basis:.2f} -- a falsy-zero "
            f"`or` resolution would silently substitute the full "
            f"brokerage_start (100000.00) instead of the explicit 0.00"
        )


class TestContributionsAddToBasisAppreciationDoesNot:
    """A year with a brokerage surplus (excess_rmd > 0) must add the
    contribution to BOTH balance and basis; that same year's price
    appreciation (brokerage_growth) must add to balance only.
    brokerage_start_basis is set well below brokerage_start (a pre-existing
    unrealized gain) so the basis<=balance invariant has headroom before
    this year's contribution even lands -- see
    TestBasisNeverExceedsBalanceAcrossFullRun for why that headroom matters:
    YearResult.brokerage_balance is the BEGIN-of-year balance (see
    engine/scenario.py:824, and the "year1 begin brokerage" framing in
    tests/test_audit_0805_c8_expense_debit.py) while
    YearResult.brokerage_basis is computed as an END-of-year quantity
    (engine/scenario.py:913) after that year's own contribution is folded
    in, so the same-year comparison only holds when prior-year headroom
    covers the new contribution.
    """

    def test_excess_rmd_year_grows_basis_by_contribution_only(self) -> None:
        hh = _bare_household(
            brokerage_start=100_000.0,
            brokerage_start_basis=60_000.0,
            growth_rate=0.05,
            your_ira=100_000.0,
            living_expenses=15_000.0,
        )
        # extra_withdrawal=$20,000 is fully absorbed by the $32,200 MFJ
        # standard deduction (taxable_income = max(20000-32200, 0) = 0), so
        # federal_tax_amt=0.00 and available_income=20,000.00 exactly.
        # living_expenses=$15,000 -> excess_rmd = 20,000-15,000 = $5,000.00
        # (the contribution), income_needed = 0.00.
        plan = ConversionPlan(extra_withdrawals={2026: 20_000.0})
        result = run_scenario(hh, plan, "basis-contrib", end_age=62)

        yr0 = result.years[0]
        assert yr0.excess_rmd == approx(5_000.0)
        assert yr0.income_needed == approx(0.0)

        # Basis rose by exactly the $5,000 contribution: 60,000 -> 65,000.
        assert yr0.brokerage_basis == approx(65_000.0)

        # Balance rose by the contribution ($5,000) PLUS untaxed price
        # appreciation ($100,000 * 5% growth_rate = $5,000) -- MORE than the
        # contribution alone. The next year's begin-of-year brokerage_balance
        # carries this year's ending balance forward.
        yr1_begin_balance = result.years[1].brokerage_balance
        balance_delta = yr1_begin_balance - 100_000.0
        assert balance_delta == approx(10_000.0)
        assert balance_delta > 5_000.0

        # Basis never exceeds balance (headroom from the partial starting
        # basis absorbs this year's contribution).
        assert yr0.brokerage_basis <= yr0.brokerage_balance


class TestBasisNeverExceedsBalanceAcrossFullRun:
    """Structural invariant across a full multi-year projection: cost basis
    is always within [0, balance] -- it can never go negative (floored at 0
    by engine/scenario.py:912) and never exceeds the balance it's tracking
    (that same clamp's upper bound, `brokerage_basis = min(brokerage_basis,
    brokerage)`).

    IMPORTANT field-timing note (pinning it here so a future reader does not
    re-derive it the hard way): YearResult.brokerage_balance is the
    BEGIN-of-year balance (engine/scenario.py:824, set from the
    carried-forward balance before that year's growth/contribution/debit are
    applied -- see also the "year1 begin brokerage" framing in
    tests/test_audit_0805_c8_expense_debit.py), while YearResult.
    brokerage_basis is an END-of-year quantity (:913, after that SAME year's
    contribution/debit). Comparing brokerage_basis against the SAME year's
    brokerage_balance is therefore apples-to-oranges (closing vs. opening)
    and DOES fail partway through a realistic decumulation-then-
    reaccumulation run: the brokerage drains to $0 (begin balance for
    several years), then a later year's Social Security/RMD surplus
    (excess_rmd) contributes to basis immediately while that year's
    begin-of-year balance field still reads the pre-contribution $0 -- e.g.
    year 2049 in this fixture: brokerage_balance=0.00 but
    brokerage_basis=2538.70.

    YearResult.brokerage_balance_end is the matching END-of-year balance
    (set alongside brokerage_basis at the same point in engine/scenario.py),
    so it is the correct like-for-like comparison and is used below instead.
    """

    # NAME LENGTH IS LOAD-BEARING: a `test_` prefix followed by EXACTLY 35
    # characters matches TruffleHog's Lob API-key detector, which stamps any
    # test_-prefixed candidate as Verified=true. CI sets fail-on-secrets: true,
    # so such a name HARD-BLOCKS the pipeline on a false positive. This test was
    # renamed for exactly that reason (see git history); do not rename it back to
    # a 35-character suffix. Note the old name cannot even be quoted here -- the
    # detector matches it inside a comment just as readily as in code.
    def test_basis_never_exceeds_balance_every_year(self) -> None:
        hh = _bare_household(
            brokerage_start=200_000.0,
            brokerage_start_basis=100_000.0,
            growth_rate=0.07,
            brok_turnover=0.30,
            living_expenses=60_000.0,
            expense_inflation=0.03,
            your_ira=500_000.0,
            spouse_ira=300_000.0,
            your_age=61,
            spouse_age=59,
        )
        plan = ConversionPlan(your_conversions={2026: 40_000.0, 2027: 40_000.0})
        result = run_scenario(hh, plan, "basis-invariant", end_age=95)

        assert len(result.years) > 1  # sanity: this is actually a multi-year run
        for yr in result.years:
            assert yr.brokerage_basis >= 0.0, (
                f"year {yr.year}: brokerage_basis went negative: "
                f"{yr.brokerage_basis:.2f}"
            )
            assert yr.brokerage_basis <= yr.brokerage_balance_end + 1e-6, (
                f"year {yr.year}: brokerage_basis ({yr.brokerage_basis:.2f}) "
                f"exceeded brokerage_balance_end ({yr.brokerage_balance_end:.2f})"
            )
