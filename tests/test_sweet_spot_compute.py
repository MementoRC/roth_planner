"""Regression tests for engine.sweet_spot_compute pure helpers."""

import pytest

from engine.aca import aca_subsidy_loss
from engine.sweet_spot_compute import (
    BaseIncome,
    all_in_at_conversion,
    base_income_for_year,
    bracket_boundary_conversion,
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
