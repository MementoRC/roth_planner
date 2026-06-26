"""Regression: ACA-Explorer IRMAA curves use payment-year (income_year + 2) indexing.

IRMAA has a 2-year lookback (IRC §1395r / CMS): income realized in year Y is
judged against the thresholds published for, and paid in, year Y+2. The cost
curves must index IRMAA thresholds — and gate Medicare eligibility — to Y+2.
"""

from engine.aca_irmaa_compute import compute_cost_curves
from engine.irmaa import irmaa_tier
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
    assert cc.irmaa_vals[0] == 0.0, (
        "no surcharge below the payment-year tier-1 threshold"
    )


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
    assert cc.irmaa_vals[0] > 0.0, (
        "payment-year age >=65 must trigger IRMAA on this year's MAGI"
    )
