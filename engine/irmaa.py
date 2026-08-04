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
# Tiers 1-4 are CPI-indexed annually. Tier 5 ($750K) is FROZEN for TY2020-2027
# (Bipartisan Budget Act of 2018, Pub. L. 115-123, §53109) and then resumes
# CPI indexing for TY2028+ per 42 U.S.C. §1395r(i)(5)(C) (audit-0802 F2 —
# corrects the prior "frozen forever" model). See _index_irmaa_tiers.
# (magi_threshold, annual_part_b_total_per_person, annual_part_d_surcharge_per_person)
IRMAA_TIERS_MFJ = [
    (218_000, 284.10 * 12, 14.50 * 12),  # Tier 1 — CPI-indexed
    (274_000, 405.80 * 12, 37.50 * 12),  # Tier 2 — CPI-indexed
    (342_000, 527.50 * 12, 60.40 * 12),  # Tier 3 — CPI-indexed
    (410_000, 649.20 * 12, 83.30 * 12),  # Tier 4 — CPI-indexed
    (750_000, 689.90 * 12, 91.00 * 12),  # Tier 5 — frozen 2020-2027, indexed 2028+
]

# 2026 IRMAA thresholds (Single) — each threshold is roughly half of MFJ.
# Tiers 1-4 are CPI-indexed annually. Tier 5 ($500K) is FROZEN for TY2020-2027
# (Bipartisan Budget Act of 2018, Pub. L. 115-123, §53109) and then resumes
# CPI indexing for TY2028+ per 42 U.S.C. §1395r(i)(5)(C) (audit-0802 F2 —
# corrects the prior "frozen forever" model). See _index_irmaa_tiers.
# (magi_threshold, annual_part_b_total_per_person, annual_part_d_surcharge_per_person)
IRMAA_TIERS_SINGLE = [
    (109_000, 284.10 * 12, 14.50 * 12),  # Tier 1 — CPI-indexed
    (137_000, 405.80 * 12, 37.50 * 12),  # Tier 2 — CPI-indexed
    (171_000, 527.50 * 12, 60.40 * 12),  # Tier 3 — CPI-indexed
    (205_000, 649.20 * 12, 83.30 * 12),  # Tier 4 — CPI-indexed
    (500_000, 689.90 * 12, 91.00 * 12),  # Tier 5 — frozen 2020-2027, indexed 2028+
]

# Base premiums (no surcharge)
BASE_PART_B = 202.90 * 12  # annual per person
BASE_PART_D = 0.0  # base Part D surcharge is $0


# Medicare Part B/D premium growth rate. Premiums — and therefore the IRMAA
# surcharge *dollars* — have historically risen materially faster than general
# CPI (the 2026 Medicare Trustees Report projects long-run per-capita Part B
# growth in the ~5-6%/yr range vs ~2.5% CPI). The MAGI *thresholds* are
# CPI-indexed (see _index_irmaa_tiers); the surcharge dollars are indexed by
# this medical-inflation rate so out-year IRMAA cost is not frozen at 2026
# levels (audit A1). Pass medical_cpi=0.0 to irmaa_surcharge to restore the
# legacy frozen-dollar behavior.
MEDICAL_INFLATION = 0.055


def _index_irmaa_tiers(
    base_tiers: Sequence[tuple[float, float, float]],
    year: int,
    cpi: float,
) -> list[tuple[float, float, float]]:
    """Return tiers with MAGI thresholds CPI-indexed, including the last tier.

    Tiers 1-4 are inflation-adjusted annually per CMS rulemaking.
    Tier 5 ($750K MFJ / $500K Single) is FROZEN by statute for TY2020-2027
    (BBA 2018, Pub. L. 115-123, §53109), then resumes CPI indexing for
    TY2028+ off an Aug-2026-effective base per 42 U.S.C. §1395r(i)(5)(C):
    top(year) = base_top * (1+cpi) ** (year - 2027). (audit-0802 F2 —
    corrects the prior "frozen forever" model.) Indexed lower tiers are
    additionally clamped to the (possibly still-indexed) top tier so the
    returned thresholds are always monotonically non-decreasing (audit C5).
    """
    if not base_tiers:
        return []
    last_t, last_pb, last_pd = base_tiers[-1]
    # index_value(last_t, year - 1, cpi) reproduces top(year) exactly:
    # index_value freezes for year <= BASE_YEAR (2026), so year - 1 <= 2026
    # (i.e. year <= 2027) keeps the top tier at its base value; for year >=
    # 2028 it scales by (1+cpi) ** ((year-1) - 2026) == (1+cpi) ** (year-2027).
    # No $50 rounding is applied here (round50 defaults to False), matching
    # the unrounded treatment already used for Tiers 1-4 below.
    top_threshold = index_value(last_t, year - 1, cpi)
    # Index tiers 1-4; clamp each to the top tier so the threshold list stays
    # monotonically non-decreasing. In extreme out-years/CPI an indexed lower
    # tier can otherwise overtake the top tier, which would desync the
    # forward scans (irmaa_tier / irmaa_next_threshold) from the reverse scan
    # (irmaa_surcharge) and report positive room to an already-crossed tier.
    indexed = [
        (min(index_value(t, year, cpi), top_threshold), pb, pd) for t, pb, pd in base_tiers[:-1]
    ]
    indexed.append((top_threshold, last_pb, last_pd))
    return indexed


def irmaa_surcharge(
    magi: float,
    num_people: int = 2,
    base_part_b: float = BASE_PART_B,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
    medical_cpi: float = MEDICAL_INFLATION,
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
        medical_cpi: annual growth rate for surcharge dollars (default MEDICAL_INFLATION);
            pass 0.0 to freeze at 2026 dollars.

    Returns:
        Total annual surcharge above base premiums.
    """
    base_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    # Index MAGI thresholds; Tier 5 is frozen through 2027, indexed 2028+ —
    # see _index_irmaa_tiers.
    tiers = _index_irmaa_tiers(base_tiers, year, cpi)
    for threshold, part_b_annual, part_d_annual in reversed(tiers):
        if magi > threshold:
            surcharge_per_person = (part_b_annual - base_part_b) + (part_d_annual - BASE_PART_D)
            # Index surcharge dollars forward at the medical-inflation rate (audit A1).
            # Both tier and base premiums share this rate, so scaling the net
            # surcharge is exact; at year==BASE_YEAR the factor is 1.0 (no change).
            surcharge_per_person = index_value(surcharge_per_person, year, medical_cpi)
            return surcharge_per_person * num_people
    return 0.0


def irmaa_tier(
    magi: float,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> int:
    """Return IRMAA tier (0 = no surcharge, 1-5 = tiers).

    Args:
        magi: Modified Adjusted Gross Income.
        filing_status: "MFJ" (default) or "Single".
        year: calendar year — indexes Tier 1-4 thresholds forward from 2026 base.
        cpi: annual CPI rate for indexing (default 2.5%).

    Tiers 1-4 are CPI-indexed; Tier 5 ($750K MFJ / $500K Single) is frozen
    by statute for 2020-2027 and resumes CPI indexing for 2028+ (see
    _index_irmaa_tiers).
    """
    base_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    tiers = _index_irmaa_tiers(base_tiers, year, cpi)
    for i, (threshold, _, _) in enumerate(tiers):
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
    medical_cpi: float = MEDICAL_INFLATION,
) -> tuple[float, int]:
    """
    Calculate IRMAA that will be charged 2 years AFTER the income year.

    Returns:
        (annual_surcharge, your_medicare_age)

    your_medicare_age is your_age_income_year + 2 — an AGE (your age in the
    payment year), NOT a calendar year. All current callers discard this
    second element; it exists only as a convenience echo of the age math
    below, not a payment-year identifier.
    The surcharge is charged in the payment year (income_year + 2).
    Only counts people who are 65+ in that payment year.
    year/cpi: index MAGI thresholds to the payment year (income_year + 2), matching CMS published thresholds.
    """
    medicare_your_age = your_age_income_year + 2
    medicare_spouse_age = spouse_age_income_year + 2
    # Only MFJ has a second Medicare beneficiary. For any non-MFJ status the spouse age is not a
    # real second enrollee — IRMAA surcharges are per-beneficiary (42 U.S.C. §1395r(i) / IRC
    # §1839(i)) — so cap the count at the primary. Mirrors the _is_mfj_curves guard in
    # engine/aca_irmaa_compute.py.
    if filing_status == "MFJ":
        on_medicare = sum(1 for a in [medicare_your_age, medicare_spouse_age] if a >= 65)
    else:
        on_medicare = 1 if medicare_your_age >= 65 else 0

    if on_medicare == 0:
        return 0.0, 0

    surcharge = irmaa_surcharge(
        income_year_magi,
        on_medicare,
        base_part_b,
        filing_status,
        year=year,
        cpi=cpi,
        medical_cpi=medical_cpi,
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
        Dollar distance to the next un-crossed tier threshold, or float('inf') if
        MAGI already exceeds all tiers (no next tier exists).  Callers can use
        ``math.isinf(room)`` to detect the "Max tier — no headroom" case and
        distinguish it from 0.0 (MAGI exactly at a tier boundary).
    """
    base_tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    tiers = _index_irmaa_tiers(base_tiers, year, cpi)
    for threshold, _, _ in tiers:
        if magi <= threshold:
            return threshold - magi
    return float("inf")
