"""Portfolio — live holdings from FinExtract ingestion server.

Shows synced brokerage holdings, equity compensation, and how the
actual allocation maps to growth rate assumptions in the planner.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.portfolio_sync import EXPECTED_RETURNS
from models.household import Household


def render(hh: Household):
    st.title("Portfolio Sync")
    st.caption(
        "Cached data from FinExtract ingestion server. "
        "Click 'Sync from FinExtract' in the sidebar to refresh."
    )

    snap = st.session_state.get("portfolio_snapshot")

    if not snap or not snap.server_available:
        st.info(
            "No portfolio data cached yet.\n\n"
            "Click **Sync from FinExtract** in the sidebar to pull live holdings."
        )
        return

    # --- Account Overview ---
    st.markdown("### Account Overview")

    acct_labels = {
        "trad_ira": "Trad IRA",
        "roth_ira": "Roth IRA",
        "403b": "403(b)",
        "hsa": "HSA",
        "brokerage": "Brokerage",
    }

    acct_rows = []
    for acct in snap.accounts:
        label = acct_labels.get(acct.account_type, acct.account_type.title())
        acct_rows.append({
            "Account": f"{label} ({acct.account_name})" if acct.account_name else label,
            "Total Value": f"${acct.total_value:,.0f}",
            "Equity": f"${acct.equity_value:,.0f}",
            "Bonds": f"${acct.bond_value:,.0f}",
            "Cash": f"${acct.cash_value:,.0f}",
            "Crypto": f"${acct.crypto_value:,.0f}",
            "Wtd Return": f"{acct.weighted_return * 100:.1f}%",
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
    total_cash = sum(a.cash_value for a in snap.accounts)
    total_crypto = sum(a.crypto_value for a in snap.accounts)

    st.metric("Total Portfolio", f"${total_val:,.0f}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Equity", f"${total_eq:,.0f}")
    c2.metric("Bonds", f"${total_bd:,.0f}")
    c3.metric("Cash", f"${total_cash:,.0f}")
    c4.metric("Crypto", f"${total_crypto:,.0f}")

    # Pre-tax retirement total (this is what feeds into the planner)
    pretax = snap.pretax_total
    if pretax > 0:
        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        c1.metric("Your Pre-Tax (IRA + 403b)", f"${pretax:,.0f}")
        c2.metric("Pre-Tax Wtd Return", f"{snap.pretax_weighted_return * 100:.1f}%")
        c3.metric("Planner IRA Balance", f"${pretax:,.0f}", help="Auto-synced to 'Your Trad IRA'")

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
    color_map = {
        "equity": "#22c55e",
        "bond": "#60a5fa",
        "cash": "#94a3b8",
        "crypto": "#f59e0b",
        "target_date": "#a78bfa",
    }
    class_attrs = [
        ("equity_value", "Equity"),
        ("bond_value", "Bonds"),
        ("cash_value", "Cash"),
        ("crypto_value", "Crypto"),
        ("target_date_value", "Target Date"),
    ]

    for acct in snap.accounts:
        acct_label = acct_labels.get(acct.account_type, acct.account_type.title())
        for attr, cls_label in class_attrs:
            val = getattr(acct, attr, 0)
            if val > 0:
                labels.append(f"{acct_label} — {cls_label}")
                values.append(val)
                colors.append(color_map.get(attr.replace("_value", ""), "#6b7280"))

    if snap.txn_shares_value > 0:
        labels.append("TXN Shares")
        values.append(snap.txn_shares_value)
        colors.append("#ef4444")

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
    returns_str = ", ".join(f"{k} {v*100:.0f}%" for k, v in EXPECTED_RETURNS.items())
    st.caption(f"Expected returns: {returns_str}.")

    rate_rows = []

    # Your pre-tax IRA (Rollover IRA + 403b combined)
    pretax = snap.pretax_total
    if pretax > 0:
        rate_rows.append({
            "Account": "Your IRA (pre-tax total)",
            "Balance": f"${pretax:,.0f}",
            "Weighted Return": f"{snap.pretax_weighted_return * 100:.1f}%",
            "Planner Uses": f"{hh.your_ira_rate(hh.base_year) * 100:.1f}%",
            "Status": "Synced" if hh.your_ira_growth else "Default",
        })
    else:
        rate_rows.append({
            "Account": "Your IRA",
            "Balance": f"${hh.your_ira:,.0f}",
            "Weighted Return": "—",
            "Planner Uses": f"{hh.your_ira_rate(hh.base_year) * 100:.1f}%",
            "Status": "Default",
        })

    rate_rows.append({
        "Account": "Spouse IRA",
        "Balance": f"${hh.spouse_ira:,.0f}",
        "Weighted Return": "—",
        "Planner Uses": f"{hh.spouse_ira_rate(hh.base_year) * 100:.1f}%",
        "Status": "Synced" if hh.spouse_ira_growth else "Default (no data)",
    })

    brok = snap.account_by_type("brokerage")
    if brok and brok.total_value > 0:
        rate_rows.append({
            "Account": "Brokerage",
            "Balance": f"${brok.total_value:,.0f}",
            "Weighted Return": f"{brok.weighted_return * 100:.1f}%",
            "Planner Uses": f"{hh.brokerage_rate(hh.base_year) * 100:.1f}%",
            "Status": "Synced" if hh.brokerage_growth else "Default",
        })

    # Show other accounts as informational
    for acct in snap.accounts:
        if acct.account_type in ("roth_ira", "hsa"):
            label = acct_labels.get(acct.account_type, acct.account_type)
            rate_rows.append({
                "Account": label,
                "Balance": f"${acct.total_value:,.0f}",
                "Weighted Return": f"{acct.weighted_return * 100:.1f}%",
                "Planner Uses": "Not modeled",
                "Status": "Info only",
            })

    st.dataframe(pd.DataFrame(rate_rows), hide_index=True, width="stretch")

    st.info(
        "**Auto-sync**: Your pre-tax IRA balance and growth rate are computed from "
        "Rollover IRA + 403(b) holdings. Spouse IRA data not yet available from scraper."
    )
