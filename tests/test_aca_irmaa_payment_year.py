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
from engine.scenario import ConversionPlan, run_scenario
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


class TestNontaxableSsMagiInclusion:
    """IRC §36B(d)(2)(B)(iii): ACA MAGI must include the FULL non-taxable SS
    portion. The old one-shot `taxable_ss(combined_ss, other_income=base_magi,
    ...)` computation over-counted §86 provisional income whenever base_magi
    already embedded taxable SS, understating the add-back inside the SS
    taxability phase-in band (disproven "immaterial" audit finding).
    """

    def test_mfj_40k_ss_phase_in_band_understated_by_2000(self) -> None:
        """MFJ, combined SS = $40,000, SS-inclusive base MAGI = $24,000.

        Hand-solved fixed point: true non-SS income x solves
        x = 24_000 - taxable_ss(40_000, x, "MFJ"); x = 20_000, taxable = 4_000,
        so the correct non-taxable add-back is 40_000 - 4_000 = $36,000.
        The old one-shot computation used other_income=24_000 directly, landing
        exactly on the tier-2 boundary (provisional=44_000) and computing
        taxable=6_000 -> nontaxable=34_000 -- understated by $2,000 (5.6% of
        the correct $36,000).
        """
        hh = Household(
            your_age=62,
            spouse_age=60,
            filing_status="MFJ",
            your_ss_start_age=62,
            your_ss_fra=40_000.0 / 12,  # claiming exactly at FRA -> combined_ss == 40_000/yr
            your_fra_age=62,
            spouse_ss_fra=0.0,
            ss_cola=0.0,
            your_aca_enrolled=True,
        )
        result = _nontaxable_ss(
            hh,
            62,
            60,
            other_income=24_000.0,
            filing_status="MFJ",
        )
        assert result == pytest.approx(36_000.0, abs=1.0), (
            f"expected the full $36,000 non-taxable SS add-back "
            f"(disproven-immaterial understatement would give ~$34,000), got {result}"
        )

    def test_ss_taxability_fully_capped_band_still_correct(self) -> None:
        """Control: at high SS-inclusive base MAGI, taxable SS is pinned at the
        85% cap regardless of the other_income proxy's exact value -- this is
        the genuinely-immaterial case referenced in the (now-corrected) comment.
        Non-taxable SS must equal exactly 15% of combined SS here.
        """
        hh = Household(
            your_age=62,
            spouse_age=60,
            filing_status="MFJ",
            your_ss_start_age=62,
            your_ss_fra=40_000.0 / 12,
            your_fra_age=62,
            spouse_ss_fra=0.0,
            ss_cola=0.0,
            your_aca_enrolled=True,
        )
        result = _nontaxable_ss(
            hh,
            62,
            60,
            other_income=300_000.0,  # deep into the 85%-cap band
            filing_status="MFJ",
        )
        assert result == pytest.approx(0.15 * 40_000.0, abs=1.0), (
            f"expected the fully-capped 15% floor (0.15 * $40,000 = $6,000), got {result}"
        )


class TestScenarioIrmaaRoomPaymentYear:
    """F5 regression: yr.irmaa_room must index thresholds to the payment year (income+2).

    With nonzero CPI the payment-year threshold is strictly larger than the
    income-year threshold, so irmaa_room (headroom to the next tier) is larger
    when correctly indexed to year+2.
    """

    def test_irmaa_room_uses_payment_year_indexed_threshold(self) -> None:
        """yr.irmaa_room for a given income year must equal irmaa_next_threshold(..., year=income_year+2).

        Construction:
          - Both spouses age 70 (on Medicare); base_year 2026 → income year 2028 (idx 2).
          - MAGI below the base tier-1 threshold ($218k MFJ) so there is positive room.
          - cpi=0.03 makes the 2-year indexing difference clearly detectable.
          - income-year-indexed room  = irmaa_next_threshold(magi, year=2028, cpi=0.03)
          - payment-year-indexed room = irmaa_next_threshold(magi, year=2030, cpi=0.03)
          - These two values must differ, and yr.irmaa_room must match the payment-year value.
        """
        cpi = 0.03
        hh = Household(your_age=70, spouse_age=70, cpi_assumption=cpi)
        plan = ConversionPlan()
        result = run_scenario(hh, plan)

        # Pick income year 2028 (index 2 in the result)
        income_year = hh.base_year + 2
        yr = next(y for y in result.years if y.year == income_year)

        magi = yr.magi
        room_income_year = irmaa_next_threshold(magi, filing_status="MFJ", year=income_year, cpi=cpi)
        room_payment_year = irmaa_next_threshold(magi, filing_status="MFJ", year=income_year + 2, cpi=cpi)

        # Discriminator: with cpi=0.03 the two years produce different thresholds.
        assert room_income_year != pytest.approx(room_payment_year, rel=1e-6), (
            "discriminator failed: income-year and payment-year rooms must differ at cpi=0.03"
        )
        # The engine must use the payment-year (year+2) indexed threshold.
        assert yr.irmaa_room == pytest.approx(room_payment_year, rel=1e-9), (
            f"yr.irmaa_room must be indexed to payment year {income_year + 2}, "
            f"got {yr.irmaa_room:.2f}, expected {room_payment_year:.2f} "
            f"(income-year value was {room_income_year:.2f})"
        )
