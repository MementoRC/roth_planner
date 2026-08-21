"""ACA Marketplace subsidy calculator for pre-Medicare coverage.

Applies only ages 61-64 (before Medicare at 65).
Enhanced ARPA/IRA subsidies expired Dec 31, 2025; 2026 uses the pre-ARP
schedule by default (ENHANCED_SUBSIDIES_ACTIVE = False). Toggle the flag
to model a hypothetical future extension.
"""

from engine.tax_indexing import BASE_YEAR, DEFAULT_CPI, index_value

# 2025 Federal Poverty Level guidelines (used for 2026 coverage, continental US)
FPL_1 = 15_650  # single person
FPL_2 = 21_150  # family of 2

# Legislative status: Enhanced subsidies (ARPA/IRA) expired Dec 31, 2025.
# OBBBA (P.L. 119-21) did not restore them; pre-ARP §36B schedule applies for TY 2026+.
# Toggle this flag to model a hypothetical future extension.
ENHANCED_SUBSIDIES_ACTIVE = False

# Enhanced premium cap schedule (% of income) — ARPA/IRA rules
# (upper_fpl_multiple, premium_cap_rate)
ACA_ENHANCED_SCHEDULE = [
    (1.50, 0.00),  # Below 150% FPL: $0 premium
    (2.00, 0.02),  # 150-200%: up to 2%
    (2.50, 0.04),  # 200-250%: up to 4%
    (3.00, 0.06),  # 250-300%: up to 6%
    # audit-0721: was 0.075 (broke ARPA ramp); 300-400% band caps at 8.5%
    (4.00, 0.085),  # 300-400%: up to 8.5%
    (float("inf"), 0.085),  # 400%+: 8.5% cap
]

# Pre-ARP schedule (reverted Jan 1, 2026 — subsidies only up to 400% FPL)
# Source: Rev. Proc. 2025-25 (IRB 2025-32, Aug 4 2025)
# Each tuple is (upper_fpl_multiple, applicable_pct_at_bracket_start).
# The IRS table defines linear ramps within each bracket; these entries capture
# the rate at the START of each bracket (i.e. the lower-bound applicable %).
ACA_PRE_ARP_SCHEDULE = [
    (1.33, 0.0210),  # 100% to <133% FPL: 2.10% flat
    (1.50, 0.0314),  # 133-150%: ramp 3.14% → 4.19%
    (2.00, 0.0419),  # 150-200%: ramp 4.19% → 6.60%
    (2.50, 0.0660),  # 200-250%: ramp 6.60% → 8.44%
    (3.00, 0.0844),  # 250-300%: ramp 8.44% → 9.96%
    (4.00, 0.0996),  # 300-400%: 9.96% flat
]

# Approximate annual benchmark silver plan premium for couple age ~60-64
# (varies by state/county — $1,600-$2,000/mo range; using $1,800/mo)
BENCHMARK_PREMIUM_ANNUAL = 1_800 * 12

# HHS Default Standard Age Curve (45 CFR 147.102), effective plan years 2018+.
# Multiplicative premium age-rating factors anchored at age 21 = 1.000 and capped
# 3:1 at age 64 and older. Source: CMS "Final Guidance Regarding Age Curves and
# State Reporting" (2016-12-16), Appendix I. Only pre-Medicare adult ages are
# tabulated (this planner never enrolls minors on ACA); ages <= 40 clamp to the
# age-40 factor and ages >= 64 use 3.000.
_HHS_AGE_CURVE: dict[int, float] = {
    40: 1.278,
    41: 1.302,
    42: 1.325,
    43: 1.357,
    44: 1.397,
    45: 1.444,
    46: 1.500,
    47: 1.563,
    48: 1.635,
    49: 1.706,
    50: 1.786,
    51: 1.865,
    52: 1.952,
    53: 2.040,
    54: 2.135,
    55: 2.230,
    56: 2.333,
    57: 2.437,
    58: 2.548,
    59: 2.603,
    60: 2.714,
    61: 2.810,
    62: 2.873,
    63: 2.952,
    64: 3.000,
}


def aca_age_factor(age: int) -> float:
    """HHS default age-rating factor for a given age (see _HHS_AGE_CURVE)."""
    if age >= 64:
        return 3.000
    if age <= 40:
        return _HHS_AGE_CURVE[40]
    return _HHS_AGE_CURVE[age]


# 2026 national average benchmark premium (second-lowest-cost silver, SLCSP) for a
# 40-year-old, GROSS of subsidies. Source: KFF State Health Facts, "Marketplace
# Average Monthly Benchmark Premiums" (county-level, weighted by county plan
# selections). 2026 = $625/mo; 2025 was $497/mo.
# NOTE: state variation is roughly 3.2x ($401/mo NH to $1,299/mo VT), so this
# national figure is only a starting default -- a household should override it
# with their own county's SLCSP from healthcare.gov/tax-tool.
SLCSP_AGE40_MONTHLY = 625.0


def derive_couple_benchmark_annual(
    your_age: int,
    spouse_age: int,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """Derive the household-level annual ACA benchmark (SLCSP) premium.

    Age-rates the national-average age-40 SLCSP monthly premium
    (``SLCSP_AGE40_MONTHLY``) for each adult via the HHS age curve
    (``aca_age_factor``), sums across the household's adults (both for MFJ,
    just the filer for Single), then indexes the total forward from
    BASE_YEAR using the SAME ``index_value`` helper ``_fpl()`` uses so the
    premium and FPL inflate consistently.

    This is only a national-average starting default -- state variation is
    roughly 3.2x (see ``SLCSP_AGE40_MONTHLY``) -- households should override
    via ``Household.aca_benchmark_premium_annual`` with their own county's
    SLCSP when known.
    """
    ages = [your_age] if filing_status == "Single" else [your_age, spouse_age]
    monthly_total = sum(
        SLCSP_AGE40_MONTHLY * (aca_age_factor(age) / aca_age_factor(40)) for age in ages
    )
    return index_value(monthly_total * 12, year, cpi)


def resolve_couple_benchmark_annual(
    override: float | None,
    *,
    your_age: int,
    spouse_age: int,
    filing_status: str = "MFJ",
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """Resolve ``Household.aca_benchmark_premium_annual`` to a couple-level
    annual benchmark premium. Single source of truth for every consumer.

    ``override`` is the raw field value. An explicit float (INCLUDING 0.0) is
    a household-supplied override and is returned VERBATIM -- no age-rating,
    no indexing. ``None`` means "derive" and calls
    ``derive_couple_benchmark_annual`` with the given ages/filing_status/
    year/cpi. None-vs-0.0 is resolved with ``is not None``, not truthiness --
    0.0 is a valid override meaning "no ACA premium exposure modeled".
    """
    if override is not None:
        return override
    return derive_couple_benchmark_annual(
        your_age, spouse_age, filing_status, year=year, cpi=cpi
    )


def effective_benchmark_premium(
    couple_benchmark: float,
    *,
    your_age: int,
    your_on_aca: bool,
    spouse_age: int,
    spouse_on_aca: bool,
    filing_status: str,
) -> float:
    """Age-rated benchmark premium for the actually-enrolled household member(s).

    ``couple_benchmark`` is a two-adult rate. Returns 0.0 when nobody is enrolled;
    the full couple rate when every household adult is enrolled; and for partial
    enrollment (an MFJ household with exactly one spouse on ACA) the enrolled
    member's age-rated SHARE of the couple rate rather than a flat 50/50 split
    (ACA premiums are age-rated, so a 61-year-old's share of a 61+55 couple
    benchmark is ~2.810/(2.810+2.230) instead of 50%). A Single filer has one
    household adult, so an enrolled Single filer gets the full individual benchmark
    rather than a halved couple rate.
    """
    if filing_status == "Single":
        # A Single filer has exactly one household adult, so couple_benchmark is
        # already that individual's own benchmark premium -- there is no second
        # adult to blend against. Return it directly, unblended (audit 2026-07-13:
        # blending via a spouse_age age-factor -- even a placeholder spouse_age=0 --
        # understated the benchmark by ~31%, contradicting this function's own
        # docstring).
        if not your_on_aca:
            return 0.0
        return couple_benchmark
    adults = [(your_age, your_on_aca), (spouse_age, spouse_on_aca)]
    enrolled_ages = [age for age, on in adults if on]
    if not enrolled_ages:
        return 0.0
    if len(enrolled_ages) == len(adults):
        return couple_benchmark
    total_factor = sum(aca_age_factor(age) for age, _ in adults)
    if total_factor <= 0:
        return couple_benchmark / len(adults)
    enrolled_factor = sum(aca_age_factor(age) for age in enrolled_ages)
    return couple_benchmark * (enrolled_factor / total_factor)


def _aca_cap_schedule(enhanced: bool) -> list[tuple[float, float]]:
    """Return the premium cap schedule for the given subsidy law state."""
    return ACA_ENHANCED_SCHEDULE if enhanced else ACA_PRE_ARP_SCHEDULE


def _fpl(filing_status: str, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI) -> float:
    """Return the applicable FPL for the given filing status, indexed for year."""
    base = FPL_1 if filing_status == "Single" else FPL_2
    return index_value(base, year, cpi)


def aca_ceiling_magi(filing_status: str, year: int, cpi: float) -> float:
    """400%-FPL MAGI cliff — the ceiling above which no ACA subsidy is available."""
    return 4.0 * _fpl(filing_status, year=year, cpi=cpi)


def aca_premium_cap_rate(
    magi: float,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """Premium cap as fraction of income based on FPL multiple.

    Pre-ARP schedule (per IRC §36B Table A / IRS Form 8962): linearly
    interpolates the applicable percentage within each ramp band. The
    bottom band (100-133% FPL) and top band (300-400% FPL) are flat per
    the IRS table; middle bands ramp from the entry's start rate at the
    band's lower-FPL edge to the NEXT entry's start rate at the band's
    upper-FPL edge.

    Enhanced (ARPA/IRA) schedule: preserves the original step-function
    cap semantics ("up to N%") — no interpolation.
    """
    fpl_ratio = magi / _fpl(filing_status, year=year, cpi=cpi)
    # Pre-ARP cliff: above 400% FPL there is no subsidy, so cap rate is irrelevant.
    # Guard here prevents AssertionError on direct callers that skip aca_subsidy().
    if not enhanced_subsidies_active and fpl_ratio > 4.0:
        return 0.0
    # Pre-ARP floor: below 100% FPL the household is PTC-ineligible (audit aca-3).
    # IRC §36B(c)(1)(A) limits the credit to 100%-400% FPL. Mirrors the 400% cliff
    # guard above. Enhanced schedule has no statutory lower bound, so only pre-ARP.
    if not enhanced_subsidies_active and fpl_ratio < 1.0:
        return 0.0
    schedule = _aca_cap_schedule(enhanced_subsidies_active)
    # Enhanced schedule: original step-function lookup preserved (ARPA caps, not ramps).
    # Use strict < so exactly 150% FPL falls into the 2% band, not the 0% band
    # (audit aca-1: <= caused the boundary to be greedily assigned to 0%).
    if enhanced_subsidies_active:
        for upper_fpl, cap_rate in schedule:
            if fpl_ratio < upper_fpl:
                return cap_rate
        raise AssertionError(
            f"aca_premium_cap_rate: no schedule entry matched fpl_ratio={fpl_ratio:.3f}"
        )
    # Pre-ARP schedule: linear interpolation within ramp bands per IRC §36B Table A.
    # Each band is "at least X but less than Y" (26 CFR §1.36B-3(g); Rev. Proc. 2025-25
    # §3.01), so upper bounds are EXCLUSIVE except for the final band (≤ 400% FPL).
    # Form 8962 Line 5 truncates the FPL ratio, so exactly 133% FPL → integer 133,
    # which must fall in the ramp band (3.14%), not the flat band (2.10%).
    band_start_fpl = 1.0  # 100% FPL — implicit lower edge of the bottom band
    last_i = len(schedule) - 1
    for i, (upper_fpl, start_rate) in enumerate(schedule):
        in_band = fpl_ratio <= upper_fpl if i == last_i else fpl_ratio < upper_fpl
        if in_band:
            # First and last bands are flat per IRS table (see schedule comments).
            if i == 0 or i == len(schedule) - 1:
                return start_rate
            # Middle band: linear-interpolate from start_rate (at band_start_fpl) to
            # next entry's start_rate (at upper_fpl).
            end_rate = schedule[i + 1][1]
            band_span = upper_fpl - band_start_fpl
            if band_span <= 0:
                return start_rate  # degenerate; should not occur in well-formed schedule
            t = (fpl_ratio - band_start_fpl) / band_span
            return start_rate + t * (end_rate - start_rate)
        band_start_fpl = upper_fpl
    # Unreachable for pre-ARP: cliff guard above handles fpl_ratio > 4.0.
    raise AssertionError(
        f"aca_premium_cap_rate: no schedule entry matched fpl_ratio={fpl_ratio:.3f}"
    )


def aca_subsidy(
    magi: float,
    benchmark: float = BENCHMARK_PREMIUM_ANNUAL,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """
    Calculate ACA premium tax credit (subsidy).

    Subsidy = benchmark_premium - (income × cap_rate)
    Cannot be negative.

    When using pre-ARP schedule, no subsidies above 400% FPL.
    """
    # Check 400% FPL cliff for pre-ARP schedule
    if not enhanced_subsidies_active and magi > aca_ceiling_magi(filing_status, year, cpi):
        return 0.0

    # Symmetric 100% FPL floor (pre-ARP): below 100% FPL the household is
    # PTC-ineligible (IRC §36B(c)(1)(A) — Medicaid / coverage-gap territory),
    # so no subsidy. Mirrors the 400% cliff above (audit E1).
    if not enhanced_subsidies_active and magi < 1.0 * _fpl(filing_status, year=year, cpi=cpi):
        return 0.0

    cap_rate = aca_premium_cap_rate(
        magi, enhanced_subsidies_active, filing_status, year=year, cpi=cpi
    )
    expected_contribution = magi * cap_rate
    return max(benchmark - expected_contribution, 0)


def aca_subsidy_loss(
    base_magi: float,
    new_magi: float,
    benchmark: float = BENCHMARK_PREMIUM_ANNUAL,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """
    How much ACA subsidy is lost due to additional income (e.g., conversion).
    """
    base = aca_subsidy(
        base_magi, benchmark, enhanced_subsidies_active, filing_status, year=year, cpi=cpi
    )
    new = aca_subsidy(
        new_magi, benchmark, enhanced_subsidies_active, filing_status, year=year, cpi=cpi
    )
    return max(base - new, 0)


def aca_net_cost(
    magi: float,
    benchmark: float = BENCHMARK_PREMIUM_ANNUAL,
    enhanced_subsidies_active: bool = ENHANCED_SUBSIDIES_ACTIVE,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """What you actually pay for the silver plan after subsidy."""
    return max(
        benchmark
        - aca_subsidy(
            magi, benchmark, enhanced_subsidies_active, filing_status, year=year, cpi=cpi
        ),
        0,
    )


def aca_excess_aptc_repayment(
    advance_aptc_annual: float,
    actual_magi: float,
    benchmark_premium_annual: float,
    enhanced_subsidies_active: bool,
    filing_status: str = "MFJ",
    *,
    year: int,
    cpi: float = DEFAULT_CPI,
) -> float:
    """Compute the Form 8962 line 29 excess-APTC repayment for a tax year.

    For year >= 2026, P.L. 119-21 eliminated the IRC §36B(f)(2)(B) repayment
    limitation: the household must repay the full excess regardless of FPL band.

    For year <= 2025 the pre-ARP / original cap table would apply but this
    project's base_year is 2026, so pre-2026 is NOT MODELED — the function
    raises NotImplementedError if called with year < 2026 to make the gap loud.

    A negative return value means the household RECEIVED LESS APTC than they
    were entitled to and will get the difference as additional PTC on Form
    1040 line 31. A positive value is owed back as additional tax (Form 1040
    Schedule 2 line 2).

    Returns repayment in dollars: positive = owed, negative = refund.
    """
    if year < 2026:
        raise NotImplementedError(
            f"APTC cap table for tax year {year} is not modeled — base_year=2026; "
            "see IRC §36B(f)(2)(B) cap table for pre-P.L. 119-21 years."
        )
    actual_ptc = aca_subsidy(
        actual_magi,
        benchmark_premium_annual,
        enhanced_subsidies_active=enhanced_subsidies_active,
        filing_status=filing_status,
        year=year,
        cpi=cpi,
    )
    return advance_aptc_annual - actual_ptc


def is_pre_medicare_age(age: int) -> bool:
    """True while under Medicare eligibility age (65) and a valid age (>0).

    Age-only half of `aca_applies`, factored out so callers that need a
    pure Medicare-age gate -- e.g. the aca_safe MAGI ceiling in
    engine/scenario.py's `_strategy_magi_ceiling` and
    engine/scenario_autofill.py's `_aca_room` -- can test Medicare
    eligibility WITHOUT also requiring marketplace enrollment. Gating on
    enrollment silently unbounds the ceiling for any household whose
    enrollment flag defaults to False, defeating the "aca_safe" strategy
    the user explicitly selected (audit fix/aca-safe-medicare-age-gate).
    Keeping this as the single source of the 65 boundary means
    `aca_applies` and the ceiling gates cannot drift on it.
    """
    return 0 < age < 65


def aca_applies(your_age: int, enrolled: bool = True) -> bool:
    """ACA marketplace only relevant if under 65 AND enrolled AND age > 0 (audit aca-4)."""
    return is_pre_medicare_age(your_age) and enrolled
