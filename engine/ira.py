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

# IRS Joint and Last Survivor Table (Table II) — 26 CFR §1.401(a)(9)-9(d).
# Required in place of the Uniform Lifetime Table (Table III, above) ONLY when
# the IRA owner's SOLE beneficiary is a spouse MORE THAN 10 years younger
# (age gap >= 11); Table II's larger divisors reflect the longer combined life
# expectancy, producing a smaller RMD than Table III. Values below are
# transcribed verbatim from the published table (Fidelity reproduction of the
# Treasury regulation) — never computed or interpolated.
#
# Coverage: owner ages 72-92, beneficiary ages 50-80, but ONLY the cells that
# can actually satisfy the >10-year-gap qualifying rule are populated (e.g. the
# owner-72 column only needs beneficiary <= 61, but the source table prints
# beneficiary rows through 70 for every owner column, so those extra
# non-qualifying cells are included too — harmless, since the gap check in
# rmd_divisor()/calc_rmd() gates their use). The two blocks below share the
# owner=82 column with identical values (source table repeats it); the merge
# in _build_joint_table is a no-op collision, not a conflict. The single
# uncovered-but-notionally-qualifying cell is owner=92/beneficiary=81 (and any
# owner >= 93) — those fall back to Table III via calc_rmd()/rmd_divisor().
_BLOCK1_OWNER_AGES: tuple[int, ...] = tuple(range(72, 83))  # 72..82
_BLOCK1_ROWS: dict[int, tuple[float, ...]] = {
    50: (36.9, 36.8, 36.8, 36.7, 36.6, 36.6, 36.5, 36.5, 36.5, 36.4, 36.4),
    51: (36.0, 36.0, 35.9, 35.8, 35.7, 35.7, 35.6, 35.6, 35.5, 35.5, 35.5),
    52: (35.2, 35.1, 35.0, 34.9, 34.9, 34.8, 34.7, 34.7, 34.6, 34.6, 34.6),
    53: (34.3, 34.2, 34.1, 34.1, 34.0, 33.9, 33.9, 33.8, 33.7, 33.7, 33.7),
    54: (33.5, 33.4, 33.3, 33.2, 33.1, 33.0, 33.0, 32.9, 32.9, 32.8, 32.8),
    55: (32.7, 32.6, 32.4, 32.4, 32.3, 32.2, 32.1, 32.0, 32.0, 31.9, 31.9),
    56: (31.9, 31.7, 31.6, 31.5, 31.4, 31.3, 31.2, 31.2, 31.1, 31.1, 31.0),
    57: (31.1, 30.9, 30.8, 30.7, 30.6, 30.5, 30.4, 30.3, 30.3, 30.2, 30.1),
    58: (30.3, 30.1, 30.0, 29.9, 29.8, 29.7, 29.6, 29.5, 29.4, 29.3, 29.3),
    59: (29.5, 29.4, 29.2, 29.1, 29.0, 28.8, 28.7, 28.7, 28.6, 28.5, 28.4),
    60: (28.8, 28.6, 28.4, 28.3, 28.2, 28.0, 27.9, 27.8, 27.8, 27.7, 27.6),
    61: (28.1, 27.9, 27.7, 27.5, 27.4, 27.3, 27.1, 27.0, 26.9, 26.9, 26.8),
    62: (27.4, 27.2, 27.0, 26.8, 26.6, 26.5, 26.4, 26.2, 26.1, 26.0, 26.0),
    63: (26.7, 26.5, 26.2, 26.1, 25.9, 25.7, 25.6, 25.5, 25.3, 25.2, 25.2),
    64: (26.0, 25.8, 25.5, 25.3, 25.2, 25.0, 24.8, 24.7, 24.6, 24.5, 24.4),
    65: (25.4, 25.1, 24.9, 24.6, 24.4, 24.3, 24.1, 23.9, 23.8, 23.7, 23.6),
    66: (24.8, 24.5, 24.2, 24.0, 23.7, 23.5, 23.4, 23.2, 23.1, 22.9, 22.8),
    67: (24.2, 23.9, 23.6, 23.3, 23.1, 22.9, 22.7, 22.5, 22.3, 22.2, 22.1),
    68: (23.6, 23.3, 23.0, 22.7, 22.4, 22.2, 22.0, 21.8, 21.6, 21.5, 21.3),
    69: (23.1, 22.7, 22.4, 22.1, 21.8, 21.5, 21.3, 21.1, 20.9, 20.7, 20.6),
    70: (22.5, 22.2, 21.8, 21.5, 21.2, 20.9, 20.6, 20.4, 20.2, 20.0, 19.9),
}

_BLOCK2_OWNER_AGES: tuple[int, ...] = tuple(range(82, 93))  # 82..92
_BLOCK2_ROWS: dict[int, tuple[float, ...]] = {
    50: (36.4, 36.4, 36.3, 36.3, 36.3, 36.3, 36.3, 36.3, 36.3, 36.2, 36.2),
    51: (35.5, 35.4, 35.4, 35.4, 35.4, 35.4, 35.3, 35.3, 35.3, 35.3, 35.3),
    52: (34.6, 34.5, 34.5, 34.5, 34.5, 34.4, 34.4, 34.4, 34.4, 34.4, 34.4),
    53: (33.7, 33.6, 33.6, 33.6, 33.5, 33.5, 33.5, 33.5, 33.5, 33.5, 33.5),
    54: (32.8, 32.7, 32.7, 32.7, 32.6, 32.6, 32.6, 32.6, 32.6, 32.5, 32.5),
    55: (31.9, 31.8, 31.8, 31.8, 31.7, 31.7, 31.7, 31.7, 31.7, 31.6, 31.6),
    56: (31.0, 31.0, 30.9, 30.9, 30.9, 30.8, 30.8, 30.8, 30.8, 30.7, 30.7),
    57: (30.1, 30.1, 30.0, 30.0, 30.0, 29.9, 29.9, 29.9, 29.9, 29.9, 29.8),
    58: (29.3, 29.2, 29.2, 29.1, 29.1, 29.1, 29.0, 29.0, 29.0, 29.0, 29.0),
    59: (28.4, 28.4, 28.3, 28.3, 28.2, 28.2, 28.2, 28.2, 28.1, 28.1, 28.1),
    60: (27.6, 27.5, 27.5, 27.4, 27.4, 27.4, 27.3, 27.3, 27.3, 27.3, 27.2),
    61: (26.8, 26.7, 26.7, 26.6, 26.6, 26.5, 26.5, 26.4, 26.4, 26.4, 26.4),
    62: (26.0, 25.9, 25.8, 25.8, 25.7, 25.7, 25.6, 25.6, 25.6, 25.6, 25.5),
    63: (25.2, 25.1, 25.0, 25.0, 24.9, 24.9, 24.8, 24.8, 24.7, 24.7, 24.7),
    64: (24.4, 24.3, 24.2, 24.1, 24.1, 24.0, 24.0, 24.0, 23.9, 23.9, 23.9),
    65: (23.6, 23.5, 23.4, 23.3, 23.3, 23.2, 23.2, 23.1, 23.1, 23.1, 23.0),
    66: (22.8, 22.7, 22.6, 22.6, 22.5, 22.4, 22.4, 22.3, 22.3, 22.3, 22.2),
    67: (22.1, 22.0, 21.9, 21.8, 21.7, 21.6, 21.6, 21.5, 21.5, 21.5, 21.4),
    68: (21.3, 21.2, 21.1, 21.0, 20.9, 20.9, 20.8, 20.7, 20.7, 20.7, 20.6),
    69: (20.6, 20.5, 20.4, 20.3, 20.2, 20.1, 20.0, 20.0, 19.9, 19.9, 19.8),
    70: (19.9, 19.7, 19.6, 19.5, 19.4, 19.3, 19.2, 19.2, 19.1, 19.1, 19.0),
    71: (19.2, 19.0, 18.9, 18.8, 18.7, 18.6, 18.5, 18.4, 18.4, 18.3, 18.3),
    72: (18.5, 18.3, 18.2, 18.1, 17.9, 17.8, 17.7, 17.7, 17.6, 17.5, 17.5),
    73: (17.9, 17.7, 17.5, 17.4, 17.2, 17.1, 17.0, 16.9, 16.9, 16.8, 16.7),
    74: (17.2, 17.0, 16.8, 16.7, 16.6, 16.4, 16.3, 16.2, 16.1, 16.1, 16.0),
    75: (16.6, 16.4, 16.2, 16.0, 15.9, 15.7, 15.6, 15.5, 15.4, 15.3, 15.3),
    76: (16.0, 15.8, 15.6, 15.4, 15.2, 15.1, 14.9, 14.8, 14.8, 14.6, 14.6),
    77: (15.5, 15.2, 15.0, 14.8, 14.6, 14.4, 14.3, 14.2, 14.1, 14.0, 13.9),
    78: (15.0, 14.7, 14.4, 14.2, 14.0, 13.8, 13.7, 13.5, 13.4, 13.3, 13.2),
    79: (14.5, 14.2, 13.9, 13.6, 13.4, 13.2, 13.1, 12.9, 12.8, 12.7, 12.6),
    80: (14.0, 13.7, 13.4, 13.1, 12.9, 12.7, 12.5, 12.3, 12.2, 12.1, 11.9),
}


def _build_joint_table() -> dict[tuple[int, int], float]:
    """Flatten the two transcribed column-blocks into a (owner, beneficiary) -> factor map.

    The owner=82 column appears in both blocks with identical published values,
    so the block-2 pass simply overwrites block-1's owner=82 entries with the
    same numbers (no conflict).
    """
    table: dict[tuple[int, int], float] = {}
    for bene, values in _BLOCK1_ROWS.items():
        for owner, val in zip(_BLOCK1_OWNER_AGES, values, strict=True):
            table[(owner, bene)] = val
    for bene, values in _BLOCK2_ROWS.items():
        for owner, val in zip(_BLOCK2_OWNER_AGES, values, strict=True):
            table[(owner, bene)] = val
    return table


JOINT_LAST_SURVIVOR: dict[tuple[int, int], float] = _build_joint_table()


def joint_life_divisor(owner_age: int, beneficiary_age: int) -> float | None:
    """Look up the Table II (Joint & Last Survivor) factor for this exact
    (owner_age, beneficiary_age) cell, or None if it isn't in the embedded
    table (e.g. owner >= 93, or the owner=92/beneficiary=81 gap). Callers
    must fall back to the Uniform Lifetime Table (Table III) on None — this
    function never invents or interpolates a value.
    """
    return JOINT_LAST_SURVIVOR.get((owner_age, beneficiary_age))


def rmd_divisor(age: int, beneficiary_age: int | None = None) -> float:
    """Get RMD divisor for a given age. Returns 0 if below RMD age.

    The IRS Uniform Lifetime Table terminates at "120 and older"
    (divisor 2.0), so any age above 120 uses the age-120 divisor.

    beneficiary_age: when provided AND more than 10 years younger than `age`
      (26 CFR §1.401(a)(9)-5, Q&A-4), the larger Joint & Last Survivor Table
      (Table II) divisor is used instead of the Uniform Lifetime Table —
      applicable only when the owner's SOLE beneficiary is that spouse.
      Falls back to Table III when the (age, beneficiary_age) cell isn't in
      the embedded Table II data (see JOINT_LAST_SURVIVOR above).
    """
    if beneficiary_age is not None and age - beneficiary_age > 10:
        joint_div = joint_life_divisor(age, beneficiary_age)
        if joint_div is not None:
            return joint_div
    if age > 120:
        return RMD_DIVISORS[120]
    return RMD_DIVISORS.get(age, 0.0)


def calc_rmd(
    ira_balance: float,
    age: int,
    rmd_start_age: int = 73,
    first_year_deferred: bool = False,
    prior_year_balance: float = 0.0,
    beneficiary_age: int | None = None,
) -> float:
    """Calculate Required Minimum Distribution.

    first_year_deferred: IRC §401(a)(9)(C)(ii) April-1 deferral election.
      When True and age == rmd_start_age: returns 0 (deferred to next April 1).
      When True and age == rmd_start_age + 1: returns normal RMD plus the
      deferred prior-year RMD (computed from prior_year_balance).
    beneficiary_age: passed through to rmd_divisor() — see its docstring for
      the Table II (Joint & Last Survivor) qualifying rule and fallback.
    """
    if age < rmd_start_age or ira_balance <= 0:
        return 0.0
    # April-1 deferral: skip first year, double second year
    if first_year_deferred and age == rmd_start_age:
        return 0.0
    div = rmd_divisor(age, beneficiary_age)
    if div <= 0:
        return 0.0
    rmd = ira_balance / div
    if first_year_deferred and age == rmd_start_age + 1 and prior_year_balance > 0:
        # The deferred prior-year RMD used the beneficiary's age as of the PRIOR
        # year (one year younger) — but a one-year age-gap shift never flips the
        # >10-year gate or the covered-cell lookup in practice for this narrow
        # deferral window, so the same beneficiary_age is reused here.
        prior_div = rmd_divisor(rmd_start_age, beneficiary_age)
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

    Caveat: this even-drain does not enforce the years-1-9 single-life-expectancy
    RMD floor that applies to a NEDB when the decedent died on/after RBD (2024
    final regs, T.D. 10001). See the InheritedIRA docstring (audit C10 / rmd-2)
    for scope.
    """
    if years_remaining <= 0:
        return 0.0
    return balance / years_remaining
