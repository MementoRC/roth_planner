"""Regression: ACA-Explorer IRMAA curves use payment-year (income_year + 2) indexing.

IRMAA has a 2-year lookback (IRC §1395r / CMS): income realized in year Y is
judged against the thresholds published for, and paid in, year Y+2. The cost
curves must index IRMAA thresholds — and gate Medicare eligibility — to Y+2.
"""

import pytest

from engine.aca_irmaa_compute import (
    _nontaxable_ss,
    compute_cost_curves,
    compute_year_by_year_timeline,
)
from engine.irmaa import irmaa_next_threshold, irmaa_tier
from models.household import Household


def test_cost_curve_irmaa_tier_uses_payment_year_thresholds():
    """A MAGI sitting between the income-year and payment-year tier-1 thresholds
    must read as tier 0 (payment-year indexing), not tier 1 (income-year).

    MFJ tier-1 base $218k:
      indexed to 2030 = 218_000 * (1.025)^4 ≈ $240,631   (income year)
      indexed to 2032 = 218_000 * (1.025)^6 ≈ $252,813   (payment year)

    MAGI = $246,000 sits between the two thresholds, so income-year indexing
    would produce tier 1, but payment-year (correct) indexing produces tier 0.
    """
    # your_age=63 → payment year 2032 age = 63 + (2032 - 2026) = 69 ≥ 65: on Medicare
    hh = Household(your_age=63, spouse_age=63)
    income_year, cpi = 2030, 0.025
    magi = 246_000

    # Discriminator sanity: the two conventions genuinely disagree at this MAGI.
    assert irmaa_tier(magi, filing_status="MFJ", year=income_year, cpi=cpi) == 1
    assert irmaa_tier(magi, filing_status="MFJ", year=income_year + 2, cpi=cpi) == 0

    cc = compute_cost_curves(
        [magi],
        base_magi=magi,
        net_inv_income=0.0,
        hh=hh,
        year=income_year,
        cpi=cpi,
    )
    assert cc.irmaa_tier_vals[0] == 0, (
        "cost curve must index IRMAA tiers to payment year (income+2)"
    )
    assert cc.irmaa_vals[0] == 0.0, "no surcharge below the payment-year tier-1 threshold"


def test_cost_curve_medicare_gate_uses_payment_year_age():
    """Someone below 65 in the income year but 65+ in the payment year DOES incur
    IRMAA on that year's high MAGI; the gate must use the payment-year age.

    your_age=59 at base_year=2026:
      income year 2030 → your age = 59 + (2030 - 2026) = 63  (<65, not yet on Medicare)
      payment year 2032 → your age = 59 + (2032 - 2026) = 65  (on Medicare)

    A MAGI well above the 2032 payment-year tier-1 threshold must produce a
    non-zero IRMAA surcharge when the gate uses the payment-year age.
    """
    hh = Household(your_age=59, spouse_age=59)
    income_year, cpi = 2030, 0.025
    magi = 300_000  # well above the 2032-indexed tier-1 threshold ≈ $252,813

    cc = compute_cost_curves(
        [magi],
        base_magi=magi,
        net_inv_income=0.0,
        hh=hh,
        year=income_year,
        cpi=cpi,
    )
    assert cc.irmaa_vals[0] > 0.0, "payment-year age >=65 must trigger IRMAA on this year's MAGI"


def test_timeline_irmaa_tier_room_uses_payment_year_indexing():
    """Timeline rows must index IRMAA tier and room to the payment year (row.year + 2).

    Both members >= 70 so medicare_count > 0 every year — IRMAA fields are always
    populated.  A MAGI sitting between the income-year and payment-year tier-1
    thresholds is used to make the two conventions produce different answers at
    the same MAGI, proving the +2 offset is load-bearing.

    MFJ tier-1 base $218k, cpi=0.025:
      indexed to 2028 (income year k=2)  = 218_000 * 1.025^2  ≈ $228,969
      indexed to 2030 (payment year k+2) = 218_000 * 1.025^4  ≈ $240,631

    MAGI = $235,000 sits between the two — income-year → tier 1, payment-year → tier 0.
    """
    # Both 70 at base_year 2026; ages stay >= 65 for the full window.
    hh = Household(your_age=70, spouse_age=70)
    cpi = 0.025
    magi = 235_000

    rows = compute_year_by_year_timeline(hh, magi, years=10, cpi=cpi)

    # Pick k=2 (year=2028): income-year threshold ≈ $228,969 < $235,000 → tier 1
    # payment-year 2030 threshold ≈ $240,631 > $235,000 → tier 0
    k = 2
    row = rows[k]
    assert row.year == hh.base_year + k

    expected_tier = irmaa_tier(magi, filing_status="MFJ", year=row.year + 2, cpi=cpi)
    expected_room = irmaa_next_threshold(magi, filing_status="MFJ", year=row.year + 2, cpi=cpi)

    assert row.irmaa_tier == expected_tier, (
        f"timeline tier must use payment year ({row.year + 2}), got {row.irmaa_tier!r}"
    )
    assert row.irmaa_room == pytest.approx(expected_room, rel=1e-9), (
        f"timeline room must use payment year ({row.year + 2})"
    )

    # Prove the +2 offset actually matters at this MAGI and cpi (would fail pre-fix).
    income_year_tier = irmaa_tier(magi, filing_status="MFJ", year=row.year, cpi=cpi)
    assert income_year_tier != expected_tier, (
        "discriminator check: income-year and payment-year tier must differ at this MAGI/cpi"
    )


class TestNontaxableSsAddback:
    """IRC §36B(d)(2)(B)(iii) non-taxable SS add-back to ACA MAGI."""

    def test_nontaxable_ss_zero_for_default_household(self) -> None:
        """Default HH claims SS at 70 — outside the ACA window (pre-65).

        _nontaxable_ss must return exactly 0.0 for ages 61-64 with ss_start_age=70.
        """
        hh = Household(your_age=61, spouse_age=55, your_aca_enrolled=True)
        # At ACA-year ages (61-64) no one is drawing SS yet (start_age defaults to 70).
        for your_age_in_year in (61, 62, 63, 64):
            result = _nontaxable_ss(
                hh,
                your_age_in_year,
                your_age_in_year - 6,  # spouse 6 years younger, also pre-70
                other_income=80_000.0,
                filing_status="MFJ",
            )
            assert result == 0.0, (
                f"expected 0.0 at age {your_age_in_year} with ss_start_age=70, got {result}"
            )

    def test_nontaxable_ss_positive_when_claiming_during_aca_years(self) -> None:
        """Someone claiming SS at 62 while on ACA (ages 62-64) must yield > 0.

        Set your_ss_start_age=62 so the person is drawing SS while still in the
        ACA window. Non-taxable portion = combined_ss − taxable_ss > 0 at modest
        income levels where the 85% cap is not fully reached.
        """
        hh = Household(
            your_age=62,
            spouse_age=58,
            your_ss_start_age=62,
            your_ss_fra=2_000.0,  # $2,000/month at FRA → $24,000/year raw
            your_fra_age=67,
            ss_cola=0.025,
            your_aca_enrolled=True,
        )
        # At age 62, ya==your_ss_start_age: they just started claiming.
        result = _nontaxable_ss(
            hh,
            62,
            None,  # Single filer perspective for simplicity
            other_income=30_000.0,
            filing_status="Single",
        )
        assert result > 0.0, (
            f"expected non-taxable SS > 0 for age-62 claimant at $30k other income, got {result}"
        )

    def test_compute_cost_curves_higher_aca_magi_when_claiming_ss(self) -> None:
        """compute_cost_curves must produce a lower ACA subsidy when SS is claimed
        during ACA years — because the non-taxable SS add-back raises ACA MAGI.

        Baseline (ss_start_age=70, no SS yet at age 62) vs claimant (ss_start_age=62).
        Both households are Single filers on ACA at the same MAGI sweep point.
        The claimant's ACA subsidy must be strictly lower (higher effective MAGI).
        """
        cpi = 0.025
        year = 2026
        magi_point = 35_000.0  # modest income; subsidy non-zero here

        # Baseline: no SS drawn during ACA years
        hh_base = Household(
            your_age=62,
            spouse_age=62,
            filing_status="Single",
            your_ss_start_age=70,
            your_aca_enrolled=True,
            aca_benchmark_premium_annual=12_000.0,
        )
        cc_base = compute_cost_curves(
            [magi_point],
            base_magi=magi_point,
            net_inv_income=0.0,
            hh=hh_base,
            year=year,
            cpi=cpi,
        )

        # Claimant: drawing SS at 62 while still on ACA
        hh_claim = Household(
            your_age=62,
            spouse_age=62,
            filing_status="Single",
            your_ss_start_age=62,
            your_ss_fra=2_000.0,
            your_fra_age=67,
            ss_cola=0.025,
            your_aca_enrolled=True,
            aca_benchmark_premium_annual=12_000.0,
        )
        cc_claim = compute_cost_curves(
            [magi_point],
            base_magi=magi_point,
            net_inv_income=0.0,
            hh=hh_claim,
            year=year,
            cpi=cpi,
        )

        assert cc_claim.aca_subsidy_vals[0] <= cc_base.aca_subsidy_vals[0], (
            "SS claimant ACA subsidy must be <= baseline (non-taxable SS raises ACA MAGI); "
            f"claimant={cc_claim.aca_subsidy_vals[0]:.2f}, "
            f"baseline={cc_base.aca_subsidy_vals[0]:.2f}"
        )


class TestCostCurvesNontaxableSsField:
    """CostCurves.nontaxable_ss is correctly populated for cliff-vline alignment (audit C7 / aca-4)."""

    def test_nontaxable_ss_zero_for_default_household(self) -> None:
        """Default HH claims SS at 70 — outside the ACA window — so nontaxable_ss is 0.0."""
        hh = Household(
            your_age=62,
            spouse_age=55,
            your_ss_start_age=70,
            your_aca_enrolled=True,
        )
        cc = compute_cost_curves(
            [50_000, 100_000, 150_000],
            base_magi=80_000.0,
            net_inv_income=0.0,
            hh=hh,
            year=2026,
            cpi=0.025,
        )
        assert cc.nontaxable_ss == 0.0, (
            f"expected 0.0 when SS not drawn during ACA years, got {cc.nontaxable_ss}"
        )

    def test_nontaxable_ss_positive_when_drawing_ss_during_aca_years(self) -> None:
        """HH drawing SS at 62 while on ACA must yield nontaxable_ss > 0.

        At modest other income ($30k) the SS inclusion rate is below 85%, so
        non-taxable SS = combined_ss − taxable_ss > 0.
        """
        hh = Household(
            your_age=62,
            spouse_age=62,
            filing_status="Single",
            your_ss_start_age=62,
            your_ss_fra=2_000.0,  # $2,000/month at FRA → ~$24k/year raw
            your_fra_age=67,
            ss_cola=0.025,
            your_aca_enrolled=True,
            aca_benchmark_premium_annual=12_000.0,
        )
        cc = compute_cost_curves(
            [50_000, 100_000, 150_000],
            base_magi=30_000.0,
            net_inv_income=0.0,
            hh=hh,
            year=2026,
            cpi=0.025,
        )
        assert cc.nontaxable_ss > 0.0, (
            f"expected nontaxable_ss > 0 for age-62 SS claimant at $30k base MAGI, "
            f"got {cc.nontaxable_ss}"
        )
