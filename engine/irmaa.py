"""IRMAA — Income-Related Monthly Adjustment Amount for Medicare.

Key facts:
- 2-year lookback: income in year X determines IRMAA in year X+2
- Applies to both Medicare Part B AND Part D premiums
- Thresholds are for MAGI (includes Roth conversions, option income, etc.)
- Both spouses pay surcharge based on joint MAGI
"""

from __future__ import annotations

import math
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


def _crosses(magi: float, threshold: float, *, is_top: bool) -> bool:
    """Whether ``magi`` falls in the tier that starts at ``threshold``.

    42 U.S.C. §1395r(i)(3)(C)(i): tiers 1-4 start at "more than" their
    amount, but the top tier starts at "at least" its amount ($500K Single /
    $750K joint), so MAGI exactly on the top threshold is already in the top
    tier (audit-1008 F-53).
    """
    return magi >= threshold if is_top else magi > threshold


def _round_to_1000(value: float) -> float:
    """Round to the nearest multiple of $1,000, halves up (42 U.S.C. §1395r(i)(5)(B))."""
    return float(math.floor(value / 1000.0 + 0.5) * 1000)


def _indexed_threshold(
    base_tiers: Sequence[tuple[float, float, float]], i: int, year: int, cpi: float
) -> float:
    """Indexed MAGI threshold for tier ``i`` in ``year``, before any clamping.

    42 U.S.C. §1395r(i)(5)(B) rounds each INDEXED individual (Single) dollar
    amount to the nearest $1,000. The joint-return amounts are then derived
    from the rounded individual amounts (§1395r(i)(3)(C)(ii): twice the
    amount for tiers 1-4, 150% for the top tier), so for the built-in MFJ
    table this indexes and rounds the matching Single amount and scales it.
    Any other table (tests, custom tiers) is rounded directly. No rounding
    applies while the value is still the published base (year <= base year).
    Audit-1008 F-52: the thresholds used to be indexed without this rounding.

    The top tier is frozen through 2027 and indexed from 2028 off the
    Aug-2026 base (§1395r(i)(5)(C)), which ``index_value(.., year - 1, ..)``
    reproduces (index_value freezes at year <= BASE_YEAR).
    """
    is_top = i == len(base_tiers) - 1
    index_year = year - 1 if is_top else year
    base_t = base_tiers[i][0]
    source: float
    if list(base_tiers) == IRMAA_TIERS_MFJ:
        source = IRMAA_TIERS_SINGLE[i][0]
        multiplier = base_t / source
    else:
        multiplier = 1.0
        source = base_t
    indexed = index_value(source, index_year, cpi)
    if indexed == source:
        return base_t
    return multiplier * _round_to_1000(indexed)


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
    _last_t, last_pb, last_pd = base_tiers[-1]
    # Frozen through 2027, indexed 2028+, rounded to $1,000 — see
    # _indexed_threshold.
    top_threshold = _indexed_threshold(base_tiers, len(base_tiers) - 1, year, cpi)
    # Index tiers 1-4; clamp each to the top tier so the threshold list stays
    # monotonically non-decreasing. In extreme out-years/CPI an indexed lower
    # tier can otherwise overtake the top tier, which would desync the
    # forward scans (irmaa_tier / irmaa_next_threshold) from the reverse scan
    # (irmaa_surcharge) and report positive room to an already-crossed tier.
    indexed = [
        (min(_indexed_threshold(base_tiers, i, year, cpi), top_threshold), pb, pd)
        for i, (_t, pb, pd) in enumerate(base_tiers[:-1])
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
        base_part_b: annual per-person standard Part B premium (default: module
            constant); the Part B surcharge scales in proportion to it
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
    top_index = len(tiers) - 1
    for i in range(top_index, -1, -1):
        threshold, part_b_annual, part_d_annual = tiers[i]
        if _crosses(magi, threshold, is_top=i == top_index):
            # audit-1018 F-37: the Part B IRMAA is a fixed share of the standard
            # premium (42 U.S.C. §1395r(i)(3): tier totals are 35-85% of program
            # cost vs the 25% base share), so the published 2026 surcharge over
            # BASE_PART_B scales with the user's base instead of shrinking as
            # the base rises. Exact at the default base. Part D is independent.
            part_b_surcharge = (part_b_annual - BASE_PART_B) * (base_part_b / BASE_PART_B)
            surcharge_per_person = part_b_surcharge + (part_d_annual - BASE_PART_D)
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
    top_index = len(tiers) - 1
    for i, (threshold, _, _) in enumerate(tiers):
        if not _crosses(magi, threshold, is_top=i == top_index):
            return i
    return len(tiers)


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
    top_index = len(tiers) - 1
    for i, (threshold, _, _) in enumerate(tiers):
        if not _crosses(magi, threshold, is_top=i == top_index):
            return threshold - magi
    return float("inf")


# IRMAA 2-year lookback: income in year X sets Medicare premiums in year X+2.
# Headroom, the exercise optimizer, scenario auto-fill and the sweet-spot
# compute read the offset from here; new code must too, not add 2 itself.
IRMAA_LOOKBACK_YEARS = 2


def irmaa_payment_year(income_year: int) -> int:
    """The calendar year whose Medicare premiums ``income_year``'s MAGI sets."""
    return income_year + IRMAA_LOOKBACK_YEARS


def irmaa_tier1_threshold(income_year: int, filing_status: str, cpi: float) -> float:
    """Tier-1 MAGI threshold that applies to ``income_year``'s income.

    Indexed to the PAYMENT year (income_year + 2), matching CMS published
    thresholds, with the statutory $1,000 rounding (see _indexed_threshold).
    Deliberately without the top-tier clamp, exactly as headroom.py and
    exercise_optimizer.py computed it inline before this helper existed.
    """
    tiers = IRMAA_TIERS_SINGLE if filing_status == "Single" else IRMAA_TIERS_MFJ
    return _indexed_threshold(tiers, 0, irmaa_payment_year(income_year), cpi)
