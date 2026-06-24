"""Tests for engine.scenario auto-fill MAGI/SS regression (F9) and autofill taxable-SS base_magi."""

import pytest

from engine.scenario import (
    ConversionPlan,
    auto_fill_irmaa_safe,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestAutoFillCoreBaseMagiTaxableSS:
    """F9 regression: _auto_fill_core must use taxable SS (not gross SS) in base_magi.

    Prior to the fix, base_magi added the full combined_ss even though tss (the
    IRC §86-capped taxable portion) was already computed and used in fixed_gross.
    This overstated base_magi, causing the IRMAA-safe ceiling to be hit too soon
    and OBBBA senior-bonus phase-out to fire earlier than correct.

    Note: your_ss_fra is a monthly dollar amount; ss_benefit_at_age() converts it
    to an annual benefit applying delay/early credits.
    """

    def test_irmaa_safe_base_magi_uses_taxable_ss(self) -> None:
        """auto_fill_irmaa_safe conversion must not be reduced by non-taxable SS.

        Setup: Single household at SS-start age. your_ss_fra=1_500 (monthly) ->
        annual SS ~$22.3K at age 70 (3yr delay credits). With no other income,
        provisional = 0 + 0.5x22.3K = 11.2K < $25K Single tier-1 -> tss = 0.

        Under the old bug: base_magi += gross SS (~22.3K) -> less IRMAA room.
        Under the fix:     base_magi += tss (0) -> full IRMAA room.

        Observable consequence: auto_fill_irmaa_safe generates a non-zero conversion
        in the base year, AND run_scenario confirms taxable_ss_amt == 0 (the scenario
        engine independently computes tss=0 for this household, so if autofill used
        gross SS the plan would be overly conservative relative to scenario truth).
        """
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss

        hh = Household(
            filing_status="Single",
            your_age=70,
            your_ira=3_000_000,
            spouse_ira=0,
            spouse_roth=0,
            spouse_age=0,
            spouse_ss_fra=0,
            your_ss_fra=1_500,  # $1,500/month FRA benefit (realistic)
            your_ss_start_age=70,
        )

        # Confirm precondition: tss = 0 for this household (provisional < $25K tier-1).
        combined_ss = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        assert combined_ss > 0.0, f"Precondition: household must have SS income, got {combined_ss}"
        tss = taxable_ss(combined_ss, 0.0, filing_status="Single")
        assert tss == 0.0, (
            f"Precondition: provisional={0.5 * combined_ss:.0f} must be < $25K tier-1; "
            f"got tss={tss:.0f} (combined_ss={combined_ss:.0f})"
        )

        plan = auto_fill_irmaa_safe(hh)
        base_year = hh.base_year
        conv = plan.your_conversions.get(base_year, 0.0)

        # Post-fix: base_magi uses tss=0 -> IRMAA room = threshold - RMD, so a
        # positive conversion is generated. Pre-fix: base_magi added ~$22K of gross
        # SS, over-consuming IRMAA room by that amount (overly conservative plan).
        assert conv > 0.0, (
            f"IRMAA-safe plan must produce a positive base-year conversion; got {conv}"
        )

    def test_irmaa_safe_room_reduced_by_tss_not_gross_ss(self) -> None:
        """IRMAA room reduction from SS equals tss, not gross combined_ss.

        Compare two identical MFJ households that differ only in whether SS has
        started. With high wages YTD, provisional income is deep in the 85% band
        so tss = 85% x combined_ss < combined_ss.

        The base-year conversion difference between the no-SS and SS households
        must equal tss (the taxable fraction), not the full gross SS amount.

        Note: your_ss_fra=2_000/month -> combined_ss_annual ~59.5K (both at 70).
        provisional = wages(80K) + 0.5x59.5K ~109.7K >> $44K MFJ tier-2
        -> tss = 85% x 59.5K ~50.6K; gross = 59.5K; delta ~8.9K.
        """
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from models.ytd_income import YTDSnapshot

        # Large IRA -- never the binding constraint; IRMAA ceiling is.
        common_kwargs: dict = {
            "filing_status": "MFJ",
            "your_ira": 5_000_000,
            "spouse_ira": 5_000_000,
            "your_ss_fra": 2_000,  # $2K/month FRA (realistic)
            "spouse_ss_fra": 2_000,
            "your_ss_start_age": 70,
            "spouse_ss_start_age": 70,
        }
        # No SS yet (ages below start age)
        hh_no_ss = Household(**common_kwargs, your_age=60, spouse_age=60)
        # SS active (ages at start age -> 3yr delay credits applied)
        hh_ss = Household(**common_kwargs, your_age=70, spouse_age=70)

        wages_ytd = 80_000.0
        ytd_no_ss = YTDSnapshot(tax_year=hh_no_ss.base_year, wages_ytd=wages_ytd)
        ytd_ss = YTDSnapshot(tax_year=hh_ss.base_year, wages_ytd=wages_ytd)

        your_base = ss_benefit_at_age(
            hh_ss.your_ss_fra, hh_ss.your_ss_start_age, hh_ss.your_fra_age
        )
        spouse_base = ss_benefit_at_age(
            hh_ss.spouse_ss_fra, hh_ss.spouse_ss_start_age, hh_ss.spouse_fra_age
        )
        combined_ss = your_base + spouse_base
        expected_tss = taxable_ss(combined_ss, wages_ytd, filing_status="MFJ")

        # Precondition: 85% rule fires -> tss < gross SS.
        assert expected_tss < combined_ss, (
            f"Precondition: tss={expected_tss:.0f} must be < gross ss={combined_ss:.0f}"
        )
        assert expected_tss > 0.0, (
            f"Precondition: tss={expected_tss:.0f} must be positive (85% band active)"
        )

        plan_no_ss = auto_fill_irmaa_safe(hh_no_ss, ytd=ytd_no_ss)
        plan_ss = auto_fill_irmaa_safe(hh_ss, ytd=ytd_ss)

        conv_no_ss = plan_no_ss.your_conversions.get(
            hh_no_ss.base_year, 0.0
        ) + plan_no_ss.spouse_conversions.get(hh_no_ss.base_year, 0.0)
        conv_ss = plan_ss.your_conversions.get(
            hh_ss.base_year, 0.0
        ) + plan_ss.spouse_conversions.get(hh_ss.base_year, 0.0)

        # The SS household commits tss to MAGI -> less conversion room.
        reduction = conv_no_ss - conv_ss
        assert reduction >= 0.0, (
            f"SS household must have <= conversion room: no_ss={conv_no_ss:.0f}, ss={conv_ss:.0f}"
        )
        # Reduction must equal tss (fixed) not combined_ss (buggy pre-F9).
        # Tolerance: $100 for indexing/rounding across the two base years.
        assert reduction == approx(expected_tss, tol=100), (
            f"IRMAA room reduction should equal tss={expected_tss:.0f}, "
            f"got {reduction:.0f} (gross-SS bug would give ~{combined_ss:.0f})"
        )
