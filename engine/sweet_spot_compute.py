"""Pure compute for the Sweet Spot Finder view.

Functions return plain dataclasses; no Streamlit, no plotly.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.aca import aca_applies, aca_subsidy_loss
from engine.ira import ss_benefit_at_age, ss_with_cola
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE, _index_irmaa_tiers, irmaa_for_year
from engine.niit import niit
from engine.tax import (
    BRACKETS_SINGLE,
    LTCG_RATES_MFJ,
    LTCG_THRESHOLDS_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    federal_tax,
    federal_tax_single,
    room_to_12,
    room_to_22,
    room_to_bracket,
    senior_bonus_deduction,
    taxable_ss,
)
from engine.tax_indexing import index_value as _index_value
from models.household import Household
from models.ytd_income import YTDSnapshot

STEP = 1_000  # sweep in $1K increments


@dataclass
class BaseIncome:
    """Year-level fixed income components (no conversion)."""

    ya: int
    sa: int
    year: int
    cpi: float
    opt: float
    combined_ss: float
    base_gross: float
    base_magi: float
    total_ded: float
    ded_base: float
    ytd_magi: float = 0.0
    ytd_niit_magi: float = 0.0


@dataclass
class ConversionResult:
    """All-in cost decomposition at a given conversion amount."""

    conv: float
    conv_tax: float
    irmaa_delta: float
    aca_loss: float
    niit_delta: float
    ltcg_delta: float
    all_in: float
    magi: float
    taxable_inc: float
    room_12: float
    room_22: float


@dataclass
class SweetSpotJump:
    """A marginal-cost jump point (>2% per $1K spike)."""

    conv: float
    label: str
    reason: str
    marginal_before: float
    marginal_after: float


@dataclass
class MarginalCosts:
    """Per-$1K marginal cost components across the conversion sweep."""

    marginals: list[float]
    marginal_tax: list[float]
    marginal_irmaa: list[float]
    marginal_aca: list[float]
    marginal_niit: list[float]
    marginal_ltcg: list[float]


@dataclass
class YearSummary:
    """One row of the multi-year sweet-spot summary table. Raw values; view formats."""

    year: int
    you_age: int
    spouse_age: int
    base_magi: float
    fill_12: float
    cost_12: float
    rate_12: float
    fill_22: float
    cost_22: float
    rate_22: float
    irmaa_safe: float | None  # None if base MAGI already exceeds tier 1


def _fmt_dollars_simple(v: float) -> str:
    """Minimal dollar formatter used only for SweetSpotJump.label inside this module."""
    return f"${v:,.0f}"


def _ltcg_stack_tax(start: float, eligible: float, thresholds: tuple[float, float]) -> float:
    """LTCG/qualified-dividend tax via the 0%/15%/20% stack-walk, mirroring
    engine.scenario:564-576. `start` is ordinary taxable income (the stack base),
    `eligible` is realized LTCG + qualified dividends, `thresholds` are the
    (0%→15%, 15%→20%) ceilings already indexed for the year. Uses LTCG_RATES_MFJ
    for both filing statuses (no separate Single rates exist)."""
    start = max(0.0, start)
    end = start + max(0.0, eligible)
    at_15 = max(0.0, min(end, thresholds[1]) - max(start, thresholds[0]))
    at_20 = max(0.0, end - max(start, thresholds[1]))
    return at_15 * LTCG_RATES_MFJ[1] + at_20 * LTCG_RATES_MFJ[2]


def estimate_ltcg_eligible(hh: Household, year: int) -> float:
    """Estimate realized LTCG + qualified dividends for `year` from the begin
    brokerage balance, mirroring engine.scenario's ltcg_eligible. Returns 0.0 when
    no brokerage data is available."""
    brokerage = hh.brokerage_start
    if hh.brokerage_growth is not None:
        appr = hh.brokerage_growth.appreciation_for(year)
        qual_div = hh.brokerage_growth.qualified_div_for(year, brokerage)
    else:
        appr = hh.brokerage_rate(year)
        qual_div = 0.0
    return brokerage * appr * hh.brok_turnover + qual_div


def base_income_for_year(hh: Household, year: int, ytd: YTDSnapshot | None = None) -> BaseIncome:
    """Compute fixed income components for a given year (no conversion)."""
    ya = hh.your_age_in(year)
    sa = hh.spouse_age_in(year)
    cpi = hh.cpi_assumption

    opt = hh.option_income(year, early=True)

    your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
    spouse_ss_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age)
    your_ss = (
        ss_with_cola(your_ss_base, ya - hh.your_ss_start_age, hh.ss_cola)
        if ya >= hh.your_ss_start_age
        else 0.0
    )
    spouse_ss = (
        ss_with_cola(spouse_ss_base, sa - hh.spouse_ss_start_age, hh.ss_cola)
        if sa >= hh.spouse_ss_start_age
        else 0.0
    )
    combined_ss = your_ss + spouse_ss

    if hh.filing_status == "Single":
        ded = deductions(ya, sa, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, year=year, cpi=cpi)
    else:
        ded = deductions(ya, sa, hh.std_deduction, hh.senior_extra, year=year, cpi=cpi)

    # Base taxable SS (without conversion)
    tss = taxable_ss(combined_ss, opt, filing_status=hh.filing_status)

    # Base gross (without conversion)
    base_gross = opt + tss

    # MAGI base (without conversion)
    ytd_magi = ytd.magi_ytd if ytd is not None else 0.0  # base-year realized YTD (niit-5)
    ytd_niit_magi = ytd.niit_magi_ytd if ytd is not None else 0.0  # IRC §1411(d)(3)
    base_magi = opt + tss + ytd_magi

    # Senior bonus deduction
    senior_bonus = senior_bonus_deduction(
        ya, sa, base_magi, year=year, cpi=cpi, filing_status=hh.filing_status
    )
    total_ded = ded + senior_bonus

    return BaseIncome(
        ya=ya,
        sa=sa,
        year=year,
        cpi=cpi,
        opt=opt,
        combined_ss=combined_ss,
        base_gross=base_gross,
        base_magi=base_magi,
        total_ded=total_ded,
        ded_base=ded,
        ytd_magi=ytd_magi,
        ytd_niit_magi=ytd_niit_magi,
    )


def bracket_boundary_conversion(base: BaseIncome, bracket_ceiling: float) -> float:
    """Conversion amount that lifts taxable income to the given bracket ceiling."""
    # all_in_at_conversion uses taxable_inc = (opt + conv + tss) - total_ded, and
    # base.base_gross == opt + tss, so solving taxable_inc == ceiling gives
    #   conv = ceiling + total_ded - opt - tss = ceiling + total_ded - base_gross.
    # (The earlier view formula subtracted opt a second time, drawing boundaries low.)
    return max(base.total_ded + bracket_ceiling - base.base_gross, 0.0)


def all_in_at_conversion(
    hh: Household,
    base: BaseIncome,
    conv: float,
    net_inv_income: float,
    ltcg_eligible: float = 0.0,
) -> ConversionResult:
    """Compute all-in costs at a given conversion amount."""
    ya, sa = base.ya, base.sa
    year: int = base.year
    cpi: float = base.cpi

    single = hh.filing_status == "Single"

    # Recalculate taxable SS with conversion income
    other_inc = base.opt + conv
    tss = taxable_ss(base.combined_ss, other_inc, filing_status=hh.filing_status)

    gross = base.opt + conv + tss
    magi = base.opt + conv + tss + base.ytd_magi

    # Recalculate senior bonus deduction at new MAGI
    senior_bonus = senior_bonus_deduction(
        ya, sa, magi, year=year, cpi=cpi, filing_status=hh.filing_status
    )
    total_ded = base.ded_base + senior_bonus

    taxable_inc = max(gross - total_ded, 0)
    tax = (federal_tax_single if single else federal_tax)(taxable_inc, year=year, cpi=cpi)

    # Base tax (no conversion)
    base_tss = taxable_ss(base.combined_ss, base.opt, filing_status=hh.filing_status)
    base_gross = base.opt + base_tss
    base_senior = senior_bonus_deduction(
        ya, sa, base.base_magi, year=year, cpi=cpi, filing_status=hh.filing_status
    )
    base_total_ded = base.ded_base + base_senior
    base_taxable = max(base_gross - base_total_ded, 0)
    base_tax = (federal_tax_single if single else federal_tax)(base_taxable, year=year, cpi=cpi)

    conv_tax = tax - base_tax

    # IRMAA (2-year lookback): irmaa_for_year indexes thresholds to the PAYMENT
    # year (income_year + 2). ya/sa stay income-year ages — irmaa_for_year adds 2
    # internally for the Medicare-eligibility gate. Matches the yr + 2 already used
    # in compute_multi_year_summary.
    irmaa_cost, _ = irmaa_for_year(
        magi, ya, sa, filing_status=hh.filing_status, year=year + 2, cpi=cpi
    )
    base_irmaa, _ = irmaa_for_year(
        base.base_magi, ya, sa, filing_status=hh.filing_status, year=year + 2, cpi=cpi
    )
    irmaa_delta = irmaa_cost - base_irmaa

    # ACA — MAGI per IRC §36B(d)(2)(B)(iii) adds back the NON-taxable portion of
    # Social Security. The `magi` above is IRMAA-compatible (§1839(i)(4)) and does
    # NOT include non-taxable SS, so add it back only for the ACA computation.
    # Mirrors engine/scenario_compute.compute_aca (aca_magi = magi + combined_ss - taxable_ss).
    num_on_aca = (1 if aca_applies(ya, hh.your_aca_enrolled) else 0) + (
        1 if aca_applies(sa, hh.spouse_aca_enrolled) else 0
    )
    effective_benchmark = hh.aca_benchmark_premium_annual * (num_on_aca / 2)
    aca_base_magi = base.base_magi + (base.combined_ss - base_tss)
    aca_magi = magi + (base.combined_ss - tss)
    aca_loss = (
        aca_subsidy_loss(
            aca_base_magi,
            aca_magi,
            benchmark=effective_benchmark,
            enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
            filing_status=hh.filing_status,
            year=year,
            cpi=cpi,
        )
        if num_on_aca > 0
        else 0.0
    )

    # NIIT — use NIIT-MAGI which excludes tax-exempt interest (IRC §1411(d)(3))
    niit_magi = base.opt + conv + tss + base.ytd_niit_magi
    niit_base_magi = base.opt + base_tss + base.ytd_niit_magi
    niit_with = niit(niit_magi, net_inv_income, filing_status=hh.filing_status)
    niit_without = niit(niit_base_magi, net_inv_income, filing_status=hh.filing_status)
    niit_delta = niit_with - niit_without

    # LTCG bracket-stacking (C1): a conversion lifts ordinary taxable income, raising
    # the start of the preferential-rate stack and pushing realized LTCG + qualified
    # dividends into higher 0%/15%/20% bands. Mirror engine.scenario:564-576. LTCG
    # thresholds index to the INCOME year (same-year tax — NOT the IRMAA +2 payment year).
    _base_ltcg_thr = LTCG_THRESHOLDS_SINGLE if single else LTCG_THRESHOLDS_MFJ
    _ltcg_thr = (
        _index_value(_base_ltcg_thr[0], year, cpi),
        _index_value(_base_ltcg_thr[1], year, cpi),
    )
    ltcg_with = _ltcg_stack_tax(taxable_inc, ltcg_eligible, _ltcg_thr)
    ltcg_without = _ltcg_stack_tax(base_taxable, ltcg_eligible, _ltcg_thr)
    ltcg_delta = ltcg_with - ltcg_without

    all_in = conv_tax + irmaa_delta + aca_loss + niit_delta + ltcg_delta

    return ConversionResult(
        conv=conv,
        conv_tax=conv_tax,
        irmaa_delta=irmaa_delta,
        aca_loss=aca_loss,
        niit_delta=niit_delta,
        ltcg_delta=ltcg_delta,
        all_in=all_in,
        magi=magi,
        taxable_inc=taxable_inc,
        room_12=room_to_bracket(gross, total_ded, _index_value(BRACKETS_SINGLE[1][0], year, cpi))
        if single
        else room_to_12(gross, total_ded, year=year, cpi=cpi),
        room_22=room_to_bracket(gross, total_ded, _index_value(BRACKETS_SINGLE[2][0], year, cpi))
        if single
        else room_to_22(gross, total_ded, year=year, cpi=cpi),
    )


def find_sweet_spots(results: list[ConversionResult]) -> list[SweetSpotJump]:
    """Identify zones where marginal cost jumps significantly."""
    spots: list[SweetSpotJump] = []
    if len(results) < 2:
        return spots

    prev_marginal = 0.0
    for i in range(1, len(results)):
        curr = results[i]
        prev = results[i - 1]
        if curr.conv == 0:
            continue
        marginal = (curr.all_in - prev.all_in) / STEP * 100  # per $100
        if i > 1 and marginal - prev_marginal > 2.0:  # >2% jump per $1K
            spots.append(
                SweetSpotJump(
                    conv=prev.conv,
                    label=_fmt_dollars_simple(prev.conv),
                    reason=classify_jump(prev, curr),
                    marginal_before=prev_marginal,
                    marginal_after=marginal,
                )
            )
        prev_marginal = marginal

    return spots


def classify_jump(before: ConversionResult, after: ConversionResult) -> str:
    """Classify what caused a marginal cost jump."""
    reasons = []
    if after.irmaa_delta > before.irmaa_delta + 100:
        reasons.append("IRMAA tier")
    if after.aca_loss > before.aca_loss + 100:
        reasons.append("ACA cliff")
    if after.niit_delta > before.niit_delta + 10:
        reasons.append("NIIT threshold")
    if not reasons:
        reasons.append("bracket change")
    return " + ".join(reasons)


def compute_marginal_costs(results: list[ConversionResult]) -> MarginalCosts:
    """Compute per-$1K marginal cost components across a conversion sweep."""
    marginals = [0.0]
    marginal_tax = [0.0]
    marginal_irmaa = [0.0]
    marginal_aca = [0.0]
    marginal_niit = [0.0]
    marginal_ltcg = [0.0]

    for i in range(1, len(results)):
        m = (results[i].all_in - results[i - 1].all_in) / STEP * 1000
        marginals.append(m)
        marginal_tax.append((results[i].conv_tax - results[i - 1].conv_tax) / STEP * 1000)
        marginal_irmaa.append((results[i].irmaa_delta - results[i - 1].irmaa_delta) / STEP * 1000)
        marginal_aca.append((results[i].aca_loss - results[i - 1].aca_loss) / STEP * 1000)
        marginal_niit.append((results[i].niit_delta - results[i - 1].niit_delta) / STEP * 1000)
        marginal_ltcg.append((results[i].ltcg_delta - results[i - 1].ltcg_delta) / STEP * 1000)

    return MarginalCosts(
        marginals=marginals,
        marginal_tax=marginal_tax,
        marginal_irmaa=marginal_irmaa,
        marginal_aca=marginal_aca,
        marginal_niit=marginal_niit,
        marginal_ltcg=marginal_ltcg,
    )


def compute_multi_year_summary(
    hh: Household,
    *,
    net_inv_income: float = 0.0,
    ytd: YTDSnapshot | None = None,
    include_ltcg_stacking: bool = False,
) -> list[YearSummary]:
    """Compute sweet-spot summary rows for all conversion years."""
    conv_window = max(hh.your_conv_window, hh.spouse_conv_window)
    conv_years = list(range(hh.base_year, hh.base_year + conv_window))

    _base_irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ

    rows: list[YearSummary] = []
    for yr in conv_years:
        cpi = hh.cpi_assumption
        # IRMAA 2-year lookback: threshold that applies is for the PAYMENT year (income_year+2).
        irmaa_tiers = _index_irmaa_tiers(_base_irmaa_tiers, yr + 2, cpi)

        b = base_income_for_year(hh, yr, ytd=ytd if yr == hh.base_year else None)
        _le = estimate_ltcg_eligible(hh, yr) if include_ltcg_stacking else 0.0
        b_result = all_in_at_conversion(hh, b, 0, net_inv_income, ltcg_eligible=_le)
        r12 = b_result.room_12
        r22 = b_result.room_22

        tier1_threshold = irmaa_tiers[0][0]
        irmaa_max = tier1_threshold - b.base_magi
        irmaa_safe: float | None = max(irmaa_max, 0) if irmaa_max > 0 else None

        r12_res = (
            all_in_at_conversion(hh, b, r12, net_inv_income, ltcg_eligible=_le) if r12 > 0 else None
        )
        r22_res = (
            all_in_at_conversion(hh, b, r22, net_inv_income, ltcg_eligible=_le) if r22 > 0 else None
        )

        rows.append(
            YearSummary(
                year=yr,
                you_age=b.ya,
                spouse_age=b.sa,
                base_magi=b.base_magi,
                fill_12=r12,
                cost_12=r12_res.all_in if r12_res else 0.0,
                rate_12=r12_res.all_in / max(r12, 1) if r12_res else 0.0,
                fill_22=r22,
                cost_22=r22_res.all_in if r22_res else 0.0,
                rate_22=r22_res.all_in / max(r22, 1) if r22_res else 0.0,
                irmaa_safe=irmaa_safe,
            )
        )

    return rows
