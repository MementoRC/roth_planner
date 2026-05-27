"""YTD Income Tracker & Conversion Headroom Calculator.

Shows real-world mid-year income events (stop-loss triggers, wages, etc.)
and computes remaining headroom for Roth conversions against bracket,
IRMAA, NIIT, and ACA thresholds.

Key insight: LTCG consumes IRMAA/NIIT room but NOT ordinary bracket room.
"""

import pandas as pd
import streamlit as st

from engine.headroom import compute_headroom
from engine.irmaa import IRMAA_TIERS_MFJ, irmaa_surcharge
from models.household import Household
from models.ytd_income import YTDSnapshot


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

    col_sync, col_status = st.columns([1, 3])
    with col_sync:
        sync_ytd = st.button(
            "Sync from FinExtract",
            help="Pull realized gains and YTD income from ingestion server",
            key="ytd_sync_btn",
        )
    if sync_ytd:
        from engine.portfolio_sync import fetch_ytd_snapshot, save_ytd_snapshot

        ytd_snap = fetch_ytd_snapshot()
        if ytd_snap.snapshot_date:
            st.session_state.ytd_snapshot = ytd_snap
            save_ytd_snapshot(ytd_snap)
            with col_status:
                st.success(f"Synced YTD data ({len(ytd_snap.gain_events)} gain events)")
        else:
            with col_status:
                st.warning("FinExtract unavailable — use manual entry below")

    manual = st.checkbox("Manual entry", value=True)

    # Get existing snapshot or create empty
    ytd: YTDSnapshot = st.session_state.get("ytd_snapshot", YTDSnapshot())

    if manual:
        col1, col2, col3 = st.columns(3)
        with col1:
            wages = st.number_input(
                "Wages YTD", value=int(ytd.wages_ytd), step=5_000, format="%d",
            )
            ltcg = st.number_input(
                "Long-Term Capital Gains YTD",
                value=int(ytd.ltcg_ytd) if ytd.ltcg_ytd > 0 else 0,
                step=10_000, format="%d",
                help="From stop-loss triggers, mutual fund distributions, etc.",
            )
        with col2:
            stcg = st.number_input(
                "Short-Term Capital Gains YTD", value=int(ytd.stcg_ytd), step=5_000, format="%d",
            )
            dividends = st.number_input(
                "Dividends YTD", value=int(ytd.dividends_ytd), step=1_000, format="%d",
            )
        with col3:
            interest = st.number_input(
                "Interest YTD", value=int(ytd.interest_ytd), step=1_000, format="%d",
            )
            conversions_done = st.number_input(
                "Roth Conversions Done YTD", value=int(ytd.ira_conversions_ytd),
                step=5_000, format="%d",
                help="Conversions already completed this year",
            )

        ytd = YTDSnapshot(
            tax_year=hh.base_year,
            wages_ytd=float(wages),
            ltcg_ytd=float(ltcg),
            stcg_ytd=float(stcg),
            ordinary_dividends_ytd=float(dividends),  # TODO(step 4): split qualified vs ordinary in UI
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
                events_data.append({
                    "Date": e.date,
                    "Description": e.description,
                    "Account": e.account_name,
                    "Proceeds": f"${e.proceeds:,.0f}",
                    "Basis": f"${e.cost_basis:,.0f}",
                    "Gain/Loss": f"${e.gain_loss:,.0f}",
                    "Type": "LTCG" if e.is_ltcg else "STCG",
                })
            st.dataframe(pd.DataFrame(events_data), use_container_width=True)

    # --- Section 2: Conversion Headroom ---
    st.markdown("---")
    st.markdown("### Conversion Headroom")

    headroom = compute_headroom(hh, ytd)

    # Summary metrics
    st.markdown("#### Current YTD Position (Locked In)")
    m1, m2, m3 = st.columns(3)
    m1.metric("Locked MAGI (YTD actuals)", f"${headroom.locked_magi:,.0f}")
    m2.metric("of which LTCG", f"${headroom.ytd_ltcg:,.0f}")
    m3.metric("Conversions Done", f"${headroom.conversions_done:,.0f}")

    if headroom.planned_option_income > 0:
        st.caption(
            f"Option exercise ({hh.base_year}): **${headroom.planned_option_income:,.0f}** — "
            "this is a choice, not locked in. Headroom shown below excludes it."
        )

    st.markdown("#### Room for Conversions (from locked income only)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Room to 12%",
        f"${headroom.room_to_12pct:,.0f}",
        help="Ordinary bracket room — LTCG does NOT consume this",
    )
    c2.metric(
        "Room to 22%",
        f"${headroom.room_to_22pct:,.0f}",
        help="Ordinary bracket room — LTCG does NOT consume this",
    )

    # IRMAA — show room but note if not yet relevant
    irmaa_label = "Room to IRMAA"
    if not headroom.irmaa_relevant:
        irmaa_label += f" (matters from {headroom.irmaa_first_relevant_year})"
    c3.metric(
        irmaa_label,
        f"${headroom.room_to_irmaa_t1:,.0f}",
        delta="TRIGGERED" if headroom.irmaa_already_triggered else None,
        delta_color="inverse" if headroom.irmaa_already_triggered else "off",
        help="MAGI-based — LTCG DOES consume this. "
        + (f"Not relevant until {headroom.irmaa_first_relevant_year} income year "
           f"(Medicare starts at 65, 2-year lookback)."
           if not headroom.irmaa_relevant else ""),
    )
    c4.metric(
        "Room to NIIT",
        f"${headroom.room_to_niit:,.0f}",
        help="MAGI-based ($250K) — LTCG DOES consume this",
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
            p1.metric("Room to 12%", f"${headroom.room_to_12pct_with_planned:,.0f}")
            p2.metric("Room to 22%", f"${headroom.room_to_22pct_with_planned:,.0f}")
            p3.metric("Room to IRMAA", f"${headroom.room_to_irmaa_t1_with_planned:,.0f}")
            p4.metric("Room to NIIT", f"${headroom.room_to_niit_with_planned:,.0f}")

    # Visual explanation
    st.info(
        "**Why bracket room differs from IRMAA/NIIT room**: Long-term capital gains are taxed at "
        "preferential rates (15%) and do NOT stack into ordinary brackets. But they DO count "
        "toward MAGI for IRMAA surcharges and NIIT. So $200K in LTCG can consume IRMAA/NIIT "
        "room while leaving your 12%/22% bracket room completely untouched."
    )

    # --- Section 3: IRMAA Impact Warning ---
    if headroom.irmaa_already_triggered:
        st.markdown("---")
        st.markdown("### IRMAA Impact Warning")
        st.error(
            f"**IRMAA Tier {headroom.irmaa_tier_current} already triggered** "
            f"with projected MAGI of ${headroom.projected_magi_base:,.0f}.\n\n"
            f"This means 2-year lookback will affect **{hh.base_year + 2} Medicare premiums**."
        )

        # Show surcharge amounts
        surcharge_1p = irmaa_surcharge(headroom.projected_magi_base, 1)
        surcharge_2p = irmaa_surcharge(headroom.projected_magi_base, 2)

        s1, s2 = st.columns(2)
        s1.metric(
            "Annual Surcharge (1 person on Medicare)",
            f"${surcharge_1p:,.0f}",
        )
        s2.metric(
            "Annual Surcharge (2 people on Medicare)",
            f"${surcharge_2p:,.0f}",
        )

        # Tier table
        with st.expander("IRMAA Tier Details"):
            tier_data = []
            for i, (threshold, part_b, part_d) in enumerate(IRMAA_TIERS_MFJ, 1):
                tier_data.append({
                    "Tier": i,
                    "MAGI Threshold": f"${threshold:,.0f}",
                    "Part B (annual/person)": f"${part_b:,.0f}",
                    "Part D Surcharge (annual/person)": f"${part_d:,.0f}",
                })
            st.dataframe(pd.DataFrame(tier_data), use_container_width=True)

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
            "YTD data is NOT being applied to the Conversion Planner. "
            "Toggle above to integrate."
        )

    # Save snapshot for persistence
    from engine.portfolio_sync import save_ytd_snapshot

    save_ytd_snapshot(ytd)
