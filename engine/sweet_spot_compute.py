"""Pure compute for the Sweet Spot Finder view.

Functions return plain dataclasses; no Streamlit, no plotly.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.aca import (
    aca_applies,
    aca_subsidy_loss,
    effective_benchmark_premium,
    resolve_couple_benchmark_annual,
)
from engine.ira import calc_rmd, inherited_ira_drain_for_year, ss_benefit_at_age, ss_with_cola
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE, _index_irmaa_tiers, irmaa_for_year
from engine.niit import niit
from engine.scenario import run_scenario
from engine.scenario_compute import QCD_MIN_AGE
from engine.scenario_types import ConversionPlan
from engine.tax import (
    BRACKETS_MFJ,
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
    ytd_ordinary: float = 0.0  # ordinary-income portion of YTD: wages+NEC+STCG+ord-divs+interest+distributions (NOT LTCG, NOT qual-divs, NOT muni)
    # R1-R4 (audit 2026-07-13): forecast brokerage income + RMD/inherited-IRA income,
    # mirroring engine.scenario's per-year assembly. All are year-level (conversion-
    # independent) so they are computed once here and reused by all_in_at_conversion.
    forecast_qual_div: float = 0.0  # forecast qualified dividends (MAGI + LTCG-stack only; suppressed to 0 in base year when ytd is supplied)
    forecast_ord_div: float = 0.0  # forecast ordinary dividends (ordinary bracket + MAGI; suppressed to 0 in base year when ytd is supplied)
    forecast_realized_gains: float = 0.0  # forecast realized LTCG from brokerage turnover (MAGI + LTCG-stack only; suppressed to 0 in base year when ytd is supplied)
    rmd_income: float = 0.0  # taxable RMD (both spouses) + inherited-IRA distributions (ordinary bracket + MAGI + SS provisional income)
    ytd_investment_income: float = 0.0  # ytd.total_investment_income (NIIT net-investment-income parity; 0 outside the base year)
    # audit-0809 #08: the traditional-IRA draw that funds this year's living
    # expenses, IRMAA surcharges and ACA premiums, solved at ZERO conversion by
    # engine.scenario's withdrawal waterfall (see zero_conversion_ira_draws).
    # Ordinary income, so it belongs in the ordinary bracket base, in MAGI and
    # in the muni-exclusive NIIT MAGI alike -- but NOT in net investment income.
    waterfall_draw: float = 0.0

    @property
    def magi_addl(self) -> float:
        """Year-level MAGI additions independent of the swept conversion amount:
        YTD MAGI + forecast qual/ord dividends + forecast realized gains + RMD/
        inherited-IRA income + the living-expense waterfall draw. Mirrors
        engine.scenario's magi assembly (compute_magi + the realized_gains fold),
        including compute_magi's forced_your_ira_draw/forced_spouse_ira_draw
        terms (audit-0809 #08)."""
        return (
            self.ytd_magi
            + self.forecast_qual_div
            + self.forecast_ord_div
            + self.forecast_realized_gains
            + self.rmd_income
            + self.waterfall_draw
        )

    @property
    def ordinary_addl(self) -> float:
        """Year-level ordinary-bracket additions independent of the swept conversion
        amount: YTD ordinary income + forecast ordinary dividends + RMD/inherited-IRA
        income + the living-expense waterfall draw (a traditional-IRA distribution
        is ordinary income exactly as an RMD is). Mirrors engine.scenario's
        combined_gross assembly."""
        return (
            self.ytd_ordinary
            + self.forecast_ord_div
            + self.rmd_income
            + self.waterfall_draw
        )

    @property
    def net_investment_income_addl(self) -> float:
        """Year-level net-investment-income additions for NIIT: forecast realized
        gains + forecast qual/ord dividends + YTD investment income. Mirrors
        engine.scenario's `net_investment_income = realized_gains +
        qual_div_this_year + ord_div_this_year` assembly in compute_scenario
        (grep for that assignment rather than a line number -- it has moved
        before). Excludes rmd_income AND waterfall_draw: neither an RMD nor a
        living-expense IRA distribution is net investment income under IRC
        1411(c). Both raise the NIIT MAGI threshold test (see niit_magi_addl)
        but never the NII the tax is charged on."""
        return (
            self.forecast_realized_gains
            + self.forecast_qual_div
            + self.forecast_ord_div
            + self.ytd_investment_income
        )

    @property
    def niit_magi_addl(self) -> float:
        """Muni-exclusive twin of `magi_addl`, for the NIIT MAGI threshold test
        and the OBBBA senior-bonus phaseout: identical except that tax-exempt
        muni interest is excluded (IRC 103 keeps it out of gross income, so it
        was never in AGI/MAGI) -- hence ytd_niit_magi in place of ytd_magi.

        Extracted because this exact sum previously appeared open-coded at four
        call sites in all_in_at_conversion plus one in base_income_for_year, and
        adding a term meant remembering all five. audit-0809 classes that shape
        of omission as Class B (a fix applied to one consumer and not its
        sibling, so one page shows two answers to one question); one expression,
        one place to extend it."""
        return (
            self.ytd_niit_magi
            + self.forecast_qual_div
            + self.forecast_ord_div
            + self.forecast_realized_gains
            + self.rmd_income
            + self.waterfall_draw
        )


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
    niit_magi: float = 0.0  # NIIT-relevant MAGI: magi minus tax-exempt muni interest (excluded from gross income under IRC §103, so it was never in AGI/MAGI)
    taxable_inc: float = 0.0
    room_12: float = 0.0
    room_22: float = 0.0


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
    the "=== LTCG tax ===" stack-walk block in engine.scenario.compute_scenario
    (grep for that header rather than a line number -- it has moved before).
    `start` is ordinary taxable income (the stack base),
    `eligible` is realized LTCG + qualified dividends, `thresholds` are the
    (0%→15%, 15%→20%) ceilings already indexed for the year. Uses LTCG_RATES_MFJ
    for both filing statuses (no separate Single rates exist)."""
    start = max(0.0, start)
    end = start + max(0.0, eligible)
    at_15 = max(0.0, min(end, thresholds[1]) - max(start, thresholds[0]))
    at_20 = max(0.0, end - max(start, thresholds[1]))
    return at_15 * LTCG_RATES_MFJ[1] + at_20 * LTCG_RATES_MFJ[2]


def estimate_brokerage_income(
    hh: Household, year: int, ytd: YTDSnapshot | None = None
) -> tuple[float, float, float]:
    """Estimate forecast (qualified_div, ordinary_div, realized_gains) for `year`,
    mirroring engine.scenario's compute_brokerage_dividends call and the "B1/B2"
    realized_gains suppression comment in compute_scenario (grep those markers
    rather than a line number -- it has moved before). Uses hh.brokerage_start as a static
    balance -- Sweet Spot Finder is a per-year snapshot, not a multi-year balance
    projection (see estimate_ltcg_eligible for the same simplification).

    R1/R5 (audit 2026-07-13): in the base year, when `ytd` actuals are supplied,
    the forecast is suppressed to 0.0 -- the YTD snapshot is the source of truth
    for that year's realized dividends/gains, avoiding double-counting."""
    if ytd is not None:
        return 0.0, 0.0, 0.0
    brokerage = hh.brokerage_start
    if hh.brokerage_growth is not None:
        qual_div = hh.brokerage_growth.qualified_div_for(year, brokerage)
        ord_div = hh.brokerage_growth.ordinary_div_for(year, brokerage)
        appr = hh.brokerage_growth.appreciation_for(year)
    else:
        qual_div = 0.0
        ord_div = 0.0
        appr = hh.brokerage_rate(year)
    realized_gains = brokerage * appr * hh.brok_turnover
    return qual_div, ord_div, realized_gains


def estimate_rmd_income(
    hh: Household,
    year: int,
    ytd: YTDSnapshot | None = None,
    *,
    your_qcd: float = 0.0,
    spouse_qcd: float = 0.0,
) -> float:
    """Estimate combined taxable RMD (both spouses) + inherited-IRA distributions
    for `year`, mirroring engine.scenario's compute_rmds + inherited_ira_drain.

    R3 (audit 2026-07-13): uses the CURRENT (undiminished) IRA balances
    (hh.your_ira / hh.spouse_ira) as a static proxy -- Sweet Spot Finder is a
    per-year snapshot, not a multi-year balance projection, so year-over-year
    RMD/growth compounding is not modeled (same simplification pattern as
    estimate_ltcg_eligible / estimate_brokerage_income).

    Audit findings 2+3 (HIGH, 2026-08): `ytd` (base year only, per caller
    convention) nets already-distributed YTD IRA withdrawals out of the
    forecast RMD -- mirroring engine.scenario's "base-year RMD net-of-YTD
    reconciliation" (ytd_year.ira_distributions_ytd reduces yr.taxable_rmd,
    yours first then spouse's). Without this, ytd.ira_distributions_ytd is
    already folded into ytd_magi/ytd_ordinary upstream in base_income_for_year,
    so the un-netted forecast RMD double-counts the already-taken portion in
    both MAGI and NIIT-MAGI.

    Audit finding 4 (MEDIUM, 2026-08): `your_qcd`/`spouse_qcd` (the household's
    planned QCD election for this year -- no ConversionPlan is available in
    this module, so the caller supplies the amount directly) net a Qualified
    Charitable Distribution out of the taxable RMD per-spouse, mirroring
    engine.scenario_compute.compute_rmds:
        taxable_rmd = max(rmd - min(qcd, qcd_limit, rmd), 0), gated on
        age >= QCD_MIN_AGE. QCD nets BEFORE the YTD-distribution reduction
    above (same order as compute_rmds, which nets QCD when it computes
    taxable_rmd, then the separate YTD-reconciliation block reduces it
    further)."""
    ya = hh.your_age_in(year)
    sa = hh.spouse_age_in(year)
    # M3 (audit-0720): beneficiary is the OTHER spouse, only passed when the
    # household elects the sole-beneficiary toggle. This snapshot function
    # doesn't model survivor scenarios at all, so no survivor gate is needed.
    _bene_gate = hh.spouse_is_sole_beneficiary
    your_rmd = calc_rmd(
        hh.your_ira,
        ya,
        hh.your_rmd_start_age,
        first_year_deferred=hh.your_defer_first_rmd,
        prior_year_balance=hh.your_ira if hh.your_defer_first_rmd else 0.0,
        beneficiary_age=sa if _bene_gate else None,
    )
    spouse_rmd = calc_rmd(
        hh.spouse_ira,
        sa,
        hh.spouse_rmd_start_age,
        first_year_deferred=hh.spouse_defer_first_rmd,
        prior_year_balance=hh.spouse_ira if hh.spouse_defer_first_rmd else 0.0,
        beneficiary_age=ya if _bene_gate else None,
    )

    # Finding 4: net QCD out of each spouse's own RMD (per-spouse, not pooled
    # -- unlike the YTD reduction below), gated on QCD_MIN_AGE and capped at
    # the inflation-indexed per-person qcd_limit.
    if your_qcd > 0 or spouse_qcd > 0:
        _qcd_limit = _index_value(hh.qcd_limit, year, hh.cpi_assumption)
        if ya >= QCD_MIN_AGE:
            _your_qcd_eff = min(your_qcd, _qcd_limit)
            your_rmd = max(your_rmd - min(_your_qcd_eff, your_rmd), 0.0)
        if sa >= QCD_MIN_AGE:
            _spouse_qcd_eff = min(spouse_qcd, _qcd_limit)
            spouse_rmd = max(spouse_rmd - min(_spouse_qcd_eff, spouse_rmd), 0.0)

    # Findings 2+3: net out YTD IRA distributions already taken (yours first,
    # then spouse's), mirroring scenario.py's C2/scenario-1 reduction. Only
    # non-conversion distributions count -- ytd.ira_distributions_ytd is
    # exactly that ("non-conversion IRA withdrawals").
    if ytd is not None and ytd.ira_distributions_ytd > 0:
        dist_remaining = ytd.ira_distributions_ytd
        your_reduction = min(your_rmd, dist_remaining)
        your_rmd -= your_reduction
        dist_remaining -= your_reduction
        spouse_reduction = min(spouse_rmd, dist_remaining)
        spouse_rmd -= spouse_reduction

    # audit-0805 C21: inherited_ira_drain_for_year replays the shrinking,
    # growth-compounded running balance year-by-year from iira.inherited_year up
    # to `year` -- mirroring engine.scenario.run_scenario's stateful per-year
    # balance tracking. Passing the STATIC iira.balance directly (the prior code)
    # drains the entire ORIGINAL balance in the final window year instead of the
    # true (much smaller) balance-of-record, a ~10x overstatement in the balloon
    # year for a 10-year window.
    inherited = sum(
        inherited_ira_drain_for_year(iira.balance, iira.inherited_year, year, iira.growth_rate)
        for iira in hh.inherited_iras
    )
    return your_rmd + spouse_rmd + inherited


def estimate_ltcg_eligible(hh: Household, year: int, ytd: YTDSnapshot | None = None) -> float:
    """Estimate realized LTCG + qualified dividends eligible for the preferential-
    rate stack for `year`, mirroring engine.scenario's ltcg_eligible (realized_gains
    + qual_div_this_year). Returns 0.0 when no brokerage data is available.

    R5 (audit 2026-07-13): when `ytd` actuals are supplied (base year), the forecast
    is suppressed and replaced with the YTD LTCG + qualified dividends actually
    realized so far this year -- mirroring scenario.py's base-year YTD LTCG
    stack-walk input (_ytd_ltcg_total = ytd.preferential_capital_gain_ytd +
    ytd.qualified_dividends_ytd, scenario.py's LTCG-tax block).

    audit-0809 Class A (site 4): preferential_capital_gain_ytd is the IRC
    §1222-netted long-term-character leg (ltcg_ytd + crypto_ltcg_ytd already
    folded in, net of any offsetting short-term loss -- see
    models/ytd_income.py::_net_capital_gain_split), not the raw ltcg_ytd +
    crypto_ltcg_ytd sum. qualified_dividends_ytd is NOT part of that netting
    and stays a separate addend.
    """
    if ytd is not None:
        return ytd.preferential_capital_gain_ytd + ytd.qualified_dividends_ytd
    qual_div, _ord_div, realized_gains = estimate_brokerage_income(hh, year, None)
    return realized_gains + qual_div


def base_income_for_year(
    hh: Household,
    year: int,
    ytd: YTDSnapshot | None = None,
    *,
    your_qcd: float = 0.0,
    spouse_qcd: float = 0.0,
    ira_draw: float = 0.0,
) -> BaseIncome:
    """Compute fixed income components for a given year (no conversion).

    Audit finding 4 (MEDIUM, 2026-08): `your_qcd`/`spouse_qcd` are the
    household's planned QCD election for `year` (no ConversionPlan is
    available in this module, so the caller -- e.g. views/sweet_spot.py, once
    wired to a QCD source -- supplies the dollar amount directly). Threaded
    through to estimate_rmd_income() to net the QCD out of taxable RMD before
    it enters magi/niit_magi. Defaults to 0.0 (no behavior change for callers
    that don't supply a QCD election).

    audit-0809 #08 (HIGH): `ira_draw` is the traditional-IRA withdrawal that
    funds this year's living expenses, IRMAA surcharges and ACA premiums,
    solved at ZERO conversion (see zero_conversion_ira_draws). Without it,
    base_gross/base_magi described a household that pays its bills from thin
    air, and every recommendation sized off that base -- fill-to-12/22,
    IRMAA-Safe Max, the marginal-cost sweep -- was optimistic by the whole
    draw. engine/scenario.py names the same failure mode from the other side:
    the IRMAA/ACA guarantee "was previously carried SOLELY by
    auto_fill_irmaa_safe/auto_fill_aca sizing the plan against a draw-blind
    base_magi". Defaults to 0.0, so direct callers that do not model a draw are
    numerically unchanged.
    """
    ya = hh.your_age_in(year)
    sa = hh.spouse_age_in(year)
    cpi = hh.cpi_assumption

    opt = hh.option_income(year)

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
        ded = deductions(ya, sa, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single", year=year, cpi=cpi)
    else:
        ded = deductions(ya, sa, hh.std_deduction, hh.senior_extra, year=year, cpi=cpi)

    # YTD ordinary income (wages, NEC, STCG, ordinary dividends, interest,
    # IRA distributions, etc.) — hoisted so it can feed both the SS provisional
    # income computation (F9) and the MAGI base below.
    # nqo_exercise_ytd is already captured in `opt` for the base year (it is part of
    # hh.option_income), so subtract it here to avoid double-counting it in the MAGI /
    # NIIT-MAGI base — mirroring the ytd_ordinary dedup below and scenario.py:343-345
    # (option_income_for_magi = option_income - nqo_exercise_ytd). (Audit 2026-07-22:
    # the ordinary path dedup'd it but this MAGI path did not, overstating base_magi,
    # niit_magi, taxable_ss, and the senior-bonus phaseout by nqo_exercise_ytd.)
    _nqo_ytd = ytd.nqo_exercise_ytd if ytd is not None else 0.0
    # audit-0805 C22/N1: `opt` above is the SCHEDULED (forecast) option income for the
    # full year. When realized YTD NQO exercises exceed that schedule, bounding `opt`
    # itself -- mirroring headroom.py's max(0.0, opt - realized) treatment -- is required
    # BEFORE it feeds tss/base_gross/base_magi/senior_bonus below (and every downstream
    # BaseIncome.opt consumer in all_in_at_conversion). The prior code subtracted
    # _nqo_ytd only from the already-netted ytd_magi/ytd_niit_magi/ytd_ordinary terms
    # (below) but left raw `opt` unbounded everywhere else, silently losing realized
    # income in excess of the schedule (unlike scenario.py, which at least attempted a
    # netting subtraction, albeit an unfloored one -- see C12). For realized <= scheduled
    # this is a no-op (max(opt, nqo_ytd) == opt).
    opt = max(opt, _nqo_ytd)
    ytd_magi = (ytd.magi_ytd - _nqo_ytd) if ytd is not None else 0.0  # base-year realized YTD (niit-5)
    ytd_niit_magi = (ytd.niit_magi_ytd - _nqo_ytd) if ytd is not None else 0.0  # muni-exclusive NIIT MAGI (see YTDSnapshot.niit_magi_ytd)
    # MU8-F1: ordinary-income portion of YTD for the bracket/LTCG-stack base.
    # Mirrors scenario.py combined_gross YTD injection (lines 394-407): wages, NEC, STCG,
    # ordinary dividends, interest, ira_conversions_ytd, spouse_ira_conversions_ytd,
    # ira_distributions_ytd. Excludes LTCG and qualified dividends (preferential-rate, not
    # in ordinary brackets) and muni interest (MAGI-only). nqo_exercise_ytd is already
    # captured in opt for the base year, so we subtract it (matching scenario_compute.py:307-309).
    ytd_ordinary = (
        (ytd.total_ordinary_income - ytd.nqo_exercise_ytd) if ytd is not None else 0.0
    )
    ytd_investment_income = ytd.total_investment_income if ytd is not None else 0.0

    # R1/R3-R5 (audit 2026-07-13): forecast brokerage dividends/gains (suppressed in
    # the base year when ytd is supplied) + RMD/inherited-IRA income, mirroring
    # engine.scenario's compute_brokerage_dividends/realized_gains + compute_rmds.
    forecast_qual_div, forecast_ord_div, forecast_realized_gains = estimate_brokerage_income(
        hh, year, ytd
    )
    rmd_income = estimate_rmd_income(
        hh, year, ytd, your_qcd=your_qcd, spouse_qcd=spouse_qcd
    )
    # audit-0809 #08: `ira_draw` is ordinary income, so it enters the MAGI base,
    # the ordinary bracket base and the muni-exclusive NIIT base identically to
    # a taxable RMD. These three locals mirror BaseIncome.magi_addl /
    # .ordinary_addl / .niit_magi_addl, which the returned object exposes.
    magi_addl = (
        ytd_magi
        + forecast_qual_div
        + forecast_ord_div
        + forecast_realized_gains
        + rmd_income
        + ira_draw
    )
    ordinary_addl = ytd_ordinary + forecast_ord_div + rmd_income + ira_draw
    niit_magi_addl = (
        ytd_niit_magi
        + forecast_qual_div
        + forecast_ord_div
        + forecast_realized_gains
        + rmd_income
        + ira_draw
    )

    # Base taxable SS (without conversion).
    # F9/R1/R3: other_inc must include ytd_magi, forecast div/gains, and RMD/
    # inherited income -- all non-SS AGI items raise provisional income per
    # IRC §86(b)(2), matching scenario_compute.compute_social_security.
    tss = taxable_ss(combined_ss, opt + magi_addl, filing_status=hh.filing_status)

    # Base gross (without conversion) -- ordinary income only.
    # R3/R4: rmd_income and forecast_ord_div are ordinary income; qualified
    # dividends and realized gains are preferential-rate (MAGI-only), excluded here.
    base_gross = opt + tss + ordinary_addl

    # MAGI base (without conversion)
    # R1/R3: forecast qual/ord dividends, realized gains, and RMD/inherited income
    # are all MAGI items, mirroring engine.scenario's compute_magi + realized_gains fold.
    base_magi = opt + tss + magi_addl

    # Senior bonus deduction — phaseout uses NIIT-relevant MAGI (excludes
    # tax-exempt muni interest, which is excluded from gross income under
    # IRC §103 and so was never in AGI/MAGI), mirroring headroom.py FIX #5.
    # R1/R3: also includes the forecast/RMD MAGI additions (muni-exclusive,
    # so ytd_niit_magi is used in place of ytd_magi).
    senior_bonus = senior_bonus_deduction(
        ya,
        sa,
        opt + tss + niit_magi_addl,
        year=year,
        cpi=cpi,
        filing_status=hh.filing_status,
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
        ytd_ordinary=ytd_ordinary,
        forecast_qual_div=forecast_qual_div,
        forecast_ord_div=forecast_ord_div,
        forecast_realized_gains=forecast_realized_gains,
        rmd_income=rmd_income,
        ytd_investment_income=ytd_investment_income,
        waterfall_draw=ira_draw,
    )


def bracket_boundary_conversion(
    hh: Household, base: BaseIncome, bracket_ceiling: float
) -> float:
    """Conversion amount that lifts taxable income to the given bracket ceiling.

    Audit finding 1 (HIGH, 2026-07): the closed-form
    `total_ded + ceiling - base_gross` assumes taxable Social Security is
    conversion-invariant. It is not: once provisional income (IRC §86(b))
    enters the 50%/85% partial-taxability zone, each extra dollar of
    conversion also raises taxable SS, so taxable income grows FASTER than
    1-per-1 with conv -- the naive formula overshoots the true conversion
    needed to reach `bracket_ceiling`, sometimes by 50%+.
    Fix: binary-search using all_in_at_conversion as the oracle -- the same
    fully-recomputed-taxable-SS approach already used by this module's
    irmaa_safe_max, and mirroring engine.scenario's SS "tax torpedo" handling
    (see conversion_ss_delta in compute_scenario, which also fully recomputes
    taxable SS with/without the conversion rather than assuming linearity).
    net_inv_income is irrelevant to taxable_inc (only affects NIIT), so 0.0
    is used for the oracle calls.
    """
    # The naive linear estimate is a valid UPPER bound: taxable_inc grows at
    # >= $1 per $1 of conv (taxable SS is non-decreasing in conv), so any conv
    # above this naive value is guaranteed to overshoot the ceiling.
    upper = max(base.total_ded + bracket_ceiling - base.base_gross, 0.0)
    if upper <= 0:
        return 0.0

    lo, hi = 0.0, upper
    for _ in range(60):  # bisection to well under a cent of precision
        mid = (lo + hi) / 2
        result = all_in_at_conversion(hh, base, mid, 0.0)
        if result.taxable_inc <= bracket_ceiling:
            lo = mid
        else:
            hi = mid
    return lo


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
    magi_addl = base.magi_addl
    ordinary_addl = base.ordinary_addl

    # Recalculate taxable SS with conversion income.
    # F9/R1/R3: other_inc must include ytd_magi, forecast div/gains, and RMD/
    # inherited income (all non-SS AGI items per IRC §86(b)(2)) so that realized/
    # forecast income raises provisional income just as in scenario_compute.py.
    other_inc = base.opt + conv + magi_addl
    tss = taxable_ss(base.combined_ss, other_inc, filing_status=hh.filing_status)

    # MU8-F1/R3/R4: include ordinary_addl (YTD ordinary + forecast ordinary
    # dividends + RMD/inherited income) in the ordinary bracket base, mirroring
    # scenario.py combined_gross. magi_addl (which additionally includes LTCG +
    # qual-divs + muni + forecast qual-div/realized-gains) stays in magi only.
    gross = base.opt + conv + tss + ordinary_addl
    magi = base.opt + conv + tss + magi_addl

    # Recalculate senior bonus deduction at new NIIT-relevant MAGI (excludes
    # tax-exempt muni interest, excluded from gross income under IRC §103),
    # mirroring headroom.py FIX #5. R1/R3: also includes the forecast/RMD
    # MAGI additions.
    senior_bonus = senior_bonus_deduction(
        ya,
        sa,
        base.opt + conv + tss + base.niit_magi_addl,
        year=year,
        cpi=cpi,
        filing_status=hh.filing_status,
    )
    total_ded = base.ded_base + senior_bonus

    taxable_inc = max(gross - total_ded, 0)
    tax = (federal_tax_single if single else federal_tax)(taxable_inc, year=year, cpi=cpi)

    # Base tax (no conversion).
    # F9/R1/R3: include magi_addl in provisional income base, consistent with the
    # with-conversion path.
    base_tss = taxable_ss(base.combined_ss, base.opt + magi_addl, filing_status=hh.filing_status)
    # MU8-F1/R3/R4: include ordinary_addl in the no-conversion ordinary base.
    base_gross = base.opt + base_tss + ordinary_addl
    base_senior = senior_bonus_deduction(
        ya,
        sa,
        base.opt + base_tss + base.niit_magi_addl,
        year=year,
        cpi=cpi,
        filing_status=hh.filing_status,
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
    _your_on_aca = aca_applies(ya, hh.your_aca_enrolled)
    _spouse_on_aca = aca_applies(sa, hh.spouse_aca_enrolled)
    num_on_aca = (1 if _your_on_aca else 0) + (1 if _spouse_on_aca else 0)
    resolved_couple_benchmark = resolve_couple_benchmark_annual(
        hh.aca_benchmark_premium_annual,
        your_age=ya,
        spouse_age=sa,
        filing_status=hh.filing_status,
        year=year,
        cpi=cpi,
    )
    effective_benchmark = effective_benchmark_premium(
        resolved_couple_benchmark,
        your_age=ya,
        your_on_aca=_your_on_aca,
        spouse_age=sa,
        spouse_on_aca=_spouse_on_aca,
        filing_status=hh.filing_status,
    )
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

    # NIIT — use NIIT-relevant MAGI, which excludes tax-exempt interest (muni
    # interest is excluded from gross income under IRC §103, so it was never
    # in AGI/MAGI to begin with). R1/R3: niit_magi mirrors `magi` but uses
    # base.niit_magi_addl (muni-exclusive) in place of base.magi_addl.
    # audit-0805 C10: net_inv_income (the manual "Additional NII $/yr" estimate)
    # is real declared income -- add it here too, not just to total_net_inv_income
    # below, so the excess-over-threshold niit() charges against isn't understated.
    # Applied identically to both the with- and without-conversion MAGI so the
    # manual figure is measured consistently on both sides of niit_delta.
    niit_magi = base.opt + conv + tss + net_inv_income + base.niit_magi_addl
    niit_base_magi = base.opt + base_tss + net_inv_income + base.niit_magi_addl
    # R2: net investment income = realized gains + qual/ord dividends + YTD investment
    # income, mirroring scenario.py's `net_investment_income = realized_gains +
    # qual_div_this_year + ord_div_this_year` assembly in compute_scenario (grep
    # for that assignment rather than a line number -- it has moved before).
    # Added to the caller-supplied net_inv_income, a manual estimate/override for NII
    # not otherwise modeled by this module (e.g. non-brokerage taxable accounts).
    total_net_inv_income = net_inv_income + base.net_investment_income_addl
    niit_with = niit(niit_magi, total_net_inv_income, filing_status=hh.filing_status)
    niit_without = niit(niit_base_magi, total_net_inv_income, filing_status=hh.filing_status)
    niit_delta = niit_with - niit_without

    # LTCG bracket-stacking (C1): a conversion lifts ordinary taxable income, raising
    # the start of the preferential-rate stack and pushing realized LTCG + qualified
    # dividends into higher 0%/15%/20% bands. Mirrors the "=== LTCG tax ==="
    # stack-walk block in engine.scenario.compute_scenario (grep for that
    # header rather than a line number -- it has moved before, same pattern
    # as _ltcg_stack_tax's docstring above). LTCG thresholds index to the
    # INCOME year (same-year tax — NOT the IRMAA +2 payment year).
    _base_ltcg_thr = LTCG_THRESHOLDS_SINGLE if single else LTCG_THRESHOLDS_MFJ
    _ltcg_thr = (
        _index_value(_base_ltcg_thr[0], year, cpi, round50=True),
        _index_value(_base_ltcg_thr[1], year, cpi, round50=True),
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
        niit_magi=niit_magi,
        taxable_inc=taxable_inc,
        room_12=room_to_bracket(gross, total_ded, _index_value(BRACKETS_SINGLE[1][0], year, cpi, round50=True))
        if single
        else room_to_12(gross, total_ded, year=year, cpi=cpi),
        room_22=room_to_bracket(gross, total_ded, _index_value(BRACKETS_SINGLE[2][0], year, cpi, round50=True))
        if single
        else room_to_22(gross, total_ded, year=year, cpi=cpi),
    )


def irmaa_safe_max(
    hh: Household,
    base: BaseIncome,
    irmaa_tier1_threshold: float,
    net_inv_income: float = 0.0,
    ltcg_eligible: float = 0.0,
) -> float:
    """Binary-search for the largest STEP-aligned conversion where magi <= irmaa_tier1_threshold.

    The naive subtraction `threshold - base_magi` overstates the safe conversion amount
    when SS provisional income is in the partial-taxability zone ($32K-$44K MFJ /
    $25K-$34K Single), where each $1 converted raises MAGI by up to $1.85 due to the
    50% or 85% additional SS taxability multiplier.

    Uses all_in_at_conversion as the oracle for the binary search so that the same
    SS-taxability logic used in the sweep is also used to determine IRMAA safety.

    Returns 0.0 if base_magi already meets or exceeds the threshold.
    """
    if base.base_magi >= irmaa_tier1_threshold:
        return 0.0

    # The naive subtraction is an upper bound: magi(conv) >= base_magi + conv
    # so any conversion above (threshold - base_magi) will exceed the threshold.
    # The true safe max is <= this bound (it can be strictly less in the partial zone).
    upper = irmaa_tier1_threshold - base.base_magi

    lo: float = 0.0
    hi: float = upper
    best: float = 0.0

    while lo <= hi:
        # Snap to nearest STEP boundary (binary search in STEP-aligned space)
        mid = round((lo + hi) / 2 / STEP) * STEP
        if mid < lo or mid > hi:
            break
        r = all_in_at_conversion(hh, base, mid, net_inv_income, ltcg_eligible=ltcg_eligible)
        if r.magi <= irmaa_tier1_threshold:
            best = mid
            lo = mid + STEP
        else:
            hi = mid - STEP

    return best


def find_sweet_spots(results: list[ConversionResult]) -> list[SweetSpotJump]:
    """Identify zones where marginal cost jumps significantly."""
    spots: list[SweetSpotJump] = []
    if len(results) < 2:
        return spots

    prev_marginal = 0.0
    for i in range(1, len(results)):
        curr = results[i]
        prev = results[i - 1]
        marginal = (curr.all_in - prev.all_in) / STEP * 100  # percent (%) of each $1K converted
        if curr.conv == 0:
            prev_marginal = marginal
            continue
        if i >= 1 and marginal - prev_marginal > 2.0:  # >2% jump per $1K
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


def zero_conversion_ira_draws(
    hh: Household,
    *,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> dict[int, float]:
    """Per-year traditional-IRA draw that funds living expenses at ZERO conversion.

    audit-0809 #08. Sourced from engine.scenario rather than re-solved here on
    purpose. Reproducing the cash-need assembly locally (living_expenses +
    irmaa_cost + aca_premium_cost - available_income, with the YTD spendable-cash
    restoration, the per-spouse QCD ordering and the IRMAA payment-year offset)
    would be a second implementation of logic that already exists, and
    engine/withdrawal_waterfall.py's own allocate_ira_draw docstring records what
    happened last time this codebase kept two copies of one rule: "a second
    implementation is free to drift from this one, and did."

    Only the two traditional-IRA legs are returned. The Roth leg is excluded
    because a qualified Roth distribution is not includible in gross income and
    so never reaches MAGI. The brokerage leg is excluded because it is largely a
    return of capital whose realized-gain component this module already models on
    its own forecast_realized_gains path -- folding the leg in here would
    double-count it.

    ZERO conversion, not the swept amount: see engine/scenario.py's "AVOIDING
    CIRCULARITY" note. A draw solved at the conversion being sized is itself a
    function of that conversion, so it cannot bound it. A nonzero conversion does
    raise this year's tax and therefore the true draw, so a second-order
    understatement of the draw survives here -- the same direction, and for the
    same reason, as the engine's own first-pass conversion_cap.
    """
    conv_window = max(hh.your_conv_window, hh.spouse_conv_window)
    end_age = hh.your_age + max(conv_window - 1, 0)
    result = run_scenario(
        hh,
        ConversionPlan(),
        "sweet-spot waterfall baseline",
        end_age=end_age,
        ytd=ytd,
        net_inv_income=net_inv_income,
    )
    return {
        yr.year: yr.forced_your_ira_draw + yr.forced_spouse_ira_draw
        for yr in result.years
    }


def compute_multi_year_summary(
    hh: Household,
    *,
    net_inv_income: float = 0.0,
    ytd: YTDSnapshot | None = None,
    include_ltcg_stacking: bool = False,
    ira_draws: dict[int, float] | None = None,
) -> list[YearSummary]:
    """Compute sweet-spot summary rows for all conversion years.

    audit-0809 #08: `ira_draws` maps year -> the living-expense waterfall draw
    folded into that year's base (see zero_conversion_ira_draws). Derived here
    when not supplied, which costs one zero-conversion projection for the whole
    table rather than one per year; pass the map explicitly when the caller
    already has it -- views/sweet_spot.py needs the same draws for its selected
    year -- to avoid paying for a second projection. Pass an empty dict for the
    pre-fix, draw-blind behaviour.
    """
    conv_window = max(hh.your_conv_window, hh.spouse_conv_window)
    conv_years = list(range(hh.base_year, hh.base_year + conv_window))
    if ira_draws is None:
        ira_draws = zero_conversion_ira_draws(
            hh, ytd=ytd, net_inv_income=net_inv_income
        )

    _base_irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ

    rows: list[YearSummary] = []
    for yr in conv_years:
        cpi = hh.cpi_assumption
        # IRMAA 2-year lookback: threshold that applies is for the PAYMENT year (income_year+2).
        irmaa_tiers = _index_irmaa_tiers(_base_irmaa_tiers, yr + 2, cpi)

        _yr_ytd = ytd if yr == hh.base_year else None
        b = base_income_for_year(hh, yr, ytd=_yr_ytd, ira_draw=ira_draws.get(yr, 0.0))
        _le = estimate_ltcg_eligible(hh, yr, ytd=_yr_ytd) if include_ltcg_stacking else 0.0

        # C23 (audit-0805): route fill_12/fill_22 through bracket_boundary_conversion
        # -- the module's own SS-torpedo-aware oracle (finding 1 / TestBracketBoundarySsTaxabilityNonlinearity)
        # -- instead of the closed-form room_12/room_22 (documented as GROSS-INCOME
        # room, valid only when taxable SS is conversion-invariant) fed back as a
        # CONVERSION amount. Feeding gross-income room in as a conversion double-counts
        # the SS-torpedo effect the closed form already ignored once, overshooting the
        # ceiling. bracket_boundary_conversion bisects against the real (non-linear)
        # taxable_inc, landing exactly on the ceiling.
        _year_brackets = BRACKETS_SINGLE if hh.filing_status == "Single" else BRACKETS_MFJ
        _ceiling_12 = _index_value(_year_brackets[1][0], yr, cpi, round50=True)
        _ceiling_22 = _index_value(_year_brackets[2][0], yr, cpi, round50=True)
        r12 = bracket_boundary_conversion(hh, b, _ceiling_12)
        r22 = bracket_boundary_conversion(hh, b, _ceiling_22)

        tier1_threshold = irmaa_tiers[0][0]
        # Binary search for the largest STEP-aligned conversion where magi <= tier1_threshold.
        # The naive subtraction (tier1_threshold - b.base_magi) overstates the safe amount
        # when SS provisional income is in the partial-taxability zone ($32K-$44K MFJ /
        # $25K-$34K Single), where each $1 converted raises MAGI by up to $1.85.
        safe_conv = irmaa_safe_max(hh, b, tier1_threshold, net_inv_income, _le)
        irmaa_safe: float | None = safe_conv if safe_conv > 0 else None

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
