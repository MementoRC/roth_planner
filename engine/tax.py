"""Federal tax calculations — TCJA/OBBBA permanent brackets, SS taxation."""

# 2025 MFJ brackets (TCJA/OBBBA permanent)
# (upper_bound_of_taxable_income, marginal_rate)
BRACKETS_MFJ = [
    (24_800, 0.10),
    (100_800, 0.12),
    (211_400, 0.22),
    (403_550, 0.24),
    (512_450, 0.32),
    (768_700, 0.35),
    (float("inf"), 0.37),
]

# 2025 Single brackets (for surviving spouse analysis)
BRACKETS_SINGLE = [
    (12_400, 0.10),
    (50_400, 0.12),
    (105_700, 0.22),
    (201_750, 0.24),
    (256_200, 0.32),
    (384_350, 0.35),
    (float("inf"), 0.37),
]

# Standard deduction — Single
STD_DEDUCTION_SINGLE = 16_100
SENIOR_EXTRA_SINGLE = 1_850  # single filer 65+

# Standard deduction — MFJ (2026)
STD_DEDUCTION_MFJ = 32_200
SENIOR_EXTRA_MFJ = 1_650  # per spouse 65+

# OBBBA senior bonus deduction (2026-2028, sunsets thereafter)
OBBBA_BONUS_PER_PERSON = 6_000
OBBBA_PHASEOUT_START = 150_000
OBBBA_PHASEOUT_RATE = 0.06  # per $1 of MAGI above threshold

# Social Security taxation tiers (MFJ provisional-income thresholds)
SS_TIER_1_MFJ = 32_000
SS_TIER_2_MFJ = 44_000
SS_MAX_TAXABLE_FRACTION = 0.85

# Social Security taxation tiers (Single provisional-income thresholds)
SS_TIER_1_SINGLE = 25_000
SS_TIER_2_SINGLE = 34_000

# Federal long-term capital gains / qualified dividend rates (MFJ statutory tiers)
LTCG_RATES_MFJ = (0.0, 0.15, 0.20)

# LTCG bracket thresholds for Single filer (taxable income upper bounds)
# 0% up to $48,350; 15% up to $533,400; 20% above
LTCG_THRESHOLDS_SINGLE = (48_350, 533_400)


def federal_tax(taxable_income: float) -> float:
    """Compute federal income tax on taxable income (MFJ)."""
    if taxable_income <= 0:
        return 0.0
    tax = 0.0
    prev = 0.0
    for ceil, rate in BRACKETS_MFJ:
        chunk = min(taxable_income, ceil) - prev
        if chunk <= 0:
            break
        tax += chunk * rate
        prev = ceil
    return tax


def marginal_rate(taxable_income: float) -> float:
    """Return the marginal bracket rate for given taxable income."""
    if taxable_income <= 0:
        return 0.0
    for ceil, rate in BRACKETS_MFJ:
        if taxable_income <= ceil:
            return rate
    return 0.37


def taxable_ss(combined_ss: float, other_income: float, filing_status: str = "MFJ") -> float:
    """
    Compute taxable portion of Social Security.

    Provisional income = other_income + 0.5 * SS
    MFJ:    Below $32,000: 0% | $32,000–$44,000: 50% of excess | Above: 85%
    Single: Below $25,000: 0% | $25,000–$34,000: 50% of excess | Above: 85%

    Capped at 85% of total SS.
    """
    if combined_ss <= 0:
        return 0.0
    if filing_status == "Single":
        tier1 = SS_TIER_1_SINGLE
        tier2 = SS_TIER_2_SINGLE
    else:
        tier1 = SS_TIER_1_MFJ
        tier2 = SS_TIER_2_MFJ
    provisional = other_income + 0.5 * combined_ss
    if provisional <= tier1:
        return 0.0
    if provisional <= tier2:
        taxable = 0.5 * (provisional - tier1)
    else:
        # Tier-1 band contributes 0.5*(tier2-tier1)
        tier1_contribution = 0.5 * (tier2 - tier1)
        taxable = SS_MAX_TAXABLE_FRACTION * (provisional - tier2) + tier1_contribution
    return min(taxable, SS_MAX_TAXABLE_FRACTION * combined_ss)


def deductions(
    your_age: int,
    spouse_age: int,
    std_ded: float = STD_DEDUCTION_MFJ,
    senior_extra: float = SENIOR_EXTRA_MFJ,
) -> float:
    """Total standard deduction including senior extras."""
    senior: float = 0
    if your_age >= 65:
        senior += senior_extra
    if spouse_age >= 65:
        senior += senior_extra
    return std_ded + senior


def senior_bonus_deduction(
    your_age: int,
    spouse_age: int,
    magi: float,
    bonus_per_person: float = OBBBA_BONUS_PER_PERSON,
    phaseout_start: float = OBBBA_PHASEOUT_START,
    phaseout_rate: float = OBBBA_PHASEOUT_RATE,
) -> float:
    """
    OBBBA Senior Bonus Deduction (2026-2028).

    $6,000 per person age 65+, phases out at $150K MAGI (MFJ).
    Reduction: $0.06 per $1 of MAGI over threshold.
    Stacks with standard deduction and $1,650 senior extra.
    """
    eligible = sum(1 for age in [your_age, spouse_age] if age >= 65)
    if eligible == 0:
        return 0.0
    total_bonus = bonus_per_person * eligible
    if magi <= phaseout_start:
        return total_bonus
    reduction = (magi - phaseout_start) * phaseout_rate
    return max(total_bonus - reduction, 0.0)


def tax_on_conversion(conversion: float, other_taxable: float) -> float:
    """
    Incremental tax caused by a Roth conversion.
    = tax(other + conversion) - tax(other)
    """
    return federal_tax(other_taxable + conversion) - federal_tax(other_taxable)


def room_to_bracket(current_gross: float, total_deductions: float, bracket_ceiling: float) -> float:
    """
    How much more gross income fits before hitting the next bracket.

    bracket_ceiling: taxable income limit (e.g., 100_800 for 12%).
    Returns gross income room (can be converted at current or lower rate).
    """
    return max(total_deductions + bracket_ceiling - current_gross, 0)


def room_to_12(current_gross: float, total_deductions: float) -> float:
    return room_to_bracket(current_gross, total_deductions, 100_800)


def room_to_22(current_gross: float, total_deductions: float) -> float:
    return room_to_bracket(current_gross, total_deductions, 211_400)


def federal_tax_single(taxable_income: float) -> float:
    """Compute federal income tax on taxable income (Single filer)."""
    if taxable_income <= 0:
        return 0.0
    tax = 0.0
    prev = 0.0
    for ceil, rate in BRACKETS_SINGLE:
        chunk = min(taxable_income, ceil) - prev
        if chunk <= 0:
            break
        tax += chunk * rate
        prev = ceil
    return tax


def marginal_rate_single(taxable_income: float) -> float:
    """Return the marginal bracket rate for Single filer."""
    if taxable_income <= 0:
        return 0.0
    for ceil, rate in BRACKETS_SINGLE:
        if taxable_income <= ceil:
            return rate
    return 0.37
