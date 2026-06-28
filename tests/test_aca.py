"""Tests for engine.aca — premium tax credit, subsidy, excess APTC, Medicare split."""

import pytest

from engine.aca import aca_applies, aca_excess_aptc_repayment, aca_subsidy, aca_subsidy_loss
from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestACA:
    def test_applies_pre_medicare(self):
        assert aca_applies(61) is True
        assert aca_applies(64) is True
        assert aca_applies(65) is False

    def test_low_income_subsidy(self):
        assert aca_subsidy(30_000) > 15_000

    def test_high_income_subsidy(self):
        aca_subsidy(300_000)  # just verify no error

    def test_benchmark_premium_default_unchanged(self):
        """Default benchmark (21600) must produce same subsidy loss as the old hardcoded constant."""
        base_magi = 60_000.0
        new_magi = 80_000.0
        default_loss = aca_subsidy_loss(base_magi, new_magi, 21_600.0)
        assert default_loss == aca_subsidy_loss(base_magi, new_magi)

    def test_benchmark_premium_doubled_increases_loss(self):
        """Doubling the benchmark raises subsidy loss when new_magi crosses 400% FPL cliff.

        Pre-ARP: subsidies cut off above 400% FPL ($84,600 for family of 2).
        base_magi (60k) stays below the cliff (subsidy positive).
        new_magi (100k) is above the cliff (subsidy = 0 by rule).
        Loss = aca_subsidy(base) - 0; a higher benchmark raises aca_subsidy(base).
        """
        base_magi = 60_000.0
        new_magi = 100_000.0  # above 400% FPL — pre-ARP subsidy = 0
        loss_default = aca_subsidy_loss(base_magi, new_magi, 21_600.0)
        loss_double = aca_subsidy_loss(base_magi, new_magi, 43_200.0)
        assert loss_double > loss_default

    def test_household_benchmark_field_wires_through_scenario(self):
        """Household.aca_benchmark_premium_annual flows into run_scenario aca_loss.

        Base SS keeps the no-conversion MAGI in-band (~148% FPL) so a real
        subsidy exists; the 100k conversion pushes new MAGI above the 400% cliff.
        """
        from dataclasses import replace

        hh_default = Household(
            your_age=61,
            spouse_age=65,  # spouse already on Medicare — only "you" trigger ACA
            your_ira=200_000,
            spouse_ira=200_000,
            your_ss_fra=4_000.0,  # ~$31.2K/yr SS at age 61 → base MAGI in-band (~148% FPL)
            spouse_ss_fra=0.0,
            your_aca_enrolled=True,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
            your_ss_start_age=61,  # claim now so SS flows in the base year
            spouse_ss_start_age=70,
        )
        hh_double = replace(hh_default, aca_benchmark_premium_annual=43_200.0)

        # "You" claim ~$31.2K/yr SS at age 61 → base MAGI ~148% FPL (in-band, a
        # real subsidy exists to lose). A 100k conversion pushes new MAGI above
        # the 400% FPL cliff ($84,600) → subsidy(new)=0, so loss = subsidy(base).
        # A higher benchmark raises subsidy(base) → loss_double > loss_default.
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result_default = run_scenario(hh_default, plan)
        result_double = run_scenario(hh_double, plan)

        loss_default = sum(yr.aca_loss for yr in result_default.years)
        loss_double = sum(yr.aca_loss for yr in result_double.years)
        assert loss_double > loss_default

    def test_enhanced_subsidies_default_off_pre_arp_cliff(self):
        """With enhanced_subsidies_active=False, subsidy = 0 above 400% FPL (pre-ARP cliff)."""
        from engine.aca import FPL_2

        above_cliff = 4.1 * FPL_2  # above 400% FPL
        assert aca_subsidy(above_cliff, enhanced_subsidies_active=False) == 0.0

    def test_enhanced_subsidies_on_no_cliff(self):
        """With enhanced_subsidies_active=True, subsidy > 0 above 400% FPL (8.5% cap, no cliff)."""
        from engine.aca import BENCHMARK_PREMIUM_ANNUAL, FPL_2

        above_cliff = 4.1 * FPL_2  # above 400% FPL
        sub = aca_subsidy(above_cliff, enhanced_subsidies_active=True)
        # Enhanced: subsidy = benchmark - income * 8.5% (no cliff)
        expected = max(BENCHMARK_PREMIUM_ANNUAL - above_cliff * 0.085, 0)
        assert sub > 0.0
        assert sub == pytest.approx(expected)

    def test_household_aca_toggle_wires_through_scenario(self):
        """Household.aca_enhanced_subsidies_active flows into run_scenario aca_loss.

        Base SS keeps MAGI in-band; a 60k conversion pushes new MAGI above the
        400% FPL cliff. Pre-ARP: loss = full base subsidy (cliff zeroes new).
        Enhanced: loss is partial (8.5% cap, new MAGI below the ~127K break-even).
        """
        from dataclasses import replace

        hh_base = Household(
            your_age=61,
            spouse_age=65,  # spouse on Medicare — only "you" triggers ACA
            your_ira=2_000_000,
            spouse_ira=0,
            your_ss_fra=4_000.0,  # ~$31.2K/yr SS at age 61 → base MAGI in-band (~148% FPL)
            spouse_ss_fra=0.0,
            your_aca_enrolled=True,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
            your_ss_start_age=61,  # claim now so SS flows in the base year
            spouse_ss_start_age=70,
            aca_enhanced_subsidies_active=False,
        )
        # Base SS → no-conversion MAGI ~148% FPL (in-band, subsidy(base) > 0).
        # A 60k conversion pushes new MAGI just above the 400% FPL cliff (~91K):
        #   pre-ARP  → subsidy(new) = 0 (cliff)    → loss = full subsidy(base)
        #   enhanced → subsidy(new) > 0 (8.5% cap, ~91K < 127K break-even) → loss is partial
        # so pre-ARP loss exceeds the enhanced loss.
        plan = ConversionPlan(your_conversions={2026: 60_000})
        result_pre_arp = run_scenario(hh_base, plan)
        result_enhanced = run_scenario(replace(hh_base, aca_enhanced_subsidies_active=True), plan)

        loss_pre_arp = result_pre_arp.years[0].aca_loss
        loss_enhanced = result_enhanced.years[0].aca_loss
        # Pre-ARP: subsidy(new)=0 (cliff) → loss = full base subsidy (~$9,500).
        # Enhanced: subsidy(new)>0 (8.5% cap partial, new<127K break-even) → smaller loss.
        assert loss_pre_arp > loss_enhanced

    def test_pre_arp_below_100pct_fpl_no_subsidy(self):
        """Pre-ARP: below 100% FPL the household is PTC-ineligible (audit E1).

        IRC §36B(c)(1)(A) limits the PTC to 100%-400% FPL. The pre-ARP first
        band has no lower bound, so without the floor a family of 2 at ~47% FPL
        was granted a near-full subsidy. The enhanced schedule keeps no floor.
        """
        from engine.aca import FPL_2

        # ~47% FPL (family of 2, 2026 FPL_2=$21,150) — statutorily ineligible
        assert aca_subsidy(10_000, enhanced_subsidies_active=False) == 0.0
        assert aca_subsidy(0.0, enhanced_subsidies_active=False) == 0.0
        # At/above 100% FPL → eligible again
        assert aca_subsidy(FPL_2, enhanced_subsidies_active=False) > 0.0
        # Enhanced (ARPA/IRA) schedule unchanged — no sub-100% floor
        assert aca_subsidy(10_000, enhanced_subsidies_active=True) > 0.0


class TestACAMedicareSplit:
    """Regression for audit B-4: benchmark scales when one spouse transitions to Medicare."""

    def _make_hh(self, your_age: int, spouse_age: int) -> "Household":
        return Household(
            your_age=your_age,
            spouse_age=spouse_age,
            your_ira=200_000,
            spouse_ira=200_000,
            your_ss_fra=0.0,
            spouse_ss_fra=4_000.0,  # ~$31.2K/yr SS (spouse age 61) → base MAGI in-band
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            aca_benchmark_premium_annual=21_600.0,
            aca_enhanced_subsidies_active=False,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=61,  # spouse claims now so SS flows in the base year
        )

    def test_solo_aca_benchmark_halved_vs_couple(self):
        """ages 65/61: you on Medicare, spouse on ACA — benchmark halved, subsidy ~50% of couple."""
        # Couple household (both pre-Medicare): ages 61/61
        hh_couple = self._make_hh(your_age=61, spouse_age=61)
        # Solo household (you on Medicare, spouse on ACA): ages 65/61
        hh_solo = self._make_hh(your_age=65, spouse_age=61)

        # Spouse (age 61 in BOTH households) claims ~$31.2K/yr SS → identical base
        # MAGI ~148% FPL. A $100K conversion pushes new MAGI above the 400% FPL
        # cliff ($84,600 MFJ pre-ARP) → subsidy(new)=0 → loss = subsidy(base).
        # Couple benchmark $21,600; solo (you on Medicare) halved to $10,800, so
        # solo loss ~ half the couple loss.
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result_couple = run_scenario(hh_couple, plan)
        result_solo = run_scenario(hh_solo, plan)

        loss_couple = result_couple.years[0].aca_loss
        loss_solo = result_solo.years[0].aca_loss

        # Solo loss must be strictly less than couple loss (benchmark is halved).
        assert loss_solo < loss_couple, (
            f"Solo ACA loss ({loss_solo:.0f}) should be < couple loss ({loss_couple:.0f})"
        )
        # Solo loss should be approximately half the couple loss (within 20% tolerance
        # to account for FPL-based contribution differences at each benchmark level).
        assert loss_solo == pytest.approx(loss_couple / 2, rel=0.20), (
            f"Expected solo loss ~{loss_couple / 2:.0f}, got {loss_solo:.0f}"
        )

    def test_both_on_medicare_no_aca_loss(self):
        """ages 65/65: both on Medicare — ACA loss must be zero."""
        hh = self._make_hh(your_age=65, spouse_age=65)
        result = run_scenario(hh, ConversionPlan())
        assert result.years[0].aca_loss == 0.0


class TestACAExcessAPTCRepayment:
    """Form 8962 excess-APTC clawback per P.L. 119-21 (uncapped for TY 2026+)."""

    _BENCHMARK = 21_600.0

    def test_zero_advance_aptc_returns_full_refund(self):
        """With advance=0, the user receives the entire actual PTC as Form 1040
        refund — returned as a negative value (negative = refund)."""
        advance = 0.0
        magi = 50_000.0  # low income, full PTC entitlement
        result = aca_excess_aptc_repayment(
            advance_aptc_annual=advance,
            actual_magi=magi,
            benchmark_premium_annual=self._BENCHMARK,
            enhanced_subsidies_active=False,
            filing_status="MFJ",
            year=2026,
        )
        assert result < 0, f"Expected refund (negative), got {result}"
        # Magnitude should equal aca_subsidy(magi, benchmark, ...)
        expected = -aca_subsidy(
            magi,
            self._BENCHMARK,
            enhanced_subsidies_active=False,
            filing_status="MFJ",
        )
        assert result == approx(expected, tol=0.01)

    def test_overpaid_full_clawback_no_cap_2026(self):
        """advance > actual_ptc → full excess owed back (no cap under P.L. 119-21).

        Pre-ARP without P.L. 119-21 would have capped repayment at ~$900-$3,650
        depending on FPL band. Post-P.L. 119-21 the full excess is always owed.
        """
        # At MAGI = $120,000 MFJ, pre-ARP: above 400% FPL ($84,600) → PTC = 0
        # advance=$10,000 → full $10,000 owed back (vs pre-P.L. 119-21 cap of ~$3,650)
        result = aca_excess_aptc_repayment(
            advance_aptc_annual=10_000.0,
            actual_magi=120_000.0,
            benchmark_premium_annual=self._BENCHMARK,
            enhanced_subsidies_active=False,
            year=2026,
        )
        assert result == pytest.approx(10_000.0, abs=1.0)

    def test_underpaid_negative_refund(self):
        """advance < actual_ptc → negative result (household gets additional PTC credit)."""
        from engine.aca import aca_subsidy

        actual_magi = 40_000.0
        actual_ptc = aca_subsidy(actual_magi, self._BENCHMARK, enhanced_subsidies_active=False)
        advance = actual_ptc - 1_000.0  # $1,000 less than entitled PTC
        result = aca_excess_aptc_repayment(
            advance_aptc_annual=advance,
            actual_magi=actual_magi,
            benchmark_premium_annual=self._BENCHMARK,
            enhanced_subsidies_active=False,
            year=2026,
        )
        assert result == pytest.approx(-1_000.0, abs=1.0)

    def test_pre_2026_raises_notimplementederror(self):
        """year=2025 → NotImplementedError (cap table not modeled, base_year=2026)."""
        with pytest.raises(NotImplementedError, match="2025"):
            aca_excess_aptc_repayment(
                advance_aptc_annual=5_000.0,
                actual_magi=60_000.0,
                benchmark_premium_annual=self._BENCHMARK,
                enhanced_subsidies_active=False,
                year=2025,
            )

    def test_scenario_clawback_added_to_federal_tax(self):
        """Integration: advance_aptc_annual > 0 → yr.aca_clawback reflected in federal_tax_amt.

        At MAGI above 400% FPL cliff (pre-ARP), actual PTC = 0, so full advance is owed back.
        The clawback is added to yr.federal_tax_amt so total federal liability increases.
        """
        advance = 8_000.0
        hh_base = Household(
            your_age=61,
            spouse_age=60,
            your_ira=200_000,
            spouse_ira=0,
            your_ss_fra=0.0,
            spouse_ss_fra=0.0,
            your_aca_enrolled=True,
            spouse_aca_enrolled=True,
            aca_benchmark_premium_annual=self._BENCHMARK,
            aca_enhanced_subsidies_active=False,
            advance_aptc_annual=0.0,
            grants=[],
            txn_price_now=0.0,
            txn_price_late=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        # $120K conversion puts aca_magi above 400% FPL → actual PTC = 0 → full clawback
        plan = ConversionPlan(your_conversions={2026: 120_000})
        result_no_aptc = run_scenario(hh_base, plan)
        from dataclasses import replace

        hh_with_aptc = replace(hh_base, advance_aptc_annual=advance)
        result_with_aptc = run_scenario(hh_with_aptc, plan)

        yr_no = result_no_aptc.years[0]
        yr_with = result_with_aptc.years[0]

        assert yr_with.aca_clawback == pytest.approx(advance, abs=1.0)
        assert yr_no.aca_clawback == 0.0
        assert yr_with.federal_tax_amt == pytest.approx(yr_no.federal_tax_amt + advance, abs=1.0)


class TestACA2026:
    """Regression tests locking in 2026 IRS values from Rev. Proc. 2025-25 (IRB 2025-32)."""

    def test_aca_2026_133pct_fpl_flat_rate(self):
        """At exactly 133% FPL, applicable_pct == 2.10% (flat bottom bracket)."""
        from engine.aca import FPL_2, aca_premium_cap_rate

        magi = 1.33 * FPL_2  # exactly at the 133% upper bound
        rate = aca_premium_cap_rate(magi, enhanced_subsidies_active=False, filing_status="MFJ")
        assert rate == pytest.approx(0.0210)

    def test_aca_2026_150pct_fpl_boundary_continuous(self):
        """At the 150% FPL band boundary, applicable_pct is continuous at 4.19%.

        With linear ramp interpolation, the 133-150% band reaches 4.19% at its
        upper edge (matching the next band's start rate), and the 150-200% band
        starts at 4.19%. So values just below and just above 150% should both
        approximate 4.19%, demonstrating ramp continuity (vs the pre-fix step
        discontinuity from 3.14% → 4.19% at 150%).
        """
        from engine.aca import FPL_2, aca_premium_cap_rate

        rate_below = aca_premium_cap_rate(
            1.4999 * FPL_2, enhanced_subsidies_active=False, filing_status="MFJ"
        )
        rate_above = aca_premium_cap_rate(
            1.5001 * FPL_2, enhanced_subsidies_active=False, filing_status="MFJ"
        )
        assert rate_below == pytest.approx(0.0419, abs=1e-4)
        assert rate_above == pytest.approx(0.0419, abs=1e-4)

    def test_aca_2026_175pct_fpl_ramp_midpoint(self):
        """At 175% FPL (midpoint of 150-200% band), applicable_pct is exactly
        midway between the band-start (4.19%) and band-end (6.60%) rates per
        IRC §36B Table A linear ramp.
        """
        from engine.aca import FPL_2, aca_premium_cap_rate

        magi = 1.75 * FPL_2
        rate = aca_premium_cap_rate(magi, enhanced_subsidies_active=False, filing_status="MFJ")
        expected = (0.0419 + 0.0660) / 2  # 0.05395
        assert rate == pytest.approx(expected, abs=1e-6)

    def test_aca_2026_225pct_fpl_ramp_midpoint(self):
        """At 225% FPL (midpoint of 200-250% band), applicable_pct is exactly
        midway between the band-start (6.60%) and band-end (8.44%) rates per
        IRC §36B Table A linear ramp.
        """
        from engine.aca import FPL_2, aca_premium_cap_rate

        magi = 2.25 * FPL_2
        rate = aca_premium_cap_rate(magi, enhanced_subsidies_active=False, filing_status="MFJ")
        expected = (0.0660 + 0.0844) / 2  # 0.0752
        assert rate == pytest.approx(expected, abs=1e-6)

    def test_aca_2026_enhanced_schedule_preserves_step_function(self):
        """Enhanced (ARPA/IRA) schedule preserves step-function caps — no
        interpolation. At 175% FPL the enhanced 150-200% band caps at 2.0%
        flat (vs pre-ARP's smooth ramp through the same FPL range).
        """
        from engine.aca import FPL_2, aca_premium_cap_rate

        rate = aca_premium_cap_rate(
            1.75 * FPL_2, enhanced_subsidies_active=True, filing_status="MFJ"
        )
        assert rate == pytest.approx(0.02)

    def test_aca_2026_300pct_fpl_flat_rate(self):
        """Just above 300% FPL, applicable_pct == 9.96% (flat 300-400% bracket).

        At exactly 300% the lookup still hits the 250-300 bracket (8.44%).
        At 300.01% it enters the flat 300-400% bracket whose rate is 9.96%.
        """
        from engine.aca import FPL_2, aca_premium_cap_rate

        magi = 3.0001 * FPL_2  # just above 300% — enters the flat 300-400% bracket
        rate = aca_premium_cap_rate(magi, enhanced_subsidies_active=False, filing_status="MFJ")
        assert rate == pytest.approx(0.0996)

    def test_aca_2026_400pct_fpl_flat_rate(self):
        """At exactly 400% FPL, applicable_pct == 9.96% (top of flat bracket)."""
        from engine.aca import FPL_2, aca_premium_cap_rate

        magi = 4.00 * FPL_2  # exactly at the 400% cliff boundary
        rate = aca_premium_cap_rate(magi, enhanced_subsidies_active=False, filing_status="MFJ")
        assert rate == pytest.approx(0.0996)

    def test_aca_2026_above_400pct_no_subsidy(self):
        """Above 400% FPL, cap rate returns 0 (cliff — no subsidy) without raising."""
        from engine.aca import FPL_2, aca_premium_cap_rate

        magi = 4.01 * FPL_2  # just above the cliff
        rate = aca_premium_cap_rate(magi, enhanced_subsidies_active=False, filing_status="MFJ")
        assert rate == 0.0

    def test_aca_2026_no_assert_on_high_magi(self):
        """Direct call with fpl_ratio ~9.46 (magi=200K, FPL_2=21150) does NOT raise.

        Regression for audit finding B-5: AssertionError when loop exhausts schedule entries.
        fpl_ratio = 200_000 / 21_150 ≈ 9.46 — well above the 4.0 cliff.
        """
        from engine.aca import aca_premium_cap_rate

        rate = aca_premium_cap_rate(200_000, enhanced_subsidies_active=False, filing_status="MFJ")
        assert rate == 0.0
