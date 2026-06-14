"""Federal tax calculations — TCJA/OBBBA permanent brackets, SS taxation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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

# OBBBA senior bonus deduction (2026-2028, sunsets thereafter)
OBBBA_BONUS_PER_PERSON = 6_000
OBBBA_PHASEOUT_START_MFJ = 150_000  # Pub. L. 119-21 §70103 — IRC §151(d)(5)(C)
OBBBA_PHASEOUT_START_SINGLE = 75_000  # Single / HoH — same citation
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
    *,
    year: int,
    filing_status: str = "MFJ",
    bonus_per_person: float = OBBBA_BONUS_PER_PERSON,
    phaseout_start: float | None = None,
    phaseout_rate: float = OBBBA_PHASEOUT_RATE,
) -> float:
    """
    OBBBA Senior Bonus Deduction (2026-2028).

    $6,000 per person age 65+, phases out linearly at 6% per $1 above threshold.
    Sunset: returns 0.0 for year > 2028 (Pub. L. 119-21 §70103).
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
    if filing_status == "MFS":
        return 0.0
    eligible = sum(1 for age in [your_age, spouse_age] if age >= 65)
    if eligible == 0:
        return 0.0
    if phaseout_start is None:
        phaseout_start = (
            OBBBA_PHASEOUT_START_MFJ if filing_status == "MFJ" else OBBBA_PHASEOUT_START_SINGLE
        )
    if magi <= phaseout_start:
        return bonus_per_person * eligible
    per_person_reduction = min(bonus_per_person, max(0.0, magi - phaseout_start) * phaseout_rate)
    deduction_per_person = bonus_per_person - per_person_reduction
    return deduction_per_person * eligible


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
    return room_to_bracket(current_gross, total_deductions, BRACKETS_MFJ[1][0])


def room_to_22(current_gross: float, total_deductions: float) -> float:
    return room_to_bracket(current_gross, total_deductions, BRACKETS_MFJ[2][0])


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
) -> YTDTaxEstimate:
    """Estimate federal tax owed YTD as if today were Dec 31.

    Stacks ordinary income through brackets, then applies preferential rates
    on LTCG/qualified dividends. NIIT applied per net investment income vs
    MAGI threshold. Does NOT include state tax, IRMAA premiums, or quarterly
    underpayment penalties.

    Standard deduction IS applied for the LTCG stack-walk base (taxable_ordinary)
    so that LTCG thresholds — which are taxable-income thresholds per IRC §1(h)(1)
    — are evaluated correctly. The ordinary-income bracket call (federal_tax) still
    receives gross ordinary income as a conservative estimate (no deduction applied).
    """
    from engine.niit import NIIT_RATE, NIIT_THRESHOLD_MFJ

    ordinary_income = ytd.total_ordinary_income
    ordinary_tax = federal_tax(ordinary_income)

    # Compute standard deduction to find taxable ordinary income for LTCG stack-walk.
    # LTCG thresholds in LTCG_THRESHOLDS_MFJ are taxable-income thresholds (IRC §1(h)(1));
    # using gross income shifts the 0%-band upward by the std deduction amount.
    if hh.filing_status == "MFJ":
        senior_count = (1 if hh.your_age >= 65 else 0) + (1 if hh.spouse_age >= 65 else 0)
        std_ded = STD_DEDUCTION_MFJ + senior_count * SENIOR_EXTRA_MFJ
    else:
        senior_count = 1 if hh.your_age >= 65 else 0
        std_ded = STD_DEDUCTION_SINGLE + senior_count * SENIOR_EXTRA_SINGLE
    taxable_ordinary = max(ordinary_income - std_ded, 0.0)

    # LTCG + qualified dividends taxed at preferential rate.
    # LTCG stacks ON TOP of taxable ordinary income; walk the stack across brackets.
    ltcg_taxable = ytd.ltcg_ytd + ytd.qualified_dividends_ytd
    ltcg_start = taxable_ordinary
    ltcg_end = taxable_ordinary + ltcg_taxable
    # 0%-rate portion (below threshold[0]) contributes $0 tax; 15% and 20% portions taxed
    ltcg_at_15 = max(
        0.0,
        min(ltcg_end, LTCG_THRESHOLDS_MFJ[1]) - max(ltcg_start, LTCG_THRESHOLDS_MFJ[0]),
    )
    ltcg_at_20 = max(0.0, ltcg_end - max(ltcg_start, LTCG_THRESHOLDS_MFJ[1]))
    ltcg_tax = ltcg_at_15 * LTCG_RATES_MFJ[1] + ltcg_at_20 * LTCG_RATES_MFJ[2]

    # NIIT: 3.8% on lesser of NII or MAGI excess over threshold
    # §1411(d)(3): NIIT MAGI excludes tax-exempt interest (unlike IRMAA MAGI).
    net_investment_income = ytd.ltcg_ytd + ytd.stcg_ytd + ytd.dividends_ytd + ytd.interest_ytd
    magi = ytd.niit_magi_ytd
    magi_excess = max(0.0, magi - NIIT_THRESHOLD_MFJ)
    niit_amount = NIIT_RATE * min(net_investment_income, magi_excess)

    total = ordinary_tax + ltcg_tax + niit_amount
    effective_rate = total / magi if magi > 0 else 0.0

    # Marginal bracket for ordinary income
    marginal = marginal_rate(ordinary_income)

    # Room to next bracket ceiling
    room_next = 0.0
    for ceil, _rate in BRACKETS_MFJ:
        if ordinary_income <= ceil:
            room_next = ceil - ordinary_income
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
    month, day = d.month, d.day

    if (month, day) <= (4, 15):
        due = date(year, 4, 15)
    elif (month, day) <= (6, 15):
        due = date(year, 6, 15)
    elif (month, day) <= (9, 15):
        due = date(year, 9, 15)
    else:
        due = date(year + 1, 1, 15)

    # Roll Saturday (weekday 5) → Monday (+2), Sunday (weekday 6) → Monday (+1)
    if due.weekday() == 5:
        due += timedelta(days=2)
    elif due.weekday() == 6:
        due += timedelta(days=1)

    return due.isoformat()


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
