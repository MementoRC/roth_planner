"""Regression tests for engine.sweet_spot_compute pure helpers."""

import pytest

from engine.aca import aca_subsidy_loss
from engine.irmaa import IRMAA_TIERS_MFJ, _index_irmaa_tiers, irmaa_for_year
from engine.sweet_spot_compute import (
    BaseIncome,
    _ltcg_stack_tax,
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
        # 0.0 (not a fixed nonzero constant): bracket_boundary_conversion now
        # recomputes taxable SS via all_in_at_conversion for each candidate
        # conv (finding 1 fix), so a nonzero combined_ss here would make
        # taxable income nonlinear in conv -- these two tests intentionally
        # exercise the pure-linear case (tss passed in as a fixed component).
        combined_ss=0.0,
        base_gross=base_gross,
        base_magi=base_gross,
        total_ded=total_ded,
        ded_base=total_ded,
        ytd_magi=0.0,
    )


def _no_ss_household() -> Household:
    # No SS at all (both claim at an age never reached in these tests) so
    # taxable SS stays 0 across the whole conversion sweep -- taxable income
    # is exactly linear in conv, matching the _base() helper's fixed tss.
    return Household(
        your_age=66,
        spouse_age=64,
        base_year=2026,
        your_ss_start_age=99,
        spouse_ss_start_age=99,
        filing_status="MFJ",
        cpi_assumption=0.0,
        ss_cola=0.0,
        grants=[],
    )


def test_bracket_boundary_conversion_hits_ceiling() -> None:
    # With no SS in play, taxable income is linear in conv, so the boundary
    # search must still land exactly on the ceiling.
    hh = _no_ss_household()
    base = _base(opt=200_000.0, tss=0.0, total_ded=31_500.0)
    ceiling = 400_000.0
    conv = bracket_boundary_conversion(hh, base, ceiling)
    taxable_at_conv = base.opt + conv - base.total_ded
    assert taxable_at_conv == pytest.approx(ceiling, abs=0.01)


def test_bracket_boundary_conversion_no_double_option_subtraction() -> None:
    # Regression: the old formula subtracted base.opt a second time, drawing every
    # bracket marker low by exactly the option-income amount.
    hh = _no_ss_household()
    base = _base(opt=200_000.0, tss=0.0, total_ded=31_500.0)
    ceiling = 400_000.0
    fixed = bracket_boundary_conversion(hh, base, ceiling)
    buggy = max(base.total_ded + ceiling - base.base_gross - base.opt, 0.0)
    assert fixed - buggy == pytest.approx(base.opt, abs=0.01)


class TestBracketBoundarySsTaxabilityNonlinearity:
    """Audit finding 1 (HIGH): bracket_boundary_conversion() must fold Social
    Security taxability nonlinearity into the boundary search. The naive
    closed-form (total_ded + ceiling - base_gross) assumes taxable SS is
    conversion-invariant; in reality, once provisional income sits in the
    50%/85% partial-taxability zone (IRC §86(b)), each extra dollar of
    conversion raises taxable SS too, so taxable income rises FASTER than
    1-per-1 -- the naive formula therefore overshoots the true conversion
    needed to reach a given taxable-income ceiling.
    """

    def _make_household(self) -> Household:
        # MFJ, ages 66/64, SS claimed by both already so combined_ss > 0 and
        # sized (~$30K/yr) so that a $0-$52K conversion sweep passes through
        # the SS partial-taxability transition zone (tier1=$32K, tier2=$44K
        # provisional income) rather than starting already-saturated at 85%.
        return Household(
            your_age=66,
            spouse_age=64,
            base_year=2026,
            your_ss_start_age=62,
            spouse_ss_start_age=62,
            your_ss_fra=1_500.0,  # $/month at FRA
            spouse_ss_fra=1_000.0,
            your_fra_age=67,
            spouse_fra_age=67,
            filing_status="MFJ",
            cpi_assumption=0.0,
            ss_cola=0.0,
            grants=[],
        )

    def test_bracket_boundary_overshoots_without_ss_nonlinearity(self) -> None:
        """The naive linear estimate must overshoot the true SS-aware boundary."""
        hh = self._make_household()
        year = 2026
        base = base_income_for_year(hh, year)
        assert base.combined_ss > 0, "precondition: SS must be active"

        ceiling = 40_000.0
        # Old (buggy) closed-form, reproduced inline so this regression stays
        # meaningful even after the production formula changes.
        naive = max(base.total_ded + ceiling - base.base_gross, 0.0)

        fixed = bracket_boundary_conversion(hh, base, ceiling)

        assert fixed < naive - 1_000, (
            f"fixed boundary ({fixed:.0f}) should be materially below the naive "
            f"SS-invariant estimate ({naive:.0f}) once SS taxability nonlinearity "
            "is folded in"
        )

        # Oracle check: at the returned conversion, all_in_at_conversion's own
        # (SS-nonlinearity-aware) taxable_inc must actually equal the ceiling.
        result = all_in_at_conversion(hh, base, fixed, 0.0)
        assert result.taxable_inc == pytest.approx(ceiling, abs=1.0), (
            f"taxable_inc at fixed boundary ({result.taxable_inc:.2f}) must equal "
            f"the target ceiling ({ceiling:.0f})"
        )


class TestComputeMultiYearSummaryFillBoundarySsTorpedo:
    """Audit C23 (MEDIUM, 2026-08-05 W5): compute_multi_year_summary's
    fill_12/fill_22 fed the closed-form room_12/room_22 (documented in
    ConversionResult as GROSS-INCOME room, valid only when taxable SS is
    conversion-invariant) back in as a CONVERSION amount, instead of routing
    through the module's own SS-torpedo-aware bracket_boundary_conversion
    oracle (same class of bug as finding 1 /
    TestBracketBoundarySsTaxabilityNonlinearity above -- see that class's
    docstring for the IRC §86(b) mechanics).
    """

    def _make_household(self) -> Household:
        # Same fixture as TestBracketBoundarySsTaxabilityNonlinearity: SS
        # claimed by both spouses, sized so the sweep crosses the 50%/85%
        # partial-taxability transition zone.
        return Household(
            your_age=66,
            spouse_age=64,
            base_year=2026,
            your_ss_start_age=62,
            spouse_ss_start_age=62,
            your_ss_fra=1_500.0,
            spouse_ss_fra=1_000.0,
            your_fra_age=67,
            spouse_fra_age=67,
            filing_status="MFJ",
            cpi_assumption=0.0,
            ss_cola=0.0,
            grants=[],
        )

    def test_fill_12_lands_on_ceiling_not_naive_gross_room(self) -> None:
        hh = self._make_household()
        year = hh.base_year
        b = base_income_for_year(hh, year)
        assert b.combined_ss > 0, "precondition: SS must be active"

        rows = compute_multi_year_summary(hh)
        row = next(r for r in rows if r.year == year)

        ceiling_12 = 100_800.0  # BRACKETS_MFJ[1][0], unindexed at base_year 2026

        # Reconstruct the pre-fix (naive) value directly: the closed-form
        # room_12 field from all_in_at_conversion at conv=0, fed back in as a
        # conversion -- exactly what compute_multi_year_summary used to do.
        naive_result = all_in_at_conversion(hh, b, 0, 0.0)
        naive_fill_12 = naive_result.room_12

        # Oracle check: converting row.fill_12 must land taxable income exactly
        # AT the ceiling, not past it.
        actual = all_in_at_conversion(hh, b, row.fill_12, 0.0)
        assert actual.taxable_inc == pytest.approx(ceiling_12, abs=1.0), (
            f"fill_12={row.fill_12:.0f} produced taxable_inc={actual.taxable_inc:.0f}, "
            f"must equal the 12% ceiling ({ceiling_12:.0f}) -- pre-fix this overshot "
            "because the naive room formula ignores the conversion's own SS-torpedo effect"
        )

        # RED assertion: pre-fix, row.fill_12 equaled naive_fill_12 exactly and
        # this would fail (they must now differ materially).
        assert row.fill_12 < naive_fill_12 - 1_000, (
            f"fill_12 ({row.fill_12:.0f}) should be materially below the naive "
            f"SS-invariant room_12 ({naive_fill_12:.0f}) once the conversion's own "
            "SS-torpedo effect is folded in via bracket_boundary_conversion"
        )


class TestAcaMagiSsAddback:
    """ACA MAGI must include non-taxable SS per IRC §36B(d)(2)(B)(iii)."""

    def _make_household(self) -> Household:
        # Ages 63/57 in base_year 2026 — both pre-65, ACA window.
        # SS already claimed at 62 (your_ss_start_age=62) so combined_ss > 0.
        # your_ss_fra=2_000 $/mo → annual base ~$24K; spouse not yet claiming (age 57 < 62).
        from models.exercise_schedule import ExerciseSchedule

        hh = Household(
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
        # Deliberately keeps default option income in base_year: this test's
        # MAGI/ACA-loss calibration was set against the pre-#373 stagger
        # default, which landed the first TXN grant's full spread in
        # base_year. The hold-to-expiration default (PR #373 follow-up) now
        # lands it in the grant's own expiry_year instead, so it's pinned
        # explicitly here to preserve the calibration.
        hh.exercise_schedule = ExerciseSchedule()
        hh.exercise_schedule.set_shares(hh.grants[0].key(), hh.base_year, hh.grants[0].shares)
        hh.exercise_schedule.set_price(hh.base_year, hh.txn_price_now)
        return hh

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

        # Manually compute the thresholds under each indexing scheme.
        income_year_tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2026, cpi)
        payment_year_tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2028, cpi)

        income_year_t1 = income_year_tiers[0][0]
        payment_year_t1 = payment_year_tiers[0][0]

        # Payment-year threshold must be strictly larger (cpi > 0).
        assert payment_year_t1 > income_year_t1, (
            "precondition: payment-year threshold must exceed income-year threshold at cpi=6%"
        )

        base_magi = row2026.base_magi
        buggy_irmaa_safe = max(income_year_t1 - base_magi, 0.0) if (income_year_t1 - base_magi) > 0 else None

        # irmaa_safe must use the payment-year threshold → strictly larger than
        # the income-year (buggy) value.  Binary search returns STEP-aligned result
        # so we do not check exact equality with the naive formula; instead we
        # verify correctness semantically:
        #   1. Result is strictly greater than the income-year (buggy) naive value.
        #   2. Result is at most the payment-year naive upper bound.
        #   3. The MAGI at the returned conversion is at or below the payment-year threshold.
        assert row2026.irmaa_safe is not None, "irmaa_safe must be non-None when base_magi < tier1"
        assert row2026.irmaa_safe > buggy_irmaa_safe, (
            f"irmaa_safe={row2026.irmaa_safe} must exceed income-year buggy value "
            f"({buggy_irmaa_safe}); payment-year threshold is {payment_year_t1:.0f}"
        )
        naive_payment_year = payment_year_t1 - base_magi
        assert row2026.irmaa_safe <= naive_payment_year + 0.01, (
            f"irmaa_safe={row2026.irmaa_safe} exceeds payment-year naive upper bound "
            f"({naive_payment_year:.0f})"
        )
        # Confirm the oracle: magi at the returned conversion is within threshold.
        b = base_income_for_year(hh, 2026)
        result = all_in_at_conversion(hh, b, row2026.irmaa_safe, 0.0)
        assert result.magi <= payment_year_t1 + 0.01, (
            f"magi={result.magi:.0f} at irmaa_safe={row2026.irmaa_safe:.0f} "
            f"exceeds payment-year threshold={payment_year_t1:.0f}"
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


class TestConversionLtcgStacking:
    """C1: conversion lifts ordinary income, stacking LTCG into higher rate bands."""

    def test_ltcg_stack_tax_matches_audit_numbers(self) -> None:
        # thresholds: 0%→15% at $98,900, 15%→20% at $613,700 (2026 MFJ, cpi=0)
        thr = (98_900.0, 613_700.0)

        # start=90_000, eligible=20_000 → stack end=110_000
        # At-15% slice: min(110_000, 613_700) - max(90_000, 98_900) = 110_000 - 98_900 = 11_100
        # At-20% slice: max(0, 110_000 - 613_700) = 0
        # Tax = 11_100 * 0.15 = 1_665.0
        assert _ltcg_stack_tax(90_000.0, 20_000.0, thr) == pytest.approx(1_665.0)

        # start=120_000, eligible=20_000 → stack end=140_000
        # At-15% slice: min(140_000, 613_700) - max(120_000, 98_900) = 140_000 - 120_000 = 20_000
        # At-20% slice: 0
        # Tax = 20_000 * 0.15 = 3_000.0
        assert _ltcg_stack_tax(120_000.0, 20_000.0, thr) == pytest.approx(3_000.0)

    def test_all_in_includes_ltcg_delta_when_eligible_passed(self) -> None:
        """When ltcg_eligible > 0 and conversion crosses the 0%→15% LTCG boundary,
        ltcg_delta > 0 and all_in exceeds the default (ltcg_eligible=0) case by
        exactly ltcg_delta. Default case must have ltcg_delta == 0.0."""
        # MFJ, no SS, no options, cpi=0 → base_taxable ≈ 0.
        # LTCG 0%→15% threshold = $98,900.  Deductions for ages 66/64 = 30,000 (std MFJ).
        # Convert $120,000 → taxable_inc ≈ 120,000 - 30,000 = 90,000 (below threshold).
        # That keeps base_taxable at 0 and conv_taxable at ~90,000.
        # With ltcg_eligible=20_000: stack tax jumps because ordinary income pushes into
        # the 15% band starting at 98,900.  We choose conv=120,000 so taxable_inc ≈ 90,000
        # (below 98,900) — but with conv=140,000, taxable_inc ≈ 110,000 (above 98,900),
        # confirming ltcg_delta > 0.
        hh = Household(
            your_age=66,
            spouse_age=64,
            base_year=2026,
            cpi_assumption=0.0,
            ss_cola=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            filing_status="MFJ",
        )
        year = 2026
        b = base_income_for_year(hh, year)

        # base_taxable should be essentially 0 (no income).
        # Choose conv that crosses the 0%→15% LTCG stack boundary.
        # With std MFJ deduction ~$30,000 and threshold $98,900,
        # need taxable_inc > 98,900  →  conv > 98,900 + 30,000 ≈ 128,900.
        conv = 140_000.0
        ltcg_eligible = 20_000.0

        result_with = all_in_at_conversion(hh, b, conv, 0.0, ltcg_eligible=ltcg_eligible)
        result_default = all_in_at_conversion(hh, b, conv, 0.0)

        # Default case: no LTCG stacking
        assert result_default.ltcg_delta == pytest.approx(0.0), (
            f"default ltcg_delta should be 0.0, got {result_default.ltcg_delta}"
        )

        # Eligible case: stacking cost is positive (ordinary income crossed threshold)
        assert result_with.ltcg_delta > 0.0, (
            f"ltcg_delta should be > 0 when conversion crosses LTCG threshold, got {result_with.ltcg_delta}"
        )

        # all_in excess equals ltcg_delta exactly
        assert result_with.all_in - result_default.all_in == pytest.approx(
            result_with.ltcg_delta
        ), "all_in excess over default must equal ltcg_delta"


class TestSweetSpotProvisionalIncomeYtd:
    """F9 regression: sweet-spot provisional income must include YTD ordinary income.

    When a household has SS + meaningful wages (ytd), the taxable-SS amount returned
    by all_in_at_conversion must be HIGHER than if ytd_magi were ignored.  Before the
    fix, other_inc = opt + conv (ytd omitted), understating provisional income and
    therefore understating taxable SS.
    """

    def _make_household(self) -> Household:
        # Ages 67/65, both collecting SS.  No option income (grants=[]), no ACA.
        # SS sized so that provisional income WITHOUT wages stays below MFJ tier 2
        # ($44K): combined_ss = $600+$400/mo → ~$12K/yr.
        # Without wages: provisional = 0 + 0.5 * 12_000 = $6,000 → 0% taxable.
        # With wages=$30K: provisional = 30_000 + 0.5 * 12_000 = $36,000 → 50% tier.
        return Household(
            your_age=67,
            spouse_age=65,
            base_year=2026,
            your_ss_start_age=67,
            spouse_ss_start_age=65,
            your_ss_fra=600.0,   # $/month — small SS so provisional stays below tier 2
            spouse_ss_fra=400.0,
            your_fra_age=67,
            spouse_fra_age=67,
            filing_status="MFJ",
            your_aca_enrolled=False,
            spouse_aca_enrolled=False,
            cpi_assumption=0.0,
            ss_cola=0.0,
            grants=[],   # no option income — prevents SS from hitting 85% cap without wages
        )

    def test_ytd_wages_raise_taxable_ss_in_sweet_spot(self) -> None:
        """Taxable SS must be higher when ytd wages push provisional income up.

        Setup: SS is small ($12K/yr combined).  Without wages provisional income =
        0.5 * 12_000 = $6,000 → below $32K MFJ tier 1 → 0% taxable.
        With wages=$30K: provisional = $36K → in the 50% tier → taxable SS > 0.
        Before the F9 fix other_inc omitted ytd_magi, so taxable SS was always 0.
        """
        from engine.tax import taxable_ss as _taxable_ss
        from models.ytd_income import YTDSnapshot

        hh = self._make_household()
        year = 2026

        # YTD wages that push provisional income into the 50%-taxable tier.
        wages = 30_000.0
        ytd = YTDSnapshot(tax_year=year, wages_ytd=wages)
        assert ytd.magi_ytd == pytest.approx(wages), "precondition: magi_ytd == wages_ytd"

        b_no_ytd = base_income_for_year(hh, year, ytd=None)
        b_with_ytd = base_income_for_year(hh, year, ytd=ytd)

        combined_ss = b_with_ytd.combined_ss
        assert combined_ss > 0, "precondition: SS must be active"

        # Without wages: provisional = 0 + 0.5*combined_ss → taxable SS should be 0.
        tss_no_wages = _taxable_ss(combined_ss, 0.0, filing_status="MFJ")
        assert tss_no_wages == pytest.approx(0.0), (
            f"precondition: no-wages taxable SS must be 0, got {tss_no_wages:.0f}"
        )

        # With wages: provisional = wages + 0.5*combined_ss → taxable SS > 0.
        tss_with_wages = _taxable_ss(combined_ss, wages, filing_status="MFJ")
        assert tss_with_wages > 0.0, (
            f"precondition: wages-included taxable SS must be > 0, got {tss_with_wages:.0f}"
        )

        # base_gross (opt + tss) must be higher with ytd wages (tss increased).
        assert b_with_ytd.base_gross > b_no_ytd.base_gross, (
            "base_gross (which includes taxable SS) must be higher with ytd wages"
        )

        # At zero conversion, magi with ytd must exceed magi without ytd.
        conv = 0.0
        res_no_ytd = all_in_at_conversion(hh, b_no_ytd, conv, 0.0)
        res_with_ytd = all_in_at_conversion(hh, b_with_ytd, conv, 0.0)

        assert res_with_ytd.magi > res_no_ytd.magi, (
            f"magi with ytd ({res_with_ytd.magi:.0f}) must exceed magi without ytd ({res_no_ytd.magi:.0f})"
        )


class TestNoFabricatedNiitCitation:
    """Audit finding 5 (MEDIUM, doc-only, 2026-08): 7 sites (5 in
    engine/sweet_spot_compute.py, 2 in models/ytd_income.py) cited a
    nonexistent "IRC §1411(d)(3)" as authority for excluding tax-exempt
    municipal-bond interest from NIIT. No such subsection supports this --
    municipal interest is excluded from NIIT because it is excluded from
    gross income entirely under IRC §103 (so it never enters AGI/MAGI in the
    first place), not because §1411(d)(3) carves it out of NIIT-MAGI.
    No functional impact; this is a citation-only regression guard."""

    def test_sweet_spot_compute_has_no_bogus_citation(self) -> None:
        import inspect

        import engine.sweet_spot_compute as mod

        src = inspect.getsource(mod)
        assert "1411(d)(3)" not in src, (
            "engine/sweet_spot_compute.py still cites the nonexistent "
            "IRC §1411(d)(3) as NIIT-MAGI muni-exclusion authority"
        )

    def test_ytd_income_has_no_bogus_citation(self) -> None:
        import inspect

        import models.ytd_income as mod

        src = inspect.getsource(mod)
        assert "1411(d)(3)" not in src, (
            "models/ytd_income.py still cites the nonexistent IRC §1411(d)(3) "
            "as NIIT-MAGI muni-exclusion authority"
        )


class TestNoStaleScenarioLineNumberPointers:
    """Audit finding 6 (LOW, doc-only, 2026-08): 3 comments in
    engine/sweet_spot_compute.py said "mirrors scenario.py:LINE" with stale
    line numbers (the formulas themselves are still correct -- only the
    pointers rotted as scenario.py evolved). Fixed by replacing brittle
    line-number references with function/section-name references (the same
    grep-friendly style already used elsewhere in this module, e.g.
    _ltcg_stack_tax's docstring: "grep for that header rather than a line
    number -- it has moved before")."""

    def test_no_stale_line_pointers(self) -> None:
        import inspect

        import engine.sweet_spot_compute as mod

        src = inspect.getsource(mod)
        # The two specific stale pointers audit finding 6 flagged.
        assert "scenario.py:643-646" not in src, (
            "stale scenario.py:643-646 line-number pointer still present"
        )
        assert "engine.scenario:564-576" not in src, (
            "stale engine.scenario:564-576 line-number pointer still present"
        )
