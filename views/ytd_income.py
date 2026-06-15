"""YTD Income Tracker & Conversion Headroom Calculator.

Shows real-world mid-year income events (stop-loss triggers, wages, etc.)
and computes remaining headroom for Roth conversions against bracket,
IRMAA, NIIT, and ACA thresholds.

Key insight: LTCG consumes IRMAA/NIIT room but NOT ordinary bracket room.
"""

from datetime import date as _date

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import is_pyodide
from engine.headroom import compute_headroom
from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE, irmaa_surcharge
from engine.niit import NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE
from engine.tax import (
    LTCG_RATES_MFJ,
    SafeHarborGuidance,
    YTDTaxEstimate,
    estimate_ytd_federal_tax,
    load_prior_year_federal_tax,
    safe_harbor_payment,
)
from models.household import Household
from models.ytd_income import YTDSnapshot
from views._format import fmt_dollars, fmt_dollars_short, fmt_pct


def _color_for_room(room: float) -> str:
    if room <= 0:
        return "inverse"  # red
    if room <= 50_000:
        return "off"  # orange-ish (streamlit uses "off" for warning-style)
    return "normal"  # green


def _metric_delta_color(room: float) -> str:
    if room <= 0:
        return "inverse"
    return "normal"


def render(hh: Household):
    st.title("YTD Income & Conversion Headroom")
    st.caption(
        "Track mid-year income events and see how much Roth conversion room remains. "
        "LTCG from stop-loss triggers consumes IRMAA room but leaves bracket room intact."
    )

    # --- Section 1: YTD Income Entry ---
    st.markdown("### YTD Income Entry")

    if is_pyodide():
        st.caption(
            "Live sync requires a local install. "
            "Use the Setup → Data Bridge tab to upload a snapshot."
        )
    else:
        col_sync, col_status = st.columns([1, 3])
        with col_sync:
            sync_ytd = st.button(
                "Sync from FinExtract",
                help="Pull realized gains and YTD income from ingestion server",
                key="ytd_sync_btn",
            )
        if sync_ytd:
            from engine.portfolio_sync import (
                apply_option_exercises,
                fetch_option_exercises,
                fetch_ytd_snapshot,
                save_ytd_snapshot,
            )

            ytd_snap = fetch_ytd_snapshot()
            exercises = fetch_option_exercises()
            if exercises.server_available:
                ytd_snap = apply_option_exercises(ytd_snap, exercises, hh)
            if ytd_snap.snapshot_date:
                st.session_state.ytd_snapshot = ytd_snap
                save_ytd_snapshot(ytd_snap)
                with col_status:
                    st.success(f"Synced YTD data ({len(ytd_snap.gain_events)} gain events)")
                # Auto-deselect manual entry so the page switches to synced-data display
                st.session_state["ytd_manual_entry"] = False
                st.rerun()
            else:
                with col_status:
                    st.warning("FinExtract unavailable — use manual entry below")

    manual = st.checkbox(
        "Manual entry",
        value=st.session_state.get("ytd_manual_entry", True),
        key="ytd_manual_entry",
    )

    # Get existing snapshot or create empty
    ytd: YTDSnapshot = st.session_state.get("ytd_snapshot", YTDSnapshot())

    if manual:
        col1, col2, col3 = st.columns(3)
        with col1:
            wages = st.number_input(
                "Wages YTD",
                value=int(ytd.wages_ytd),
                step=5_000,
                format="%d",
            )
            ltcg = st.number_input(
                "Long-Term Capital Gains YTD",
                value=int(ytd.ltcg_ytd) if ytd.ltcg_ytd > 0 else 0,
                step=10_000,
                format="%d",
                help="From stop-loss triggers, mutual fund distributions, etc.",
            )
        with col2:
            stcg = st.number_input(
                "Short-Term Capital Gains YTD",
                value=int(ytd.stcg_ytd),
                step=5_000,
                format="%d",
            )
            div_col1, div_col2 = st.columns(2)
            with div_col1:
                qualified_dividends = st.number_input(
                    "Qualified dividends YTD",
                    value=int(ytd.qualified_dividends_ytd),
                    step=500,
                    format="%d",
                    help=f"Taxed at LTCG rates ({fmt_pct(LTCG_RATES_MFJ[1], 0)}/{fmt_pct(LTCG_RATES_MFJ[2], 0)}); counts toward MAGI but not ordinary brackets.",
                )
            with div_col2:
                ordinary_dividends = st.number_input(
                    "Ordinary dividends YTD",
                    value=int(ytd.ordinary_dividends_ytd),
                    step=500,
                    format="%d",
                    help="Taxed as ordinary income; stacks into brackets and SS taxation.",
                )
        with col3:
            interest = st.number_input(
                "Interest YTD",
                value=int(ytd.interest_ytd),
                step=1_000,
                format="%d",
            )
            conversions_done = st.number_input(
                "Roth Conversions Done YTD",
                value=int(ytd.ira_conversions_ytd),
                step=5_000,
                format="%d",
                help="Conversions already completed this year",
            )

        ytd = YTDSnapshot(
            tax_year=hh.base_year,
            wages_ytd=float(wages),
            ltcg_ytd=float(ltcg),
            stcg_ytd=float(stcg),
            qualified_dividends_ytd=float(qualified_dividends),
            ordinary_dividends_ytd=float(ordinary_dividends),
            interest_ytd=float(interest),
            ira_conversions_ytd=float(conversions_done),
            gain_events=ytd.gain_events,
            manually_entered=True,
        ).with_snapshot_date()

        st.session_state.ytd_snapshot = ytd

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
        st.caption("No realized gains synced yet. Sync from FinExtract to populate.")
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
                st.dataframe(gain_rows, use_container_width=True, hide_index=True)

    # --- Section B: Tax Bracket Position ---
    # --- Section C: Estimated YTD Federal Tax ---
    estimate: YTDTaxEstimate = estimate_ytd_federal_tax(ytd, hh)

    st.markdown("---")
    st.subheader("Tax Bracket Position")
    b1, b2, b3 = st.columns(3)
    b1.metric(
        "Current bracket",
        fmt_pct(estimate.marginal_bracket_pct, 0),
        help="Marginal MFJ tax bracket your next dollar of ordinary income falls into.",
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
    t3.metric("NIIT (3.8%)", fmt_dollars(estimate.niit))
    t4.metric("Total federal", fmt_dollars(estimate.total))
    st.caption(
        "Estimate assumes today were Dec 31 (current YTD income only — not annualized). "
        "Excludes state tax, IRMAA premium impact, and quarterly underpayment penalties. "
        "MFJ standard deduction NOT applied — figures represent gross liability before deductions."
    )

    # --- Section D: Mid-Year Safe-Harbor Payment ---
    st.markdown("---")
    st.subheader("Mid-Year Safe-Harbor Payment Guidance")
    prior_year_tax = load_prior_year_federal_tax()
    # federal_withholding_ytd not yet on YTDSnapshot — treat as 0 until added
    # TODO: add federal_withholding_ytd field to YTDSnapshot
    already_paid = float(getattr(ytd, "federal_withholding_ytd", 0.0))
    guidance: SafeHarborGuidance = safe_harbor_payment(
        prior_year_tax=prior_year_tax,
        current_year_estimate=estimate.total,
        already_paid_ytd=already_paid,
        payment_date=_date.today().isoformat(),
    )
    if prior_year_tax == 0:
        st.warning(
            "Prior year tax unknown — only current-year estimate path active. "
            "Upload your prior year 1040 PDF in Setup → Parameters → Joint to unlock "
            "the 110% safe-harbor rule."
        )
    g1, g2, g3 = st.columns(3)
    g1.metric(
        "Safe-harbor target",
        fmt_dollars(guidance.safe_harbor_target),
        help=guidance.rule_used,
    )
    g2.metric("Already paid YTD", fmt_dollars(guidance.already_paid_ytd))
    g3.metric(
        f"Remaining to pay by {guidance.next_quarterly_due}",
        fmt_dollars(guidance.remaining_to_pay),
        help="Pay this before the next quarterly deadline to maintain safe-harbor protection.",
    )

    st.markdown("#### Room for Conversions (from locked income only)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Room to 12%",
        fmt_dollars(headroom.room_to_12pct),
        help="Ordinary bracket room — LTCG does NOT consume this",
    )
    c2.metric(
        "Room to 22%",
        fmt_dollars(headroom.room_to_22pct),
        help="Ordinary bracket room — LTCG does NOT consume this",
    )

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
        st.info(
            f"**IRMAA does not apply to {hh.base_year} income.** "
            f"You are {hh.your_age} — Medicare starts at 65 with a 2-year lookback. "
            f"IRMAA first matters for income year **{headroom.irmaa_first_relevant_year}** "
            f"(age {hh.your_age + headroom.irmaa_first_relevant_year - hh.base_year})."
        )

    # Show with-planned comparison if there's option income
    if headroom.planned_option_income > 0:
        with st.expander("If you also exercise options this year"):
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Room to 12%", fmt_dollars(headroom.room_to_12pct_with_planned))
            p2.metric("Room to 22%", fmt_dollars(headroom.room_to_22pct_with_planned))
            p3.metric("Room to IRMAA", fmt_dollars(headroom.room_to_irmaa_t1_with_planned))
            p4.metric("Room to NIIT", fmt_dollars(headroom.room_to_niit_with_planned))

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

        # Show surcharge amounts
        surcharge_1p = irmaa_surcharge(
            headroom.projected_magi_base, 1, filing_status=hh.filing_status
        )
        surcharge_2p = irmaa_surcharge(
            headroom.projected_magi_base, 2, filing_status=hh.filing_status
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
            _irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
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

    # --- Section 4: Integration Toggle ---
    st.markdown("---")
    st.markdown("### Integration with Conversion Planner")

    apply_ytd = st.checkbox(
        "Apply YTD actuals to 2026 projection",
        value=st.session_state.get("apply_ytd_to_projection", False),
        help="When enabled, the Conversion Planner page will use these YTD numbers "
        "for the base year instead of projecting from zero.",
    )
    st.session_state.apply_ytd_to_projection = apply_ytd

    if apply_ytd:
        st.success(
            "YTD data will be used in the Conversion Planner. "
            "Switch to that page to see the updated 2026 row."
        )
    else:
        st.info(
            "YTD data is NOT being applied to the Conversion Planner. Toggle above to integrate."
        )

    # Save snapshot for persistence
    from engine.portfolio_sync import save_ytd_snapshot

    save_ytd_snapshot(ytd)
