"""IRMAA — Income-Related Monthly Adjustment Amount for Medicare.

Key facts:
- 2-year lookback: income in year X determines IRMAA in year X+2
- Applies to Medicare Part B and Part D premiums
- Thresholds are for MAGI (includes Roth conversions, option income, etc.)
- Both spouses pay surcharge based on joint MAGI
"""

from __future__ import annotations

# 2026 IRMAA thresholds (MFJ) — indexed annually
# (magi_threshold, annual_part_b_total_per_person, annual_part_d_surcharge_per_person)
IRMAA_TIERS_MFJ = [
    (218_000, 284.10 * 12, 14.50 * 12),  # Tier 1
    (274_000, 405.80 * 12, 37.50 * 12),  # Tier 2
    (342_000, 527.50 * 12, 60.40 * 12),  # Tier 3
    (410_000, 649.20 * 12, 83.30 * 12),  # Tier 4
    (750_000, 689.90 * 12, 91.00 * 12),  # Tier 5
]

# Base premiums (no surcharge)
BASE_PART_B = 202.90 * 12  # annual per person
BASE_PART_D = 0.0  # base Part D surcharge is $0


def irmaa_surcharge(magi: float, num_people: int = 2) -> float:
    """
    Calculate total annual IRMAA surcharge for household.

    Args:
        magi: Modified Adjusted Gross Income (joint)
        num_people: number of people on Medicare (1 or 2)

    Returns:
        Total annual surcharge above base premiums.
    """
    for threshold, part_b_annual, part_d_annual in reversed(IRMAA_TIERS_MFJ):
        if magi > threshold:
            surcharge_per_person = (part_b_annual - BASE_PART_B) + (part_d_annual - BASE_PART_D)
            return surcharge_per_person * num_people
    return 0.0


def irmaa_tier(magi: float) -> int:
    """Return IRMAA tier (0 = no surcharge, 1-5 = tiers)."""
    for i, (threshold, _, _) in enumerate(IRMAA_TIERS_MFJ):
        if magi <= threshold:
            return 0 if i == 0 else i
    return 5


def irmaa_for_year(
    income_year_magi: float, your_age_income_year: int, spouse_age_income_year: int
) -> tuple[float, int]:
    """
    Calculate IRMAA that will be charged 2 years AFTER the income year.

    Returns:
        (annual_surcharge, medicare_year)

    The surcharge applies in medicare_year = income_year + 2.
    Only counts people who are 65+ in the medicare_year.
    """
    medicare_your_age = your_age_income_year + 2
    medicare_spouse_age = spouse_age_income_year + 2
    on_medicare = sum(1 for a in [medicare_your_age, medicare_spouse_age] if a >= 65)

    if on_medicare == 0:
        return 0.0, 0

    surcharge = irmaa_surcharge(income_year_magi, on_medicare)
    return surcharge, your_age_income_year + 2


def irmaa_next_threshold(magi: float) -> float:
    """How much room before hitting the next IRMAA tier."""
    for threshold, _, _ in IRMAA_TIERS_MFJ:
        if magi <= threshold:
            return threshold - magi
    return 0.0
