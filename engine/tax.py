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


def bracket_label(taxable_income: float) -> str:
    """Human-readable bracket label."""
    return f"{marginal_rate(taxable_income) * 100:.0f}%"


def taxable_ss(combined_ss: float, other_income: float) -> float:
    """
    Compute taxable portion of Social Security (MFJ).

    Provisional income = other_income + 0.5 * SS
    Below $32,000: 0% taxable
    $32,000–$44,000: 50% of excess
    Above $44,000: 85% of excess + $6,000

    Capped at 85% of total SS.
    """
    if combined_ss <= 0:
        return 0.0
    provisional = other_income + 0.5 * combined_ss
    if provisional <= 32_000:
        return 0.0
    if provisional <= 44_000:
        taxable = 0.5 * (provisional - 32_000)
    else:
        taxable = 0.85 * (provisional - 44_000) + 6_000
    return min(taxable, 0.85 * combined_ss)


def deductions(
    your_age: int, spouse_age: int, std_ded: float = 32_200, senior_extra: float = 1_650
) -> float:
    """Total standard deduction including senior extras."""
    senior: float = 0
    if your_age >= 65:
        senior += senior_extra
    if spouse_age >= 65:
        senior += senior_extra
    return std_ded + senior


def senior_bonus_deduction(your_age: int, spouse_age: int, magi: float,
                           bonus_per_person: float = 6_000,
                           phaseout_start: float = 150_000,
                           phaseout_rate: float = 0.06) -> float:
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


def effective_rate(taxable_income: float) -> float:
    """Average effective tax rate."""
    if taxable_income <= 0:
        return 0.0
    return federal_tax(taxable_income) / taxable_income


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
