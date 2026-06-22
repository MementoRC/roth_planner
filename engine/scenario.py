"""Scenario engine — full multi-year Roth conversion projection.

Produces a year-by-year DataFrame with all income sources, taxes, costs,
IRA balances, brokerage tracking, and net benefit analysis.
"""

from __future__ import annotations

from engine.ira import inherited_ira_drain
from engine.irmaa import irmaa_for_year, irmaa_next_threshold
from engine.niit import niit
from engine.scenario_autofill import (
    _auto_fill_core as _auto_fill_core,
)
from engine.scenario_autofill import (
    add_bracket_fill_withdrawals as add_bracket_fill_withdrawals,
)
from engine.scenario_autofill import (
    auto_fill_12 as auto_fill_12,
)
from engine.scenario_autofill import (
    auto_fill_22 as auto_fill_22,
)
from engine.scenario_autofill import (
    auto_fill_irmaa_safe as auto_fill_irmaa_safe,
)
from engine.scenario_compute import (
    compute_aca,
    compute_bracket_room,
    compute_brokerage_dividends,
    compute_conversions,
    compute_federal_tax,
    compute_magi,
    compute_phase,
    compute_rmds,
    compute_social_security,
)
from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult
from engine.tax import (
    LTCG_THRESHOLDS_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    senior_bonus_deduction,
)
from engine.tax_indexing import index_tuple as _index_tuple
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot


def run_scenario(
    hh: Household,
    plan: ConversionPlan,
    name: str = "Scenario",
    end_age: int = 95,
    early_exercise: bool = True,
    ytd: YTDSnapshot | None = None,
) -> ScenarioResult:
    """
    Run a full projection from base_year through end_age.

    Phase 1 (your_age <= 74): Conversion years — you and/or spouse convert
    Phase 2 (your_age >= 75): RMD years — forced distributions, spouse may still convert
    """
    results = []
    cpi = hh.cpi_assumption
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira
    your_roth = hh.your_roth
    spouse_roth = hh.spouse_roth
    # TODO(math-audit-2026-06-12 P3): Brokerage starting balance not initialized from YTD
    # snapshot. Projection always starts at 0.0, ignoring any brokerage balance already
    # accumulated by the snapshot date. Fix requires adding a brokerage_balance field to
    # YTDSnapshot (model extension deferred — see ai_docs/MATH_AUDIT_2026-06-12.md P3).
    brokerage = 0.0
    cum_conv_tax = 0.0
    cum_irmaa = 0.0
    cum_aca = 0.0
    cum_niit = 0.0
    cum_rmd_tax = 0.0
    cum_brok_tax = 0.0
    # Accumulates projected MAGI per calendar year for IRMAA 2-year lookback
    magi_history: dict[int, float] = {}

    # Survivor scenario pre-check
    surv: SurvivorScenario | None = hh.survivor
    _rollover_done: bool = False

    # Mutable copies of inherited IRA balances (one per InheritedIRA), keyed by index
    inherited_balances: list[float] = [iira.balance for iira in hh.inherited_iras]

    total_years = end_age - hh.your_age + 1

    for yr_idx in range(total_years):
        year = hh.base_year + yr_idx
        ya = hh.your_age + yr_idx
        sa = hh.spouse_age + yr_idx

        # === Survivor scenario: determine filing status and effective ages ===
        survivor_active = surv is not None and year >= surv.death_year + 1
        current_filing_status = "Single" if survivor_active else hh.filing_status

        # IRA rollover: at the first year survivor_active, roll deceased into survivor
        if survivor_active and not _rollover_done:
            assert surv is not None  # narrowing: survivor_active implies surv is not None
            if surv.who_dies == "you":
                spouse_ira += your_ira
                your_ira = 0.0
            else:
                your_ira += spouse_ira
                spouse_ira = 0.0
            _rollover_done = True

        yr = YearResult(year=year, your_age=ya, spouse_age=sa, phase="")

        # === Phase classification ===
        yr.phase = compute_phase(ya, sa, year, hh, early_exercise)

        # === IRA balances ===
        yr.your_ira_begin = your_ira
        yr.spouse_ira_begin = spouse_ira
        yr.your_roth_begin = your_roth
        yr.spouse_roth_begin = spouse_roth

        # === Option income ===
        yr.option_income = hh.option_income(year, early_exercise)

        # === Brokerage dividend forecast ===
        # Skip in base year if YTD actuals are provided (they already carry real dividends).
        # yield_rate defaults to 0.0 on GrowthProfile, so this is zero-cost when not configured.
        qual_div_this_year, ord_div_this_year = compute_brokerage_dividends(
            year, hh.base_year, brokerage, hh.brokerage_growth, ytd
        )
        yr.brokerage_qual_div = qual_div_this_year
        yr.brokerage_ord_div = ord_div_this_year

        # === YTD injection (base year only) ===
        # Resolve to a concrete YTDSnapshot for the base year, or None.
        # This avoids repeated `ytd is not None` narrowing for mypy.
        ytd_year: YTDSnapshot | None = ytd if year == hh.base_year else None
        if ytd_year is not None:
            yr.ytd_wages = ytd_year.wages_ytd
            yr.ytd_ltcg = ytd_year.ltcg_ytd
            yr.ytd_stcg = ytd_year.stcg_ytd
            yr.ytd_qualified_dividends = ytd_year.qualified_dividends_ytd
            yr.ytd_ordinary_dividends = ytd_year.ordinary_dividends_ytd
            yr.ytd_dividends = ytd_year.dividends_ytd  # aggregate; backward compat
            yr.ytd_interest = ytd_year.interest_ytd
            yr.ytd_conversions_done = ytd_year.ira_conversions_ytd

        # === Conversions ===
        # NOT MODELED: IRA non-deductible basis (Form 8606)
        # Per IRC §408(d)(2), conversions from a Traditional IRA with non-deductible
        # basis are pro-rated: only (pretax_balance / total_balance) of the converted
        # amount is taxable. This tool assumes basis = $0 (i.e., all Trad IRA dollars
        # are pretax). If you have non-deductible contributions tracked on Form 8606,
        # the actual taxable income from a conversion will be lower than what this
        # tool reports.
        yr.your_conversion, yr.spouse_conversion = compute_conversions(
            year,
            ya,
            sa,
            plan.your_conversions.get(year, 0.0),
            plan.spouse_conversions.get(year, 0.0),
            ytd_year,
            hh.your_rmd_start_age,
            hh.spouse_rmd_start_age,
        )

        # === RMD ===
        # When survivor_active, deceased's IRA was rolled to survivor at death_year+1.
        # The deceased's IRA variable is now 0, so calc_rmd returns 0 naturally.
        # QCD: after death the deceased's QCD limit is unavailable; survivor keeps
        # their own limit (qcd_limit is per-person, so no change needed for survivor).
        (
            yr.your_rmd,
            yr.qcd,
            yr.taxable_rmd,
            yr.spouse_rmd,
            yr.spouse_qcd,
            yr.spouse_taxable_rmd,
        ) = compute_rmds(
            your_ira,
            spouse_ira,
            ya,
            sa,
            hh.your_rmd_start_age,
            hh.spouse_rmd_start_age,
            plan.qcds.get(year, 0.0),
            plan.spouse_qcds.get(year, 0.0),
            hh.qcd_limit,
        )

        # === Extra voluntary withdrawals (bracket fill post-RMD) ===
        yr.extra_withdrawal = plan.extra_withdrawals.get(year, 0.0)
        yr.spouse_extra_withdrawal = plan.spouse_extra_withdrawals.get(year, 0.0)

        # === Inherited IRA drains (SECURE Act 10-year rule) ===
        your_inherited_distribution = 0.0
        spouse_inherited_distribution = 0.0
        for idx, iira in enumerate(hh.inherited_iras):
            if year < iira.inherited_year:
                continue  # not yet inherited
            years_in = year - iira.inherited_year
            years_remaining = 10 - years_in
            if years_remaining <= 0:
                continue  # fully drained
            drain = inherited_ira_drain(inherited_balances[idx], years_remaining)
            if iira.owner == "you":
                your_inherited_distribution += drain
            else:
                spouse_inherited_distribution += drain
            # Apply drain + growth to balance for next year
            inherited_balances[idx] = max(inherited_balances[idx] - drain, 0.0) * (
                1 + iira.growth_rate
            )
        yr.your_inherited_distribution = your_inherited_distribution
        yr.spouse_inherited_distribution = spouse_inherited_distribution

        # === Brokerage appreciation rate + realized gains (hoisted for SS provisional income) ===
        # F4: realized_gains is an AGI item required in SS provisional income (IRC §86(b)(2)).
        # It depends only on the begin-of-year brokerage balance and appreciation rate — both
        # known here — so hoisting is safe and does not change any downstream value.
        # Also needed for MAGI ordering (OBBBA senior-bonus phase-out and IRMAA fallback).
        brok_rate = hh.brokerage_rate(year)
        if hh.brokerage_growth is not None:
            brok_appreciation_rate = hh.brokerage_growth.appreciation_for(year)
        else:
            brok_appreciation_rate = brok_rate
        realized_gains = brokerage * brok_appreciation_rate * hh.brok_turnover

        # === Social Security + taxable SS ===
        # SS survivor step-up: survivor keeps max(your_ss, spouse_ss); implemented in compute_social_security.
        # D-1: MAGI uses taxable SS, not full SS (computed here, before MAGI block).
        # F3/F4: qual_div_this_year and realized_gains are now passed in for provisional income.
        yr.your_ss, yr.spouse_ss, yr.combined_ss, yr.taxable_ss_amt = compute_social_security(
            hh,
            ya,
            sa,
            survivor_active,
            surv.who_dies if surv is not None else None,
            current_filing_status,
            yr.your_conversion,
            yr.spouse_conversion,
            yr.taxable_rmd,
            yr.spouse_taxable_rmd,
            yr.extra_withdrawal,
            yr.spouse_extra_withdrawal,
            yr.option_income,
            yr.your_inherited_distribution,
            yr.spouse_inherited_distribution,
            ord_div_this_year,
            ytd_year,
            qual_div_this_year,
            realized_gains,
        )

        # === MAGI (for IRMAA/ACA — uses full amounts, not taxable) ===
        # D-1: use taxable_ss_amt (up to 85% of SS) not full combined_ss — per §1395r(i)(4)
        # C-7: subtract nqo_exercise_ytd from option_income contribution when ytd is present.
        # QCD IS excluded from MAGI, so use taxable_rmd / spouse_taxable_rmd.
        # NOTE: realized_gains excluded here; folded into yr.magi in the MAGI ordering block below.
        option_income_for_magi = yr.option_income - (
            ytd_year.nqo_exercise_ytd if ytd_year is not None else 0.0
        )
        yr.magi = compute_magi(
            option_income_for_magi,
            yr.your_conversion,
            yr.spouse_conversion,
            yr.taxable_rmd,
            yr.spouse_taxable_rmd,
            yr.extra_withdrawal,
            yr.spouse_extra_withdrawal,
            yr.taxable_ss_amt,
            yr.your_inherited_distribution,
            yr.spouse_inherited_distribution,
            qual_div_this_year,
            ord_div_this_year,
            ytd_year,
        )

        # === Brokerage realized gains added to MAGI ===
        # brok_rate, brok_appreciation_rate, and realized_gains were computed above (hoisted
        # for SS provisional income). Add realized_gains to MAGI here for the ordering block.
        yr.magi += realized_gains
        magi_history[year] = yr.magi

        # === Combined gross (for tax) ===
        # Includes ordinary income only — LTCG taxed separately at preferential rate
        yr.combined_gross = (
            yr.option_income
            + yr.your_conversion
            + yr.spouse_conversion
            + yr.taxable_rmd
            + yr.spouse_taxable_rmd
            + yr.extra_withdrawal
            + yr.spouse_extra_withdrawal
            + yr.taxable_ss_amt
            + yr.your_inherited_distribution
            + yr.spouse_inherited_distribution
        )
        # YTD: add all ordinary income components to gross.
        # LTCG and qualified dividends are excluded (taxed at preferential rate).
        # nec_income_ytd and ira_distributions_ytd are ordinary income; include them.
        # ira_conversions_ytd: yr.your_conversion was already reduced by this amount
        # (line 281), so adding it back here makes the full planned conversion stack
        # into combined_gross correctly.
        if ytd_year is not None:
            yr.combined_gross += (
                ytd_year.wages_ytd
                + ytd_year.nec_income_ytd
                + ytd_year.stcg_ytd
                + ytd_year.ordinary_dividends_ytd
                + ytd_year.interest_ytd
                + ytd_year.ira_conversions_ytd
                + ytd_year.ira_distributions_ytd
            )
        # Forecast ordinary dividends are ordinary income; qualified dividends are MAGI-only (like LTCG)
        yr.combined_gross += ord_div_this_year

        # === Deductions ===
        if survivor_active:
            assert surv is not None  # narrowing: survivor_active implies surv is not None
            # Use single-filer std deduction + senior extra; zero deceased age so
            # only the survivor counts toward the senior-extra and OBBBA bonus.
            ya_eff = 0 if surv.who_dies == "you" else ya
            sa_eff = 0 if surv.who_dies == "spouse" else sa
            yr.total_deductions = deductions(
                ya_eff, sa_eff, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, year=year, cpi=cpi
            )
            yr.total_deductions += senior_bonus_deduction(
                ya_eff, sa_eff, yr.magi, year=year, cpi=cpi, filing_status="Single"
            )
        else:
            # Non-survivor path. A single-from-the-start household (filing_status
            # "Single", spouse inputs zeroed by the Setup gate) uses the single
            # standard deduction + senior extra; an MFJ couple keeps hh values.
            _std_ded: float
            _senior_extra: float
            if current_filing_status == "Single":
                _std_ded, _senior_extra = STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE
            else:
                _std_ded, _senior_extra = hh.std_deduction, hh.senior_extra
            yr.total_deductions = deductions(ya, sa, _std_ded, _senior_extra, year=year, cpi=cpi)
            yr.total_deductions += senior_bonus_deduction(
                ya, sa, yr.magi, year=year, cpi=cpi, filing_status=current_filing_status
            )

        # Baseline (no-conversion) deductions for the incremental conversion tax:
        # recompute the OBBBA bonus at the no-conversion MAGI so a conversion cannot
        # phase out its own baseline deduction (scenario-math-3).
        base_magi = yr.magi - yr.your_conversion - yr.spouse_conversion
        if survivor_active:
            base_total_deductions = deductions(
                ya_eff, sa_eff, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, year=year, cpi=cpi
            ) + senior_bonus_deduction(
                ya_eff, sa_eff, base_magi, year=year, cpi=cpi, filing_status="Single"
            )
        else:
            _b_std_ded: float
            _b_senior_extra: float
            if current_filing_status == "Single":
                _b_std_ded, _b_senior_extra = STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE
            else:
                _b_std_ded, _b_senior_extra = hh.std_deduction, hh.senior_extra
            base_total_deductions = deductions(
                ya, sa, _b_std_ded, _b_senior_extra, year=year, cpi=cpi
            ) + senior_bonus_deduction(
                ya, sa, base_magi, year=year, cpi=cpi, filing_status=current_filing_status
            )

        # === Taxable income ===
        yr.taxable_income = max(yr.combined_gross - yr.total_deductions, 0)

        # === Federal tax + conversion tax (incremental) ===
        yr.federal_tax_amt, yr.marginal_bracket, yr.conversion_tax = compute_federal_tax(
            yr.taxable_income,
            yr.combined_gross,
            yr.your_conversion,
            yr.spouse_conversion,
            base_total_deductions,
            current_filing_status,
            year,
            cpi,
        )

        # === IRMAA (2-year lookback) ===
        # IRMAA paid in year Y is based on filed MAGI of year Y-2.
        # Resolution priority: prior_year_magi anchor > magi_history > same-year fallback.
        income_year = year - 2
        if income_year in hh.prior_year_magi:
            # User has provided actual filed MAGI for the lookback year
            magi_for_irmaa = hh.prior_year_magi[income_year]
        elif income_year in magi_history:
            # We've already projected the lookback year in this loop
            magi_for_irmaa = magi_history[income_year]
        else:
            # Fallback: lookback year predates the projection window and no anchor provided.
            # Use this year's projected MAGI as a same-year approximation
            # (only reached for yr_idx < 2 when prior_year_magi is empty).
            magi_for_irmaa = yr.magi
        # irmaa_for_year() adds +2 internally for the 2-year MAGI lookback;
        # pass income-year ages (ya - 2, sa - 2) so Medicare-year ages come out correctly.
        # H1 fix: in survivor years, zero the deceased person's income-year age so
        # irmaa_for_year() counts only the surviving beneficiary (1 person, not 2).
        # The MAGI lookback (income side) is preserved exactly — only the person-count changes.
        if survivor_active and surv is not None:
            ya_irmaa = 0 if surv.who_dies == "you" else ya
            sa_irmaa = 0 if surv.who_dies == "spouse" else sa
        else:
            ya_irmaa = ya
            sa_irmaa = sa
        irmaa_cost, _ = irmaa_for_year(
            magi_for_irmaa,
            ya_irmaa - 2,
            sa_irmaa - 2,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
            filing_status=current_filing_status,
            year=year,  # CMS indexes IRMAA thresholds to the payment year (income_year + 2)
            cpi=cpi,
        )
        yr.irmaa_cost = irmaa_cost
        yr.irmaa_room = irmaa_next_threshold(
            yr.magi, filing_status=current_filing_status, year=year, cpi=cpi
        )

        # === ACA subsidy loss + clawback ===
        # ACA applies if anyone in household is enrolled and pre-Medicare.
        # Audit B-4: scale the couple benchmark when only one spouse is on ACA.
        # R2-D: a deceased spouse must not count as an ACA enrollee. In survivor
        # years drop the decedent's enrollment so the benchmark scales to the
        # survivor alone (single-from-the-start households already zero the spouse
        # ACA flag via the Setup gate).
        _your_aca_enrolled = hh.your_aca_enrolled and not (
            survivor_active and surv is not None and surv.who_dies == "you"
        )
        _spouse_aca_enrolled = hh.spouse_aca_enrolled and not (
            survivor_active and surv is not None and surv.who_dies == "spouse"
        )
        yr.aca_magi, yr.aca_loss, yr.aca_clawback = compute_aca(
            yr.magi,
            yr.combined_ss,
            yr.taxable_ss_amt,
            yr.your_conversion,
            yr.spouse_conversion,
            ya,
            sa,
            _your_aca_enrolled,
            _spouse_aca_enrolled,
            hh.aca_benchmark_premium_annual,
            hh.aca_enhanced_subsidies_active,
            hh.advance_aptc_annual,
            current_filing_status,
            year,
            cpi,
        )
        # Positive clawback = additional tax; negative = additional refund.
        # DO NOT subtract from aca_loss — they model different things.
        if yr.aca_clawback != 0.0:
            yr.federal_tax_amt += yr.aca_clawback

        # === LTCG tax (computed separately at preferential rate) ===
        # Stack-walk 0%/15%/20% brackets: ordinary taxable income sets the
        # starting point; YTD LTCG walks up through the bands.
        # F5: guard widened to include qualified_dividends_ytd (IRC §1(h)(11) — both taxed
        # at preferential rates). If only qual-divs exist and ltcg_ytd==0 the old guard
        # skipped the entire block, applying $0 LTCG-rate tax to qual dividends.
        _ytd_ltcg_total = (ytd_year.ltcg_ytd + ytd_year.qualified_dividends_ytd) if ytd_year is not None else 0.0
        if ytd_year is not None and _ytd_ltcg_total > 0:
            # Thresholds depend on filing status: Single for survivor years, MFJ otherwise.
            _base_ytd_ltcg_thresholds = (
                LTCG_THRESHOLDS_SINGLE if current_filing_status == "Single" else LTCG_THRESHOLDS_MFJ
            )
            _ytd_ltcg_thresholds = _index_tuple(_base_ytd_ltcg_thresholds, year, cpi)
            _ytd_ltcg_start = max(0.0, yr.taxable_income)
            _ytd_ltcg_end = _ytd_ltcg_start + max(0.0, _ytd_ltcg_total)
            _ytd_ltcg_at_15 = max(
                0.0,
                min(_ytd_ltcg_end, _ytd_ltcg_thresholds[1])
                - max(_ytd_ltcg_start, _ytd_ltcg_thresholds[0]),
            )
            _ytd_ltcg_at_20 = max(
                0.0, _ytd_ltcg_end - max(_ytd_ltcg_start, _ytd_ltcg_thresholds[1])
            )
            yr.ytd_ltcg_tax = _ytd_ltcg_at_15 * 0.15 + _ytd_ltcg_at_20 * 0.20
            # grid-05: YTD realized LTCG tax is a real federal tax for the base year
            # but was previously orphaned (computed, never counted in any total). Fold
            # it into federal_tax_amt so lifetime tax / all-in cost reflect it.
            yr.federal_tax_amt += yr.ytd_ltcg_tax

        # === NIIT (3.8% surtax on investment income when MAGI > $250K) ===
        # Net investment income = realized appreciation gains + all dividends (qual + ord)
        # Computed on beginning brokerage balance (carry-forward from prior year)
        net_investment_income = (
            brokerage * brok_appreciation_rate * hh.brok_turnover
            + qual_div_this_year
            + ord_div_this_year
        )
        # YTD: add realized gains, dividends, interest to investment income
        if ytd_year is not None:
            net_investment_income += ytd_year.total_investment_income
        # IRC §1411: realized capital gains belong in NIIT MAGI with no exclusion.
        # yr.magi already includes realized_gains (folded in the MAGI ordering block),
        # so niit_magi only needs to strip muni interest per IRC §1411(d)(3).
        yr.niit_magi = yr.magi - (ytd_year.tax_exempt_interest_ytd if ytd_year else 0.0)
        yr.niit_cost = niit(
            yr.niit_magi, net_investment_income, filing_status=current_filing_status
        )

        # === All-in cost of conversions ===
        yr.all_in_cost = yr.conversion_tax + yr.irmaa_cost + yr.aca_loss + yr.niit_cost

        # === Bracket room ===
        yr.room_12, yr.room_22 = compute_bracket_room(
            yr.combined_gross, yr.total_deductions, current_filing_status, year, cpi
        )

        # === Living expenses & brokerage ===
        years_from_base = yr_idx
        yr.living_expenses = hh.living_expenses * (1 + hh.expense_inflation) ** years_from_base

        after_tax_rmd = (yr.your_rmd - yr.qcd) + yr.spouse_taxable_rmd  # taxable RMDs (net of QCDs)
        available_income = (
            after_tax_rmd
            + yr.extra_withdrawal
            + yr.spouse_extra_withdrawal
            + yr.combined_ss
            - yr.federal_tax_amt
        )
        yr.income_needed = max(yr.living_expenses - available_income, 0)
        yr.excess_rmd = max(available_income - yr.living_expenses, 0)

        # Brokerage: accumulates excess, grows (appreciation), dividends reinvest, pays cap gains
        yr.brokerage_balance = brokerage
        yr.brokerage_growth = brokerage * brok_appreciation_rate
        # realized_gains, the yr.magi fold, and magi_history[year] were computed earlier
        # (MAGI ordering block) so the phase-out and IRMAA fallback see the full MAGI.
        # Stack-walk LTCG brackets: ordinary taxable income sets the starting
        # point; realized gains + qualified dividends (IRC §1(h)(11)) walk up
        # through 0% / 15% / 20% bands.
        # yr.taxable_income is already ordinary-only; do NOT subtract realized_gains.
        # Thresholds depend on filing status: Single for survivor years, MFJ otherwise.
        _base_ltcg_thresholds = (
            LTCG_THRESHOLDS_SINGLE if current_filing_status == "Single" else LTCG_THRESHOLDS_MFJ
        )
        ltcg_thresholds = _index_tuple(_base_ltcg_thresholds, year, cpi)
        ltcg_eligible = realized_gains + qual_div_this_year
        _ltcg_start = max(0.0, yr.taxable_income)
        _ltcg_end = _ltcg_start + max(0.0, ltcg_eligible)
        _ltcg_at_15 = max(
            0.0,
            min(_ltcg_end, ltcg_thresholds[1]) - max(_ltcg_start, ltcg_thresholds[0]),
        )
        _ltcg_at_20 = max(0.0, _ltcg_end - max(_ltcg_start, ltcg_thresholds[1]))
        yr.brokerage_gain_tax = _ltcg_at_15 * 0.15 + _ltcg_at_20 * 0.20
        total_div = qual_div_this_year + ord_div_this_year

        brokerage = (
            brokerage
            + yr.brokerage_growth
            - yr.brokerage_gain_tax
            + total_div  # dividends reinvested (taxable event already captured in income stacks)
            + yr.excess_rmd
        )

        # === IRA end of year ===
        your_withdrawal = yr.your_conversion + yr.your_rmd + yr.extra_withdrawal
        spouse_withdrawal = yr.spouse_conversion + yr.spouse_rmd + yr.spouse_extra_withdrawal

        yr.your_ira_end = max(your_ira - your_withdrawal, 0) * (1 + hh.your_ira_rate(year))
        yr.spouse_ira_end = max(spouse_ira - spouse_withdrawal, 0) * (1 + hh.spouse_ira_rate(year))

        # === Roth end of year ===
        # Credit conversions (only) to Roth; grow tax-free.
        # rmd and extra_withdrawal are NOT Roth-eligible (they go to taxable accounts).
        yr.your_roth_end = (your_roth + yr.your_conversion) * (1 + hh.your_roth_rate(year))
        yr.spouse_roth_end = (spouse_roth + yr.spouse_conversion) * (1 + hh.spouse_roth_rate(year))

        # Inherited IRA end-of-year balances (sum by owner, after drain+growth applied above)
        yr.your_inherited_balance_end = sum(
            inherited_balances[i] for i, iira in enumerate(hh.inherited_iras) if iira.owner == "you"
        )
        yr.spouse_inherited_balance_end = sum(
            inherited_balances[i]
            for i, iira in enumerate(hh.inherited_iras)
            if iira.owner == "spouse"
        )

        # Carry forward
        your_ira = yr.your_ira_end
        spouse_ira = yr.spouse_ira_end
        your_roth = yr.your_roth_end
        spouse_roth = yr.spouse_roth_end

        # Accumulate totals
        cum_conv_tax += yr.conversion_tax
        cum_irmaa += yr.irmaa_cost
        cum_aca += yr.aca_loss
        cum_niit += yr.niit_cost
        if ya >= hh.your_rmd_start_age:
            cum_rmd_tax += yr.federal_tax_amt
        cum_brok_tax += yr.brokerage_gain_tax

        results.append(yr)

    return ScenarioResult(
        name=name,
        years=results,
        household=hh,
        plan=plan,
        total_your_conv=sum(yr.your_conversion for yr in results),
        total_spouse_conv=sum(yr.spouse_conversion for yr in results),
        total_conv_tax=cum_conv_tax,
        total_irmaa=cum_irmaa,
        total_aca_loss=cum_aca,
        total_niit=cum_niit,
        total_rmd_tax=cum_rmd_tax,
        total_brok_tax=cum_brok_tax,
    )


def run_no_conversion(
    hh: Household, end_age: int = 95, early_exercise: bool = True
) -> ScenarioResult:
    """Baseline scenario: no conversions at all."""
    return run_scenario(hh, ConversionPlan(), "No Conversion", end_age, early_exercise)
