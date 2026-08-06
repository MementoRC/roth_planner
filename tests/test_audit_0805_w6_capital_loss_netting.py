"""TDD regression tests for audit-0805 W6 finding C2.

C2 -- no IRC §1222 netting, no IRC §1211(b) $3,000 loss cap
--------------------------------------------------------------
``models/ytd_income.py`` deliberately leaves ``ltcg_ytd``/``stcg_ytd``
unclamped (see ``YTDSnapshot.__post_init__``) so losses can be negative.
Two real consequences follow from the fact that nothing then NETS or CAPS
those signed values before they enter the tax computation:

(a) IRC §1222 short/long netting DOES NOT EXIST. ``stcg_ytd`` flows
    straight into ``total_ordinary_income`` while ``ltcg_ytd`` never enters
    that property at all (it only reaches the preferential-rate stack in
    ``engine/tax.py``) -- so a long-term LOSS and a short-term GAIN of the
    same size never offset each other; the short-term gain hits ordinary
    income undiminished.

(b) IRC §1211(b)'s $3,000 net-capital-loss cap against ordinary income is
    never applied -- a large net capital LOSS is either ignored entirely
    (``total_ordinary_income`` doesn't reference ``ltcg_ytd`` today) or, once
    it reaches ``engine.tax.estimate_ytd_federal_tax``'s
    ordinary/preferential floor-and-stack arithmetic, ends up offsetting
    ordinary income by far more than the statutory $3,000 ceiling.

ORDER MATTERS: net first per §1222, THEN cap the net loss at $3,000.

OUT OF SCOPE (deliberately not built here): the capital-loss CARRYFORWARD.
``YTDSnapshot`` is a single-tax-year object with no prior-year-loss field;
persisting a carryforward needs a new model field plus a migration for
cached snapshots. The disallowed excess (net loss beyond $3,000) is simply
DROPPED by the fix below, not carried forward -- see the code comment at
the fix site and the PR body.

All expected values below are hand-derived from the §1222/§1211(b) netting
+ cap algorithm directly (simple arithmetic -- no bracket tables needed for
the property-level tests; the two integration-level tests are engineered so
every branch collapses to a taxable base of exactly $0, so
``federal_tax(0, ...) == 0`` trivially without consulting any bracket
table).
"""

from __future__ import annotations

import pytest

from engine.tax import estimate_ytd_federal_tax
from models.household import Household
from models.ytd_income import YTDSnapshot


def approx(expected: float, tol: float = 0.01) -> object:
    return pytest.approx(expected, abs=tol)


def _hh_mfj(your_age: int = 60, spouse_age: int = 60) -> Household:
    """MFJ household, base_year=2026, no CPI inflation, neither spouse 65+
    (so no OBBBA senior bonus deduction complicates the arithmetic)."""
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=2026,
        cpi_assumption=0.0,
        filing_status="MFJ",
    )


class TestNettingPropertyLevel:
    """IRC §1222: net short-term and long-term capital gain/loss against
    each other BEFORE either enters total_ordinary_income or the
    preferential-rate stack."""

    def test_lt_loss_plus_st_gain_of_equal_size_nets_to_zero_ordinary(self) -> None:
        """$50K LT loss + $50K ST gain => net capital position is exactly
        $0 -- nothing should be added to ordinary income.

        Hand-derivation: net_short_term = 50_000, net_long_term = -50_000.
        Net capital gain/loss = 50_000 + (-50_000) = 0. Since the net is
        >= 0 and the short-term side is the (only) gain, the netted $0
        stays characterized as ordinary (a trivial $0 either way).
        Correct total_ordinary_income = 0.00 (no wages).

        Defective code: total_ordinary_income sums stcg_ytd raw (50_000)
        with no reference to ltcg_ytd at all => 50_000.00.
        """
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=-50_000.0, stcg_ytd=50_000.0)
        assert ytd.total_ordinary_income == approx(0.0), (
            f"Expected total_ordinary_income=0.00 (a $50000 short-term GAIN "
            f"netted against a $50000 long-term LOSS per IRC §1222 leaves "
            f"nothing to stack into ordinary brackets), got "
            f"{ytd.total_ordinary_income:.2f} -- stcg_ytd is not being netted "
            f"against ltcg_ytd before entering total_ordinary_income"
        )

    def test_lt_loss_plus_st_gain_of_equal_size_nets_to_zero_magi(self) -> None:
        """Same fixture: MAGI (which sums every income source regardless of
        character) must also reflect the $0 net -- this one already holds
        today because summation is order-independent, but is pinned here as
        a companion invariant to the ordinary-income assertion above."""
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=-50_000.0, stcg_ytd=50_000.0)
        assert ytd.magi_ytd == approx(0.0)


class TestCapPropertyLevel:
    """IRC §1211(b): once netted, an overall net capital LOSS can offset at
    most $3,000 of ordinary income per year; any larger loss is simply
    unusable this year (the excess is not carried forward -- see module
    docstring)."""

    def test_pure_lt_loss_caps_ordinary_income_reduction_at_3000(self) -> None:
        """$100K wages + a $50K pure long-term capital LOSS (no ST activity).

        Hand-derivation: net_short_term = 0, net_long_term = -50_000. Net
        capital position = -50_000 (a loss) => capped at -3_000 per
        §1211(b); the disallowed $47,000 excess is dropped (not carried
        forward). Correct total_ordinary_income = 100_000 - 3_000 = 97_000.00.

        Defective code: total_ordinary_income never references ltcg_ytd at
        all, so it stays at the full 100_000.00 -- neither netted nor capped,
        just silently ignoring the loss.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=100_000.0, ltcg_ytd=-50_000.0)
        assert ytd.total_ordinary_income == approx(97_000.0), (
            f"Expected total_ordinary_income=97000.00 (100000 wages minus the "
            f"IRC §1211(b)-capped $3000 net capital loss), got "
            f"{ytd.total_ordinary_income:.2f} -- the $50000 long-term loss is "
            f"not being netted+capped into total_ordinary_income at all"
        )


class TestNettingAndCapIntegrationLevel:
    """Same two scenarios, but run all the way through
    engine.tax.estimate_ytd_federal_tax to prove the defect (and the fix)
    hold end-to-end, not just at the YTDSnapshot property level. Both
    fixtures are engineered so the CORRECT taxable base collapses to
    exactly $0 at every stack (ordinary and preferential), so
    ``federal_tax(0, ...) == 0`` and ``ltcg_tax == 0`` are trivial identities
    -- no bracket table lookup required to hand-derive the expected value.
    """

    def test_netting_zeroes_total_tax_when_st_gain_offsets_lt_loss(self) -> None:
        """$50K LT loss + $50K ST gain, no wages, no SS.

        Hand-derivation (correct): total_ordinary_income = 0 (per
        TestNettingPropertyLevel above); preferential_capital_gain_ytd = 0
        (the LT loss was fully absorbed into the ST gain, nothing left with
        long-term character) => ltcg_taxable = 0. taxable_total =
        max(0 + 0 - std_ded, 0) = 0 => ordinary_tax = federal_tax(0) = 0 and
        ltcg_tax = 0 (empty stack). niit_magi_ytd = magi_ytd (0) = 0, below
        the $250K MFJ threshold => niit = 0. Correct total = 0.00.

        Defective code (traced by hand against the CURRENT formulas):
        ordinary_income = total_ordinary_income = 50_000 (raw stcg_ytd,
        ltcg_ytd never netted in); ltcg_taxable = ltcg_ytd = -50_000.
        taxable_total = max(50_000 + (-50_000) - std_ded, 0) = 0.
        ltcg_preferential = min(-50_000, 0) = -50_000.
        taxable_ordinary = taxable_total - ltcg_preferential
                          = 0 - (-50_000) = 50_000 (nonzero!).
        ordinary_tax = federal_tax(50_000, ...) > 0 -- the exact "\\$50,000
        ordinary income hit" described in the audit finding, versus the
        correct $0.
        """
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=-50_000.0, stcg_ytd=50_000.0)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        assert result.total == approx(0.0), (
            f"Expected total=0.00 (a $50000 short-term GAIN fully netted "
            f"against a $50000 long-term LOSS per IRC §1222 leaves $0 "
            f"taxable at every stack), got {result.total:.2f} -- got "
            f"ordinary_tax={result.ordinary_tax:.2f}, "
            f"ltcg_tax={result.ltcg_tax:.2f}, niit={result.niit:.2f} "
            f"(engine/tax.py estimate_ytd_federal_tax)"
        )

    def test_cap_leaves_zero_tax_when_pure_lt_loss_fully_absorbed_by_deduction(self) -> None:
        """No wages, no SS, a pure $50K long-term capital LOSS (no ST
        activity).

        Hand-derivation (correct): total_ordinary_income = -3_000 (netted
        + capped per TestCapPropertyLevel's logic, zero wages this time);
        preferential_capital_gain_ytd = 0 (it's a loss, not a gain) =>
        ltcg_taxable = 0. ordinary_income_with_ss = -3_000.
        taxable_total = max(-3_000 + 0 - std_ded, 0) = 0 (std_ded is
        ~$32K, comfortably absorbing -3_000). ordinary_tax = 0,
        ltcg_tax = 0. niit_magi_ytd = magi_ytd = -3_000 <= $250K threshold
        => niit = 0. Correct total = 0.00.

        Defective code: total_ordinary_income never references ltcg_ytd =>
        stays 0 (not -3_000, but that difference doesn't matter here since
        both floor to 0 either way). ltcg_taxable = ltcg_ytd = -50_000 (the
        FULL uncapped loss, not the correct $0). taxable_total =
        max(0 + (-50_000) - std_ded, 0) = 0.
        ltcg_preferential = min(-50_000, 0) = -50_000.
        taxable_ordinary = 0 - (-50_000) = 50_000 (nonzero!) ->
        ordinary_tax = federal_tax(50_000, ...) > 0, versus the correct $0
        -- the full $50,000 loss leaking through as a phantom ordinary
        income HIT instead of being capped (and, since it's a loss, having
        no business raising tax at all).
        """
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=-50_000.0)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        assert result.total == approx(0.0), (
            f"Expected total=0.00 (a pure $50000 long-term capital LOSS, "
            f"capped at $3000 per IRC §1211(b), is fully absorbed by the "
            f"~$32K standard deduction -> $0 taxable), got "
            f"{result.total:.2f} -- got ordinary_tax={result.ordinary_tax:.2f}, "
            f"ltcg_tax={result.ltcg_tax:.2f}, niit={result.niit:.2f} "
            f"(engine/tax.py estimate_ytd_federal_tax)"
        )


class TestNetGainRegressionGuard:
    """A net capital GAIN (no loss anywhere) must be byte-identical to
    current behavior -- the §1222/§1211(b) fix only changes outcomes when a
    LOSS is involved. This test is expected to PASS both before and after
    the fix (it is a regression guard, not a defect-confirming test)."""

    def test_pure_gains_unaffected_st_ordinary_lt_preferential_split_unchanged(self) -> None:
        """$10K wages + $20K ST gain + $80K LT gain -- no losses anywhere.

        Hand-derivation: net_short_term = 20_000 (>= 0), net_long_term =
        80_000 (>= 0) -- both gains, no cross-netting condition triggers
        (the §1222/§1211(b) algorithm is a no-op whenever both legs are
        already non-negative). total_ordinary_income = wages + ST gain =
        10_000 + 20_000 = 30_000.00 (LT gain excluded, as today). magi_ytd =
        wages + ST gain + LT gain = 10_000 + 20_000 + 80_000 = 110_000.00
        (both included, as today) -- i.e. the short-term gain still stacks
        into ordinary brackets and the long-term gain still only reaches
        the preferential-rate stack (via magi_ytd/ltcg_ytd), exactly as
        pre-fix.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=10_000.0, stcg_ytd=20_000.0, ltcg_ytd=80_000.0)
        assert ytd.total_ordinary_income == approx(30_000.0)
        assert ytd.magi_ytd == approx(110_000.0)
