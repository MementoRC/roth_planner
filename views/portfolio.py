"""Portfolio — live holdings from FinExtract ingestion server.

Shows synced brokerage holdings, equity compensation, and how the
actual allocation maps to growth rate assumptions in the planner.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.portfolio_sync import EXPECTED_RETURNS, fetch_portfolio
from models.household import Household


def render(hh: Household):
    st.title("Portfolio Sync")
    st.caption(
        "Live data from FinExtract ingestion server. "
        "Click 'Sync from FinExtract' in the sidebar to refresh."
    )

    snap = st.session_state.get("portfolio_snapshot")

    if not snap or not snap.server_available:
        # Try fetching now
        snap = fetch_portfolio()
        if snap.server_available:
            st.session_state.portfolio_snapshot = snap
        else:
            st.warning(
                f"FinExtract server not available. {snap.error or ''}\n\n"
                "Start the ingestion server and click 'Sync from FinExtract' in the sidebar."
            )
            return

    # --- Account Overview ---
    st.markdown("### Account Overview")

    acct_rows = []
    for acct in snap.accounts:
        acct_rows.append({
            "Account": acct.account_type.replace("_", " ").title(),
            "Owner": acct.owner.title(),
            "Total Value": f"${acct.total_value:,.0f}",
            "Equity": f"${acct.equity_value:,.0f}",
            "Bonds": f"${acct.bond_value:,.0f}",
            "Equity %": f"{acct.equity_pct * 100:.0f}%",
            "Expected Return": f"{acct.weighted_return * 100:.1f}%",
        })

    if snap.txn_shares_value > 0:
        acct_rows.append({
            "Account": "TXN Shares (ESPP/RSU)",
            "Owner": "You",
            "Total Value": f"${snap.txn_shares_value:,.0f}",
            "Equity": f"${snap.txn_shares_value:,.0f}",
            "Bonds": "$0",
            "Equity %": "100%",
            "Expected Return": "—",
        })

    st.dataframe(pd.DataFrame(acct_rows), hide_index=True, width="stretch")

    total_val = snap.total_portfolio_value
    total_eq = sum(a.equity_value for a in snap.accounts) + snap.txn_shares_value
    total_bd = sum(a.bond_value for a in snap.accounts)
    st.metric("Total Portfolio", f"${total_val:,.0f}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Equity", f"${total_eq:,.0f}")
    c2.metric("Total Bonds", f"${total_bd:,.0f}")
    c3.metric("Overall Equity %", f"{total_eq / total_val * 100:.0f}%" if total_val > 0 else "—")

    # --- Holdings Detail ---
    st.markdown("### Holdings")

    holdings_rows = []
    for acct in snap.accounts:
        for h in acct.holdings:
            holdings_rows.append({
                "Account": acct.account_type.replace("_", " ").title(),
                "Symbol": h.symbol,
                "Description": h.description,
                "Shares": f"{h.quantity:,.1f}",
                "Value": f"${h.market_value:,.0f}",
                "Class": h.asset_class.title(),
                "Gain/Loss": f"${h.total_gain_loss:,.0f}" if h.total_gain_loss is not None else "—",
            })

    st.dataframe(pd.DataFrame(holdings_rows), hide_index=True, width="stretch")

    # --- Allocation Pie ---
    st.markdown("### Allocation by Account")

    fig_alloc = go.Figure()
    labels = []
    values = []
    colors = []
    color_map = {"equity": "#22c55e", "bond": "#60a5fa"}

    for acct in snap.accounts:
        acct_label = acct.account_type.replace("_", " ").title()
        if acct.equity_value > 0:
            labels.append(f"{acct_label} — Equity")
            values.append(acct.equity_value)
            colors.append(color_map["equity"])
        if acct.bond_value > 0:
            labels.append(f"{acct_label} — Bonds")
            values.append(acct.bond_value)
            colors.append(color_map["bond"])

    if snap.txn_shares_value > 0:
        labels.append("TXN Shares")
        values.append(snap.txn_shares_value)
        colors.append("#f59e0b")

    fig_alloc.add_trace(go.Pie(
        labels=labels,
        values=values,
        marker={"colors": colors},
        hole=0.4,
        textinfo="label+percent",
    ))
    fig_alloc.update_layout(height=400, showlegend=False)
    st.plotly_chart(fig_alloc, width="stretch")

    # --- Active Equity Grants ---
    if snap.equity_grants:
        st.markdown("### Active Stock Option Grants (NQO)")

        grant_rows = []
        for g in snap.equity_grants:
            grant_rows.append({
                "Grant ID": g.grant_id,
                "Type": g.grant_type,
                "Grant Date": g.grant_date,
                "Granted": f"{g.shares_granted:,}",
                "Outstanding": f"{g.outstanding:,}",
                "Current Value": f"${g.current_value:,.0f}",
            })

        st.dataframe(pd.DataFrame(grant_rows), hide_index=True, width="stretch")

        # Compare with planner defaults
        st.markdown("#### vs. Planner Defaults")
        plan_grants = hh.grants
        comp_rows = []
        for i, g in enumerate(snap.equity_grants):
            plan = plan_grants[i] if i < len(plan_grants) else None
            comp_rows.append({
                "Source": "FinExtract",
                "Grant": g.grant_date,
                "Outstanding": g.outstanding,
                "Value": f"${g.current_value:,.0f}",
            })
            if plan:
                comp_rows.append({
                    "Source": "Planner Default",
                    "Grant": str(plan.year),
                    "Outstanding": plan.shares,
                    "Value": f"${plan.spread(hh.txn_price_now):,.0f}",
                })

        st.dataframe(pd.DataFrame(comp_rows), hide_index=True, width="stretch")

    # --- TXN Shares ---
    if snap.txn_shares_held > 0:
        st.markdown("### TXN Shares Held (ESPP + RSU)")
        c1, c2 = st.columns(2)
        c1.metric("Shares", f"{snap.txn_shares_held:,}")
        c2.metric("Value", f"${snap.txn_shares_value:,.0f}")

    # --- Growth Rate Mapping ---
    st.markdown("---")
    st.markdown("### Growth Rate Mapping")
    st.caption(
        "How your actual allocation maps to growth assumptions in the planner. "
        f"Expected returns: equity {EXPECTED_RETURNS['equity']*100:.0f}%, "
        f"bond {EXPECTED_RETURNS['bond']*100:.0f}%."
    )

    rate_rows = []
    brok = snap.account_by_type("brokerage")
    if brok and brok.total_value > 0:
        rate_rows.append({
            "Account": "Brokerage",
            "Equity %": f"{brok.equity_pct * 100:.0f}%",
            "Weighted Return": f"{brok.weighted_return * 100:.1f}%",
            "Planner Uses": f"{hh.brokerage_rate(hh.base_year) * 100:.1f}%",
            "Status": "Synced" if hh.brokerage_growth else "Default",
        })

    rate_rows.append({
        "Account": "Your IRA",
        "Equity %": "—",
        "Weighted Return": "—",
        "Planner Uses": f"{hh.your_ira_rate(hh.base_year) * 100:.1f}%",
        "Status": "Synced" if hh.your_ira_growth else "Default",
    })

    rate_rows.append({
        "Account": "Spouse IRA",
        "Equity %": "—",
        "Weighted Return": "—",
        "Planner Uses": f"{hh.spouse_ira_rate(hh.base_year) * 100:.1f}%",
        "Status": "Synced" if hh.spouse_ira_growth else "Default",
    })

    roth = snap.account_by_type("roth_ira")
    if roth and roth.total_value > 0:
        rate_rows.append({
            "Account": "Roth IRA",
            "Equity %": f"{roth.equity_pct * 100:.0f}%",
            "Weighted Return": f"{roth.weighted_return * 100:.1f}%",
            "Planner Uses": "Not modeled",
            "Status": "Info only",
        })

    st.dataframe(pd.DataFrame(rate_rows), hide_index=True, width="stretch")

    st.info(
        "**Note**: IRA holdings are not yet available from FinExtract (Vanguard IRA scraping "
        "not implemented). Once available, IRA growth rates will be auto-calibrated from "
        "your actual allocation. For now, use the sidebar Growth Rate slider as the default."
    )
