"""Federal tax calculations — TCJA/OBBBA permanent brackets, SS taxation."""

from __future__ import annotations

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
# $0.06 reduction per $1 of excess MAGI ($60 per $1,000; phases out over $100K range)
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

# LTCG bracket thresholds for MFJ (taxable income upper bounds, 2026 — IRS Rev. Proc. 2025-32 §3.03)
# 0% up to $98,900; 15% up to $613,700; 20% above
# OBBBA did NOT modify LTCG thresholds; they follow the inflation-adjusted Rev. Proc. schedule.
LTCG_THRESHOLDS_MFJ = (98_900, 613_700)

# LTCG bracket thresholds for Single filer (taxable income upper bounds, 2026 — IRS Rev. Proc. 2025-32)
# 0% up to $49,450; 15% up to $545,500; 20% above
LTCG_THRESHOLDS_SINGLE = (49_450, 545_500)

# IRS safe-harbor threshold: prior-year AGI > $150K MFJ ($75K Single) requires 110% safe harbor;
# below threshold qualifies for 100% safe harbor. Source: IRC §6654(d)(1)(C).
SAFE_HARBOR_AGI_THRESHOLD_MFJ = 150_000.0
SAFE_HARBOR_AGI_THRESHOLD_SINGLE = 75_000.0


def federal_tax(taxable_income: float, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI) -> float:
    """Compute federal income tax on taxable income (MFJ)."""
    if taxable_income <= 0:
        return 0.0
    brackets = index_bracket_list(BRACKETS_MFJ, year, cpi)
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
    brackets = index_bracket_list(BRACKETS_MFJ, year, cpi)
    for ceil, rate in brackets:
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
        # Tier-1 band contributes at most half of benefits (IRC 86(a)(2))
        tier1_contribution = min(0.5 * combined_ss, 0.5 * (tier2 - tier1))
        taxable = SS_MAX_TAXABLE_FRACTION * (provisional - tier2) + tier1_contribution
    return min(taxable, SS_MAX_TAXABLE_FRACTION * combined_ss)


def deductions(
    your_age: int,
    spouse_age: int,
    std_ded: float = STD_DEDUCTION_MFJ,
    senior_extra: float = SENIOR_EXTRA_MFJ,
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
) -> float:
    """Total standard deduction including senior extras."""
    ded = index_value(std_ded, year, cpi)
    se = index_value(senior_extra, year, cpi)
    senior: float = 0.0
    if your_age >= 65:
        senior += se
    if spouse_age >= 65:
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

    $6,000 per person age 65+, phases out at $0.06 per $1 of excess MAGI ($60/$1,000) above threshold.
    Sunset: returns 0.0 for year < 2025 or year > 2028 (Pub. L. 119-21 §70103).
    Threshold depends on filing status (Pub. L. 119-21 §70103 — IRC §151(d)(5)(C)):
      MFJ:    phase-out starts $150,000, ends $250,000
      Single/HoH: phase-out starts $75,000, ends $175,000
      MFS:    ineligible (returns 0)

    Phaseout is applied per-person independently so the dual-eligible MFJ case
    zeros at MAGI=$250K (same endpoint as single-eligible MFJ), matching statute intent.

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
    if magi <= phaseout_start:
        return bonus_per_person * eligible
    per_person_reduction = min(bonus_per_person, max(0.0, magi - phaseout_start) * phaseout_rate)
    deduction_per_person = bonus_per_person - per_person_reduction
    return deduction_per_person * eligible


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


def room_to_12(
    current_gross: float,
    total_deductions: float,
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
    filing_status: str = "MFJ",
) -> float:
    brackets = BRACKETS_SINGLE if filing_status == "Single" else BRACKETS_MFJ
    ceiling = index_value(brackets[1][0], year, cpi)
    return room_to_bracket(current_gross, total_deductions, ceiling)


def room_to_22(
    current_gross: float,
    total_deductions: float,
    *,
    year: int = BASE_YEAR,
    cpi: float = DEFAULT_CPI,
    filing_status: str = "MFJ",
) -> float:
    brackets = BRACKETS_SINGLE if filing_status == "Single" else BRACKETS_MFJ
    ceiling = index_value(brackets[2][0], year, cpi)
    return room_to_bracket(current_gross, total_deductions, ceiling)


def federal_tax_single(
    taxable_income: float, *, year: int = BASE_YEAR, cpi: float = DEFAULT_CPI
) -> float:
    """Compute federal income tax on taxable income (Single filer)."""
    if taxable_income <= 0:
        return 0.0
    brackets = index_bracket_list(BRACKETS_SINGLE, year, cpi)
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
    brackets = index_bracket_list(BRACKETS_SINGLE, year, cpi)
    for ceil, rate in brackets:
        if taxable_income <= ceil:
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

    # Step 3: standard deduction (indexed) + senior extras + OBBBA bonus.
    # LTCG thresholds in LTCG_THRESHOLDS_MFJ are taxable-income thresholds
    # (IRC §1(h)(1)); the same std_ded base is used for both the ordinary bracket
    # walk and the LTCG stack-walk so both are evaluated on taxable income.
    if hh.filing_status == "MFJ":
        senior_count = (1 if hh.your_age >= 65 else 0) + (1 if hh.spouse_age >= 65 else 0)
        std_ded = index_value(STD_DEDUCTION_MFJ, _year, _cpi) + senior_count * index_value(
            SENIOR_EXTRA_MFJ, _year, _cpi
        )
    else:
        senior_count = 1 if hh.your_age >= 65 else 0
        std_ded = index_value(STD_DEDUCTION_SINGLE, _year, _cpi) + senior_count * index_value(
            SENIOR_EXTRA_SINGLE, _year, _cpi
        )
    # OBBBA senior bonus deduction also lowers the taxable income base.
    std_ded += senior_bonus_deduction(
        hh.your_age,
        hh.spouse_age,
        ytd.niit_magi_ytd,
        year=_year,
        cpi=_cpi,
        filing_status=hh.filing_status,
    )
    # Taxable ordinary income: gross (including taxable SS) minus deductions (IRC §63(a)).
    taxable_ordinary = max(ordinary_income_with_ss - std_ded, 0.0)

    # Step 4: ordinary income tax on TAXABLE ordinary income (not gross).
    ordinary_tax = (federal_tax_single if is_single else federal_tax)(
        taxable_ordinary, year=_year, cpi=_cpi
    )

    # Step 5: LTCG + qualified dividends taxed at preferential rate.
    # LTCG stacks ON TOP of taxable ordinary income; walk the stack across brackets.
    _ltcg_thresholds = index_tuple(
        LTCG_THRESHOLDS_SINGLE if is_single else LTCG_THRESHOLDS_MFJ, _year, _cpi
    )
    ltcg_taxable = ytd.ltcg_ytd + ytd.qualified_dividends_ytd
    ltcg_start = taxable_ordinary
    ltcg_end = taxable_ordinary + ltcg_taxable
    # 0%-rate portion (below threshold[0]) contributes $0 tax; 15% and 20% portions taxed
    ltcg_at_15 = max(
        0.0,
        min(ltcg_end, _ltcg_thresholds[1]) - max(ltcg_start, _ltcg_thresholds[0]),
    )
    ltcg_at_20 = max(0.0, ltcg_end - max(ltcg_start, _ltcg_thresholds[1]))
    ltcg_tax = ltcg_at_15 * LTCG_RATES_MFJ[1] + ltcg_at_20 * LTCG_RATES_MFJ[2]

    # Step 6: NIIT — 3.8% on lesser of NII or MAGI excess over threshold.
    # §1411(d)(3): NIIT MAGI excludes tax-exempt interest (unlike IRMAA MAGI).
    net_investment_income = ytd.ltcg_ytd + ytd.stcg_ytd + ytd.dividends_ytd + ytd.interest_ytd
    magi = ytd.niit_magi_ytd
    niit_amount = niit(magi, net_investment_income, filing_status=hh.filing_status)

    total = ordinary_tax + ltcg_tax + niit_amount
    effective_rate = total / magi if magi > 0 else 0.0

    # Step 7: marginal bracket rate — derived from TAXABLE ordinary income (IRC §1).
    marginal = (marginal_rate_single if is_single else marginal_rate)(
        taxable_ordinary, year=_year, cpi=_cpi
    )

    # Step 8: room to next bracket ceiling — measured from TAXABLE ordinary income
    # against the taxable-income bracket ceilings (not from gross income).
    _indexed_brackets = index_bracket_list(
        BRACKETS_SINGLE if is_single else BRACKETS_MFJ, _year, _cpi
    )
    room_next = 0.0
    for ceil, _rate in _indexed_brackets:
        if taxable_ordinary <= ceil:
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
    prior_year_agi: float = 200_000.0,
) -> SafeHarborGuidance:
    """Compute remaining safe-harbor payment to avoid underpayment penalty.

    IRS Form 2210 safe harbor: pay LESSER of:
    - 100% of prior year tax  (when prior-year AGI ≤ $150K MFJ / $75K Single)
    - 110% of prior year tax  (when prior-year AGI > $150K MFJ / $75K Single)
    - current year tax estimate (90% rule approximated as 100% here)

    The AGI threshold used here is $150,000 MFJ (IRS Rev. Proc., Form 2210).
    ``prior_year_agi`` defaults to 200,000 so callers that don't supply it
    continue to receive the 110% rule (preserves pre-fix behaviour).

    If prior_year_tax is 0 (no data), uses current-year estimate only.
    """
    next_due = _next_quarterly_due(payment_date)

    # IRS threshold: $150K MFJ (or $75K Single) → 110%; at or below → 100%
    prior_multiplier = 1.10 if prior_year_agi > SAFE_HARBOR_AGI_THRESHOLD_MFJ else 1.00

    if prior_year_tax <= 0:
        safe_harbor_target = current_year_estimate
        rule_used = "100% current estimate (prior year unknown)"
    else:
        prior_safe = prior_multiplier * prior_year_tax
        pct_label = "110%" if prior_multiplier > 1.0 else "100%"
        if prior_safe <= current_year_estimate:
            safe_harbor_target = prior_safe
            rule_used = f"{pct_label} prior year"
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
