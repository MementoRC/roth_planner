"""ACA Marketplace subsidy calculator for pre-Medicare coverage.

Applies only before Medicare (ages under 65).
Enhanced ARPA/IRA subsidies expired Dec 31, 2025; 2026 uses the pre-ARP
schedule by default (ENHANCED_SUBSIDIES_ACTIVE = False). Toggle the flag
to model a hypothetical future extension.
"""

import math

from engine.tax_indexing import BASE_YEAR, DEFAULT_CPI, index_value

# HHS poverty guidelines (48 contiguous states + DC), keyed by the COVERAGE year
# they govern. 26 CFR 1.36B-1(h) uses the guidelines most recently published as
# of the first day of open enrollment for that coverage year, i.e. the January
# guidelines of the PRIOR calendar year (audit-1008 F-03). Add an entry each
# January when HHS publishes; later coverage years project from the latest.
FPL_BY_COVERAGE_YEAR: dict[int, tuple[float, float]] = {
    # 2025 guidelines (Federal Register, Jan 2025) -> 2026 coverage
    2026: (15_650, 21_150),
    # 2026 guidelines (Federal Register, Jan 2026; ASPE poverty-guidelines page) -> 2027 coverage
    # https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines
    2027: (15_960, 21_640),
}

# BASE_YEAR (2026 coverage) values; kept as module constants for existing importers.
FPL_1 = FPL_BY_COVERAGE_YEAR[BASE_YEAR][0]  # single person
FPL_2 = FPL_BY_COVERAGE_YEAR[BASE_YEAR][1]  # family of 2

# Legislative status: Enhanced subsidies (ARPA/IRA) expired Dec 31, 2025.
# OBBBA (P.L. 119-21) did not restore them; pre-ARP §36B schedule applies for TY 2026+.
# Toggle this flag to model a hypothetical future extension.
ENHANCED_SUBSIDIES_ACTIVE = False

# Enhanced applicable-percentage schedule — ARPA/IRA, IRC §36B(b)(3)(A)(iii).
# Each tuple is (upper_fpl_multiple, applicable_pct_at_band_END). The statute
# gives an initial and a final percentage per band and the percentage rises
# LINEARLY between them (audit-1008 F-41: this was read as a step function).
# Each band starts where the previous one ended; the implicit start is
# (100% FPL, 0%). The 400%+ band is flat at 8.5%.
ACA_ENHANCED_SCHEDULE = [
    (1.50, 0.00),  # 100-150%: 0% -> 0%
    (2.00, 0.02),  # 150-200%: 0% -> 2%
    (2.50, 0.04),  # 200-250%: 2% -> 4%
    (3.00, 0.06),  # 250-300%: 4% -> 6%
    # audit-0721: was 0.075 (broke ARPA ramp); 300-400% band ends at 8.5%
    (4.00, 0.085),  # 300-400%: 6% -> 8.5%
    (float("inf"), 0.085),  # 400%+: 8.5% flat
]

# Pre-ARP schedule (reverted Jan 1, 2026 — subsidies only up to 400% FPL)
# Each tuple is (upper_fpl_multiple, applicable_pct_at_bracket_start).
# The IRS table defines linear ramps within each bracket; these entries capture
# the rate at the START of each bracket (i.e. the lower-bound applicable %).
# Each band's FINAL percentage equals the NEXT band's INITIAL percentage, which
# is what lets aca_premium_cap_rate interpolate using schedule[i + 1][1].
#
# audit-0823 const/APPLICABLE-PCT: this was ONE frozen 2026 list, so every
# projected year priced ACA on superseded percentages while the FPL denominator
# beside it aged normally via _fpl() -> index_value(). Half the ratio moved with
# the projection and half did not.
#
# These CANNOT be run through index_value the way the FPL denominator is. IRC
# §36B(b)(3)(A)(ii) indexes them by the excess of premium growth over income
# growth -- not CPI -- so the published table has to be carried per year.
ACA_PRE_ARP_SCHEDULE_BY_YEAR: dict[int, list[tuple[float, float]]] = {
    # Rev. Proc. 2025-25 (IRB 2025-32, Aug 4 2025)
    # https://www.irs.gov/pub/irs-drop/rp-25-25.pdf
    2026: [
        (1.33, 0.0210),  # 100% to <133% FPL: 2.10% flat
        (1.50, 0.0314),  # 133-150%: ramp 3.14% → 4.19%
        (2.00, 0.0419),  # 150-200%: ramp 4.19% → 6.60%
        (2.50, 0.0660),  # 200-250%: ramp 6.60% → 8.44%
        (3.00, 0.0844),  # 250-300%: ramp 8.44% → 9.96%
        (4.00, 0.0996),  # 300-400%: 9.96% flat
    ],
    # Rev. Proc. 2026-26 (IRB 2026-31, Jul 27 2026)
    # https://www.irs.gov/pub/irs-drop/rp-26-26.pdf
    # Every band rose against 2026. Because a higher applicable percentage means
    # a higher expected contribution and so a SMALLER subsidy, the old frozen
    # table overstated the subsidy for every year from 2027 on.
    2027: [
        (1.33, 0.0215),  # 100% to <133% FPL: 2.15% flat
        (1.50, 0.0323),  # 133-150%: ramp 3.23% → 4.30%
        (2.00, 0.0430),  # 150-200%: ramp 4.30% → 6.78%
        (2.50, 0.0678),  # 200-250%: ramp 6.78% → 8.66%
        (3.00, 0.0866),  # 250-300%: ramp 8.66% → 10.22%
        (4.00, 0.1022),  # 300-400%: 10.22% flat
    ],
}


def _pre_arp_schedule_for_year(year: int) -> list[tuple[float, float]]:
    """Published §36B applicable-percentage table in force for `year`.

    Years past the latest published table HOLD at that table rather than
    extrapolating. Extrapolation would need the §36B(b)(3)(A)(ii)
    premium-growth-over-income-growth ratio, which this model has no input for
    -- cpi_assumption is emphatically not that ratio -- so holding flat is a
    declared assumption rather than an invented number. Add a new entry to
    ACA_PRE_ARP_SCHEDULE_BY_YEAR each August as the IRS publishes one.

    Years before the earliest published table use the earliest.
    """
    published = sorted(ACA_PRE_ARP_SCHEDULE_BY_YEAR)
    applicable = [y for y in published if y <= year]
    return ACA_PRE_ARP_SCHEDULE_BY_YEAR[applicable[-1] if applicable else published[0]]


# Back-compatible alias: the BASE_YEAR table. tests/test_constants_provenance.py
# and tests/test_single_filer.py import this name directly.
ACA_PRE_ARP_SCHEDULE = _pre_arp_schedule_for_year(BASE_YEAR)

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
    BASE_YEAR with ``index_value`` at the same ``cpi``.

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
    return derive_couple_benchmark_annual(your_age, spouse_age, filing_status, year=year, cpi=cpi)


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
    (ACA premiums are age-rated, so each spouse's share of the couple benchmark
    is their age factor over the sum of both factors instead of 50%). A Single filer has one
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


def _aca_cap_schedule(enhanced: bool, *, year: int = BASE_YEAR) -> list[tuple[float, float]]:
    """Return the premium cap schedule for the given subsidy law state and year.

    The ARPA/IRA enhanced caps are statutory percentages with no annual
    indexing provision, so they do not vary by year. The pre-ARP §36B
    applicable percentages are re-published annually -- see
    _pre_arp_schedule_for_year.
    """
    if enhanced:
        return ACA_ENHANCED_SCHEDULE
    return _pre_arp_schedule_for_year(year)


def aca_cap_bands(
    enhanced: bool, *, year: int = BASE_YEAR
) -> list[tuple[float, float, float, float]]:
    """The applicable-percentage schedule as display bands.

    Each tuple is ``(lower_fpl, upper_fpl, rate_at_lower, rate_at_upper)``,
    matching how aca_premium_cap_rate reads the two schedules: the enhanced
    entries carry each band's END rate (the band starts at the previous end,
    from 100% FPL / 0%); the pre-ARP entries carry each band's START rate,
    with the first and last bands flat and middle bands ramping to the next
    entry's start. A flat band has equal rates at both edges.
    """
    schedule = _aca_cap_schedule(enhanced, year=year)
    bands: list[tuple[float, float, float, float]] = []
    lower = 1.0
    if enhanced:
        start_rate = 0.0
        for upper, end_rate in schedule:
            if upper == float("inf"):
                bands.append((lower, upper, end_rate, end_rate))
            else:
                bands.append((lower, upper, start_rate, end_rate))
            lower, start_rate = upper, end_rate
        return bands
    last_i = len(schedule) - 1
    for i, (upper, start_rate) in enumerate(schedule):
        end_rate = start_rate if i in (0, last_i) else schedule[i + 1][1]
        bands.append((lower, upper, start_rate, end_rate))
        lower = upper
    return bands


def _fpl(filing_status: str, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI) -> float:
    """Return the FPL that governs ACA coverage in ``year`` for the filing status.

    Published coverage years read FPL_BY_COVERAGE_YEAR verbatim, whatever the
    CPI setting. Later years project the latest published guideline forward at
    ``cpi`` from that table's coverage year; earlier years use the earliest.
    """
    idx = 0 if filing_status == "Single" else 1
    published = sorted(FPL_BY_COVERAGE_YEAR)
    if year in FPL_BY_COVERAGE_YEAR:
        return FPL_BY_COVERAGE_YEAR[year][idx]
    if year < published[0]:
        return FPL_BY_COVERAGE_YEAR[published[0]][idx]
    latest = published[-1]
    return FPL_BY_COVERAGE_YEAR[latest][idx] * (1.0 + cpi) ** (year - latest)


def fpl_for(filing_status: str, year: int, cpi: float) -> float:
    """Public alias of ``_fpl`` for views and other engines (single source)."""
    return _fpl(filing_status, year=year, cpi=cpi)


def _truncated_fpl_ratio(magi: float, fpl: float) -> float:
    """MAGI as a fraction of FPL with the percentage truncated to a whole percent.

    Form 8962 line 5 (and Worksheet 2) drop any digits after the decimal point,
    so 199.99% reads as 199% and 400.9% reads as 400% (still eligible; 401% is
    not). The 1e-9 term keeps an exact boundary (e.g. 1.33 * FPL) from falling a
    percent short through float noise.
    """
    return math.floor(magi / fpl * 100.0 + 1e-9) / 100.0


def aca_ceiling_magi(filing_status: str, year: int, cpi: float) -> float:
    """400%-FPL MAGI cliff — the planning ceiling above which no ACA subsidy is assumed.

    Deliberately the exact 400% mark: Form 8962 truncation (see
    ``_truncated_fpl_ratio``) keeps MAGI up to just under 401% eligible, so this is
    conservative by about 1% of FPL; ``aca_subsidy`` applies the truncated rule.

    Owner decision (issue #42): KEEP the exact 4.0 x FPL bound for planning. On the
    2026-coverage two-person FPL ($21,150) the truncated rule would allow up to
    $211.50 more MAGI (about $156.50 Single). Headroom and the Explorer's cliff
    solve plan against this bound on purpose: a projected MAGI is an estimate,
    and a ~$211 margin under a cliff that can cost the whole subsidy is cheap
    insurance. Do not "fix" this to the truncation edge; tests pin the value.
    """
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

    Enhanced (ARPA/IRA) schedule: linear ramp from each band's initial to its
    final percentage (IRC §36B(b)(3)(A)(iii)); flat 8.5% above 400% FPL.
    """
    fpl = _fpl(filing_status, year=year, cpi=cpi)
    # The pre-ARP table is looked up on Form 8962's whole-percent FPL figure; the
    # hypothetical enhanced schedule is modeled as a continuous linear ramp.
    # Issue #42: the enhanced ratio is deliberately NOT truncated. Enhanced
    # (ARPA/IRA) subsidies expired after 2025, so this branch only serves
    # historical and what-if runs, where whole-percent steps would add sawtooth
    # noise without changing any decision. The enhanced schedule also has no
    # 400% cliff for truncation to move, and the W5 tests rely on its continuity.
    fpl_ratio = magi / fpl if enhanced_subsidies_active else _truncated_fpl_ratio(magi, fpl)
    # Pre-ARP cliff: above 400% FPL there is no subsidy, so cap rate is irrelevant.
    # Guard here prevents AssertionError on direct callers that skip aca_subsidy().
    if not enhanced_subsidies_active and fpl_ratio > 4.0:
        return 0.0
    # Below 100% FPL the household is PTC-ineligible under BOTH schedules (audit
    # aca-3; audit-1008 F-40). IRC §36B(c)(1)(A) sets the 100% floor; the ARPA/IRA
    # rule in §36B(c)(1)(E) lifts only the 400% upper limit.
    if fpl_ratio < 1.0:
        return 0.0
    schedule = _aca_cap_schedule(enhanced_subsidies_active, year=year)
    if enhanced_subsidies_active:
        band_start_fpl, band_start_rate = 1.0, 0.0
        for upper_fpl, end_rate in schedule:
            if fpl_ratio < upper_fpl:
                if upper_fpl == float("inf"):
                    return end_rate
                t = (fpl_ratio - band_start_fpl) / (upper_fpl - band_start_fpl)
                return band_start_rate + t * (end_rate - band_start_rate)
            band_start_fpl, band_start_rate = upper_fpl, end_rate
        raise AssertionError(
            f"aca_premium_cap_rate: no schedule entry matched fpl_ratio={fpl_ratio:.3f}"
        )
    # Pre-ARP schedule: linear interpolation within ramp bands per IRC §36B Table A.
    # Each band is "at least X but less than Y" (26 CFR §1.36B-3(g); Rev. Proc. 2025-25
    # §3.01), so upper bounds are EXCLUSIVE except for the final band (≤ 400% FPL).
    # fpl_ratio is already the Form 8962 Line 5 whole-percent truncation (see
    # _truncated_fpl_ratio), so exactly 133% FPL → integer 133, which must fall in
    # the ramp band (3.14%), not the flat band (2.10%), and the ramp below
    # interpolates on that truncated percentage.
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
    # 400% FPL cliff for the pre-ARP schedule, on the Form 8962 truncated percentage
    # (401% or more is ineligible; 400.9% still reads as 400).
    if (
        not enhanced_subsidies_active
        and _truncated_fpl_ratio(magi, _fpl(filing_status, year=year, cpi=cpi)) > 4.0
    ):
        return 0.0

    # 100% FPL floor under both schedules: below 100% FPL the household is
    # PTC-ineligible (IRC §36B(c)(1)(A) — Medicaid / coverage-gap territory),
    # so no subsidy (audit E1; audit-1008 F-40: ARPA/IRA kept this floor).
    if magi < 1.0 * _fpl(filing_status, year=year, cpi=cpi):
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
