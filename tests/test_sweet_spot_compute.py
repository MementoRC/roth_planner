"""Regression tests for engine.sweet_spot_compute pure helpers."""

import pytest

from engine.aca import aca_subsidy_loss
from engine.irmaa import IRMAA_TIERS_MFJ, _index_irmaa_tiers, irmaa_for_year
from engine.sweet_spot_compute import (
    BaseIncome,
    all_in_at_conversion,
    base_income_for_year,
    bracket_boundary_conversion,
    compute_multi_year_summary,
)
from engine.tax import taxable_ss
from models.household import Household


def _base(opt: float, tss: float, total_ded: float) -> BaseIncome:
    base_gross = opt + tss
    return BaseIncome(
        ya=66,
        sa=64,
        year=2026,
        cpi=0.0,
        opt=opt,
        combined_ss=40_000.0,
        base_gross=base_gross,
        base_magi=base_gross,
        total_ded=total_ded,
        ded_base=total_ded,
        ytd_magi=0.0,
    )


def test_bracket_boundary_conversion_hits_ceiling() -> None:
    # At the returned conversion, linear taxable income equals the ceiling.
    base = _base(opt=200_000.0, tss=30_000.0, total_ded=31_500.0)
    ceiling = 400_000.0
    conv = bracket_boundary_conversion(base, ceiling)
    tss = base.base_gross - base.opt
    taxable_at_conv = base.opt + conv + tss - base.total_ded
    assert taxable_at_conv == pytest.approx(ceiling)


def test_bracket_boundary_conversion_no_double_option_subtraction() -> None:
    # Regression: the old formula subtracted base.opt a second time, drawing every
    # bracket marker low by exactly the option-income amount.
    base = _base(opt=200_000.0, tss=30_000.0, total_ded=31_500.0)
    ceiling = 400_000.0
    fixed = bracket_boundary_conversion(base, ceiling)
    buggy = max(base.total_ded + ceiling - base.base_gross - base.opt, 0.0)
    assert fixed - buggy == pytest.approx(base.opt)


class TestAcaMagiSsAddback:
    """ACA MAGI must include non-taxable SS per IRC §36B(d)(2)(B)(iii)."""

    def _make_household(self) -> Household:
        # Ages 63/57 in base_year 2026 — both pre-65, ACA window.
        # SS already claimed at 62 (your_ss_start_age=62) so combined_ss > 0.
        # your_ss_fra=2_000 $/mo → annual base ~$24K; spouse not yet claiming (age 57 < 62).
        return Household(
            your_age=63,
            spouse_age=57,
            base_year=2026,
            your_ss_start_age=62,
            spouse_ss_start_age=70,
            your_ss_fra=2_000.0,  # $/month at FRA
            spouse_ss_fra=1_500.0,
            your_fra_age=67,
            spouse_fra_age=67,
            filing_status="MFJ",
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            aca_benchmark_premium_annual=18_000.0,
            aca_enhanced_subsidies_active=False,
            cpi_assumption=0.0,
            ss_cola=0.0,
        )

    def test_aca_loss_includes_nontaxable_ss_addback(self) -> None:
        hh = self._make_household()
        year = 2026
        b = base_income_for_year(hh, year)

        # combined_ss must be positive for the add-back to matter
        assert b.combined_ss > 0, "SS must be claimed for this test to be meaningful"

        conv = 30_000.0
        res = all_in_at_conversion(hh, b, conv, 0.0)

        # Recompute expected ACA loss with correct IRC §36B MAGI (adds back non-taxable SS)
        tss_conv = taxable_ss(b.combined_ss, b.opt + conv, filing_status=hh.filing_status)
        base_tss = taxable_ss(b.combined_ss, b.opt, filing_status=hh.filing_status)
        aca_base_magi_expected = b.base_magi + (b.combined_ss - base_tss)
        aca_magi_expected = (b.opt + conv + tss_conv + b.ytd_magi) + (b.combined_ss - tss_conv)
        expected = aca_subsidy_loss(
            aca_base_magi_expected,
            aca_magi_expected,
            benchmark=hh.aca_benchmark_premium_annual * 1.0,  # both on ACA → factor=2/2=1
            enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
            filing_status=hh.filing_status,
            year=year,
            cpi=hh.cpi_assumption,
        )

        assert res.aca_loss == pytest.approx(expected), (
            f"aca_loss {res.aca_loss} != expected {expected} with SS add-back"
        )

        # Prove the add-back actually changed the result vs. using raw IRMAA-compatible MAGI
        old_result = aca_subsidy_loss(
            b.base_magi,
            b.opt + conv + tss_conv + b.ytd_magi,
            benchmark=hh.aca_benchmark_premium_annual,
            enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
            filing_status=hh.filing_status,
            year=year,
            cpi=hh.cpi_assumption,
        )
        assert res.aca_loss != pytest.approx(old_result), (
            "SS add-back did not change aca_loss — test scenario may not exercise the fix"
        )


class TestIrmaaPaymentYearIndexingInSweetSpot:
    """G2 regression: compute_multi_year_summary must index IRMAA thresholds to
    the PAYMENT year (income_year + 2), not the income year.

    At high CPI the payment-year threshold is meaningfully higher, so a MAGI
    that would exceed the income-year threshold is still safely below the
    payment-year threshold — and irmaa_safe should reflect that distance.
    """

    def _make_household(self, base_year: int, cpi: float) -> Household:
        return Household(
            your_age=66,
            spouse_age=64,
            base_year=base_year,
            cpi_assumption=cpi,
            ss_cola=0.0,
        )

    def test_irmaa_safe_uses_payment_year_threshold(self) -> None:
        """irmaa_safe in YearSummary row must be computed from income_year+2 threshold.

        Setup: base_year=2026, high cpi=0.06 to widen the gap between income-year
        and payment-year thresholds.

        MFJ Tier-1 base = $218,000.
          income-year 2026 → threshold = $218,000 (base, no shift)
          payment-year 2028 → threshold ≈ 218_000 * 1.06^2 ≈ $245,000

        With base_magi near zero (no option income, ages set so no SS yet), irmaa_safe
        from each indexing year differs by ~$27K. The fix (payment-year) must yield
        a larger irmaa_safe than the broken (income-year) value.
        """
        cpi = 0.06
        hh = self._make_household(base_year=2026, cpi=cpi)

        rows = compute_multi_year_summary(hh)
        assert rows, "expected at least one YearSummary row"

        row2026 = rows[0]
        assert row2026.year == 2026

        # Manually compute the expected irmaa_safe for 2026 under each indexing scheme.
        income_year_tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2026, cpi)
        payment_year_tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2028, cpi)

        income_year_t1 = income_year_tiers[0][0]
        payment_year_t1 = payment_year_tiers[0][0]

        # Payment-year threshold must be strictly larger (cpi > 0).
        assert payment_year_t1 > income_year_t1, (
            "precondition: payment-year threshold must exceed income-year threshold at cpi=6%"
        )

        # irmaa_safe must use the payment-year threshold (larger value → larger room).
        base_magi = row2026.base_magi
        expected_irmaa_safe = (
            max(payment_year_t1 - base_magi, 0.0) if (payment_year_t1 - base_magi) > 0 else None
        )
        buggy_irmaa_safe = (
            max(income_year_t1 - base_magi, 0.0) if (income_year_t1 - base_magi) > 0 else None
        )

        # The two values differ (payment-year threshold is ~$27K higher).
        assert expected_irmaa_safe != buggy_irmaa_safe, (
            "test scenario does not distinguish income-year vs payment-year indexing"
        )

        assert row2026.irmaa_safe == pytest.approx(expected_irmaa_safe, abs=1.0), (
            f"irmaa_safe={row2026.irmaa_safe} does not match payment-year threshold "
            f"({payment_year_t1:.0f} - {base_magi:.0f} = {expected_irmaa_safe}); "
            f"income-year buggy value would be {buggy_irmaa_safe}"
        )


class TestAllInAtConversionIrmaaPaymentYear:
    """Regression: all_in_at_conversion must index IRMAA thresholds to the
    PAYMENT year (income_year + 2), not the income year.

    MFJ Tier-1 base = $218,000; cpi = 2.5%:
      indexed to income year 2030 = 218_000 * 1.025^4 ≈ $240,631
      indexed to payment year 2032 = 218_000 * 1.025^6 ≈ $252,813

    MAGI = $246,000 sits between the two thresholds:
      - income-year indexing  → tier 1 triggered → positive surcharge  (WRONG)
      - payment-year indexing → tier 0            → no surcharge        (CORRECT)
    """

    def _make_household(self) -> Household:
        # your_age=59 in base_year=2026:
        #   income year 2030 → age 63 (not yet on Medicare)
        #   irmaa_for_year adds +2 internally → Medicare age 65 ✓
        # No SS (both claim at 70, age 63 < 70), no option income → base_magi ≈ 0.
        return Household(
            your_age=59,
            spouse_age=59,
            base_year=2026,
            cpi_assumption=0.025,
            ss_cola=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )

    def test_all_in_irmaa_uses_payment_year_thresholds(self) -> None:
        hh = self._make_household()
        income_year = 2030
        cpi = hh.cpi_assumption

        b = base_income_for_year(hh, income_year)
        ya, sa = b.ya, b.sa  # income-year ages (63/63)

        # Discriminator sanity: verify the two conventions genuinely disagree.
        # irmaa_for_year adds +2 internally for Medicare eligibility gate;
        # the year= param controls MAGI threshold indexing only.
        income_yr_surcharge, _ = irmaa_for_year(
            246_000, ya, sa, filing_status="MFJ", year=income_year, cpi=cpi
        )
        payment_yr_surcharge, _ = irmaa_for_year(
            246_000, ya, sa, filing_status="MFJ", year=income_year + 2, cpi=cpi
        )
        assert income_yr_surcharge > 0, (
            "precondition: income-year indexing must trigger tier-1 at MAGI=246_000"
        )
        assert payment_yr_surcharge == 0.0, (
            "precondition: payment-year indexing must produce no surcharge at MAGI=246_000"
        )

        # base_magi must be well below both thresholds so base_irmaa == 0
        # under both conventions (isolates the conversion-MAGI effect).
        assert b.base_magi < 218_000, (
            f"base_magi={b.base_magi} must be below MFJ tier-1 base so base_irmaa==0"
        )

        # conv chosen so result.magi ≈ 246_000 (sits in the disagreement window).
        # magi = opt + tss + ytd_magi + conv; with base_magi ≈ 0, conv ≈ 246_000.
        conv = 246_000.0 - b.base_magi
        result = all_in_at_conversion(hh, b, conv, net_inv_income=0.0)

        # Confirm magi is in the discriminator window.
        threshold_income = 218_000 * (1 + cpi) ** 4  # ~240_631
        threshold_payment = 218_000 * (1 + cpi) ** 6  # ~252_813
        assert threshold_income < result.magi < threshold_payment, (
            f"result.magi={result.magi:.0f} must sit between income-year threshold "
            f"({threshold_income:.0f}) and payment-year threshold ({threshold_payment:.0f})"
        )

        # PRIMARY ASSERTION: payment-year indexing → no IRMAA surcharge above base.
        # base_irmaa == 0 (base_magi << tier-1), payment-year conv_irmaa == 0
        # → irmaa_delta == 0.0.
        assert result.irmaa_delta == pytest.approx(0.0), (
            f"irmaa_delta={result.irmaa_delta} must be 0.0 under payment-year indexing; "
            f"income-year (bug) would produce {income_yr_surcharge:.0f}"
        )

        # EQUIVALENCE ASSERTION: result matches direct recompute with payment-year args.
        expected_irmaa, _ = irmaa_for_year(
            result.magi, ya, sa, filing_status="MFJ", year=income_year + 2, cpi=cpi
        )
        base_irmaa_direct, _ = irmaa_for_year(
            b.base_magi, ya, sa, filing_status="MFJ", year=income_year + 2, cpi=cpi
        )
        assert result.irmaa_delta == pytest.approx(expected_irmaa - base_irmaa_direct), (
            "irmaa_delta must equal direct recompute with year=income_year+2"
        )
