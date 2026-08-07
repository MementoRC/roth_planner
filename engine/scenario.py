"""Scenario engine — full multi-year Roth conversion projection.

Produces a year-by-year DataFrame with all income sources, taxes, costs,
IRA balances, brokerage tracking, and net benefit analysis.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.ira import inherited_ira_drain
from engine.irmaa import irmaa_for_year, irmaa_next_threshold
from engine.niit import niit
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
    LTCG_RATES_MFJ,
    LTCG_RATES_SINGLE,
    LTCG_THRESHOLDS_MFJ,
    LTCG_THRESHOLDS_SINGLE,
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    senior_bonus_deduction,
)
from engine.tax_indexing import index_tuple as _index_tuple
from engine.tax_indexing import index_value as _index_value
from engine.withdrawal_waterfall import (
    Accounts,
    WaterfallDraw,
    allocate_ira_draw,
    solve_waterfall,
)
from models.household import Household, SurvivorScenario
from models.ytd_income import YTDSnapshot


@dataclass
class _YearOutcome:
    """Single-year projection output plus the loop-carried state for the next iteration."""

    yr: YearResult
    your_ira: float
    spouse_ira: float
    your_roth: float
    spouse_roth: float
    brokerage: float
    brokerage_basis: float
    prev_your_ira_begin: float
    prev_spouse_ira_begin: float
    rollover_done: bool
    ya: float
    sa: float


def _project_year(
    yr_idx: int,
    hh: Household,
    plan: ConversionPlan,
    cpi: float,
    ytd: YTDSnapshot | None,
    net_inv_income: float,
    surv: SurvivorScenario | None,
    your_ira: float,
    spouse_ira: float,
    your_roth: float,
    spouse_roth: float,
    prev_your_ira_begin: float,
    prev_spouse_ira_begin: float,
    brokerage: float,
    rollover_done: bool,
    magi_history: dict[int, float],
    inherited_balances: list[float],
    brokerage_basis: float = 0.0,
    forced_brokerage_draw: float = 0.0,
    forced_your_ira_draw: float = 0.0,
    forced_spouse_ira_draw: float = 0.0,
    forced_your_roth_draw: float = 0.0,
    forced_spouse_roth_draw: float = 0.0,
    forced_early_withdrawal_penalty: float = 0.0,
    conversion_cap: float | None = None,
) -> _YearOutcome:
    """Compute a single projection year — verbatim extraction of the run_scenario loop body.

    `magi_history` and `inherited_balances` are mutated in place, exactly as
    they were when this code lived inline in the run_scenario loop.

    The `forced_*`/`conversion_cap` params are IRA-withdrawal-waterfall hooks
    (see engine/withdrawal_waterfall.py). All default to inert no-op values;
    `run_scenario` sizes them from `solve_waterfall()` whenever a year's
    baseline `income_needed > 0` (stage 3b activation), calling this function
    a second time with the solved draws.
    """
    _rollover_done = rollover_done

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
    yr.filing_status = current_filing_status

    # === Waterfall hooks: record the (default no-op) inputs for visibility ===
    # See engine/withdrawal_waterfall.py. yr.forced_brokerage_draw is set
    # later, alongside the balance/basis effect it triggers.
    yr.forced_your_ira_draw = forced_your_ira_draw
    yr.forced_spouse_ira_draw = forced_spouse_ira_draw
    yr.forced_your_roth_draw = forced_your_roth_draw
    yr.forced_spouse_roth_draw = forced_spouse_roth_draw
    yr.forced_early_withdrawal_penalty = forced_early_withdrawal_penalty

    # === Phase classification ===
    yr.phase = compute_phase(ya, sa, year, hh)

    # === IRA balances ===
    yr.your_ira_begin = your_ira
    yr.spouse_ira_begin = spouse_ira
    yr.your_roth_begin = your_roth
    yr.spouse_roth_begin = spouse_roth

    # === Option income ===
    yr.option_income = hh.option_income(year)

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
        # QCD cap is inflation-indexed forward (SECURE 2.0 §307)
        _index_value(hh.qcd_limit, year, cpi),
        your_defer_first_rmd=hh.your_defer_first_rmd,
        spouse_defer_first_rmd=hh.spouse_defer_first_rmd,
        your_prior_year_balance=prev_your_ira_begin,
        spouse_prior_year_balance=prev_spouse_ira_begin,
        # M3 (audit-0720): the beneficiary is the OTHER spouse, only passed
        # when the household elects the sole-beneficiary toggle AND that
        # spouse is still alive (a deceased spouse can't be a beneficiary).
        your_beneficiary_age=(
            sa if hh.spouse_is_sole_beneficiary and not survivor_active else None
        ),
        spouse_beneficiary_age=(
            ya if hh.spouse_is_sole_beneficiary and not survivor_active else None
        ),
    )

    # === C2/scenario-1: base-year RMD net-of-YTD reconciliation ===
    # ytd_year.ira_distributions_ytd ("non-conversion IRA withdrawals") already
    # includes any RMD taken so far this year, and is re-added downstream to
    # combined_gross, MAGI (via magi_ytd), and SS provisional income. The forecast
    # taxable_rmd from compute_rmds() has no YTD awareness, so without this clamp the
    # already-taken portion of the RMD is double-counted in the base year. Mirror the
    # conversion net-of-YTD clamp: reduce the forecast taxable RMD (yours first, then
    # spouse) by the pooled YTD distributions so each income aggregate nets to
    # max(required RMD, actual distributions taken). The reduction is restored below
    # for the brokerage/excess-RMD cash-flow calc (full RMD cash is available
    # regardless of when in the year it was taken), and the gross RMD (your_rmd/
    # spouse_rmd) is never touched, so IRA balances are unaffected.
    _rmd_ytd_reduction = 0.0
    _spouse_rmd_ytd_reduction = 0.0
    if ytd_year is not None and ytd_year.ira_distributions_ytd > 0:
        _dist_remaining = ytd_year.ira_distributions_ytd
        _rmd_ytd_reduction = min(yr.taxable_rmd, _dist_remaining)
        yr.taxable_rmd -= _rmd_ytd_reduction
        _dist_remaining -= _rmd_ytd_reduction
        _spouse_rmd_ytd_reduction = min(yr.spouse_taxable_rmd, _dist_remaining)
        yr.spouse_taxable_rmd -= _spouse_rmd_ytd_reduction

    # === Extra voluntary withdrawals (bracket fill post-RMD) ===
    # C8 (audit-0721): clamp to the IRA balance remaining after RMD/QCD, mirroring
    # the scenario-core-5 conversion clamp below. Without this, a plan
    # extra_withdrawal larger than what the IRA holds records phantom taxable
    # income even though yr.your_ira_end floors at 0.
    yr.extra_withdrawal = min(
        plan.extra_withdrawals.get(year, 0.0),
        max(your_ira - max(yr.your_rmd, yr.qcd), 0.0),
    )
    yr.spouse_extra_withdrawal = min(
        plan.spouse_extra_withdrawals.get(year, 0.0),
        max(spouse_ira - max(yr.spouse_rmd, yr.spouse_qcd), 0.0),
    )

    # === Survivor income gate ===
    # Per IRC §408A(d)(3), a decedent cannot be the distributee of a conversion;
    # the deceased's IRA was rolled to the survivor at death_year+1 (above), so
    # their RMDs already self-zero via the 0 IRA balance.  Conversions and extra
    # withdrawals, however, are read directly from the plan dict and must be
    # explicitly cleared here — before they reach combined_gross / MAGI / tax /
    # and the Roth carry-forward lines (~:572-573).
    if survivor_active and surv is not None:
        if surv.who_dies == "spouse":
            yr.spouse_conversion = 0.0
            yr.spouse_extra_withdrawal = 0.0
        else:  # who_dies == "you"
            yr.your_conversion = 0.0
            yr.extra_withdrawal = 0.0

    # === Waterfall hook: clamp forced IRA draws to the available balance ===
    # Mirrors the extra_withdrawal clamp (:251-258) and the scenario-core-5
    # conversion clamp below. Without this, a forced draw larger than the IRA
    # holds records PHANTOM TAXABLE INCOME in combined_gross (:452-453) even
    # though yr.your_ira_end floors at 0 (:963) — the floor HIDES the over-draw
    # instead of preventing it, and the phantom tax then inflates the very
    # shortfall the draw exists to cover, driving a larger draw next iteration.
    # Mandatory withdrawals (RMD/QCD/extra_withdrawal) have priority.
    forced_your_ira_draw = min(
        forced_your_ira_draw,
        max(your_ira - max(yr.your_rmd, yr.qcd) - yr.extra_withdrawal, 0.0),
    )
    forced_spouse_ira_draw = min(
        forced_spouse_ira_draw,
        max(spouse_ira - max(yr.spouse_rmd, yr.spouse_qcd) - yr.spouse_extra_withdrawal, 0.0),
    )
    # Record the POST-clamp draws, so YearResult reports what was actually
    # withdrawn rather than what the solver asked for.
    yr.forced_your_ira_draw = forced_your_ira_draw
    yr.forced_spouse_ira_draw = forced_spouse_ira_draw
    yr.forced_brokerage_draw = forced_brokerage_draw
    yr.forced_your_roth_draw = forced_your_roth_draw
    yr.forced_spouse_roth_draw = forced_spouse_roth_draw
    yr.forced_early_withdrawal_penalty = forced_early_withdrawal_penalty

    # === scenario-core-5: clamp conversions to available IRA balance ===
    # A planned conversion can exceed the IRA balance when the IRA has been
    # largely depleted by prior-year growth/withdrawals.  Without this clamp,
    # yr.your_conversion records the full planned amount even though
    # yr.your_ira_end is floored at 0 by max(., 0) — inflating combined_gross,
    # taxable income, and the Roth carry-forward by the excess phantom dollars.
    # Mandatory withdrawals (RMD/QCD/extra_withdrawal) have priority; the
    # conversion receives only what remains.
    # The forced waterfall draw is also subtracted: it funds living expenses,
    # so it outranks a discretionary conversion for the same dollars (and per
    # the approved design the draw is sized FIRST, the conversion second).
    _your_avail_for_conv = max(
        your_ira - max(yr.your_rmd, yr.qcd) - yr.extra_withdrawal - forced_your_ira_draw, 0.0
    )
    yr.your_conversion = min(yr.your_conversion, _your_avail_for_conv)
    _spouse_avail_for_conv = max(
        spouse_ira
        - max(yr.spouse_rmd, yr.spouse_qcd)
        - yr.spouse_extra_withdrawal
        - forced_spouse_ira_draw,
        0.0,
    )
    yr.spouse_conversion = min(yr.spouse_conversion, _spouse_avail_for_conv)

    # === Waterfall hook: conversion_cap ===
    # Sized by run_scenario (from the planned pre-cap conversion minus this
    # year's total forced IRA draw) so a forced IRA draw is not immediately
    # re-converted. Applied AFTER the scenario-core-5 balance clamp above.
    # None (default) is a no-op.
    if conversion_cap is not None:
        _total_conv = yr.your_conversion + yr.spouse_conversion
        if _total_conv > conversion_cap:
            _cap_scale = conversion_cap / _total_conv if _total_conv > 0 else 0.0
            yr.your_conversion *= _cap_scale
            yr.spouse_conversion *= _cap_scale

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
    # B1/B2: in the base year, YTD actuals (ytd_year.ltcg_ytd) are the source of
    # truth for realized capital gains and are already wired into the LTCG stack
    # (ytd_ltcg_tax), NIIT (ytd_year.total_investment_income), MAGI (magi_ytd), and
    # SS provisional income. Suppress the forecast estimate in the base year to avoid
    # double-counting the same gains — mirroring the forecast-dividend suppression in
    # compute_brokerage_dividends. ytd_year is non-None only in the base year.
    realized_gains = (
        0.0
        if ytd_year is not None
        else brokerage * brok_appreciation_rate * hh.brok_turnover
    )

    # === Waterfall hook: forced brokerage draw ===
    # Sized by run_scenario via solve_waterfall(); at the default 0.0 this
    # is a no-op. Draw realizes gain proportional to the current basis
    # fraction, folded into the SAME realized_gains the LTCG stack-walk below
    # consumes, and reduces both brokerage and its basis before this year's
    # appreciation/dividends are applied.
    # `_brokerage_opening_balance` is captured BEFORE that reduction so
    # yr.brokerage_balance (set later, from the reduced `brokerage`) can be
    # overridden back to the TRUE begin-of-year amount -- its documented
    # contract (scenario_types.YearResult.brokerage_balance) is "before...
    # the living-expense debit is applied", and the forced draw IS that
    # debit, just applied earlier in this function than the legacy one.
    _brokerage_opening_balance = brokerage
    yr.forced_brokerage_draw = forced_brokerage_draw
    if forced_brokerage_draw > 0.0:
        _fbd_basis_fraction = (brokerage_basis / brokerage) if brokerage > 0.0 else 0.0
        realized_gains += forced_brokerage_draw * (1 - _fbd_basis_fraction)
        brokerage_basis = max(0.0, brokerage_basis - forced_brokerage_draw * _fbd_basis_fraction)
        brokerage = max(0.0, brokerage - forced_brokerage_draw)

    # === Social Security + taxable SS ===
    # SS survivor step-up: survivor keeps max(your_ss, spouse_ss); implemented in compute_social_security.
    # D-1: MAGI uses taxable SS, not full SS (computed here, before MAGI block).
    # F3/F4: qual_div_this_year and realized_gains are now passed in for provisional income.
    # audit-0805 C12/N1: yr.option_income is the SCHEDULED (forecast) option income
    # for the full year. When realized YTD NQO exercises exceed that schedule (a plan
    # can under-forecast, or the household exercised more than planned), the raw
    # scheduled value understates gross income, SS provisional income, AND MAGI's
    # option-income term. Bound it to the realized amount (mirroring headroom.py's
    # max(0.0, opt - realized) treatment, which nets ON TOP of the unreduced
    # ytd.magi_ytd) so realized income is never lost. For realized <= scheduled this
    # is a no-op (max(opt, nqo_ytd) == opt).
    _nqo_ytd = ytd_year.nqo_exercise_ytd if ytd_year is not None else 0.0
    option_income_bounded = max(yr.option_income, _nqo_ytd)
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
        option_income_bounded,
        yr.your_inherited_distribution,
        yr.spouse_inherited_distribution,
        ord_div_this_year,
        ytd_year,
        qual_div_this_year,
        realized_gains,
        death_year=surv.death_year if surv is not None else None,
        # Waterfall: the forced IRA draws are ordinary distributions (IRC §408(d)(1)) and
        # belong in the §86(b)(2) provisional-income base exactly like the sibling
        # extra_withdrawal quantities passed above. Omitting them understated the taxable
        # portion of SS wherever a draw and a benefit coincide (10 years on the default
        # household, 26 on a large one). Invisible to the combined_gross-vs-magi gap check
        # that caught the parallel MAGI omission, since taxable_ss_amt feeds both.
        forced_your_ira_draw=forced_your_ira_draw,
        forced_spouse_ira_draw=forced_spouse_ira_draw,
    )

    # === MAGI (for IRMAA/ACA — uses full amounts, not taxable) ===
    # D-1: use taxable_ss_amt (up to 85% of SS) not full combined_ss — per §1395r(i)(4)
    # C-7/audit-0805 C12: subtract nqo_exercise_ytd from the BOUNDED option-income
    # contribution when ytd is present. Deriving this from option_income_bounded
    # (rather than raw yr.option_income) gives max(0.0, opt - nqo_ytd) — a floor at
    # zero — instead of the unfloored (and potentially negative) opt - nqo_ytd that
    # silently erased realized income exceeding the schedule.
    # QCD IS excluded from MAGI, so use taxable_rmd / spouse_taxable_rmd.
    # NOTE: realized_gains excluded here; folded into yr.magi in the MAGI ordering block below.
    # Waterfall: the forced IRA draws are ordinary distributions (IRC §408(d)(1)) and belong
    # in MAGI exactly like the sibling extra_withdrawal quantities two lines above. They were
    # added to combined_gross below when the waterfall was wired in but not here, leaving
    # magi short by the whole draw in 23 of 41 projected years — which silently zeroed
    # IRMAA (via magi_history's 2-year lookback), NIIT and ACA for any household whose
    # draw would have carried it across a threshold. The Roth leg (tax-free), the brokerage
    # leg (its realized gain already reaches MAGI via realized_gains) and the §72(t) penalty
    # (a tax, not income) are all correctly excluded.
    option_income_for_magi = option_income_bounded - _nqo_ytd
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
        forced_your_ira_draw=forced_your_ira_draw,
        forced_spouse_ira_draw=forced_spouse_ira_draw,
    )

    # === Brokerage realized gains added to MAGI ===
    # brok_rate, brok_appreciation_rate, and realized_gains were computed above (hoisted
    # for SS provisional income). Add realized_gains to MAGI here for the ordering block.
    yr.magi += realized_gains
    magi_history[year] = yr.magi

    # === Combined gross (for tax) ===
    # Includes ordinary income only — LTCG taxed separately at preferential rate
    # audit-0805 N1: use the bounded option-income value (see above) so realized
    # YTD NQO exercises in excess of the schedule are not lost from gross income.
    yr.combined_gross = (
        option_income_bounded
        + yr.your_conversion
        + yr.spouse_conversion
        + yr.taxable_rmd
        + yr.spouse_taxable_rmd
        + yr.extra_withdrawal
        + yr.spouse_extra_withdrawal
        + yr.taxable_ss_amt
        + yr.your_inherited_distribution
        + yr.spouse_inherited_distribution
        + forced_your_ira_draw
        + forced_spouse_ira_draw
    )
    # YTD: add all ordinary income components to gross.
    # LTCG and qualified dividends are excluded (taxed at preferential rate).
    # nec_income_ytd and ira_distributions_ytd are ordinary income; include them.
    # ira_conversions_ytd: yr.your_conversion was already reduced by this amount
    # (scenario_compute.py clamp), so adding it back here makes the full planned
    # conversion stack into combined_gross correctly.
    # spouse_ira_conversions_ytd: same symmetric logic — yr.spouse_conversion was
    # reduced by this amount; re-add it so the full spouse conversion appears in gross.
    # audit-0720 F: above_the_line_adjustments_ytd (HSA/deductible-IRA) is subtracted
    # here to match yr.magi's existing treatment (via magi_ytd) — both are AGI-basis
    # aggregates and above-the-line adjustments reduce AGI, hence both the ordinary
    # bracket base and MAGI.
    if ytd_year is not None:
        yr.combined_gross += (
            ytd_year.wages_ytd
            + ytd_year.nec_income_ytd
            + ytd_year.stcg_ytd
            + ytd_year.ordinary_dividends_ytd
            + ytd_year.interest_ytd
            + ytd_year.ira_conversions_ytd
            + ytd_year.spouse_ira_conversions_ytd
            + ytd_year.ira_distributions_ytd
            + ytd_year.crypto_stcg_ytd
            + ytd_year.crypto_income_ytd
            - ytd_year.above_the_line_adjustments_ytd
        )
    # Forecast ordinary dividends are ordinary income; qualified dividends are MAGI-only (like LTCG)
    yr.combined_gross += ord_div_this_year

    # === Deductions ===
    # OBBBA phaseout is measured on AGI (muni-excluded), matching estimate_ytd_federal_tax
    # which passes niit_magi_ytd. yr.magi is IRMAA MAGI (includes muni), so strip muni here.
    _phaseout_muni = ytd_year.tax_exempt_interest_ytd if ytd_year is not None else 0.0
    if survivor_active:
        assert surv is not None  # narrowing: survivor_active implies surv is not None
        # Use single-filer std deduction + senior extra; zero deceased age so
        # only the survivor counts toward the senior-extra and OBBBA bonus.
        ya_eff = 0 if surv.who_dies == "you" else ya
        sa_eff = 0 if surv.who_dies == "spouse" else sa
        yr.total_deductions = deductions(
            ya_eff, sa_eff, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single", year=year, cpi=cpi
        )
        yr.total_deductions += senior_bonus_deduction(
            ya_eff, sa_eff, yr.magi - _phaseout_muni, year=year, cpi=cpi, filing_status="Single"
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
        yr.total_deductions = deductions(ya, sa, _std_ded, _senior_extra, filing_status=current_filing_status, year=year, cpi=cpi)
        yr.total_deductions += senior_bonus_deduction(
            ya, sa, yr.magi - _phaseout_muni, year=year, cpi=cpi, filing_status=current_filing_status
        )

    # === Federal tax + conversion tax (incremental) ===
    # SS "tax torpedo" (audit C6 / scenario-2): recompute taxable SS with the
    # planned conversions removed so conversion_tax below captures the extra SS
    # the conversion pushed into taxability — not just the ordinary bracket
    # delta on the conversion dollars. Only the conversion args change to 0.0.
    # F12: this must be computed BEFORE base_magi so the OBBBA senior-deduction
    # baseline also strips the conversion-induced taxable-SS delta, not just the
    # raw conversion dollars.
    _, _, _, _taxable_ss_no_conv = compute_social_security(
        hh,
        ya,
        sa,
        survivor_active,
        surv.who_dies if surv is not None else None,
        current_filing_status,
        0.0,
        0.0,
        yr.taxable_rmd,
        yr.spouse_taxable_rmd,
        yr.extra_withdrawal,
        yr.spouse_extra_withdrawal,
        option_income_bounded,
        yr.your_inherited_distribution,
        yr.spouse_inherited_distribution,
        ord_div_this_year,
        ytd_year,
        qual_div_this_year,
        realized_gains,
        death_year=surv.death_year if surv is not None else None,
    )
    conversion_ss_delta = yr.taxable_ss_amt - _taxable_ss_no_conv

    # Baseline (no-conversion) deductions for the incremental conversion tax:
    # recompute the OBBBA bonus at the no-conversion MAGI so a conversion cannot
    # phase out its own baseline deduction (scenario-math-3).
    # F12: also subtract conversion_ss_delta so the baseline MAGI reflects the
    # taxable-SS amount WITHOUT the conversion, capturing the full SS tax torpedo
    # in conversion_tax.
    base_magi = yr.magi - yr.your_conversion - yr.spouse_conversion - conversion_ss_delta
    if survivor_active:
        base_total_deductions = deductions(
            ya_eff, sa_eff, STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE, filing_status="Single", year=year, cpi=cpi
        ) + senior_bonus_deduction(
            ya_eff, sa_eff, base_magi - _phaseout_muni, year=year, cpi=cpi, filing_status="Single"
        )
    else:
        _b_std_ded: float
        _b_senior_extra: float
        if current_filing_status == "Single":
            _b_std_ded, _b_senior_extra = STD_DEDUCTION_SINGLE, SENIOR_EXTRA_SINGLE
        else:
            _b_std_ded, _b_senior_extra = hh.std_deduction, hh.senior_extra
        base_total_deductions = deductions(
            ya, sa, _b_std_ded, _b_senior_extra, filing_status=current_filing_status, year=year, cpi=cpi
        ) + senior_bonus_deduction(
            ya, sa, base_magi - _phaseout_muni, year=year, cpi=cpi, filing_status=current_filing_status
        )

    # === Taxable income ===
    yr.taxable_income = max(yr.combined_gross - yr.total_deductions, 0)

    yr.federal_tax_amt, yr.marginal_bracket, yr.conversion_tax, base_taxable = compute_federal_tax(
        yr.taxable_income,
        yr.combined_gross,
        yr.your_conversion,
        yr.spouse_conversion,
        base_total_deductions,
        current_filing_status,
        year,
        cpi,
        conversion_ss_delta,
    )

    # Waterfall hook: forced early-withdrawal penalty (IRC §72(t)) adds to
    # federal_tax_amt. Default 0.0 is a no-op.
    yr.federal_tax_amt += forced_early_withdrawal_penalty

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
    # H1 fix: in survivor years, map the survivor's age into the primary slot that
    # irmaa_for_year reads. For non-MFJ filing status irmaa_for_year only examines
    # your_age_income_year (the "ya" slot); the spouse slot is ignored. So when the
    # surviving party is the spouse (who_dies=="you"), her age must be placed in ya_irmaa
    # so the Medicare-age check fires correctly. Zero the deceased's slot in both cases.
    if survivor_active and surv is not None:
        if surv.who_dies == "you":
            ya_irmaa = sa  # spouse survives → promote to primary slot
            sa_irmaa = 0
        else:  # who_dies == "spouse" → you survive
            ya_irmaa = ya
            sa_irmaa = 0
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
        yr.magi, filing_status=current_filing_status, year=year + 2, cpi=cpi
    )  # +2: this year's MAGI is judged against payment-year (income_year + 2) thresholds

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
    # audit-0720 F3: crypto_ltcg_ytd is taxed at the same preferential 0/15/20%
    # rates (IRC §1(h)) as ltcg_ytd/qualified_dividends_ytd and must be included
    # in the stack-walk base — it already reaches MAGI/NIIT but was silently
    # skipping the LTCG-rate tax computation itself.
    _ytd_ltcg_total = (
        (ytd_year.ltcg_ytd + ytd_year.qualified_dividends_ytd + ytd_year.crypto_ltcg_ytd)
        if ytd_year is not None
        else 0.0
    )
    # P3-2 (audit-0723): base-year ltcg_eligible below (realized_gains + qual_div_this_year)
    # is force-suppressed to 0.0 whenever ytd_year is not None (see the B1/B2 comment
    # above), so the C2 conversion_ltcg_cost block later in this function — which reuses
    # that same ltcg_eligible — silently drops the conversion-induced bracket-stacking
    # cost on YTD-*actual* realized gains. Compute the without-conversion counterfactual
    # for _ytd_ltcg_total here (mirroring the with-conversion stack immediately below) so
    # the difference can be folded into conversion_ltcg_cost alongside the forecast case.
    _ytd_ltcg_marginal_cost = 0.0
    if ytd_year is not None and _ytd_ltcg_total > 0:
        # Thresholds depend on filing status: Single for survivor years, MFJ otherwise.
        _base_ytd_ltcg_thresholds = (
            LTCG_THRESHOLDS_SINGLE if current_filing_status == "Single" else LTCG_THRESHOLDS_MFJ
        )
        _ytd_ltcg_thresholds = _index_tuple(_base_ytd_ltcg_thresholds, year, cpi, round50=True)
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
        _ltcg_rates = LTCG_RATES_SINGLE if current_filing_status == "Single" else LTCG_RATES_MFJ
        yr.ytd_ltcg_tax = (
            _ytd_ltcg_at_15 * _ltcg_rates[1] + _ytd_ltcg_at_20 * _ltcg_rates[2]
        )
        # P3-2: without-conversion counterfactual for the SAME _ytd_ltcg_total,
        # stacked from base_taxable (the no-conversion ordinary taxable income)
        # instead of yr.taxable_income (which includes this year's conversion).
        _ytd_ltcg_start_base = max(0.0, base_taxable)
        _ytd_ltcg_end_base = _ytd_ltcg_start_base + max(0.0, _ytd_ltcg_total)
        _ytd_ltcg_at_15_base = max(
            0.0,
            min(_ytd_ltcg_end_base, _ytd_ltcg_thresholds[1])
            - max(_ytd_ltcg_start_base, _ytd_ltcg_thresholds[0]),
        )
        _ytd_ltcg_at_20_base = max(
            0.0, _ytd_ltcg_end_base - max(_ytd_ltcg_start_base, _ytd_ltcg_thresholds[1])
        )
        _ytd_ltcg_tax_base = (
            _ytd_ltcg_at_15_base * _ltcg_rates[1] + _ytd_ltcg_at_20_base * _ltcg_rates[2]
        )
        _ytd_ltcg_marginal_cost = max(0.0, yr.ytd_ltcg_tax - _ytd_ltcg_tax_base)
        # grid-05: YTD realized LTCG tax is a real federal tax for the base year
        # but was previously orphaned (computed, never counted in any total). Fold
        # it into federal_tax_amt so lifetime tax / all-in cost reflect it.
        yr.federal_tax_amt += yr.ytd_ltcg_tax

    # === NIIT (3.8% surtax on investment income when MAGI > $250K) ===
    # Net investment income = realized appreciation gains + all dividends (qual + ord)
    # Computed on beginning brokerage balance (carry-forward from prior year)
    # Reuse `realized_gains` (identical operands) so the base-year suppression above
    # also zeroes the forecast NII term; ytd_year.total_investment_income below is then
    # the sole base-year source. Non-base years are unchanged (realized_gains is the
    # same brokerage * brok_appreciation_rate * hh.brok_turnover product).
    net_investment_income = realized_gains + qual_div_this_year + ord_div_this_year
    # YTD: add realized gains, dividends, interest to investment income
    if ytd_year is not None:
        net_investment_income += ytd_year.total_investment_income
    # Manual additional NII not otherwise modeled (e.g. interest, off-portfolio
    # gains). Applied uniformly to every projected year, matching the "$/yr"
    # Additional-NII input already consumed by Sweet Spot Finder
    # (all_in_at_conversion) and the ACA+IRMAA Explorer (compute_cost_curves);
    # the Conversion Planner previously had no channel for it, silently
    # omitting NIIT on this income (audit-0802 F1).
    net_investment_income += net_inv_income
    # IRC §1411: realized capital gains belong in NIIT MAGI with no exclusion.
    # yr.magi already includes realized_gains (folded in the MAGI ordering block),
    # so niit_magi only needs to strip tax-exempt muni interest -- excluded not
    # by a §1411(d) carve-out but because IRC §103 keeps it out of gross income
    # (hence out of AGI and §1411(d)'s MAGI) entirely.
    # audit-0805 C10: net_inv_income was already folded into net_investment_income
    # above (the NII side of niit()) but was missing from the MAGI side, so a
    # user-declared manual NII understated the excess-over-threshold and therefore
    # the tax. It is real declared income, so it belongs in MAGI too.
    yr.niit_magi = (
        yr.magi + net_inv_income - (ytd_year.tax_exempt_interest_ytd if ytd_year else 0.0)
    )
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

    # Restore the base-year YTD reduction: the full required RMD cash is available for
    # living expenses / excess-RMD reinvestment regardless of when in the year it was
    # taken (see C2/scenario-1 reconciliation above). _*_reduction are 0.0 outside the
    # base year, so forecast years are unchanged.
    after_tax_rmd = (yr.taxable_rmd + _rmd_ytd_reduction) + (
        yr.spouse_taxable_rmd + _spouse_rmd_ytd_reduction
    )  # taxable RMDs (net of QCDs), full pre-YTD-clamp amount
    available_income = (
        after_tax_rmd
        + yr.extra_withdrawal
        + yr.spouse_extra_withdrawal
        + yr.combined_ss
        + yr.option_income
        # Inherited-IRA distributions are spendable, taxable cash (already in
        # combined_gross/MAGI, so already in federal_tax_amt). Omitting them here
        # subtracted their tax with no offsetting inflow, overstating income_needed
        # (audit: unmasked when the hold-to-expiry default removed early option income).
        + yr.your_inherited_distribution
        + yr.spouse_inherited_distribution
        - yr.federal_tax_amt
    )
    # audit-0720 F4: YTD wages/NEC/interest/STCG are spendable cash already
    # received this year (already taxed via combined_gross/federal_tax_amt
    # above, which IS subtracted) but were never added back as an inflow —
    # producing a phantom shortfall (understated available_income) for
    # households with substantial YTD ordinary cash income.
    # audit-0721 C7: ira_distributions_ytd is also spendable cash already
    # received (taxed via combined_gross/federal_tax_amt) but only the
    # portion absorbed by the forecast RMD is restored via after_tax_rmd
    # (_rmd_ytd_reduction/_spouse_rmd_ytd_reduction above). Any distributions
    # taken BEYOND the forecast RMD (voluntary withdrawals, or distributions
    # taken before RMD age) were never added back anywhere -- add only that
    # excess here to avoid double-counting the RMD-absorbed portion.
    if ytd_year is not None:
        _ytd_dist_excess = max(
            0.0,
            ytd_year.ira_distributions_ytd
            - _rmd_ytd_reduction
            - _spouse_rmd_ytd_reduction,
        )
        available_income += (
            ytd_year.wages_ytd
            + ytd_year.nec_income_ytd
            + ytd_year.interest_ytd
            + ytd_year.stcg_ytd
            + _ytd_dist_excess
        )
    yr.income_needed = max(yr.living_expenses - available_income, 0)
    yr.excess_rmd = max(available_income - yr.living_expenses, 0)

    # Brokerage: accumulates excess, grows (appreciation), dividends reinvest, pays cap gains
    # yr.brokerage_balance is the TRUE begin-of-year amount (see the capture
    # above); growth/gain-tax/div/debit below all still key off the
    # (possibly forced-draw-reduced) `brokerage` local, unaffected.
    yr.brokerage_balance = _brokerage_opening_balance
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
    ltcg_thresholds = _index_tuple(_base_ltcg_thresholds, year, cpi, round50=True)
    ltcg_eligible = realized_gains + qual_div_this_year
    _ltcg_start = max(0.0, yr.taxable_income)
    _ltcg_end = _ltcg_start + max(0.0, ltcg_eligible)
    _ltcg_at_15 = max(
        0.0,
        min(_ltcg_end, ltcg_thresholds[1]) - max(_ltcg_start, ltcg_thresholds[0]),
    )
    _ltcg_at_20 = max(0.0, _ltcg_end - max(_ltcg_start, ltcg_thresholds[1]))
    _ltcg_rates = LTCG_RATES_SINGLE if current_filing_status == "Single" else LTCG_RATES_MFJ
    yr.brokerage_gain_tax = _ltcg_at_15 * _ltcg_rates[1] + _ltcg_at_20 * _ltcg_rates[2]

    # C2: the conversion-induced LTCG bracket-stacking bump is a real marginal
    # cost of converting this year. It is already captured in brokerage_gain_tax
    # (and thus lifetime tax), but was missing from the per-year all_in_cost
    # optimization signal. Re-stack the SAME ltcg_eligible at the without-conversion
    # ordinary start and attribute the difference. Kept SEPARATE from conversion_tax
    # so cum_conv_tax / lifetime totals are not double-counted against cum_brok_tax.
    _ltcg_start_base = max(0.0, base_taxable)
    _ltcg_end_base = _ltcg_start_base + max(0.0, ltcg_eligible)
    _ltcg_at_15_base = max(
        0.0,
        min(_ltcg_end_base, ltcg_thresholds[1]) - max(_ltcg_start_base, ltcg_thresholds[0]),
    )
    _ltcg_at_20_base = max(0.0, _ltcg_end_base - max(_ltcg_start_base, ltcg_thresholds[1]))
    _brokerage_gain_tax_base = (
        _ltcg_at_15_base * _ltcg_rates[1] + _ltcg_at_20_base * _ltcg_rates[2]
    )
    # P3-2: ltcg_eligible (and thus the two lines above) is 0.0 in the base year
    # (see B1/B2 suppression), so add the YTD-actual marginal cost computed
    # earlier alongside the forecast-sourced one — the two are mutually
    # exclusive per year (ytd_year is only non-None in the base year).
    yr.conversion_ltcg_cost = (
        max(0.0, yr.brokerage_gain_tax - _brokerage_gain_tax_base) + _ytd_ltcg_marginal_cost
    )
    yr.all_in_cost += yr.conversion_ltcg_cost

    total_div = qual_div_this_year + ord_div_this_year

    # audit-0805 C8: yr.income_needed and yr.excess_rmd are two halves of the
    # SAME quantity (living-expense shortfall vs. surplus vs. available_income)
    # split at :769-770, but only the surplus half (excess_rmd) was ever applied
    # here -- a deficit year silently cost nothing. available_income already
    # subtracts yr.federal_tax_amt, so a Roth conversion raises income_needed;
    # without this debit the conversion's real cash cost never depleted any
    # balance, making conversions appear costless in the headline comparison.
    # Debit the shortfall from the same running balance that credits the
    # surplus, floored at 0 (a taxable brokerage account cannot go negative).
    _brokerage_before_expense_debit = (
        brokerage
        + yr.brokerage_growth
        - yr.brokerage_gain_tax
        + total_div  # dividends reinvested (taxable event already captured in income stacks)
        + yr.excess_rmd
    )
    # Basis bookkeeping (IRA-withdrawal-waterfall, stage 3): total_div
    # (reinvested dividends) and yr.excess_rmd are CONTRIBUTIONS and add to basis;
    # yr.brokerage_growth is APPRECIATION and adds nothing. brokerage_gain_tax does
    # not reduce basis here.
    _brokerage_basis_before_debit = brokerage_basis + total_div + yr.excess_rmd
    # Stage 3b activation: when run_scenario has already solved the waterfall
    # for this year (signalled by ANY forced draw being non-default), the
    # shortfall has already been funded via forced_brokerage_draw (subtracted
    # from the OPENING balance above, before this year's growth) plus the
    # forced IRA/Roth draws (which never touch brokerage at all). Debiting
    # yr.income_needed AGAIN here would fund the same shortfall twice -- once
    # from brokerage pre-growth, once from this post-growth balance. Skip the
    # second debit in that case; run_scenario overwrites yr.unfunded_need with
    # the solver's own residual (WaterfallDraw.unfunded) immediately after the
    # call returns.
    _waterfall_active = (
        forced_brokerage_draw > 0.0
        or forced_your_ira_draw > 0.0
        or forced_spouse_ira_draw > 0.0
        or forced_your_roth_draw > 0.0
        or forced_spouse_roth_draw > 0.0
    )
    _expense_debit_amount = 0.0 if _waterfall_active else yr.income_needed
    yr.unfunded_need = max(_expense_debit_amount - _brokerage_before_expense_debit, 0.0)
    brokerage = max(_brokerage_before_expense_debit - _expense_debit_amount, 0.0)
    # The expense debit reduces basis PROPORTIONALLY: on a reduction of D from
    # balance B, basis -= D * (basis / B); guard B <= 0 -> basis 0.
    if _brokerage_before_expense_debit <= 0:
        brokerage_basis = 0.0
    else:
        _expense_debit = _brokerage_before_expense_debit - brokerage
        brokerage_basis = _brokerage_basis_before_debit - _expense_debit * (
            _brokerage_basis_before_debit / _brokerage_before_expense_debit
        )
    brokerage_basis = max(0.0, min(brokerage_basis, brokerage))
    yr.brokerage_basis = brokerage_basis
    # brokerage here is already the post-growth/post-dividend/post-excess_rmd/
    # post-expense-debit CLOSING balance -- expose it so basis (also closing)
    # can be compared like-for-like instead of against the opening
    # yr.brokerage_balance set at the top of this block.
    yr.brokerage_balance_end = brokerage

    # === IRA end of year ===
    # QCD distributions leave the IRA: a QCD exceeding the RMD pulls an extra
    # income-excluded distribution (max(rmd, qcd)), shrinking future RMDs. The
    # excess goes to charity, so it is NOT reinvested (excess_rmd uses taxable_rmd).
    # forced_your_ira_draw/forced_spouse_ira_draw: IRA-withdrawal-waterfall
    # hooks, sized by run_scenario via solve_waterfall(); default 0.0 is a no-op.
    your_withdrawal = (
        yr.your_conversion + max(yr.your_rmd, yr.qcd) + yr.extra_withdrawal + forced_your_ira_draw
    )
    spouse_withdrawal = (
        yr.spouse_conversion
        + max(yr.spouse_rmd, yr.spouse_qcd)
        + yr.spouse_extra_withdrawal
        + forced_spouse_ira_draw
    )

    yr.your_ira_end = max(your_ira - your_withdrawal, 0) * (1 + hh.your_ira_rate(year))
    yr.spouse_ira_end = max(spouse_ira - spouse_withdrawal, 0) * (1 + hh.spouse_ira_rate(year))

    # === Roth end of year ===
    # Credit conversions (only) to Roth; grow tax-free.
    # rmd and extra_withdrawal are NOT Roth-eligible (they go to taxable accounts).
    # forced_your_roth_draw/forced_spouse_roth_draw: IRA-withdrawal-waterfall
    # hooks (tax-free, no income effect); default 0.0 is a no-op.
    yr.your_roth_end = max(
        0.0, (your_roth + yr.your_conversion) * (1 + hh.your_roth_rate(year)) - forced_your_roth_draw
    )
    yr.spouse_roth_end = max(
        0.0,
        (spouse_roth + yr.spouse_conversion) * (1 + hh.spouse_roth_rate(year))
        - forced_spouse_roth_draw,
    )

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
    prev_your_ira_begin = yr.your_ira_begin
    prev_spouse_ira_begin = yr.spouse_ira_begin

    return _YearOutcome(
        yr=yr,
        your_ira=your_ira,
        spouse_ira=spouse_ira,
        your_roth=your_roth,
        spouse_roth=spouse_roth,
        brokerage=brokerage,
        brokerage_basis=brokerage_basis,
        prev_your_ira_begin=prev_your_ira_begin,
        prev_spouse_ira_begin=prev_spouse_ira_begin,
        rollover_done=_rollover_done,
        ya=ya,
        sa=sa,
    )


def _solve_waterfall_year(
    yr_idx: int,
    hh: Household,
    plan: ConversionPlan,
    cpi: float,
    ytd: YTDSnapshot | None,
    net_inv_income: float,
    surv: SurvivorScenario | None,
    your_ira: float,
    spouse_ira: float,
    your_roth: float,
    spouse_roth: float,
    prev_your_ira_begin: float,
    prev_spouse_ira_begin: float,
    brokerage: float,
    brokerage_basis: float,
    rollover_done: bool,
    magi_history: dict[int, float],
    inherited_balances: list[float],
    baseline: _YearOutcome,
) -> _YearOutcome:
    """Solve the IRA-withdrawal waterfall for a shortfall year and re-run
    `_project_year` once more with the solved draws (stage 3b activation).

    `baseline` is the no-op-hooks outcome the caller already computed this
    year (`baseline.yr.income_needed > 0`) -- reused here for its federal
    tax, planned conversion, and begin-of-year account balances so there is
    only ONE tax stack (`_project_year`), never a second one assembled from
    the standalone compute_* helpers.

    `inherited_balances` mutates cumulatively on every `_project_year` call
    (SECURE Act 10-year drain), so every speculative call here (the `tax_of`
    probes, run repeatedly by `solve_waterfall`) gets its own throwaway copy;
    only the FINAL re-run's drain is committed back into the caller's list.
    """
    planned_conversion = baseline.yr.your_conversion + baseline.yr.spouse_conversion

    def _no_draw_outcome(conversion_cap: float | None) -> _YearOutcome:
        """This year's projection with NO waterfall draw, at a given conversion cap.

        `baseline` already IS that projection whenever the cap does not bind, so
        it is reused rather than paying for an identical `_project_year` call.
        """
        if conversion_cap is None or conversion_cap >= planned_conversion:
            return baseline
        return _project_year(
            yr_idx,
            hh,
            plan,
            cpi,
            ytd,
            net_inv_income,
            surv,
            your_ira,
            spouse_ira,
            your_roth,
            spouse_roth,
            prev_your_ira_begin,
            prev_spouse_ira_begin,
            brokerage,
            rollover_done,
            magi_history,
            list(inherited_balances),
            brokerage_basis=brokerage_basis,
            conversion_cap=conversion_cap,
        )

    def _solve_draw(nd: _YearOutcome, conversion_cap: float | None) -> WaterfallDraw:
        """Size the waterfall draw against `nd`'s shortfall and conversion."""
        need = nd.yr.income_needed
        nd_tax = nd.yr.federal_tax_amt
        brok_begin = nd.yr.brokerage_balance
        basis_fraction = brokerage_basis / brok_begin if brok_begin > 0 else 0.0
        # Net the IRA ceilings for this year's MANDATORY claims on the same
        # balance (RMD/QCD and extra_withdrawal). Offering the gross
        # begin-of-year balance would hand the solver dollars that are already
        # spoken for, so it allocates them twice and the draw is then silently
        # truncated downstream -- leaving the need under-funded while the
        # phantom income is still taxed. Conversions are deliberately NOT
        # subtracted: per the approved design the draw is sized first and the
        # conversion is capped against the leftover headroom afterwards.
        _your_mandatory = max(nd.yr.your_rmd, nd.yr.qcd) + nd.yr.extra_withdrawal
        _spouse_mandatory = (
            max(nd.yr.spouse_rmd, nd.yr.spouse_qcd) + nd.yr.spouse_extra_withdrawal
        )
        _your_ira_available = max(nd.yr.your_ira_begin - _your_mandatory, 0.0)
        _spouse_ira_available = max(nd.yr.spouse_ira_begin - _spouse_mandatory, 0.0)

        def tax_of(extra_ordinary: float) -> float:
            # Marginal federal tax ONLY -- the early-withdrawal penalty is added
            # by solve_waterfall itself, outside this closure.
            #
            # The probe must SPLIT the speculative draw exactly the way the solver
            # will really take it. Attributing the whole amount to "your" IRA
            # under-reported the tax whenever that balance was the smaller one
            # (the forced-draw clamp truncated the probe), and because swapping the
            # two households swaps which IRA is smaller, the error was asymmetric
            # and broke the me/spouse parity invariants. allocate_ira_draw is the
            # solver's own ordering rule, reused rather than reimplemented.
            #
            # The probe also carries the SAME conversion_cap as `nd`, so the
            # measured delta is the marginal tax of the DRAW alone and not a
            # blend of the draw and a conversion that differs between the two
            # projections being differenced.
            your_probe, spouse_probe = allocate_ira_draw(
                extra_ordinary,
                _your_ira_available,
                _spouse_ira_available,
                nd.ya,
                nd.sa,
            )
            speculative = _project_year(
                yr_idx,
                hh,
                plan,
                cpi,
                ytd,
                net_inv_income,
                surv,
                your_ira,
                spouse_ira,
                your_roth,
                spouse_roth,
                prev_your_ira_begin,
                prev_spouse_ira_begin,
                brokerage,
                rollover_done,
                magi_history,
                list(inherited_balances),
                brokerage_basis=brokerage_basis,
                forced_your_ira_draw=your_probe,
                forced_spouse_ira_draw=spouse_probe,
                conversion_cap=conversion_cap,
            )
            return speculative.yr.federal_tax_amt - nd_tax

        accounts = Accounts(
            brokerage=brok_begin,
            brokerage_basis_fraction=basis_fraction,
            your_ira=_your_ira_available,
            spouse_ira=_spouse_ira_available,
            your_roth=nd.yr.your_roth_begin,
            spouse_roth=nd.yr.spouse_roth_begin,
        )
        return solve_waterfall(need, accounts, tax_of, nd.ya, nd.sa)

    def _base_headroom() -> float:
        """Ordinary-income room for a conversion, measured against BASE income only.

        Reuses the engine's own `compute_bracket_room` output (`yr.room_12` /
        `yr.room_22`, already computed inside `_project_year`) rather than
        re-deriving bracket edges here -- a second copy of the bracket math is
        the dual-writer trap this feature was designed to avoid.

        CEILING USED: the top of the 22% bracket (`room_22`), always.

        Selecting `room_12` while the marginal rate is still 12% was tried and
        is WRONG: it makes a deliberate 22%-bracket-fill plan self-limiting,
        because capping every conversion at the 12% ceiling keeps the marginal
        rate at 12% forever. It inverts the tool's own invariant -- filling 22%
        then leaves MORE IRA at 75 than filling 12%
        (test_scenario_core.py::test_22pct_fill_reduces_ira_more).

        THE DRAW IS DELIBERATELY EXCLUDED. Measuring the room as
        `ceiling - (base + draw)` was tried and is ALSO wrong: it makes the
        conversion self-throttling, because a larger planned conversion raises
        this year's tax, which inflates the living-expense shortfall, which
        drives a larger IRA draw, which eats the very headroom the conversion
        is then capped against. The net effect is that a LARGER plan converts
        LESS in absolute terms -- a 22k/yr plan left $7,546.86 of IRA at 75
        while a 90k/yr plan left $510,465.89. Sizing against base income alone
        breaks that feedback loop: the cap no longer sees the draw, so the plan
        the user wrote is honoured.

        CONSEQUENCE, deliberate and approved: the bracket ceiling is now a
        TARGET FOR THE CONVERSION, not a hard cap on total ordinary income.
        In a heavy-draw year, base + conversion + draw may EXCEED the 22% top,
        and the engine does NOT suppress that -- suppressing it is precisely the
        self-throttling excluded above. Any overshoot carries IRMAA (2-year
        lookback) and ACA (same-year, cliff) exposure that the UI should
        surface. Measured on the default household it never actually binds
        (0 of 41 years for both a 22k/yr and a 90k/yr plan, peak taxable
        $157,364 against a $211,400 ceiling), because the IRA is exhausted by
        the draws before the ceiling is reached -- but a household with larger
        balances or more outside income can overshoot.

        `_no_draw_outcome(0.0)` is already exactly the projection required --
        no forced draws, conversion suppressed -- so it is reused rather than
        paying for a second identical `_project_year` call.
        """
        return _no_draw_outcome(0.0).yr.room_22

    # === Draw/conversion solve ===
    # The dependency is ONE-WAY, so a SINGLE solve is exact. A conversion's tax
    # raises the living-expense shortfall the draw must itself fund, so the draw
    # depends on the conversion -- but the cap is measured against BASE income
    # only (see _base_headroom), so the draw does NOT feed back into it. Sizing
    # the conversion first and the draw second therefore lands on the fixed
    # point in one pass; there is nothing left to iterate.
    #
    # This previously ran a max-3-pass loop. That loop was dead: `conv_estimate`
    # was computed once and never updated, so every pass called _solve_draw with
    # identical arguments, reproduced pass 1 exactly, and always broke on the
    # tolerance test -- leaving the "did not stabilise" branch unreachable.
    # Removing it halves the solver calls in a conversion year and moves no
    # projected figure.
    if planned_conversion <= 0.0:
        # Nothing to convert, so the two do not interact at all.
        conversion_cap: float | None = None
        draw = _solve_draw(baseline, None)
    else:
        conversion_cap = min(planned_conversion, max(0.0, _base_headroom()))
        draw = _solve_draw(_no_draw_outcome(conversion_cap), conversion_cap)

    # solve_waterfall returns a single combined roth_draw; split it against
    # the two Roth balances (your first, spouse absorbs the remainder) since
    # _project_year needs the per-owner amounts to reduce each balance.
    your_roth_draw = min(draw.roth_draw, baseline.yr.your_roth_begin)
    spouse_roth_draw = draw.roth_draw - your_roth_draw

    final_inherited = list(inherited_balances)
    outcome = _project_year(
        yr_idx,
        hh,
        plan,
        cpi,
        ytd,
        net_inv_income,
        surv,
        your_ira,
        spouse_ira,
        your_roth,
        spouse_roth,
        prev_your_ira_begin,
        prev_spouse_ira_begin,
        brokerage,
        rollover_done,
        magi_history,
        final_inherited,
        brokerage_basis=brokerage_basis,
        forced_brokerage_draw=draw.brokerage_draw,
        forced_your_ira_draw=draw.your_ira_draw,
        forced_spouse_ira_draw=draw.spouse_ira_draw,
        forced_your_roth_draw=your_roth_draw,
        forced_spouse_roth_draw=spouse_roth_draw,
        forced_early_withdrawal_penalty=draw.early_withdrawal_penalty,
        conversion_cap=conversion_cap,
    )
    # The solver's own residual/convergence flag is authoritative -- it
    # accounts for the brokerage + IRA + Roth allocation as a whole, which
    # _project_year (funding one account at a time) cannot re-derive.
    outcome.yr.unfunded_need = draw.unfunded
    outcome.yr.waterfall_converged = draw.converged
    outcome.yr.waterfall_ira_leg_saturated = draw.ira_leg_saturated
    inherited_balances[:] = final_inherited
    return outcome


def run_scenario(
    hh: Household,
    plan: ConversionPlan,
    name: str = "Scenario",
    end_age: int = 95,
    ytd: YTDSnapshot | None = None,
    net_inv_income: float = 0.0,
) -> ScenarioResult:
    """
    Run a full projection from base_year through end_age.

    Phase 1 (your_age < hh.your_rmd_start_age): Conversion years — you and/or spouse convert
    Phase 2 (your_age >= hh.your_rmd_start_age): RMD years — forced distributions, spouse may still convert
    """
    results = []
    cpi = hh.cpi_assumption
    your_ira = hh.your_ira
    spouse_ira = hh.spouse_ira
    your_roth = hh.your_roth
    spouse_roth = hh.spouse_roth
    # ira-rmd-1: seed prior-year balance with hh.your_ira when defer_first_rmd is
    # elected. Without this, prev_*_ira_begin stays 0.0 when base_year age ==
    # rmd_start_age+1 (the doubled year), and calc_rmd's prior_year_balance > 0
    # guard (ira.py:91) silently suppresses the deferred prior-year RMD term.
    prev_your_ira_begin = hh.your_ira if hh.your_defer_first_rmd else 0.0
    prev_spouse_ira_begin = hh.spouse_ira if hh.spouse_defer_first_rmd else 0.0
    brokerage = hh.brokerage_start
    # Derived cost basis of the brokerage balance (bookkeeping only -- see
    # Household.brokerage_start_basis docstring). None resolves to full basis.
    brokerage_basis = (
        hh.brokerage_start_basis
        if hh.brokerage_start_basis is not None
        else hh.brokerage_start
    )
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
        # IRA-withdrawal-waterfall (stage 3b): baseline call with every hook
        # at its inert default. inherited_balances mutates cumulatively per
        # call (SECURE Act drain), so the baseline gets its own copy -- it is
        # only committed to the real list if it turns out to BE this year's
        # final outcome (income_needed <= 0, no solver call needed).
        _inherited_baseline = list(inherited_balances)
        outcome = _project_year(
            yr_idx,
            hh,
            plan,
            cpi,
            ytd,
            net_inv_income,
            surv,
            your_ira,
            spouse_ira,
            your_roth,
            spouse_roth,
            prev_your_ira_begin,
            prev_spouse_ira_begin,
            brokerage,
            _rollover_done,
            magi_history,
            _inherited_baseline,
            brokerage_basis=brokerage_basis,
        )
        if outcome.yr.income_needed > 0:
            outcome = _solve_waterfall_year(
                yr_idx,
                hh,
                plan,
                cpi,
                ytd,
                net_inv_income,
                surv,
                your_ira,
                spouse_ira,
                your_roth,
                spouse_roth,
                prev_your_ira_begin,
                prev_spouse_ira_begin,
                brokerage,
                brokerage_basis,
                _rollover_done,
                magi_history,
                inherited_balances,
                outcome,
            )
        else:
            inherited_balances[:] = _inherited_baseline
        yr = outcome.yr
        your_ira = outcome.your_ira
        spouse_ira = outcome.spouse_ira
        your_roth = outcome.your_roth
        spouse_roth = outcome.spouse_roth
        brokerage = outcome.brokerage
        brokerage_basis = outcome.brokerage_basis
        prev_your_ira_begin = outcome.prev_your_ira_begin
        prev_spouse_ira_begin = outcome.prev_spouse_ira_begin
        _rollover_done = outcome.rollover_done
        ya = outcome.ya
        sa = outcome.sa

        # Accumulate totals
        cum_conv_tax += yr.conversion_tax
        cum_irmaa += yr.irmaa_cost
        cum_aca += yr.aca_loss
        cum_niit += yr.niit_cost
        if (
            ya >= hh.your_rmd_start_age
            or sa >= hh.spouse_rmd_start_age
            or yr.extra_withdrawal > 0
            or yr.spouse_extra_withdrawal > 0
        ):
            # Exclude conversion_tax from the RMD-phase accumulator so total_rmd_tax
            # reflects only the pure RMD burden.  In overlap years (RMD fires while
            # a spouse is still converting), federal_tax_amt already absorbs
            # conversion_tax; cum_conv_tax also accumulates it — subtracting here
            # ensures each year's conversion tax is counted exactly once across
            # total_rmd_tax + total_conv_tax (audit 0705 #views-financial-5).
            # scenario-core-4: federal_tax_amt includes extra_withdrawal_tax (elective
            # bracket-fill withdrawals); this is intentional grouping — the total
            # lifetime tax is unaffected.  extra_withdrawal_tax is NOT separately
            # tracked, so it lands in cum_rmd_tax rather than cum_conv_tax.
            # audit-2026-07-13 defect A: extra_withdrawal has no age gate in the
            # engine (it can fire pre-RMD), but this accumulator originally only
            # fired on the RMD-age gate — a PRE-RMD extra_withdrawal's tax impact
            # (correctly present in yr.federal_tax_amt) was silently dropped from
            # the lifetime total. The OR-clauses above ensure any year with a
            # non-zero extra_withdrawal is also swept in, without double-adding
            # RMD-phase years (single `if`/single addition per year either way).
            cum_rmd_tax += yr.federal_tax_amt - yr.conversion_tax
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
    hh: Household, end_age: int = 95, ytd: YTDSnapshot | None = None
) -> ScenarioResult:
    """Baseline scenario: no conversions at all."""
    return run_scenario(hh, ConversionPlan(), "No Conversion", end_age, ytd=ytd)
