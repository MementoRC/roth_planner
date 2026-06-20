"""Regression tests for engine.sweet_spot_compute pure helpers."""

import pytest

from engine.sweet_spot_compute import BaseIncome, bracket_boundary_conversion


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
