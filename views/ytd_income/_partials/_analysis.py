from datetime import date as _date

import pandas as pd
import streamlit as st

from engine.dividend_breakdown import qualified_breakdown
from engine.dividend_forecast import forecast_portfolio
from engine.headroom import compute_headroom
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE, _index_irmaa_tiers, irmaa_surcharge
from engine.niit import NIIT_RATE, NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE
from engine.portfolio_sync.holdings import positions_for_forecast_multi
from engine.scenario_compute import social_security_benefits
from engine.tax import (
    LTCG_RATES_MFJ,
    SafeHarborGuidance,
    YTDTaxEstimate,
    estimate_ytd_federal_tax,
    load_prior_year_agi,
    load_prior_year_federal_tax,
    safe_harbor_payment,
)
from engine.tax_return_pdf import load_pdf_tax_records
from engine.this_year import draw_aware_bracket_rooms
from models.household import Household
from models.ytd_income import YTDSnapshot
from views._format import fmt_dollars, fmt_dollars_short, fmt_pct
from views.optimizer_panel import manual_nii


def irmaa_not_yet_notice(hh: Household, headroom) -> str:
    """Notice text for 'IRMAA not yet relevant', naming whoever sets the first year.

    Mirrors engine.headroom: for MFJ the first relevant year is driven by the
    older spouse (min years-to-Medicare over both); otherwise by you.
    """
    first_year = headroom.irmaa_first_relevant_year
    elapsed = first_year - hh.base_year
    spouse_drives = hh.filing_status == "MFJ" and hh.spouse_age > hh.your_age
    who, age = ("Your spouse is", hh.spouse_age) if spouse_drives else ("You are", hh.your_age)
    age_label = "spouse age" if spouse_drives else "age"
    return (
        f"**IRMAA does not apply to {hh.base_year} income.** "
        f"{who} {age} — Medicare starts at 65 with a 2-year lookback. "
        f"IRMAA first matters for income year **{first_year}** "
        f"({age_label} {age + elapsed})."
    )


def ytd_combined_ss(hh: Household) -> float:
    """Combined base-year Social Security, from the engine's own rule (COLA
    compounds from the base year; audit-1026 F-06)."""
    return sum(
        social_security_benefits(
            hh, hh.your_age, hh.spouse_age, survivor_active=False, who_dies=None, death_year=None
        )
    )


def ytd_bracket_rooms(hh: Household, ytd: YTDSnapshot) -> tuple[float, float]:
    """(room to 12%, room to 22%) as Room This Year shows them: the full-year
    projection, so they equal what "Fill X%" converts in every year (issue #5,
    audit-1018 F-03/F-06)."""
    return draw_aware_bracket_rooms(hh, ytd, manual_nii())


def _render_qualified_breakdown() -> None:
    """Per-ticker qualified-vs-ordinary table from the forward dividend forecast."""
    snap = st.session_state.get("portfolio_snapshot")
    accounts = snap.brokerage_accounts if snap else []
    if not accounts or snap.brokerage_total <= 0:
        return
    fcst = forecast_portfolio(
        positions_for_forecast_multi(accounts), total_balance=snap.brokerage_total
    )
    bd = qualified_breakdown(fcst.per_position)
    if not bd.rows:
        return
    with st.expander("Qualified vs ordinary by ticker"):
        st.caption("Forward annual estimate from the dividend forecast, not YTD actuals.")
        rows = [
            {
                "Ticker": r.ticker,
                "Annual dividends": fmt_dollars(r.annual_div),
                "Qualified": fmt_dollars(r.qualified),
                "Ordinary": fmt_dollars(r.ordinary),
                "Qualified %": fmt_pct(r.qualified_pct),
            }
            for r in bd.rows
        ]
        rows.append(
            {
                "Ticker": "Total",
                "Annual dividends": fmt_dollars(bd.total_annual_div),
                "Qualified": fmt_dollars(bd.total_qualified),
                "Ordinary": fmt_dollars(bd.total_ordinary),
                "Qualified %": fmt_pct(bd.total_qualified_pct),
            }
        )
        st.dataframe(rows, width="stretch", hide_index=True)


def render_analysis_partial(hh: Household, ytd: YTDSnapshot) -> None:
    # Gain events drill-down
    if ytd.gain_events:
        with st.expander(f"Realized Gain Events ({len(ytd.gain_events)})"):
            events_data = []
            for e in ytd.gain_events:
                events_data.append(
                    {
                        "Date": e.date,
                        "Description": e.description,
                        "Account": e.account_name,
                        "Proceeds": fmt_dollars(e.proceeds),
                        "Basis": fmt_dollars(e.cost_basis),
                        "Gain/Loss": fmt_dollars(e.gain_loss),
                        "Type": "LTCG" if e.is_ltcg else "STCG",
                    }
                )
            st.dataframe(pd.DataFrame(events_data), width="stretch")

    # --- Section 2: Conversion Headroom ---
    st.markdown("---")
    st.markdown("### Conversion Headroom")

    headroom = compute_headroom(hh, ytd, filing_status=hh.filing_status)

    # Summary metrics
    st.markdown("#### Current YTD Position (Locked In)")
    m1, m2, m3 = st.columns(3)
    m1.metric("Locked MAGI (YTD actuals)", fmt_dollars(headroom.locked_magi))
    m2.metric("of which LTCG", fmt_dollars(headroom.ytd_ltcg))
    m3.metric("Conversions Done", fmt_dollars(headroom.conversions_done))

    # Surface dividend/interest impact on conversion headroom (PR #95).
    # Qualified divs hit MAGI only (IRMAA/NIIT/ACA); ordinary divs + interest
    # hit BOTH ordinary brackets AND MAGI.
    if ytd.qualified_dividends_ytd or ytd.ordinary_dividends_ytd or ytd.interest_ytd:
        st.caption("Investment income impacting headroom")
        dq, do, di = st.columns(3)
        dq.metric(
            "Qualified dividends (YTD)",
            fmt_dollars(ytd.qualified_dividends_ytd),
            help="LTCG-rate taxed. Reduces MAGI room (IRMAA / NIIT / ACA) but NOT ordinary-bracket conversion room.",
        )
        do.metric(
            "Ordinary dividends (YTD)",
            fmt_dollars(ytd.ordinary_dividends_ytd),
            help="Ordinary-rate taxed. Reduces BOTH ordinary-bracket AND MAGI conversion room.",
        )
        di.metric(
            "Interest (YTD)",
            fmt_dollars(ytd.interest_ytd),
            help="Ordinary-rate taxed. Reduces BOTH ordinary-bracket AND MAGI conversion room.",
        )
        _render_qualified_breakdown()

    # NQO exercises YTD (FinExtract sync, PR3 of finextract-nqo-exercises)
    if ytd.nqo_exercise_ytd or getattr(ytd, "_option_exercises_by_grant", None):
        st.metric(
            "NQO exercises (YTD)",
            fmt_dollars(ytd.nqo_exercise_ytd),
            help=(
                "Realized NQO ordinary-income spread from FinExtract equity_compensation. "
                "Subtracted from planned option income in the conversion-room calc."
            ),
        )
        captured = st.session_state.get("exercises_captured_at", "")
        if captured:
            st.caption(f"Exercises last captured: {captured}")
        by_grant: dict[str, float] = getattr(ytd, "_option_exercises_by_grant", {}) or {}
        if by_grant:
            with st.expander("Per-grant breakdown"):
                rows = []
                # Build lookup: grant_id -> StockGrant
                grants_by_id = {
                    g.grant_id: g for g in (hh.grants or []) if getattr(g, "grant_id", "")
                }
                sale_info_map: dict = getattr(ytd, "_option_exercises_sale_info", {}) or {}
                for grant_id, spread in by_grant.items():
                    g = grants_by_id.get(grant_id)
                    if g:
                        rows.append(
                            {
                                "Grant #": grant_id,
                                "YTD spread": fmt_dollars(spread),
                                "Year": str(g.year),
                                "Strike": fmt_dollars(g.strike, decimals=2),
                                "Shares": str(g.shares),
                                "Expiry": str(g.expiry_year),
                            }
                        )
                    else:
                        sale_info = sale_info_map.get(grant_id, {})
                        rows.append(
                            {
                                "Grant #": grant_id,
                                "YTD spread": fmt_dollars(spread),
                                "Year": str(sale_info.get("grant_year") or "—"),
                                "Strike": fmt_dollars(sale_info["strike"], decimals=2)
                                if sale_info.get("strike")
                                else "—",
                                "Shares": str(sale_info.get("shares_ytd") or "—"),
                                "Expiry": "—",
                            }
                        )
                st.dataframe(rows, width="stretch", hide_index=True)
                unmatched = sum(1 for r in rows if r["Expiry"] == "—")
                if unmatched:
                    st.caption(
                        f"⚠️ {unmatched} grant(s) shown from sale data only; not joined to household "
                        "StockGrant (check .user_defaults.json grant strikes)."
                    )

    if headroom.planned_option_income > 0:
        st.caption(
            f"Option exercise ({hh.base_year}): **{fmt_dollars(headroom.planned_option_income)}** — "
            "this is a choice, not locked in. Headroom shown below excludes it."
        )
        if headroom.realized_option_income_ytd:
            st.caption(
                f"Planned reflects {fmt_dollars(headroom.realized_option_income_ytd)} already realized YTD "
                f"(of {fmt_dollars(headroom.planned_option_income + headroom.realized_option_income_ytd)} "
                "originally planned)."
            )

    # --- Section A: Realized Capital Gains ---
    st.markdown("---")
    st.subheader("Realized Capital Gains (YTD)")
    if not ytd.gain_events:
        st.caption(
            "No realized gains synced yet. Import a statement on Data › Import Statements to populate."
        )
    else:
        cg1, cg2 = st.columns(2)
        cg1.metric(
            "Long-term gains",
            fmt_dollars(ytd.ltcg_ytd),
            help="Preferential rate (0/15/20%)",
        )
        cg2.metric(
            "Short-term gains",
            fmt_dollars(ytd.stcg_ytd),
            help="Ordinary-income rate; stacks into brackets",
        )
        by_source: dict[str, dict[str, float]] = {}
        for e in ytd.gain_events:
            src = e.account_name or "unknown"
            by_source.setdefault(src, {"long": 0.0, "short": 0.0})
            if e.is_ltcg:
                by_source[src]["long"] += e.gain_loss
            else:
                by_source[src]["short"] += e.gain_loss
        if by_source:
            with st.expander("Breakdown by source"):
                gain_rows = [
                    {
                        "Source": str(src),
                        "Long-term": fmt_dollars(v["long"]),
                        "Short-term": fmt_dollars(v["short"]),
                    }
                    for src, v in sorted(by_source.items())
                ]
                st.dataframe(gain_rows, width="stretch", hide_index=True)

    # --- Section B: Tax Bracket Position ---
    # --- Section C: Estimated YTD Federal Tax ---
    # F20: pass combined annual SS so taxable_ss() applies IRC §86 cap.
    # Apply COLA growth for years already collecting so the YTD estimate reflects
    # the actual current-year benefit, not the bare at-claim-age amount.
    _combined_ss = ytd_combined_ss(hh)
    estimate: YTDTaxEstimate = estimate_ytd_federal_tax(ytd, hh, combined_ss=_combined_ss)

    st.markdown("---")
    st.subheader("Tax Bracket Position")
    b1, b2, b3 = st.columns(3)
    b1.metric(
        "Current bracket",
        fmt_pct(estimate.marginal_bracket_pct, 0),
        help=f"Marginal {hh.filing_status} tax bracket your next dollar of ordinary income falls into.",
    )
    b2.metric(
        "Room to next bracket",
        fmt_dollars(estimate.room_to_next_bracket),
        help="Additional ordinary income before pushing into the next bracket.",
    )
    b3.metric(
        "Effective rate (so far)",
        fmt_pct(estimate.effective_rate),
        help=(
            "Estimated total tax divided by MAGI. "
            "Lower than marginal because preferential LTCG rate is averaged in."
        ),
    )

    st.markdown("---")
    st.subheader("Estimated YTD Federal Tax")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Ordinary bracket tax", fmt_dollars(estimate.ordinary_tax))
    t2.metric("LTCG / qualified div tax", fmt_dollars(estimate.ltcg_tax))
    t3.metric(f"NIIT ({fmt_pct(NIIT_RATE)})", fmt_dollars(estimate.niit))
    t4.metric("Total federal", fmt_dollars(estimate.total))
    st.caption(
        "Estimate assumes today were Dec 31 (current YTD income only — not annualized). "
        "Standard deduction and OBBBA senior bonus are applied. "
        "Excludes state tax, IRMAA, and quarterly underpayment penalties."
    )

    # --- Section D: Mid-Year Safe-Harbor Payment ---
    st.markdown("---")
    st.subheader("Mid-Year Safe-Harbor Payment Guidance")
    # The snapshot's own tax year, not today's calendar year: viewed on Jan 1-15 a
    # prior-year snapshot is still the one whose Q4 installment is due.
    prior_year = ytd.tax_year - 1
    # §6654(d)(1)(B): the safe harbor is the PRECEDING year's tax — read that
    # year's 1040 only, never an older/newer return that happens to be cached.
    prior_year_tax = load_prior_year_federal_tax(prior_year)
    # Already paid = W-2 federal withholding + quarterly estimated tax payments
    # (Form 1040-ES). Withholding-only understated "already paid" to $0 for
    # anyone who pays estimated tax instead of (or in addition to) withholding,
    # showing a full balance due even after real payments were made.
    already_paid = float(ytd.federal_withholding_ytd) + float(ytd.estimated_payments_ytd)
    # Prior-year AGI governs the 100% vs 110% safe-harbor rule (§6654(d)(1)(C)): the AGI
    # shown on the prior-year 1040. Fall back to the cached prior-year MAGI (AGI + tax-exempt
    # interest) only when that return's AGI is not on file, and say so; None when both are
    # unknown → the engine assumes 110% and labels it.
    prior_year_agi = load_prior_year_agi(prior_year)
    agi_is_proxy = False
    if prior_year_agi is None:
        prior_year_agi = hh.prior_year_magi.get(prior_year)
        agi_is_proxy = prior_year_agi is not None
    guidance: SafeHarborGuidance = safe_harbor_payment(
        prior_year_tax=prior_year_tax,
        current_year_estimate=estimate.total,
        already_paid_ytd=already_paid,
        payment_date=_date.today().isoformat(),
        prior_year_agi=prior_year_agi,
        filing_status=hh.filing_status,
        tax_year=ytd.tax_year,
    )
    if prior_year_tax == 0:
        _pdf_records = load_pdf_tax_records()
        if prior_year in _pdf_records:
            st.warning(
                "Prior year tax unknown — only current-year estimate path active. "
                f"Your {prior_year} 1040 was imported, but it did not yield Line 24 "
                "(Total tax), so the prior-year safe-harbor rule stays locked. Line 24 is "
                "on **page 2** of the federal 1040 — re-import the complete federal "
                "return, not just page 1."
            )
        elif _pdf_records:
            _years = ", ".join(str(y) for y in sorted(_pdf_records))
            st.warning(
                "Prior year tax unknown — only current-year estimate path active. "
                f"The safe harbor uses your {prior_year} return, but only other years "
                f"are imported ({_years}). Upload your {prior_year} 1040 PDF on "
                "Data › Import Statements to unlock the prior-year rule."
            )
        else:
            st.warning(
                "Prior year tax unknown — only current-year estimate path active. "
                "Upload your prior year 1040 PDF on Data › Import Statements to unlock "
                "the 110% safe-harbor rule."
            )
    g1, g2, g3 = st.columns(3)
    g1.metric(
        "Safe-harbor target",
        fmt_dollars(guidance.safe_harbor_target),
        help=guidance.rule_used,
    )
    g2.metric(
        "Already paid YTD",
        fmt_dollars(guidance.already_paid_ytd),
        help="Federal tax withholding plus quarterly estimated tax payments (Form 1040-ES) made year-to-date.",
    )
    g3.metric(
        f"Remaining to pay by {guidance.next_quarterly_due}",
        fmt_dollars(guidance.remaining_to_pay),
        help="Pay this before the next quarterly deadline to maintain safe-harbor protection.",
    )
    if agi_is_proxy:
        st.caption(
            f"Your {prior_year} AGI is not on an imported 1040, so cached MAGI is used as a "
            "proxy for the 100% / 110% test. MAGI adds tax-exempt interest, so it can apply "
            f"110% when AGI is just under the limit — import the {prior_year} 1040 to use AGI."
        )

    st.markdown("#### Room for Conversions")

    room_12, room_22 = ytd_bracket_rooms(hh, ytd)
    c1, c2, c3, c4 = st.columns(4)
    _bracket_help = (
        "Ordinary bracket room — LTCG does NOT consume this. Full-year projection: "
        "counts forced IRA draws, forecast RMD, dividends, scheduled options and the "
        'senior-bonus phase-out, so it equals "Fill to X%". Room to IRMAA / NIIT '
        "below are from locked income only."
    )
    c1.metric("Room to 12%", fmt_dollars(room_12), help=_bracket_help)
    c2.metric("Room to 22%", fmt_dollars(room_22), help=_bracket_help)

    # Prior-year MAGI anchor for IRMAA 2-year lookback
    prior_magi = st.session_state.get("prior_year_magi") or {}
    if prior_magi:
        sorted_years = sorted(prior_magi.keys(), reverse=True)
        most_recent = sorted_years[0]
        st.caption(
            f"Prior-year MAGI anchor ({most_recent}): {fmt_dollars(prior_magi[most_recent])}"
            " — used for IRMAA 2-year lookback"
        )

    # IRMAA — show room but note if not yet relevant
    irmaa_label = "Room to IRMAA"
    if not headroom.irmaa_relevant:
        irmaa_label += f" (matters from {headroom.irmaa_first_relevant_year})"
    c3.metric(
        irmaa_label,
        fmt_dollars(headroom.room_to_irmaa_t1),
        delta="TRIGGERED" if headroom.irmaa_already_triggered else None,
        delta_color="inverse" if headroom.irmaa_already_triggered else "off",
        help="MAGI-based — LTCG DOES consume this. "
        + (
            f"Not relevant until {headroom.irmaa_first_relevant_year} income year "
            f"(Medicare starts at 65, 2-year lookback)."
            if not headroom.irmaa_relevant
            else ""
        ),
    )
    _niit_thr = NIIT_THRESHOLD_SINGLE if hh.filing_status == "Single" else NIIT_THRESHOLD_MFJ
    c4.metric(
        "Room to NIIT",
        fmt_dollars(headroom.room_to_niit),
        help=f"MAGI-based ({fmt_dollars_short(_niit_thr, decimals=0, suffix='K')}) — LTCG DOES consume this",
    )

    if not headroom.irmaa_relevant:
        st.info(irmaa_not_yet_notice(hh, headroom))

    # Show with-planned comparison if there's option income
    if headroom.planned_option_income > 0:
        with st.expander("If you also exercise options this year"):
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Room to 12%", fmt_dollars(headroom.room_to_12pct_with_planned))
            p2.metric("Room to 22%", fmt_dollars(headroom.room_to_22pct_with_planned))
            p3.metric("Room to IRMAA", fmt_dollars(headroom.room_to_irmaa_t1_with_planned))
            p4.metric("Room to NIIT", fmt_dollars(headroom.room_to_niit_with_planned))
            st.caption("These bracket rooms do not account for forced living-expense IRA draws.")

    # Visual explanation
    st.info(
        f"**Why bracket room differs from IRMAA/NIIT room**: Long-term capital gains are taxed at "
        f"preferential rates ({fmt_pct(LTCG_RATES_MFJ[1], 0)}) and do NOT stack into ordinary brackets. But they DO count "
        f"toward MAGI for IRMAA surcharges and NIIT. So $200K in LTCG can consume IRMAA/NIIT "
        f"room while leaving your 12%/22% bracket room completely untouched."
    )

    # --- Section 3: IRMAA Impact Warning ---
    if headroom.irmaa_already_triggered:
        st.markdown("---")
        st.markdown("### IRMAA Impact Warning")
        st.error(
            f"**IRMAA Tier {headroom.irmaa_tier_current} already triggered** "
            f"with projected MAGI of {fmt_dollars(headroom.projected_magi_base)}.\n\n"
            f"This means 2-year lookback will affect **{hh.base_year + 2} Medicare premiums**."
        )

        # IRMAA 2-year lookback: the surcharge shown is what will be charged in
        # base_year + 2 Medicare premiums, so index the MAGI thresholds to the
        # payment year (base_year + 2), not the income year.
        surcharge_1p = irmaa_surcharge(
            headroom.projected_magi_base,
            1,
            filing_status=hh.filing_status,
            year=hh.base_year + 2,
            cpi=hh.cpi_assumption,
        )
        surcharge_2p = irmaa_surcharge(
            headroom.projected_magi_base,
            2,
            filing_status=hh.filing_status,
            year=hh.base_year + 2,
            cpi=hh.cpi_assumption,
        )

        s1, s2 = st.columns(2)
        s1.metric(
            "Annual Surcharge (1 person on Medicare)",
            fmt_dollars(surcharge_1p),
        )
        s2.metric(
            "Annual Surcharge (2 people on Medicare)",
            fmt_dollars(surcharge_2p),
        )

        # Tier table
        with st.expander("IRMAA Tier Details"):
            _base_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
            _irmaa_tiers = _index_irmaa_tiers(
                _base_tiers, year=hh.base_year + 2, cpi=hh.cpi_assumption
            )
            tier_data = []
            for i, (threshold, part_b, part_d) in enumerate(_irmaa_tiers, 1):
                tier_data.append(
                    {
                        "Tier": i,
                        "MAGI Threshold": fmt_dollars(threshold),
                        "Part B (annual/person)": fmt_dollars(part_b),
                        "Part D Surcharge (annual/person)": fmt_dollars(part_d),
                    }
                )
            st.dataframe(pd.DataFrame(tier_data), width="stretch")

    # --- Section 4: Integration status (toggle lives in the sidebar, redesign PR 2) ---
    st.markdown("---")
    st.markdown("### Integration with projections")
    if st.session_state.get("apply_ytd_to_projection", False):
        st.success(
            f"These YTD actuals are included in the {hh.base_year} projections on every "
            "page. Turn off **Include this year's actuals** in the sidebar to exclude them."
        )
    else:
        st.info(
            f"These YTD actuals are NOT included in the {hh.base_year} projections. "
            "Turn on **Include this year's actuals** in the sidebar."
        )
