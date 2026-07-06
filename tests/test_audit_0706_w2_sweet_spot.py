"""TDD regression tests for audit-0706 wave-2 sweet_spot_compute findings.

Finding headroom-sweetspot-0 (medium):
    senior_bonus_deduction phaseout uses MAGI (including muni interest) instead of
    NIIT-MAGI (excluding muni interest, per IRC §1411(d)(3)).  When muni interest is
    present ytd_magi > ytd_niit_magi, so the bonus is phased out too aggressively,
    producing a lower deduction than correct.  Fix: pass (opt + tss + ytd_niit_magi)
    to senior_bonus_deduction in both base_income_for_year and all_in_at_conversion.

Finding headroom-sweetspot-3 (low):
    find_sweet_spots: `continue` guard for curr.conv==0 skips prev_marginal update,
    so a mid-sweep zero-conv entry leaves prev_marginal stale.  On the next non-zero
    step a large apparent jump can be detected spuriously.  Fix: move
    prev_marginal = marginal BEFORE the continue so it updates every iteration.
"""

import pytest

from engine.sweet_spot_compute import (
    ConversionResult,
    all_in_at_conversion,
    base_income_for_year,
    find_sweet_spots,
)
from models.household import Household
from models.ytd_income import YTDSnapshot

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DEFAULTS_CR: dict = {
    "conv_tax": 0.0,
    "irmaa_delta": 0.0,
    "aca_loss": 0.0,
    "niit_delta": 0.0,
    "ltcg_delta": 0.0,
    "magi": 0.0,
    "taxable_inc": 0.0,
    "room_12": 0.0,
    "room_22": 0.0,
}


def _cr(conv: float, all_in: float, **kwargs: float) -> ConversionResult:
    """Build a minimal ConversionResult for find_sweet_spots testing."""
    fields = dict(_DEFAULTS_CR)
    fields.update(kwargs)
    return ConversionResult(conv=conv, all_in=all_in, **fields)


def _minimal_household(base_year: int = 2026) -> Household:
    """Return a minimal MFJ Household with no YTD data."""
    return Household(
        your_age=65,
        spouse_age=65,
        your_ira=1_700_000,
        spouse_ira=1_700_000,
        your_ss_fra=2_400,
        spouse_ss_fra=1_200,
        your_ss_start_age=70,
        spouse_ss_start_age=67,
        base_year=base_year,
    )


# ---------------------------------------------------------------------------
# headroom-sweetspot-0: senior_bonus_deduction uses NIIT-MAGI, not MAGI
# ---------------------------------------------------------------------------


class TestSeniorBonusNiitMagi:
    """senior_bonus_deduction must use NIIT-MAGI (excl. muni interest), not MAGI."""

    def test_base_income_senior_bonus_uses_niit_magi(self) -> None:
        """When muni interest is present, senior_bonus should be the same as without it.

        senior_bonus_deduction phaseout must use NIIT-MAGI (excludes tax-exempt
        muni interest per IRC §1411(d)(3)), not MAGI.

        YTDSnapshot.tax_exempt_interest_ytd drives the magi_ytd vs niit_magi_ytd
        difference: niit_magi_ytd = magi_ytd - tax_exempt_interest_ytd.

        Setup: one YTD has $10K muni (tax_exempt_interest_ytd=10_000) so
        magi_ytd > niit_magi_ytd by $10K; the other has no muni.
        Income is in the OBBBA phaseout range ($150K–$250K MFJ 2026) so the
        $10K difference meaningfully shifts the senior_bonus amount.

        After fix: total_ded with muni == total_ded without muni
        (senior_bonus uses niit_magi_ytd, same in both cases).
        Before fix: total_ded with muni < total_ded without (over-phaseout).
        """
        hh = _minimal_household()

        # Build YTD incomes in phaseout range.  Use wages as the base income
        # source so it flows into magi_ytd.  Add muni interest to the first only.
        muni_interest = 10_000.0
        wages = 150_000.0  # in OBBBA phaseout range for MFJ

        ytd_with_muni = YTDSnapshot(wages_ytd=wages, tax_exempt_interest_ytd=muni_interest)
        ytd_no_muni = YTDSnapshot(wages_ytd=wages, tax_exempt_interest_ytd=0.0)

        # Sanity: the two YTDs have the same niit_magi_ytd, different magi_ytd.
        assert ytd_with_muni.niit_magi_ytd == pytest.approx(ytd_no_muni.niit_magi_ytd)
        assert ytd_with_muni.magi_ytd == pytest.approx(ytd_no_muni.magi_ytd + muni_interest)

        base_with_muni = base_income_for_year(hh, 2026, ytd=ytd_with_muni)
        base_no_muni = base_income_for_year(hh, 2026, ytd=ytd_no_muni)

        # After fix: senior_bonus uses niit_magi_ytd (same in both) → equal total_ded.
        assert base_with_muni.total_ded == pytest.approx(base_no_muni.total_ded, rel=1e-6), (
            "base_income_for_year: senior_bonus_deduction is using MAGI (incl. muni) "
            "instead of NIIT-MAGI. "
            f"with-muni total_ded={base_with_muni.total_ded:.2f}, "
            f"no-muni total_ded={base_no_muni.total_ded:.2f}"
        )

    def test_all_in_at_conversion_senior_bonus_uses_niit_magi(self) -> None:
        """all_in_at_conversion senior_bonus must use NIIT-MAGI, not MAGI.

        When muni interest is present the conv_tax should be identical to the
        case where no muni interest exists, because senior_bonus phaseout is
        driven by NIIT-MAGI (which excludes muni interest) only.
        """
        hh = _minimal_household()

        muni_interest = 10_000.0
        wages = 150_000.0

        ytd_with_muni = YTDSnapshot(wages_ytd=wages, tax_exempt_interest_ytd=muni_interest)
        ytd_no_muni = YTDSnapshot(wages_ytd=wages, tax_exempt_interest_ytd=0.0)

        base_with = base_income_for_year(hh, 2026, ytd=ytd_with_muni)
        base_without = base_income_for_year(hh, 2026, ytd=ytd_no_muni)

        conv = 50_000.0
        result_with = all_in_at_conversion(hh, base_with, conv, net_inv_income=0.0)
        result_without = all_in_at_conversion(hh, base_without, conv, net_inv_income=0.0)

        # After fix: senior_bonus uses (opt + conv + tss + ytd_niit_magi) → same in both.
        assert result_with.conv_tax == pytest.approx(result_without.conv_tax, rel=1e-6), (
            "all_in_at_conversion: senior_bonus_deduction is using MAGI (incl. muni) "
            "instead of NIIT-MAGI. "
            f"conv_tax with muni={result_with.conv_tax:.2f}, "
            f"conv_tax no muni={result_without.conv_tax:.2f}"
        )


# ---------------------------------------------------------------------------
# headroom-sweetspot-3: find_sweet_spots false jump when mid-sweep conv==0
# ---------------------------------------------------------------------------


class TestFindSweetSpotsFalseJump:
    """find_sweet_spots must NOT produce false jumps when a mid-sweep conv==0 entry exists."""

    def test_no_false_positive_after_mid_sweep_zero_conv(self) -> None:
        """Mid-sweep conv==0 entry must not leave prev_marginal stale, causing false jump.

        Scenario where bug fires:
          i=1: conv=1_000, all_in=10.0   marginal=(10-0)/1000*100=1.0%  → prev_marginal=1.0
          i=2: conv=0,     all_in=500.0  continue skips prev_marginal update → stale 1.0%
          i=3: conv=3_000, all_in=560.0  marginal=(560-500)/1000*100=6.0%
               delta = 6.0 - 1.0 = 5.0 > 2.0  → FALSE SPOT (bug)
               delta = 6.0 - 49.0 = -43.0      → no spot  (fix; 49%=(500-10)/1000*100)

        After fix: prev_marginal is updated to 49.0% at i==2, so no false spot fires.
        """
        results = [
            _cr(conv=0, all_in=0.0),
            _cr(conv=1_000, all_in=10.0),   # marginal=1.0%
            _cr(conv=0, all_in=500.0),       # mid-sweep zero; marginal=(500-10)/1000*100=49%
            _cr(conv=3_000, all_in=560.0),   # marginal=(560-500)/1000*100=6.0%
        ]

        spots = find_sweet_spots(results)

        # With fix: prev_marginal after i==2 is 49%. Delta at i==3 = 6%-49% < 0 → no spot.
        # With bug: prev_marginal is stale 1.0%. Delta = 6%-1% = 5% > 2% → false spot.
        assert len(spots) == 0, (
            f"find_sweet_spots produced {len(spots)} false spot(s) due to stale "
            f"prev_marginal after mid-sweep conv==0 entry. Spots: {spots}"
        )

    def test_no_false_positive_simpler_case(self) -> None:
        """Simpler variant: mid-sweep zero, next step small — both before and after should be 0."""
        results = [
            _cr(conv=0, all_in=0.0),
            _cr(conv=1_000, all_in=15.0),   # marginal=1.5%
            _cr(conv=0, all_in=300.0),       # mid-sweep zero; marginal=28.5%
            _cr(conv=3_000, all_in=345.0),   # marginal=(345-300)/1000*100=4.5%
        ]

        spots = find_sweet_spots(results)

        # With fix: prev_marginal after conv==0 step = 28.5%. Delta = 4.5-28.5 < 0 → no spot.
        # With bug: prev_marginal stale=1.5%. Delta = 4.5-1.5 = 3.0 > 2.0 → FALSE spot.
        stale_baseline_spots = [
            s for s in spots
            if abs(s.marginal_before - 1.5) < 0.01 and abs(s.marginal_after - 4.5) < 0.01
        ]
        assert len(stale_baseline_spots) == 0, (
            "find_sweet_spots: false jump detected because prev_marginal was not "
            f"updated for the mid-sweep conv==0 entry. Spots: {spots}"
        )
