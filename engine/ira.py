"""IRA projection, RMD calculations, and growth modeling."""

# Uniform Lifetime Table — IRS T.D. 9930 (eff. 2022); SECURE 2.0 §107 sets start age 73 (born 1951-1959) or 75 (born 1960+)
RMD_DIVISORS = {
    72: 27.4,
    73: 26.5,
    74: 25.5,
    75: 24.6,
    76: 23.7,
    77: 22.9,
    78: 22.0,
    79: 21.1,
    80: 20.2,
    81: 19.4,
    82: 18.5,
    83: 17.7,
    84: 16.8,
    85: 16.0,
    86: 15.2,
    87: 14.4,
    88: 13.7,
    89: 12.9,
    90: 12.2,
    91: 11.5,
    92: 10.8,
    93: 10.1,
    94: 9.5,
    95: 8.9,
    96: 8.4,
    97: 7.8,
    98: 7.3,
    99: 6.8,
    100: 6.4,
    101: 6.0,
    102: 5.6,
    103: 5.2,
    104: 4.9,
    105: 4.6,
    106: 4.3,
    107: 4.1,
    108: 3.9,
    109: 3.7,
    110: 3.5,
    111: 3.4,
    112: 3.3,
    113: 3.1,
    114: 3.0,
    115: 2.9,
    116: 2.8,
    117: 2.7,
    118: 2.5,
    119: 2.3,
    120: 2.0,
}


def rmd_divisor(age: int) -> float:
    """Get RMD divisor for a given age. Returns 0 if below RMD age.

    The IRS Uniform Lifetime Table terminates at "120 and older"
    (divisor 2.0), so any age above 120 uses the age-120 divisor.
    """
    if age > 120:
        return RMD_DIVISORS[120]
    return RMD_DIVISORS.get(age, 0.0)


def calc_rmd(
    ira_balance: float,
    age: int,
    rmd_start_age: int = 75,
    first_year_deferred: bool = False,
    prior_year_balance: float = 0.0,
) -> float:
    """Calculate Required Minimum Distribution.

    first_year_deferred: IRC §401(a)(9)(C)(ii) April-1 deferral election.
      When True and age == rmd_start_age: returns 0 (deferred to next April 1).
      When True and age == rmd_start_age + 1: returns normal RMD plus the
      deferred prior-year RMD (computed from prior_year_balance).
    """
    if age < rmd_start_age or ira_balance <= 0:
        return 0.0
    # April-1 deferral: skip first year, double second year
    if first_year_deferred and age == rmd_start_age:
        return 0.0
    div = rmd_divisor(age)
    if div <= 0:
        return 0.0
    rmd = ira_balance / div
    if first_year_deferred and age == rmd_start_age + 1 and prior_year_balance > 0:
        prior_div = rmd_divisor(rmd_start_age)
        if prior_div > 0:
            rmd += prior_year_balance / prior_div
    return rmd


def project_ira(
    starting_balance: float, growth_rate: float, years: int, annual_withdrawal: float = 0
) -> float:
    """
    Project IRA balance forward, with optional annual withdrawal.
    Withdrawal happens at start of year, growth on remainder.
    """
    balance = starting_balance
    for _ in range(years):
        balance = max(balance - annual_withdrawal, 0) * (1 + growth_rate)
    return balance


def ss_benefit_at_age(monthly_fra: float, claim_age: int, fra_age: int = 67) -> float:
    """
    Compute annual SS benefit at a given claim age.

    Before FRA: reduced ~6.67%/yr first 3 yrs, 5%/yr beyond
    After FRA: increased 8%/yr (delayed retirement credits)
    """
    months_diff = (claim_age - fra_age) * 12
    if months_diff == 0:
        return monthly_fra * 12
    if months_diff < 0:
        early_months = abs(months_diff)
        if early_months <= 36:
            factor = 1 - early_months * (5 / 9 / 100)
        else:
            factor = 1 - 36 * (5 / 9 / 100) - (early_months - 36) * (5 / 12 / 100)
        return monthly_fra * max(factor, 0) * 12
    # Delayed: 8% per year = 2/3% per month; credits stop accruing at age 70
    max_delay_months = (70 - fra_age) * 12
    effective_delay = min(months_diff, max_delay_months)
    factor = 1 + effective_delay * (2 / 3 / 100)
    return monthly_fra * factor * 12


def ss_with_cola(base_annual: float, years_collecting: int, cola: float = 0.025) -> float:
    """Apply COLA to SS benefit."""
    return base_annual * (1 + cola) ** years_collecting


def inherited_ira_drain(balance: float, years_remaining: int) -> float:
    """Year-N-of-10 drain: balance / years_remaining.

    years_remaining = 10 - (current_year - inherited_year)
    Returns 0 if years_remaining <= 0 (already fully drained).
    Final year (years_remaining=1) drains the entire balance ("balloon").
    """
    if years_remaining <= 0:
        return 0.0
    return balance / years_remaining
