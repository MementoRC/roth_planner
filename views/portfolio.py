"""Portfolio — live holdings from FinExtract ingestion server.

Shows synced brokerage holdings, equity compensation, and how the
actual allocation maps to growth rate assumptions in the planner.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.loader import load_defaults
from engine.portfolio_sync import EXPECTED_RETURNS
from models.household import Household
from views._format import fmt_dollars, fmt_pct


def render(hh: Household):
    _cfg = load_defaults()
    ticker = _cfg["stock_ticker"]
    st.title("Portfolio")
    st.caption(
        "Cached data from FinExtract ingestion server. "
        "Go to **⚙️ Setup → 💼 Portfolio** and click **Sync from FinExtract** to refresh."
    )

    snap = st.session_state.get("portfolio_snapshot")

    if not snap or not snap.server_available:
        st.info(
            "No portfolio data cached yet.\n\n"
            "Go to **⚙️ Setup → 💼 Portfolio** and click **Sync from FinExtract** to populate this page."
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
        acct_rows.append(
            {
                "Account": f"{label} ({acct.account_name})" if acct.account_name else label,
                "Total Value": fmt_dollars(acct.total_value),
                "Equity": fmt_dollars(acct.equity_value),
                "Bonds": fmt_dollars(acct.bond_value),
                "Cash": fmt_dollars(acct.cash_value),
                "Crypto": fmt_dollars(acct.crypto_value),
                "Wtd Return": fmt_pct(acct.weighted_return),
            }
        )

    if snap.txn_shares_value > 0:
        acct_rows.append(
            {
                "Account": f"{ticker} Shares (ESPP/RSU)",
                "Total Value": fmt_dollars(snap.txn_shares_value),
                "Equity": fmt_dollars(snap.txn_shares_value),
                "Bonds": fmt_dollars(0),
                "Cash": fmt_dollars(0),
                "Crypto": fmt_dollars(0),
                "Wtd Return": "—",
            }
        )

    st.dataframe(pd.DataFrame(acct_rows), hide_index=True, width="stretch")

    total_val = snap.total_portfolio_value
    total_eq = sum(a.equity_value for a in snap.accounts) + snap.txn_shares_value
    total_bd = sum(a.bond_value for a in snap.accounts)
    total_cash = sum(a.cash_value for a in snap.accounts)
    total_crypto = sum(a.crypto_value for a in snap.accounts)

    st.metric("Total Portfolio", fmt_dollars(total_val))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Equity", fmt_dollars(total_eq))
    c2.metric("Bonds", fmt_dollars(total_bd))
    c3.metric("Cash", fmt_dollars(total_cash))
    c4.metric("Crypto", fmt_dollars(total_crypto))

    # Pre-tax retirement total (this is what feeds into the planner)
    pretax = snap.pretax_total
    if pretax > 0:
        st.markdown("---")
        c1, c2 = st.columns(2)
        c1.metric("Household Pre-Tax (IRA + 403b)", fmt_dollars(pretax))
        c2.metric("Household Pre-Tax Wtd Return", fmt_pct(snap.pretax_weighted_return))

    # --- Holdings Detail ---
    st.markdown("### Holdings")

    holdings_rows = []
    for acct in snap.accounts:
        for h in acct.holdings:
            holdings_rows.append(
                {
                    "Account": acct.account_type.replace("_", " ").title(),
                    "Symbol": h.symbol,
                    "Description": h.description,
                    "Shares": f"{h.quantity:,.1f}",
                    "Value": fmt_dollars(h.market_value),
                    "Class": h.asset_class.title(),
                    "Gain/Loss": fmt_dollars(h.total_gain_loss)
                    if h.total_gain_loss is not None
                    else "—",
                }
            )

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
        labels.append(f"{ticker} Shares")
        values.append(snap.txn_shares_value)
        colors.append("#ef4444")

    fig_alloc.add_trace(
        go.Pie(
            labels=labels,
            values=values,
            marker={"colors": colors},
            hole=0.4,
            textinfo="label+percent",
        )
    )
    fig_alloc.update_layout(height=400, showlegend=False)
    st.plotly_chart(fig_alloc, width="stretch")

    # --- Active Equity Grants ---
    if snap.equity_grants:
        st.markdown("### Active Stock Option Grants (NQO)")

        grant_rows = []
        for g in snap.equity_grants:
            grant_rows.append(
                {
                    "Grant ID": g.grant_id,
                    "Type": g.grant_type,
                    "Grant Date": g.grant_date,
                    "Granted": f"{g.shares_granted:,}",
                    "Outstanding": f"{g.outstanding:,}",
                    "Current Value": fmt_dollars(g.current_value),
                }
            )

        st.dataframe(pd.DataFrame(grant_rows), hide_index=True, width="stretch")

        # Compare with planner defaults
        st.markdown("#### vs. Planner Defaults")
        plan_grants = hh.grants
        comp_rows = []
        for i, g in enumerate(snap.equity_grants):
            plan = plan_grants[i] if i < len(plan_grants) else None
            comp_rows.append(
                {
                    "Source": "FinExtract",
                    "Grant": g.grant_date,
                    "Outstanding": g.outstanding,
                    "Value": fmt_dollars(g.current_value),
                }
            )
            if plan:
                comp_rows.append(
                    {
                        "Source": "Planner Default",
                        "Grant": str(plan.year),
                        "Outstanding": plan.shares,
                        "Value": fmt_dollars(plan.spread(hh.txn_price_now)),
                    }
                )

        st.dataframe(pd.DataFrame(comp_rows), hide_index=True, width="stretch")

    # --- TXN Shares ---
    if snap.txn_shares_held > 0:
        st.markdown(f"### {ticker} Shares Held (ESPP + RSU)")
        c1, c2 = st.columns(2)
        c1.metric("Shares", f"{snap.txn_shares_held:,}")
        c2.metric("Value", fmt_dollars(snap.txn_shares_value))

    # --- Growth Rate Mapping ---
    st.markdown("---")
    st.markdown("### Growth Rate Mapping")
    returns_str = ", ".join(f"{k} {fmt_pct(v, 0)}" for k, v in EXPECTED_RETURNS.items())
    st.caption(f"Expected returns: {returns_str}.")

    rate_rows = []

    # Your pre-tax IRA (Rollover IRA + 403b, owner="you" only)
    your_pretax_accts = [a for a in snap.pretax_accounts if a.owner == "you"]
    your_pretax = sum(a.total_value for a in your_pretax_accts)
    if your_pretax > 0:
        rate_rows.append(
            {
                "Account": "Your IRA (pre-tax)",
                "Balance": fmt_dollars(your_pretax),
                "Weighted Return": fmt_pct(snap.pretax_weighted_return_for("you")),
                "Planner Uses": fmt_pct(hh.your_ira_rate(hh.base_year)),
                "Status": "Synced" if hh.your_ira_growth else "Default",
            }
        )
    else:
        rate_rows.append(
            {
                "Account": "Your IRA",
                "Balance": fmt_dollars(hh.your_ira),
                "Weighted Return": "—",
                "Planner Uses": fmt_pct(hh.your_ira_rate(hh.base_year)),
                "Status": "Default",
            }
        )

    spouse_pretax_accts = [a for a in snap.pretax_accounts if a.owner == "spouse"]
    spouse_pretax_return = (
        fmt_pct(snap.pretax_weighted_return_for("spouse")) if spouse_pretax_accts else "—"
    )
    rate_rows.append(
        {
            "Account": "Spouse IRA (pre-tax)",
            "Balance": fmt_dollars(hh.spouse_ira),
            "Weighted Return": spouse_pretax_return,
            "Planner Uses": fmt_pct(hh.spouse_ira_rate(hh.base_year)),
            "Status": "Synced" if hh.spouse_ira_growth else "Default (no data)",
        }
    )

    brok = snap.account_by_type("brokerage")
    if brok and brok.total_value > 0:
        rate_rows.append(
            {
                "Account": "Brokerage",
                "Balance": fmt_dollars(brok.total_value),
                "Weighted Return": fmt_pct(brok.weighted_return),
                "Planner Uses": fmt_pct(hh.brokerage_rate(hh.base_year)),
                "Status": "Synced" if hh.brokerage_growth else "Default",
            }
        )

    # Show other accounts as informational
    for acct in snap.accounts:
        if acct.account_type in ("roth_ira", "hsa"):
            label = acct_labels.get(acct.account_type, acct.account_type)
            rate_rows.append(
                {
                    "Account": label,
                    "Balance": fmt_dollars(acct.total_value),
                    "Weighted Return": fmt_pct(acct.weighted_return),
                    "Planner Uses": "Not modeled",
                    "Status": "Info only",
                }
            )

    st.dataframe(pd.DataFrame(rate_rows), hide_index=True, width="stretch")

    st.info(
        "**Auto-sync**: Your pre-tax IRA balance and growth rate are computed from "
        "Rollover IRA + 403(b) holdings. Spouse IRA data not yet available from scraper."
    )
