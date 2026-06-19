"""IRMAA — Income-Related Monthly Adjustment Amount for Medicare.

Key facts:
- 2-year lookback: income in year X determines IRMAA in year X+2
- Applies to both Medicare Part B AND Part D premiums
- Thresholds are for MAGI (includes Roth conversions, option income, etc.)
- Both spouses pay surcharge based on joint MAGI
"""

from __future__ import annotations

from collections.abc import Sequence

from engine.tax_indexing import BASE_YEAR, DEFAULT_CPI, index_value

# 2026 IRMAA thresholds (MFJ).
# Tiers 1-4 are CPI-indexed annually; Tier 5 ($750K) is FROZEN by statute since 2020.
# (magi_threshold, annual_part_b_total_per_person, annual_part_d_surcharge_per_person)
IRMAA_TIERS_MFJ = [
    (218_000, 284.10 * 12, 14.50 * 12),  # Tier 1 — CPI-indexed
    (274_000, 405.80 * 12, 37.50 * 12),  # Tier 2 — CPI-indexed
    (342_000, 527.50 * 12, 60.40 * 12),  # Tier 3 — CPI-indexed
    (410_000, 649.20 * 12, 83.30 * 12),  # Tier 4 — CPI-indexed
    (750_000, 689.90 * 12, 91.00 * 12),  # Tier 5 — FROZEN (not indexed)
]

# 2026 IRMAA thresholds (Single) — each threshold is roughly half of MFJ.
# Tiers 1-4 are CPI-indexed annually; Tier 5 ($500K) is FROZEN by statute since 2020.
# (magi_threshold, annual_part_b_total_per_person, annual_part_d_surcharge_per_person)
IRMAA_TIERS_SINGLE = [
    (109_000, 284.10 * 12, 14.50 * 12),  # Tier 1 — CPI-indexed
    (137_000, 405.80 * 12, 37.50 * 12),  # Tier 2 — CPI-indexed
    (171_000, 527.50 * 12, 60.40 * 12),  # Tier 3 — CPI-indexed
    (205_000, 649.20 * 12, 83.30 * 12),  # Tier 4 — CPI-indexed
    (500_000, 689.90 * 12, 91.00 * 12),  # Tier 5 — FROZEN (not indexed)
]

# Base premiums (no surcharge)
BASE_PART_B = 202.90 * 12  # annual per person
BASE_PART_D = 0.0  # base Part D surcharge is $0


def _index_irmaa_tiers(
    base_tiers: Sequence[tuple[float, float, float]],
    year: int,
    cpi: float,
) -> list[tuple[float, float, float]]:
    """Return tiers with MAGI thresholds CPI-indexed, except the last (frozen) tier.

    Tiers 1-4 are inflation-adjusted annually per CMS rulemaking.
    Tier 5 ($750K MFJ / $500K Single) has been frozen by statute since 2020
    and must never be indexed forward.
    """
    if not base_tiers:
        return []
    indexed = [(index_value(t, year, cpi), pb, pd) for t, pb, pd in base_tiers[:-1]]
    # Last tier: preserve base threshold exactly (frozen)
    last_t, last_pb, last_pd = base_tiers[-1]
    indexed.append((last_t, last_pb, last_pd))
    return indexed


def irmaa_surcharge(
    magi: float,
    num_people: int = 2,
    base_part_b: float = BASE_PART_B,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """
    Calculate total annual IRMAA surcharge for household.

    Args:
        magi: Modified Adjusted Gross Income
        num_people: number of people on Medicare (1 or 2)
        base_part_b: annual per-person base Part B premium (default: module constant)
        filing_status: "MFJ" (default) or "Single" — selects threshold table
        year: calendar year (used to index MAGI thresholds forward from 2026 base)
        cpi: annual CPI rate for indexing (default 2.5%)

    Returns:
        Total annual surcharge above base premiums.
    """
    base_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    # Index MAGI thresholds (Tiers 1-4 only); Tier 5 is frozen — see _index_irmaa_tiers
    tiers = _index_irmaa_tiers(base_tiers, year, cpi)
    for threshold, part_b_annual, part_d_annual in reversed(tiers):
        if magi > threshold:
            surcharge_per_person = (part_b_annual - base_part_b) + (part_d_annual - BASE_PART_D)
            return surcharge_per_person * num_people
    return 0.0


def irmaa_tier(magi: float, filing_status: str = "MFJ") -> int:
    """Return IRMAA tier (0 = no surcharge, 1-5 = tiers)."""
    base_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    for i, (threshold, _, _) in enumerate(base_tiers):
        if magi <= threshold:
            return 0 if i == 0 else i
    return 5


def irmaa_for_year(
    income_year_magi: float,
    your_age_income_year: int,
    spouse_age_income_year: int,
    base_part_b: float = BASE_PART_B,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> tuple[float, int]:
    """
    Calculate IRMAA that will be charged 2 years AFTER the income year.

    Returns:
        (annual_surcharge, medicare_year)

    The surcharge applies in medicare_year = income_year + 2.
    Only counts people who are 65+ in the medicare_year.
    year/cpi: index MAGI thresholds to the payment year (income_year + 2), matching CMS published thresholds.
    """
    medicare_your_age = your_age_income_year + 2
    medicare_spouse_age = spouse_age_income_year + 2
    on_medicare = sum(1 for a in [medicare_your_age, medicare_spouse_age] if a >= 65)

    if on_medicare == 0:
        return 0.0, 0

    surcharge = irmaa_surcharge(
        income_year_magi, on_medicare, base_part_b, filing_status, year=year, cpi=cpi
    )
    return surcharge, your_age_income_year + 2


def irmaa_next_threshold(
    magi: float,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """How much room before hitting the next IRMAA tier.

    Args:
        magi: Modified Adjusted Gross Income for the income year.
        filing_status: "MFJ" (default) or "Single" — selects threshold table.
        year/cpi: index MAGI thresholds forward from 2026 base.

    Returns:
        Dollar distance to the next un-crossed tier threshold, or 0.0 if already
        above the highest tier.
    """
    base_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    tiers = _index_irmaa_tiers(base_tiers, year, cpi)
    for threshold, _, _ in tiers:
        if magi <= threshold:
            return threshold - magi
    return 0.0
