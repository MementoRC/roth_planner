"""Portfolio section helpers for the Holdings page (views/profile_pages.py).

Account overview, allocation, employer-stock shares, the FinExtract-vs-planner grant
comparison, and how the actual allocation maps to growth rate assumptions.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.loader import load_defaults
from engine.data_sources.resolver import GRANTS_KEY
from engine.portfolio_sync import EXPECTED_RETURNS, EquityGrant, PortfolioSnapshot
from models.grants import StockGrant
from models.household import Household
from models.sourced import Source
from views._format import fmt_dollars, fmt_pct
from views.navigation import label_of


def grants_pending_review() -> bool:
    """True when imported grants wait as a candidate for confirmation.

    Reads the same ``_pending_review`` gate Command Center uses, so the
    Holdings tables agree with Review Changes.
    """
    return GRANTS_KEY in (st.session_state.get("_pending_review") or ())


def render_grants_pending_caption(container=st) -> None:
    """Caption under a snapshot-grants table while the grants are unconfirmed."""
    if grants_pending_review():
        container.caption(
            "Pending review — these grants are not used by the plan until you "
            f"confirm them in {label_of('review')}."
        )


def _pair_grants(
    snap_grants: list[EquityGrant], planner_grants: list[StockGrant]
) -> list[tuple[EquityGrant | None, StockGrant | None]]:
    """Pair FinExtract grants with planner grants by year identity.

    Positional (index-based) pairing misaligns rows whenever the two lists
    differ in order or length — e.g. PR #374 dropped the 2019 grant from
    ``hh.grants``, which used to make the 2019 FinExtract row line up against
    the 2020 planner-default row (audit-0720 M10). ``EquityGrant`` has no
    strike/expiry_year field, so the grant year (parsed from ``grant_date``)
    is the identity available on both sides. A grant with no counterpart on
    the other side is paired with ``None`` so the caller can render a blank
    side instead of a wrong pairing.
    """
    remaining_planner = list(planner_grants)
    pairs: list[tuple[EquityGrant | None, StockGrant | None]] = []
    for g in snap_grants:
        year = int(g.grant_date.split("-")[0]) if g.grant_date else None
        match = next((p for p in remaining_planner if p.year == year), None)
        if match is not None:
            remaining_planner.remove(match)
        pairs.append((g, match))
    for p in remaining_planner:
        pairs.append((None, p))
    return pairs


def grant_source_label(snap: PortfolioSnapshot) -> str:
    """Where the snapshot's grants came from, for the comparison table.

    Grants reach the snapshot from a backup import or a FinExtract sync; the
    label used to say "FinExtract" for both.
    """
    if getattr(snap, "imported_from_bundle", False):
        return "Backup import"
    if getattr(snap, "server_available", False):
        return "FinExtract"
    return "Imported"


PLANNER_DEFAULT_LABEL = "Planner Default"

_PLANNER_SOURCE_LABELS: dict[Source, str] = {
    Source.BUNDLE: "Planner (from backup)",
    Source.PDF: "Planner (from statement)",
    Source.FINEXTRACT_LIVE: "Planner (from FinExtract)",
    Source.MANUAL: "Planner (manual)",
}


def planner_grant_label(planner_grants: list[StockGrant], plan: StockGrant | None) -> str:
    """Label for a planner grant row, naming where a confirmed grant came from.

    ``hh.grants`` is a ``SourcedList`` once committed, with one provenance per
    grant. A confirmed backup/statement/FinExtract grant used to read "Planner
    Default" like an untouched built-in one. Built-in and pre-migration grants
    (``DEFAULT``/``UNKNOWN``) keep "Planner Default".
    """
    prov = getattr(planner_grants, "prov", None)
    if plan is None or not prov:
        return PLANNER_DEFAULT_LABEL
    for grant, grant_prov in zip(planner_grants, prov, strict=False):
        if grant is plan:
            return _PLANNER_SOURCE_LABELS.get(grant_prov.source, PLANNER_DEFAULT_LABEL)
    return PLANNER_DEFAULT_LABEL


def _build_grant_comparison_rows(
    pairs: list[tuple[EquityGrant | None, StockGrant | None]],
    stock_price_now: float,
    source: str = "Imported",
    planner_grants: list[StockGrant] | None = None,
) -> list[dict[str, str]]:
    """Build the imported-vs-planner comparison table rows.

    Every value is formatted to ``str`` up front (including "Outstanding",
    which is otherwise a bare ``int``) so every column is uniformly typed —
    a column mixing ``int`` and the ``"—"`` placeholder string makes
    pyarrow infer int64 and then raise ``ArrowInvalid`` on the placeholder
    when Streamlit serializes the dataframe for ``st.dataframe``.

    ``planner_grants`` (``hh.grants``) supplies each planner row's provenance
    label; without it every planner row reads "Planner Default".
    """
    comp_rows: list[dict[str, str]] = []
    for g, plan in pairs:
        comp_rows.append(
            {
                "Source": source,
                "Grant": g.grant_date if g else "—",
                "Outstanding": f"{g.outstanding:,}" if g else "—",
                "Value": fmt_dollars(g.current_value) if g else "—",
            }
        )
        comp_rows.append(
            {
                "Source": planner_grant_label(planner_grants or [], plan),
                "Grant": str(plan.year) if plan else "—",
                "Outstanding": f"{plan.shares:,}" if plan else "—",
                "Value": fmt_dollars(plan.spread(stock_price_now)) if plan else "—",
            }
        )
    return comp_rows


_ACCT_LABELS = {
    "trad_ira": "Trad IRA",
    "roth_ira": "Roth IRA",
    "403b": "403(b)",
    "hsa": "HSA",
    "brokerage": "Brokerage",
}


_CLASS_LABELS = {
    "equity": "Equity",
    "bond": "Bonds",
    "cash": "Cash",
    "crypto": "Crypto",
    "target_date": "Target Date",
}

# (AccountSummary attribute, column label) for every asset class the engine
# defines, so the class breakdown always adds up to Total Value (audit-1008
# F-79: target-date value was counted in the total but had no column).
CLASS_COLUMNS: list[tuple[str, str]] = [
    (f"{cls}_value", _CLASS_LABELS.get(cls, cls.replace("_", " ").title()))
    for cls in EXPECTED_RETURNS
]


def _ticker() -> str:
    return load_defaults()["stock_ticker"]


def render_account_overview(snap: PortfolioSnapshot) -> None:
    ticker = _ticker()
    st.markdown("### Account Overview")
    acct_labels = _ACCT_LABELS

    acct_rows = []
    for acct in snap.accounts:
        label = acct_labels.get(acct.account_type, acct.account_type.title())
        row = {
            "Account": f"{label} ({acct.account_name})" if acct.account_name else label,
            "Total Value": fmt_dollars(acct.total_value),
        }
        for attr, cls_label in CLASS_COLUMNS:
            row[cls_label] = fmt_dollars(getattr(acct, attr, 0.0))
        row["Wtd Return"] = fmt_pct(acct.weighted_return)
        acct_rows.append(row)

    if snap.stock_shares_value > 0:
        row = {
            "Account": f"{ticker} Shares (ESPP/RSU)",
            "Total Value": fmt_dollars(snap.stock_shares_value),
        }
        for attr, cls_label in CLASS_COLUMNS:
            row[cls_label] = fmt_dollars(snap.stock_shares_value if attr == "equity_value" else 0)
        row["Wtd Return"] = "—"
        acct_rows.append(row)

    st.dataframe(pd.DataFrame(acct_rows), hide_index=True, width="stretch")

    st.metric("Total Portfolio", fmt_dollars(snap.total_portfolio_value))

    cols = st.columns(len(CLASS_COLUMNS))
    for col, (attr, cls_label) in zip(cols, CLASS_COLUMNS, strict=True):
        total = sum(getattr(a, attr, 0.0) for a in snap.accounts)
        if attr == "equity_value":
            total += snap.stock_shares_value
        col.metric(cls_label, fmt_dollars(total))

    # Pre-tax retirement total (this is what feeds into the planner)
    pretax = snap.pretax_total
    if pretax > 0:
        st.markdown("---")
        c1, c2 = st.columns(2)
        c1.metric("Household Pre-Tax (IRA + 403b)", fmt_dollars(pretax))
        c2.metric("Household Pre-Tax Wtd Return", fmt_pct(snap.pretax_weighted_return))


def render_allocation(snap: PortfolioSnapshot) -> None:
    ticker = _ticker()
    acct_labels = _ACCT_LABELS
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
    for acct in snap.accounts:
        acct_label = acct_labels.get(acct.account_type, acct.account_type.title())
        for attr, cls_label in CLASS_COLUMNS:
            val = getattr(acct, attr, 0)
            if val > 0:
                labels.append(f"{acct_label} — {cls_label}")
                values.append(val)
                colors.append(color_map.get(attr.replace("_value", ""), "#6b7280"))

    if snap.stock_shares_value > 0:
        labels.append(f"{ticker} Shares")
        values.append(snap.stock_shares_value)
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


def render_grant_comparison(snap: PortfolioSnapshot, hh: Household) -> None:
    """FinExtract-vs-planner grant comparison (the one grants table lives in
    ``render_options_partial``)."""
    if not snap.equity_grants:
        return
    # Paired by grant-year identity, not list position, so the two sources
    # never misalign (audit-0720 M10).
    st.markdown("#### vs. Planner Defaults")
    pairs = _pair_grants(snap.equity_grants, hh.grants)
    comp_rows = _build_grant_comparison_rows(
        pairs, hh.stock_price_now, grant_source_label(snap), planner_grants=hh.grants
    )
    st.dataframe(pd.DataFrame(comp_rows), hide_index=True, width="stretch")
    render_grants_pending_caption()


def render_stock_shares(snap: PortfolioSnapshot) -> None:
    ticker = _ticker()
    if snap.stock_shares_held > 0:
        st.markdown(f"### {ticker} Shares Held (ESPP + RSU)")
        c1, c2 = st.columns(2)
        c1.metric("Shares", f"{snap.stock_shares_held:,}")
        c2.metric("Value", fmt_dollars(snap.stock_shares_value))


def render_growth_mapping(snap: PortfolioSnapshot, hh: Household) -> None:
    acct_labels = _ACCT_LABELS
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

    # All brokerage accounts, both owners -- the same aggregate snapshot_ingest
    # writes into hh.brokerage_growth (audit-1008 F-80: the first account only).
    if snap.brokerage_total > 0:
        rate_rows.append(
            {
                "Account": "Brokerage",
                "Balance": fmt_dollars(snap.brokerage_total),
                "Weighted Return": fmt_pct(snap.brokerage_weighted_return),
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
        "**Auto-sync**: Pre-tax IRA balances and growth rates are computed from "
        "Rollover IRA + 403(b) holdings for each owner."
    )
