"""IRA projection, RMD calculations, and growth modeling."""

# Uniform Lifetime Table (SECURE 2.0, age 72+)
RMD_DIVISORS = {
    72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9,
    78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7,
    84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9,
    90: 12.2, 91: 11.5, 92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9,
    96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4,
}


def rmd_divisor(age: int) -> float:
    """Get RMD divisor for a given age. Returns 0 if below RMD age."""
    return RMD_DIVISORS.get(age, 0.0)


def calc_rmd(ira_balance: float, age: int, rmd_start_age: int = 75) -> float:
    """Calculate Required Minimum Distribution."""
    if age < rmd_start_age or ira_balance <= 0:
        return 0.0
    div = rmd_divisor(age)
    if div <= 0:
        return 0.0
    return ira_balance / div


def project_ira(starting_balance: float, growth_rate: float, years: int,
                annual_withdrawal: float = 0) -> float:
    """
    Project IRA balance forward, with optional annual withdrawal.
    Withdrawal happens at start of year, growth on remainder.
    """
    balance = starting_balance
    for _ in range(years):
        balance = max(balance - annual_withdrawal, 0) * (1 + growth_rate)
    return balance


def project_ira_with_schedule(starting_balance: float, growth_rate: float,
                               withdrawals: list) -> list:
    """
    Project IRA year by year with variable withdrawals.

    Args:
        starting_balance: IRA at beginning of first year
        growth_rate: annual growth
        withdrawals: list of annual withdrawal amounts

    Returns:
        list of (beginning_balance, withdrawal, ending_balance) per year
    """
    results = []
    balance = starting_balance
    for w in withdrawals:
        begin = balance
        actual_w = min(w, balance)  # can't withdraw more than balance
        balance = max(balance - actual_w, 0) * (1 + growth_rate)
        results.append((begin, actual_w, balance))
    return results


def ss_benefit_at_age(monthly_fra: float, claim_age: int, fra_age: int = 67) -> float:
    """
    Compute annual SS benefit at a given claim age.

    Before FRA: reduced ~6.67%/yr first 3 yrs, 5%/yr beyond
    After FRA: increased 8%/yr (delayed retirement credits)
    """
    months_diff = (claim_age - fra_age) * 12
    if months_diff == 0:
        return monthly_fra * 12
    elif months_diff < 0:
        early_months = abs(months_diff)
        if early_months <= 36:
            factor = 1 - early_months * (5 / 9 / 100)
        else:
            factor = 1 - 36 * (5 / 9 / 100) - (early_months - 36) * (5 / 12 / 100)
        return monthly_fra * max(factor, 0) * 12
    else:
        # Delayed: 8% per year = 2/3% per month
        factor = 1 + months_diff * (2 / 3 / 100)
        return monthly_fra * factor * 12


def ss_with_cola(base_annual: float, years_collecting: int, cola: float = 0.025) -> float:
    """Apply COLA to SS benefit."""
    return base_annual * (1 + cola) ** years_collecting
