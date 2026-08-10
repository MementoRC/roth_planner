"""Federal tax calculations — TCJA/OBBBA permanent brackets, SS taxation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.tax_indexing import BASE_YEAR, DEFAULT_CPI, index_bracket_list, index_tuple, index_value

if TYPE_CHECKING:
    from models.household import Household
    from models.ytd_income import YTDSnapshot

# 2026 MFJ brackets (TCJA/OBBBA permanent — IRS Rev. Proc. 2025-32)
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

# 2026 Single brackets (for surviving spouse analysis — IRS Rev. Proc. 2025-32)
BRACKETS_SINGLE = [
    (12_400, 0.10),
    (50_400, 0.12),
    (105_700, 0.22),
    (201_775, 0.24),
    (256_225, 0.32),
    (640_600, 0.35),
    (float("inf"), 0.37),
]

# Standard deduction — Single (2026 — IRS Rev. Proc. 2025-32)
STD_DEDUCTION_SINGLE = 16_100
SENIOR_EXTRA_SINGLE = 2_050  # single filer 65+ — IRC §63(f), Rev. Proc. 2025-32

# Standard deduction — MFJ (2026)
STD_DEDUCTION_MFJ = 32_200
SENIOR_EXTRA_MFJ = 1_650  # per spouse 65+

# OBBBA senior bonus deduction (2025-2028, sunsets thereafter)
OBBBA_BONUS_PER_PERSON = 6_000
OBBBA_PHASEOUT_START_MFJ = 150_000  # Pub. L. 119-21 §70103 — IRC §151(d)(5)(C)
OBBBA_PHASEOUT_START_SINGLE = 75_000  # Single / HoH — same citation
# $0.06 reduction per $1 of excess MAGI ($60 per $1,000), applied PER PERSON —
# each eligible person's own $6,000 phases out over its own $100K range
# (IRS Schedule 1-A Part V lines 31-37); it is NOT a household-wide range.
OBBBA_PHASEOUT_RATE = 0.06

# Social Security taxation tiers (MFJ provisional-income thresholds)
SS_TIER_1_MFJ = 32_000
SS_TIER_2_MFJ = 44_000
SS_MAX_TAXABLE_FRACTION = 0.85

# Social Security taxation tiers (Single provisional-income thresholds)
SS_TIER_1_SINGLE = 25_000
SS_TIER_2_SINGLE = 34_000

# Federal long-term capital gains / qualified dividend rates (MFJ statutory tiers)
LTCG_RATES_MFJ = (0.0, 0.15, 0.20)
# LTCG rates for Single filers (identical to MFJ in 2026 — IRC §1(h); defined
# separately for clarity and filing-status-aware branching)
LTCG_RATES_SINGLE = (0.0, 0.15, 0.20)

# LTCG bracket thresholds for MFJ (taxable income upper bounds, 2026 — IRS Rev. Proc. 2025-32 §3.03)
# 0% up to $98,900; 15% up to $613,700; 20% above
# OBBBA did NOT modify LTCG thresholds; they follow the inflation-adjusted Rev. Proc. schedule.
LTCG_THRESHOLDS_MFJ = (98_900, 613_700)

# LTCG bracket thresholds for Single filer (taxable income upper bounds, 2026 — IRS Rev. Proc. 2025-32)
# 0% up to $49,450; 15% up to $545,500; 20% above
LTCG_THRESHOLDS_SINGLE = (49_450, 545_500)

# IRS safe-harbor threshold (IRC §6654(d)(1)(C)): prior-year AGI ABOVE the threshold requires the
# 110% safe harbor; at or below qualifies for 100%. The threshold is $150K for every filing status
# EXCEPT married-filing-separately, which is $75K per §6654(d)(1)(C)(ii). (The app supports MFJ and
# Single, both $150K; MFS is included for correctness.)
SAFE_HARBOR_AGI_THRESHOLD = 150_000.0
SAFE_HARBOR_AGI_THRESHOLD_MFS = 75_000.0


def federal_tax(taxable_income: float, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI) -> float:
    """Compute federal income tax on taxable income (MFJ)."""
    if taxable_income <= 0:
        return 0.0
    brackets = index_bracket_list(BRACKETS_MFJ, year, cpi, round50=True)
    tax = 0.0
    prev = 0.0
    for ceil, rate in brackets:
        chunk = min(taxable_income, ceil) - prev
        if chunk <= 0:
            break
        tax += chunk * rate
        prev = ceil
    return tax


def marginal_rate(
    taxable_income: float, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI
) -> float:
    """Return the marginal bracket rate for given taxable income."""
    if taxable_income <= 0:
        return 0.0
    brackets = index_bracket_list(BRACKETS_MFJ, year, cpi, round50=True)
    for ceil, rate in brackets:
        # Strict '<': income exactly on a bracket ceiling has its next dollar taxed
        # at the NEXT bracket, so the marginal rate is the higher one (audit C6 /
        # tax-core-3).
        if taxable_income < ceil:
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
        # IRC §86(a)(1): the tier-1 band taxable amount is the LESSER of half the
        # excess over the base amount and half of benefits. The 0.5*combined_ss
        # cap (prong A) was missing, so low-SS / high-other-income households in
        # the tier-1 band overstated taxable SS (audit C6 / tax-core-1).
        taxable = min(0.5 * (provisional - tier1), 0.5 * combined_ss)
    else:
        # Tier-1 band contributes at most half of benefits (IRC 86(a)(2))
        tier1_contribution = min(0.5 * combined_ss, 0.5 * (tier2 - tier1))
        taxable = SS_MAX_TAXABLE_FRACTION * (provisional - tier2) + tier1_contribution
    return min(taxable, SS_MAX_TAXABLE_FRACTION * combined_ss)


def deductions(
    your_age: int,
    spouse_age: int,
    std_ded: float | None = None,
    senior_extra: float | None = None,
    filing_status: str = "MFJ",
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """Total standard deduction including senior extras.

    When ``std_ded`` or ``senior_extra`` are omitted (None), the correct
    amounts are inferred from ``filing_status`` — Single/HoH/MFS use
    STD_DEDUCTION_SINGLE / SENIOR_EXTRA_SINGLE; MFJ uses STD_DEDUCTION_MFJ /
    SENIOR_EXTRA_MFJ.  Callers that pass explicit values are unaffected.

    For MFJ each spouse's age is checked independently (up to two extras).
    For any other filing status (Single, HoH, MFS) exactly one filer is
    eligible; the real filer's age may live in either slot when the scenario
    zeroes the deceased spouse's age, so we use max(your_age, spouse_age) to
    select whichever slot holds the live filer — mirroring the already-correct
    logic in senior_bonus_deduction() (IRC §63(f), audit 0706 #tax-deductions-1).
    """
    if std_ded is None:
        std_ded = STD_DEDUCTION_MFJ if filing_status == "MFJ" else STD_DEDUCTION_SINGLE
    if senior_extra is None:
        # C1 (audit-0721 W5): MFS is in the "married" bucket for the additional
        # standard deduction (IRC §63(f)) — SENIOR_EXTRA_MFJ, not the Single
        # amount. std_ded stays Single-sized (MFS std ded == half MFJ, already
        # correct above).
        senior_extra = SENIOR_EXTRA_MFJ if filing_status in ("MFJ", "MFS") else SENIOR_EXTRA_SINGLE
    ded = index_value(std_ded, year, cpi, round50=True)
    se = index_value(senior_extra, year, cpi, round50=True)
    senior: float = 0.0
    if filing_status == "MFJ":
        if your_age >= 65:
            senior += se
        if spouse_age >= 65:
            senior += se
    else:
        _filer_age = max(your_age, spouse_age)
        if _filer_age >= 65:
            senior += se
    return ded + senior


def senior_bonus_deduction(
    your_age: int,
    spouse_age: int,
    magi: float,
    *,
    year: int,
    cpi: float = DEFAULT_CPI,
    filing_status: str = "MFJ",
    bonus_per_person: float = OBBBA_BONUS_PER_PERSON,
    phaseout_start: float | None = None,
    phaseout_rate: float = OBBBA_PHASEOUT_RATE,
) -> float:
    """
    OBBBA Senior Bonus Deduction (2025-2028).

    $6,000 per person age 65+, phases out at $0.06 per $1 of excess MAGI ($60/$1,000)
    above threshold, applied INDEPENDENTLY PER PERSON then summed — see below.
    Sunset: returns 0.0 for year < 2025 or year > 2028 (Pub. L. 119-21 §70103).
    Threshold depends on filing status (Pub. L. 119-21 §70103 — IRC §151(d)(5)(C)):
      MFJ (one eligible spouse):  phase-out starts $150,000, ends $250,000
      MFJ (both eligible):        phase-out starts $150,000, ends $250,000 (per person)
      Single/HoH: phase-out starts $75,000, ends $175,000
      MFS:    ineligible (returns 0)

    Phaseout is computed PER PERSON and floored at zero PER PERSON before
    summing, per IRS Schedule 1-A (Form 1040), Part V, lines 31-37: the form
    derives one reduced amount and enters that SAME amount on both line 36a
    (you) and line 36b (spouse), then line 37 sums them. So each person's
    $6,000 is independently reduced by $0.06 per $1 of MAGI above the
    threshold and independently floored at zero:

        per_person = max(0.0, bonus_per_person - phaseout_rate * (magi - phaseout_start))
        deduction  = per_person * eligible

    For dual-eligible MFJ the deduction zeros at MAGI=$150,000+$6,000/0.06
    = $250,000 (not $350,000 — that endpoint comes from incorrectly reducing
    the aggregate $12,000 once instead of each $6,000 independently).

    Pass ``phaseout_start`` explicitly to override the filing-status default.
    Stacks with standard deduction and senior extra.
    """
    if year > 2028:
        return 0.0
    if year < 2025:
        return 0.0
    if filing_status == "MFS":
        return 0.0
    if filing_status == "MFJ":
        ages_to_count = [your_age, spouse_age]
    else:
        # Single/HoH: exactly one eligible filer; the real filer's age may be in
        # either slot (scenario zeroes the deceased spouse's age), so use the
        # non-zero one. max() picks the survivor since the deceased slot is 0.
        ages_to_count = [max(your_age, spouse_age)]
    eligible = sum(1 for age in ages_to_count if age >= 65)
    if eligible == 0:
        return 0.0
    if phaseout_start is None:
        _base_phaseout = (
            OBBBA_PHASEOUT_START_MFJ if filing_status == "MFJ" else OBBBA_PHASEOUT_START_SINGLE
        )
        # Statutory nominal amount — NOT CPI-indexed (Pub. L. 119-21 §70103)
        phaseout_start = _base_phaseout
    reduction = phaseout_rate * max(0.0, magi - phaseout_start)
    per_person = max(0.0, bonus_per_person - reduction)
    return per_person * eligible


def tax_on_conversion(
    conversion: float,
    other_taxable: float,
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """
    Incremental tax caused by a Roth conversion.
    = tax(other + conversion) - tax(other)
    """
    return federal_tax(other_taxable + conversion, year=year, cpi=cpi) - federal_tax(
        other_taxable, year=year, cpi=cpi
    )


def room_to_bracket(current_gross: float, total_deductions: float, bracket_ceiling: float) -> float:
    """
    How much more gross income fits before hitting the next bracket.

    bracket_ceiling: taxable income limit (e.g., 100_800 for 12%).
    Returns gross income room (can be converted at current or lower rate).
    """
    return max(total_deductions + bracket_ceiling - current_gross, 0)


def bisect_conversion_for_ceiling(
    measure_at: Callable[[float], float],
    ceiling: float,
    upper_bound: float,
    *,
    iterations: int = 60,
) -> float:
    """Binary-search the largest conversion amount ``c`` in ``[0, upper_bound]``
    such that ``measure_at(c) <= ceiling``.

    Closed-form "room" formulas (room_to_bracket / room_to_12 / room_to_22, or
    a bare ``ceiling - base_magi`` subtraction) implicitly assume the measured
    quantity (taxable ordinary income or MAGI) grows exactly $1-per-$1 with the
    conversion. That assumption is false whenever the conversion pushes
    additional Social Security into taxability (IRC §86(b) provisional-income
    tiers): each dollar converted can raise the measured quantity by up to
    $1.85 while provisional income sits in the 50%/85% partial-taxability
    band, so a room sized by simple subtraction silently overshoots the true
    ceiling. Bisecting against the ACTUAL (non-linear) ``measure_at`` function
    avoids that overshoot -- mirrors the binary-search oracle pattern already
    used by engine.sweet_spot_compute.bracket_boundary_conversion /
    irmaa_safe_max (audit C14/C81/C23 -- one family of closed-form-overshoot
    bugs, one shared primitive).

    Requires ``measure_at`` to be monotonically non-decreasing in ``c`` (true
    for any measure built from taxable_ss, since taxable Social Security is
    non-decreasing in provisional income). Returns 0.0 if ``upper_bound`` is
    non-positive (no room to search).
    """
    if upper_bound <= 0:
        return 0.0
    lo, hi = 0.0, upper_bound
    for _ in range(iterations):
        mid = (lo + hi) / 2
        if measure_at(mid) <= ceiling:
            lo = mid
        else:
            hi = mid
    return lo


# Filing statuses with a modeled bracket/std-deduction/senior-extra table in
# this module. C2 (audit-0721 W5): filing_status was treated as a binary
# is_single switch, so an unmodeled status (e.g. "HoH") would silently be
# taxed on the full MFJ brackets instead of failing loud. The UI can only
# ever emit "MFJ" or "Single" (see views/setup/parameters.py
# filing_status_from_label) — HoH is not reachable today — but this guard
# ensures it can never silently mis-tax if that ever changes.
_MODELED_FILING_STATUSES = ("MFJ", "Single")


def _require_modeled_filing_status(filing_status: str, fn_name: str) -> None:
    if filing_status not in _MODELED_FILING_STATUSES:
        raise NotImplementedError(
            f"{fn_name}: filing_status={filing_status!r} is not modeled "
            f"(only {_MODELED_FILING_STATUSES} have bracket/deduction tables in engine/tax.py)"
        )


def room_to_12(
    current_gross: float,
    total_deductions: float,
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
    filing_status: str = "MFJ",
) -> float:
    _require_modeled_filing_status(filing_status, "room_to_12")
    brackets = BRACKETS_SINGLE if filing_status == "Single" else BRACKETS_MFJ
    ceiling = index_value(brackets[1][0], year, cpi, round50=True)
    return room_to_bracket(current_gross, total_deductions, ceiling)


def room_to_22(
    current_gross: float,
    total_deductions: float,
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
    filing_status: str = "MFJ",
) -> float:
    _require_modeled_filing_status(filing_status, "room_to_22")
    brackets = BRACKETS_SINGLE if filing_status == "Single" else BRACKETS_MFJ
    ceiling = index_value(brackets[2][0], year, cpi, round50=True)
    return room_to_bracket(current_gross, total_deductions, ceiling)


def room_to_24(
    current_gross: float,
    total_deductions: float,
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
    filing_status: str = "MFJ",
) -> float:
    _require_modeled_filing_status(filing_status, "room_to_24")
    brackets = BRACKETS_SINGLE if filing_status == "Single" else BRACKETS_MFJ
    ceiling = index_value(brackets[3][0], year, cpi, round50=True)
    return room_to_bracket(current_gross, total_deductions, ceiling)


def federal_tax_single(
    taxable_income: float, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI
) -> float:
    """Compute federal income tax on taxable income (Single filer)."""
    if taxable_income <= 0:
        return 0.0
    brackets = index_bracket_list(BRACKETS_SINGLE, year, cpi, round50=True)
    tax = 0.0
    prev = 0.0
    for ceil, rate in brackets:
        chunk = min(taxable_income, ceil) - prev
        if chunk <= 0:
            break
        tax += chunk * rate
        prev = ceil
    return tax


def marginal_rate_single(
    taxable_income: float, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI
) -> float:
    """Return the marginal bracket rate for Single filer."""
    if taxable_income <= 0:
        return 0.0
    brackets = index_bracket_list(BRACKETS_SINGLE, year, cpi, round50=True)
    for ceil, rate in brackets:
        # Strict '<': income exactly on a bracket ceiling has its next dollar taxed
        # at the NEXT bracket, so the marginal rate is the higher one (audit C6 /
        # tax-core-3).
        if taxable_income < ceil:
            return rate
    return 0.37


# ---------------------------------------------------------------------------
# YTD federal tax estimate
# ---------------------------------------------------------------------------


@dataclass
class YTDTaxEstimate:
    """Year-to-date federal tax estimate as if today were Dec 31."""

    ordinary_tax: float = 0.0
    ltcg_tax: float = 0.0
    niit: float = 0.0
    total: float = 0.0
    effective_rate: float = 0.0
    marginal_bracket_pct: float = 0.0
    room_to_next_bracket: float = 0.0


def estimate_ytd_federal_tax(
    ytd: YTDSnapshot,
    hh: Household,
    combined_ss: float = 0.0,
) -> YTDTaxEstimate:
    """Estimate federal tax owed YTD as if today were Dec 31.

    Stacks ordinary income through brackets, then applies preferential rates
    on LTCG/qualified dividends. NIIT applied per net investment income vs
    MAGI threshold. Does NOT include state tax, IRMAA premiums, or quarterly
    underpayment penalties.

    ``combined_ss`` is the total annual Social Security benefit received by the
    household (both spouses combined). Defaults to 0.0 so existing callers that
    do not pass SS remain unaffected. When provided, taxable_ss() is used to
    compute the includable portion (IRC §86) and fold it into ordinary income
    before the bracket walk.

    Standard deduction and OBBBA senior bonus are applied to derive taxable
    ordinary income (IRC §63(a)). All bracket-dependent outputs — ordinary_tax,
    marginal_bracket_pct, room_to_next_bracket — use taxable_ordinary, not gross.
    """
    from engine.niit import niit

    _year = hh.base_year
    _cpi = hh.cpi_assumption

    _require_modeled_filing_status(hh.filing_status, "estimate_ytd_federal_tax")
    is_single = hh.filing_status == "Single"

    # Step 1: gross ordinary income (excludes LTCG/qualified divs — those go through
    # the preferential-rate stack below).
    ordinary_income = ytd.total_ordinary_income

    # Step 2: taxable SS — computed first so it can be added to ordinary_income
    # before the standard-deduction subtraction.  provisional income = other_income
    # + 0.5 * SS; taxable_ss() handles the tier thresholds per IRC §86.
    #
    # Per IRC §86(b)(2), provisional income is MAGI (AGI + tax-exempt interest)
    # not just ordinary income. ytd.magi_ytd captures all §86-modified-AGI
    # components (wages, LTCG, qualified dividends, muni interest, etc.) and
    # excludes SS, so it is the correct "other_income" arg here.
    tss = taxable_ss(combined_ss, ytd.magi_ytd, filing_status=hh.filing_status)
    ordinary_income_with_ss = ordinary_income + tss
    # AGI includes the taxable portion of SS (§86), so the OBBBA senior-bonus phase-out
    # (§151(d)(5)) and the NIIT MAGI test (§1411) must both sit on niit_magi_ytd PLUS tss —
    # mirroring the headroom engine (locked_niit_magi = niit_magi_ytd + locked_tss).
    niit_magi_with_ss = ytd.niit_magi_ytd + tss

    # Step 3: standard deduction (indexed) + senior extras + OBBBA bonus.
    # LTCG thresholds in LTCG_THRESHOLDS_MFJ are taxable-income thresholds
    # (IRC §1(h)(1)); the same std_ded base is used for both the ordinary bracket
    # walk and the LTCG stack-walk so both are evaluated on taxable income.
    if hh.filing_status == "MFJ":
        senior_count = (1 if hh.your_age >= 65 else 0) + (1 if hh.spouse_age >= 65 else 0)
        std_ded = index_value(STD_DEDUCTION_MFJ, _year, _cpi, round50=True) + senior_count * index_value(
            SENIOR_EXTRA_MFJ, _year, _cpi, round50=True
        )
    else:
        senior_count = 1 if hh.your_age >= 65 else 0
        std_ded = index_value(STD_DEDUCTION_SINGLE, _year, _cpi, round50=True) + senior_count * index_value(
            SENIOR_EXTRA_SINGLE, _year, _cpi, round50=True
        )
    # OBBBA senior bonus deduction also lowers the taxable income base.
    std_ded += senior_bonus_deduction(
        hh.your_age,
        hh.spouse_age,
        niit_magi_with_ss,
        year=_year,
        cpi=_cpi,
        filing_status=hh.filing_status,
    )
    # Step 4: LTCG + qualified dividends taxed at preferential rate. Computed
    # BEFORE taxable_ordinary because IRC §1(h)'s Qualified Dividends and
    # Capital Gain Tax Worksheet floors TOTAL taxable income (ordinary + LTCG)
    # minus ALL deductions, not ordinary income alone -- see audit-0805 C1 below.
    _ltcg_thresholds = index_tuple(
        LTCG_THRESHOLDS_SINGLE if is_single else LTCG_THRESHOLDS_MFJ, _year, _cpi, round50=True
    )
    # audit-0805 C78: crypto_ltcg_ytd is long-term capital gain (Koinly-sourced;
    # see YTDSnapshot.total_investment_income) and belongs in the same §1(h)
    # preferential-rate stack as ltcg_ytd/qualified_dividends_ytd -- omitting it
    # let crypto LTCG escape preferential-rate tax entirely.
    # audit-0805 C2: ytd.preferential_capital_gain_ytd is ltcg_ytd + crypto_ltcg_ytd
    # AFTER IRC §1222 short/long netting and the IRC §1211(b) $3,000 loss cap (see
    # models/ytd_income.py::_net_capital_gain_split) -- not the raw ltcg_ytd +
    # crypto_ltcg_ytd sum. A net capital LOSS is entirely dropped from the
    # preferential stack (it has no business raising preferential-rate tax); any
    # surviving long-term gain net of a short-term loss lands here undiminished.
    ltcg_taxable = ytd.preferential_capital_gain_ytd + ytd.qualified_dividends_ytd

    # Step 5: taxable ordinary income + preferential-stack floor (IRC §63(a) / §1(h)).
    # audit-0805 C1: taxable income is TOTAL income (ordinary + LTCG) minus ALL
    # deductions, floored at 0 -- not ordinary income minus deductions floored at
    # 0, with the full LTCG amount stacked on top unadjusted. A standard deduction
    # unused by ordinary income must offset LTCG too (Qualified Dividends and
    # Capital Gain Tax Worksheet, Form 1040 Instructions, lines 1/6/7/9: the
    # preferential amount actually taxed is capped at total taxable income, and
    # ordinary taxable income is whatever total taxable income remains).
    taxable_total = max(ordinary_income_with_ss + ltcg_taxable - std_ded, 0.0)
    ltcg_preferential = min(ltcg_taxable, taxable_total)
    taxable_ordinary = taxable_total - ltcg_preferential

    # Step 6: ordinary income tax on TAXABLE ordinary income (not gross).
    ordinary_tax = (federal_tax_single if is_single else federal_tax)(
        taxable_ordinary, year=_year, cpi=_cpi
    )

    # Step 7: LTCG stacks ON TOP of taxable ordinary income; walk the stack
    # across brackets. 0%-rate portion (below threshold[0]) contributes $0 tax;
    # 15% and 20% portions taxed.
    ltcg_start = taxable_ordinary
    ltcg_end = taxable_total
    ltcg_at_15 = max(
        0.0,
        min(ltcg_end, _ltcg_thresholds[1]) - max(ltcg_start, _ltcg_thresholds[0]),
    )
    ltcg_at_20 = max(0.0, ltcg_end - max(ltcg_start, _ltcg_thresholds[1]))
    _ltcg_rates = LTCG_RATES_SINGLE if is_single else LTCG_RATES_MFJ
    ltcg_tax = ltcg_at_15 * _ltcg_rates[1] + ltcg_at_20 * _ltcg_rates[2]

    # Step 8: NIIT — 3.8% on lesser of NII or MAGI excess over threshold.
    # §1411(d)(3): NIIT MAGI excludes tax-exempt interest (unlike IRMAA MAGI).
    # Use the YTDSnapshot property (not a hand-summed subset) so crypto STCG/LTCG
    # are included in the NII base per §1411(c)(1) — audit 2026-07-13 R1/R2.
    net_investment_income = ytd.total_investment_income
    magi = niit_magi_with_ss
    niit_amount = niit(magi, net_investment_income, filing_status=hh.filing_status)

    total = ordinary_tax + ltcg_tax + niit_amount
    # Denominator must include taxable SS (folded into ordinary income above).
    # Use niit_magi_with_ss which EXCLUDES tax-exempt muni interest per IRC §1411(d)(3):
    # including muni interest inflates the denominator and understates the effective rate.
    # niit_magi_with_ss = ytd.niit_magi_ytd + tss = (ytd.magi_ytd - tax_exempt_interest) + tss.
    _rate_base = niit_magi_with_ss
    effective_rate = total / _rate_base if _rate_base > 0 else 0.0

    # Step 9: marginal bracket rate — derived from TAXABLE ordinary income (IRC §1).
    marginal = (marginal_rate_single if is_single else marginal_rate)(
        taxable_ordinary, year=_year, cpi=_cpi
    )

    # Step 10: room to next bracket ceiling — measured from TAXABLE ordinary income
    # against the taxable-income bracket ceilings (not from gross income).
    _indexed_brackets = index_bracket_list(
        BRACKETS_SINGLE if is_single else BRACKETS_MFJ, _year, _cpi, round50=True
    )
    room_next = 0.0
    for ceil, _rate in _indexed_brackets:
        # Strict '<': income exactly on a ceiling has full headroom in the NEXT
        # bracket, not zero room in the current one (audit C6 / tax-core-3).
        if taxable_ordinary < ceil:
            room_next = ceil - taxable_ordinary
            break

    return YTDTaxEstimate(
        ordinary_tax=ordinary_tax,
        ltcg_tax=ltcg_tax,
        niit=niit_amount,
        total=total,
        effective_rate=effective_rate,
        marginal_bracket_pct=marginal,
        room_to_next_bracket=room_next,
    )


# ---------------------------------------------------------------------------
# Safe-harbor payment guidance
# ---------------------------------------------------------------------------


@dataclass
class SafeHarborGuidance:
    """Mid-year safe-harbor payment guidance to avoid underpayment penalty."""

    prior_year_tax: float = 0.0
    current_year_estimate: float = 0.0
    safe_harbor_target: float = 0.0
    already_paid_ytd: float = 0.0
    remaining_to_pay: float = 0.0
    next_quarterly_due: str = ""
    rule_used: str = ""


def _next_quarterly_due(payment_date: str) -> str:
    """Return ISO date of the next quarterly estimated-tax due date.

    Q1: Apr 15  (Jan 1 – Apr 15)
    Q2: Jun 15  (Apr 16 – Jun 15)
    Q3: Sep 15  (Jun 16 – Sep 15)
    Q4: Jan 15 next year  (Sep 16 – Dec 31)

    Per IRS rules, if the nominal due date falls on a Saturday or Sunday
    it rolls forward to the following Monday.
    """
    from datetime import date, timedelta

    try:
        d = date.fromisoformat(payment_date)
    except ValueError:
        d = date.today()

    year = d.year

    def _rolled(candidate: date) -> date:
        # Roll Saturday (weekday 5) -> Monday (+2), Sunday (weekday 6) -> Monday (+1)
        if candidate.weekday() == 5:
            return candidate + timedelta(days=2)
        if candidate.weekday() == 6:
            return candidate + timedelta(days=1)
        return candidate

    # Roll each candidate deadline BEFORE comparing, so a date in the gap between
    # a weekend nominal date and its rolled deadline maps to the still-open
    # deadline rather than skipping to the next quarter.
    candidates = [
        _rolled(date(year, 4, 15)),
        _rolled(date(year, 6, 15)),
        _rolled(date(year, 9, 15)),
        _rolled(date(year + 1, 1, 15)),
    ]
    for due in candidates:
        if due >= d:
            return due.isoformat()
    return candidates[-1].isoformat()


def safe_harbor_payment(
    prior_year_tax: float,
    current_year_estimate: float,
    already_paid_ytd: float,
    payment_date: str,
    prior_year_agi: float | None = None,
    filing_status: str = "MFJ",
) -> SafeHarborGuidance:
    """Compute remaining safe-harbor payment to avoid underpayment penalty.

    IRS Form 2210 safe harbor: pay LESSER of:
    - 100% of prior year tax  (when prior-year AGI ≤ threshold)
    - 110% of prior year tax  (when prior-year AGI > threshold)
    - current year tax estimate (90% rule approximated as 100% here)

    Per IRC §6654(d)(1)(C) the AGI threshold is $150,000 for all filing statuses
    except married-filing-separately ($75,000). When ``prior_year_agi`` is None
    (unknown), the 110% rule is conservatively assumed and the ``rule_used`` label
    is annotated so the UI can disclose the assumption rather than silently applying
    110% to every household.

    If prior_year_tax is 0 (no data), uses current-year estimate only.
    """
    next_due = _next_quarterly_due(payment_date)

    # §6654(d)(1)(C): $150K threshold for all statuses except MFS ($75K). Above → 110%; at or below
    # → 100%. When prior-year AGI is unknown, conservatively assume 110% and annotate the label.
    agi_unknown = prior_year_agi is None
    if prior_year_agi is None:
        prior_multiplier = 1.10
    else:
        agi_threshold = (
            SAFE_HARBOR_AGI_THRESHOLD_MFS
            if filing_status == "MFS"
            else SAFE_HARBOR_AGI_THRESHOLD
        )
        prior_multiplier = 1.10 if prior_year_agi > agi_threshold else 1.00

    if prior_year_tax <= 0:
        safe_harbor_target = current_year_estimate
        rule_used = "100% current estimate (prior year unknown)"
    else:
        prior_safe = prior_multiplier * prior_year_tax
        pct_label = "110%" if prior_multiplier > 1.0 else "100%"
        agi_note = " (assumed — prior AGI unknown)" if agi_unknown else ""
        if prior_safe <= current_year_estimate:
            safe_harbor_target = prior_safe
            rule_used = f"{pct_label} prior year{agi_note}"
        else:
            safe_harbor_target = current_year_estimate
            rule_used = "100% current estimate"

    remaining = max(0.0, safe_harbor_target - already_paid_ytd)

    return SafeHarborGuidance(
        prior_year_tax=prior_year_tax,
        current_year_estimate=current_year_estimate,
        safe_harbor_target=safe_harbor_target,
        already_paid_ytd=already_paid_ytd,
        remaining_to_pay=remaining,
        next_quarterly_due=next_due,
        rule_used=rule_used,
    )


# ---------------------------------------------------------------------------
# Prior year federal tax from PDF cache
# ---------------------------------------------------------------------------


def load_prior_year_federal_tax() -> float:
    """Read prior year total federal tax (Form 1040 Line 24) from .tax_pdf_cache.json.

    Returns 0.0 if no PDF has been parsed or the field isn't present.
    The PDF cache currently stores MAGI components but not total_federal_tax;
    this function is forward-compatible once that field is added.
    """
    import json
    from pathlib import Path

    cache_path = Path(__file__).resolve().parent.parent / ".tax_pdf_cache.json"
    if not cache_path.exists():
        return 0.0
    try:
        data = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return 0.0
    # data is keyed by tax year; try most-recent year first
    if isinstance(data, dict):
        for year_key in sorted(data.keys(), reverse=True):
            entry = data[year_key]
            if isinstance(entry, dict):
                for key in ("total_federal_tax", "total_tax", "line_24"):
                    val = entry.get(key)
                    if val:
                        try:
                            return float(val)
                        except (TypeError, ValueError):
                            continue
    # Flat dict fallback (single-year cache)
    for key in ("total_federal_tax", "total_tax", "line_24"):
        val = data.get(key)
        if val:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return 0.0
