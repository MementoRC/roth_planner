"""RMD Squeeze Analyzer — visualize the forced distribution pressure.

Required Minimum Distributions (onset age 73 or 75 by birth-year cohort) compound:
  RMD → higher bracket → IRMAA surcharges → excess flows to brokerage →
  cap gains tax → NIIT exposure

This page shows:
1. RMD waterfall: how much comes out and where it goes
2. Tax bracket escalation year-by-year
3. Excess RMD overflow into brokerage and its tax drag
4. Plan-QCD analysis (charitable giving to offset RMD)
5. Side-by-side: no-conversion vs conversion RMD impact
"""

import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.scalar_registry import scalar_default
from engine.ira import RMD_DIVISORS
from engine.irmaa import irmaa_surcharge, irmaa_tier1_threshold
from engine.scenario import ConversionPlan, rmd_era_years, run_no_conversion, run_scenario
from engine.scenario_autofill import auto_fill_12
from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE
from engine.tax_indexing import index_value as _index_value
from models.household import Household
from views._format import (
    FORM_8606_CAPTION,
    fmt_dollars,
    fmt_dollars_short,
    fmt_pct,
    md_dollars,
)
from views._shared import render_completeness_badge, render_readonly_input
from views._style import render_page_header


def plan_qcds(state) -> tuple[dict[int, float], dict[int, float]]:
    """The Conversion Plan's QCDs as ``({year: $}, {year: $})``, zeros dropped.

    Keys are normalised to ``int`` (session state may hold string years).
    """

    def _clean(raw) -> dict[int, float]:
        out: dict[int, float] = {}
        for y, v in dict(raw or {}).items():
            try:
                year, amount = int(y), float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(amount) and amount > 0:
                out[year] = amount
        return out

    return _clean(state.get("conv_plan_qcd")), _clean(state.get("conv_plan_spouse_qcd"))


def render(hh: Household):
    render_page_header("rmd")
    st.caption(
        "See how Required Minimum Distributions force you into higher brackets, "
        "trigger IRMAA, and overflow into taxable brokerage accounts."
    )
    st.caption(FORM_8606_CAPTION)
    render_completeness_badge(hh)

    # --- QCDs: one home, the Conversion Plan's QCD columns (redesign PR 2) ---
    _is_mfj = hh.filing_status == "MFJ"
    your_qcd_years, spouse_qcd_years = plan_qcds(st.session_state)
    if not _is_mfj:
        spouse_qcd_years = {}
    _planned_total = sum(your_qcd_years.values()) + sum(spouse_qcd_years.values())

    # --- Run scenarios ---
    # Inject base-year realized YTD income when the user opted in (mirrors
    # Sweet Spot / ACA+IRMAA gating, views/sweet_spot.py).
    _apply_ytd = st.session_state.get("apply_ytd_to_projection", False)
    _ytd = st.session_state.get("ytd_snapshot") if _apply_ytd else None
    # audit-1008 F-78: price NIIT on the Assumptions NII estimate, as Conversion
    # Plan and Sweet Spot do.
    _nii = int(st.session_state.get("net_inv_income", scalar_default("net_inv_income")))
    no_conv = run_no_conversion(hh, end_age=95, ytd=_ytd, net_inv_income=_nii)
    # audit-1018 F-10: size Fill 12% with the same YTD the projection runs with.
    plan_12 = auto_fill_12(hh, _ytd, _nii)

    # Plan-QCD scenarios run only when the engine actually applies some QCD
    # (it gates by QCD age and the indexed limit), so the shown total is the applied one.
    no_conv_qcd = None
    _applied_total = 0.0
    if _planned_total:
        _nc_qcd_run = run_scenario(
            hh,
            ConversionPlan(qcds=your_qcd_years, spouse_qcds=spouse_qcd_years),
            "No Conv + QCD",
            end_age=95,
            ytd=_ytd,
            net_inv_income=_nii,
        )
        _applied_total = sum(yr.qcd + yr.spouse_qcd for yr in _nc_qcd_run.years)
        if _applied_total > 0:
            no_conv_qcd = _nc_qcd_run

    render_readonly_input(
        "QCDs in your plan",
        f"${_applied_total:,.0f} applied" if _applied_total > 0 else "none",
        key="qcd",
        page="conversions",
        where="Conversion Plan",
    )
    if _planned_total and _applied_total < _planned_total:
        st.caption(
            f"Planned \\${_planned_total:,.0f}, \\${_applied_total:,.0f} applies "
            "after the QCD age/limit."
        )

    if no_conv_qcd is not None:
        qcd_plan = ConversionPlan(
            your_conversions=dict(plan_12.your_conversions),
            spouse_conversions=dict(plan_12.spouse_conversions),
            qcds=your_qcd_years,
            spouse_qcds=spouse_qcd_years,
        )
        with_conv = run_scenario(
            hh, qcd_plan, "Fill 12% + QCD", end_age=95, ytd=_ytd, net_inv_income=_nii
        )
    else:
        with_conv = run_scenario(hh, plan_12, "Fill 12%", end_age=95, ytd=_ytd, net_inv_income=_nii)

    # Filter to RMD years (each person's RMD start age and later; the engine's rule)
    rmd_nc = rmd_era_years(hh, no_conv.years)
    rmd_wc = rmd_era_years(hh, with_conv.years)
    rmd_nc_qcd = rmd_era_years(hh, no_conv_qcd.years) if no_conv_qcd else None
    first_rmd_age = rmd_nc[0].your_age if rmd_nc else hh.your_rmd_start_age

    if not rmd_nc:
        st.warning("No RMD years in projection range.")
        return

    ages = [yr.your_age for yr in rmd_nc]

    # --- Top metrics ---
    c1, c2, c3, c4 = st.columns(4)

    yr75_nc = rmd_nc[0]
    yr75_wc = rmd_wc[0]

    with c1:
        st.metric(
            f"IRA at first RMD year, age {first_rmd_age} (No Conv)",
            fmt_dollars_short(yr75_nc.your_ira_begin + yr75_nc.spouse_ira_begin, decimals=2),
        )
    with c2:
        st.metric(
            f"IRA at first RMD year, age {first_rmd_age} (With Conv)",
            fmt_dollars_short(yr75_wc.your_ira_begin + yr75_wc.spouse_ira_begin, decimals=2),
            fmt_dollars_short(
                yr75_wc.your_ira_begin
                + yr75_wc.spouse_ira_begin
                - yr75_nc.your_ira_begin
                - yr75_nc.spouse_ira_begin,
                decimals=2,
            ),
        )
    with c3:
        total_rmd_nc = sum(yr.your_rmd + yr.spouse_rmd for yr in rmd_nc)
        total_rmd_wc = sum(yr.your_rmd + yr.spouse_rmd for yr in rmd_wc)
        # Guard a fully-drained no-conversion IRA: calc_rmd returns 0 on a zero balance,
        # so the RMD-year list can be non-empty with total_rmd_nc == 0 → the percentage
        # would ZeroDivisionError (audit C9 / rmd-4).
        _rmd_delta_pct = (total_rmd_wc - total_rmd_nc) / total_rmd_nc if total_rmd_nc else 0.0
        st.metric(
            f"Total RMDs (your age {first_rmd_age}-95)",
            fmt_dollars_short(total_rmd_nc),
            f"Conv: {fmt_dollars_short(total_rmd_wc)} ({fmt_pct(_rmd_delta_pct, 0, sign=True)})",
        )
    with c4:
        total_tax_nc = sum(yr.federal_tax_amt for yr in rmd_nc)
        total_tax_wc = sum(yr.federal_tax_amt for yr in rmd_wc)
        # The delta is rendered as markdown, so its two $ amounts must be
        # escaped or the text between them renders as LaTeX.
        st.metric(
            "Total RMD-Era Tax",
            fmt_dollars(total_tax_nc),
            md_dollars(
                f"Conv: {fmt_dollars(total_tax_wc)} (save {fmt_dollars(total_tax_nc - total_tax_wc)})"
            ),
        )

    st.markdown("---")

    # Prior-year MAGI anchor for IRMAA 2-year lookback
    prior_magi = st.session_state.get("prior_year_magi") or {}
    if prior_magi:
        sorted_years = sorted(prior_magi.keys(), reverse=True)
        most_recent = sorted_years[0]
        st.caption(
            f"Prior-year MAGI anchor ({most_recent}): ${prior_magi[most_recent]:,.0f}"
            " — used for IRMAA 2-year lookback"
        )

    # --- Chart 1: RMD Income Waterfall ---
    st.markdown("### Income Composition During RMD Years")
    st.caption("Stacked view: where your income comes from and how it fills brackets.")

    fig_w = go.Figure()

    # No-conversion scenario stacks — audit C10 / rmd-5: represent the same ordinary income
    # the engine brackets (combined_gross) so the stacked total matches what drives tax.
    # Components: your taxable RMD, spouse taxable RMD, conversions, option income,
    # extra withdrawals, inherited distributions, taxable SS, ordinary dividends.
    # Minor items (extra withdrawals + inherited distributions) combined into "Other ordinary".
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.taxable_rmd for yr in rmd_nc],
            name="Your Taxable RMD",
            marker_color="#ef4444",
            hovertemplate="Your RMD: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.spouse_taxable_rmd for yr in rmd_nc],
            name="Spouse Taxable RMD",
            marker_color="#f87171",
            hovertemplate="Spouse RMD: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.your_conversion + yr.spouse_conversion for yr in rmd_nc],
            name="Conversions",
            marker_color="#a78bfa",
            hovertemplate="Conversions: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.option_income for yr in rmd_nc],
            name="Option Income",
            marker_color="#fb923c",
            hovertemplate="Option Income: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.taxable_ss_amt for yr in rmd_nc],
            name="Taxable SS",
            marker_color="#60a5fa",
            hovertemplate="Taxable SS: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[yr.brokerage_ord_div for yr in rmd_nc],
            name="Ordinary Dividends",
            marker_color="#34d399",
            hovertemplate="Ord Dividends: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_w.add_trace(
        go.Bar(
            x=ages,
            y=[
                yr.extra_withdrawal
                + yr.spouse_extra_withdrawal
                + yr.your_inherited_distribution
                + yr.spouse_inherited_distribution
                for yr in rmd_nc
            ],
            name="Other ordinary",
            marker_color="#94a3b8",
            hovertemplate="Other ordinary: $%{y:,.0f}<extra></extra>",
        )
    )

    # Bracket ceiling lines — CPI-indexed, per-year filing-status-aware.
    # Uses yr.filing_status (not hh.filing_status) so post-death Single years
    # in a SurvivorScenario get Single bracket tops instead of MFJ values,
    # which would overstate the ceiling by ~$50K (12%) / ~$90K (22%) per year.
    _cpi = hh.cpi_assumption
    ceil_12_values = [
        yr.total_deductions
        + _index_value(
            (BRACKETS_SINGLE if yr.filing_status == "Single" else BRACKETS_MFJ)[1][0],
            yr.year,
            _cpi,
            round50=True,
        )
        for yr in rmd_nc
    ]
    ceil_22_values = [
        yr.total_deductions
        + _index_value(
            (BRACKETS_SINGLE if yr.filing_status == "Single" else BRACKETS_MFJ)[2][0],
            yr.year,
            _cpi,
            round50=True,
        )
        for yr in rmd_nc
    ]
    fig_w.add_trace(
        go.Scatter(
            x=ages,
            y=ceil_12_values,
            name="12% ceiling",
            line={"color": "#22c55e", "width": 2, "dash": "dash"},
            mode="lines",
        )
    )
    fig_w.add_trace(
        go.Scatter(
            x=ages,
            y=ceil_22_values,
            name="22% ceiling",
            line={"color": "#f59e0b", "width": 2, "dash": "dash"},
            mode="lines",
        )
    )

    fig_w.update_layout(
        barmode="stack",
        xaxis_title="Your Age",
        yaxis_title="Income ($)",
        yaxis_tickformat="$,.0s",
        height=400,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_w, width="stretch")

    # --- Chart 2: Bracket Comparison ---
    st.markdown("### Marginal Bracket: No Conversion vs With Conversion")

    fig_br = go.Figure()
    fig_br.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.marginal_bracket * 100 for yr in rmd_nc],
            name="No Conversion",
            line={"color": "#ef4444", "width": 3},
            mode="lines+markers",
            hovertemplate="Age %{x}: %{y:.0f}%<extra>No Conv</extra>",
        )
    )
    fig_br.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.marginal_bracket * 100 for yr in rmd_wc],
            name="With Conversion (12%)",
            line={"color": "#22c55e", "width": 3},
            mode="lines+markers",
            hovertemplate="Age %{x}: %{y:.0f}%<extra>With Conv</extra>",
        )
    )

    fig_br.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Marginal Bracket (%)",
        yaxis={"dtick": 2},
        height=350,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
    )
    st.plotly_chart(fig_br, width="stretch")

    # --- Chart 3: Annual Tax + IRMAA + NIIT + Brokerage Tax ---
    st.markdown("### All-In Annual Cost: Tax + IRMAA + NIIT + Brokerage Drag")

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown("#### No Conversion")
        fig_nc = go.Figure()
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_nc],
                name="Fed Tax",
                marker_color="#ef4444",
            )
        )
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.irmaa_cost for yr in rmd_nc],
                name="IRMAA",
                marker_color="#f59e0b",
            )
        )
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.brokerage_gain_tax for yr in rmd_nc],
                name="Brok Cap Gains",
                marker_color="#8b5cf6",
            )
        )
        fig_nc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.niit_cost for yr in rmd_nc],
                name="NIIT",
                marker_color="#ec4899",
            )
        )
        fig_nc.update_layout(
            barmode="stack",
            xaxis_title="Your Age",
            yaxis_title="Annual Cost ($)",
            yaxis_tickformat="$,.0s",
            height=350,
        )
        st.plotly_chart(fig_nc, width="stretch")

    with col_r:
        st.markdown("#### With Conversion (Fill 12%)")
        fig_wc = go.Figure()
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_wc],
                name="Fed Tax",
                marker_color="#3b82f6",
            )
        )
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.irmaa_cost for yr in rmd_wc],
                name="IRMAA",
                marker_color="#f59e0b",
            )
        )
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.brokerage_gain_tax for yr in rmd_wc],
                name="Brok Cap Gains",
                marker_color="#8b5cf6",
            )
        )
        fig_wc.add_trace(
            go.Bar(
                x=ages,
                y=[yr.niit_cost for yr in rmd_wc],
                name="NIIT",
                marker_color="#ec4899",
            )
        )
        fig_wc.update_layout(
            barmode="stack",
            xaxis_title="Your Age",
            yaxis_title="Annual Cost ($)",
            yaxis_tickformat="$,.0s",
            height=350,
        )
        st.plotly_chart(fig_wc, width="stretch")

    # --- Chart 4: Brokerage Overflow ---
    st.markdown("### Brokerage Overflow — Excess RMD Accumulation")
    st.caption(
        "RMDs exceeding living expenses flow into taxable brokerage, "
        "creating ongoing capital gains tax drag."
    )

    fig_brok = go.Figure()
    fig_brok.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.brokerage_balance for yr in rmd_nc],
            name="No Conversion",
            fill="tozeroy",
            fillcolor="rgba(239,68,68,0.15)",
            line={"color": "#ef4444", "width": 2},
            hovertemplate="Age %{x}: $%{y:,.0f}<extra>No Conv Brok</extra>",
        )
    )
    fig_brok.add_trace(
        go.Scatter(
            x=ages,
            y=[yr.brokerage_balance for yr in rmd_wc],
            name="With Conversion",
            fill="tozeroy",
            fillcolor="rgba(59,130,246,0.15)",
            line={"color": "#3b82f6", "width": 2},
            hovertemplate="Age %{x}: $%{y:,.0f}<extra>With Conv Brok</extra>",
        )
    )
    fig_brok.update_layout(
        xaxis_title="Your Age",
        yaxis_title="Brokerage Balance ($)",
        yaxis_tickformat="$,.0s",
        height=350,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_brok, width="stretch")

    # --- Chart 5: QCD impact (if the plan has applied QCDs) ---
    if no_conv_qcd and rmd_nc_qcd:
        st.markdown("### QCD Impact — Tax Savings from Charitable Distributions")
        fig_qcd = go.Figure()
        fig_qcd.add_trace(
            go.Scatter(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_nc],
                name="No Conv, No QCD",
                line={"color": "#ef4444", "width": 2, "dash": "dash"},
            )
        )
        fig_qcd.add_trace(
            go.Scatter(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_nc_qcd],
                name="No Conv + plan QCDs",
                line={"color": "#f59e0b", "width": 2},
            )
        )
        fig_qcd.add_trace(
            go.Scatter(
                x=ages,
                y=[yr.federal_tax_amt for yr in rmd_wc],
                name="Fill 12% + plan QCDs",
                line={"color": "#22c55e", "width": 3},
            )
        )
        fig_qcd.update_layout(
            xaxis_title="Your Age",
            yaxis_title="Annual Federal Tax ($)",
            yaxis_tickformat="$,.0s",
            height=400,
            legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
        )
        st.plotly_chart(fig_qcd, width="stretch")

    # --- Detail table ---
    st.markdown("### Year-by-Year RMD Detail")

    rows = []
    for nc, wc in zip(rmd_nc, rmd_wc, strict=False):
        rows.append(
            {
                "Year": str(nc.year),
                "You/Sp": f"{nc.your_age}/{nc.spouse_age}",
                "IRA (NC)": fmt_dollars_short(nc.your_ira_begin + nc.spouse_ira_begin, decimals=2),
                "IRA (WC)": fmt_dollars_short(wc.your_ira_begin + wc.spouse_ira_begin, decimals=2),
                "RMD (NC)": fmt_dollars(nc.your_rmd + nc.spouse_rmd),
                "RMD (WC)": fmt_dollars(wc.your_rmd + wc.spouse_rmd),
                "SS": fmt_dollars(nc.combined_ss),
                "Bracket (NC)": fmt_pct(nc.marginal_bracket, 0),
                "Bracket (WC)": fmt_pct(wc.marginal_bracket, 0),
                "Tax (NC)": fmt_dollars(nc.federal_tax_amt),
                "Tax (WC)": fmt_dollars(wc.federal_tax_amt),
                "Saved": fmt_dollars(nc.federal_tax_amt - wc.federal_tax_amt),
                "IRMAA (NC)": fmt_dollars(nc.irmaa_cost),
                "IRMAA (WC)": fmt_dollars(wc.irmaa_cost),
                "Excess RMD (NC)": fmt_dollars(nc.excess_rmd),
                "Brok (NC)": fmt_dollars(nc.brokerage_balance),
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch")

    # --- RMD divisor reference ---
    with st.expander("📖 RMD Divisor Table (Uniform Lifetime)"):
        div_rows = [
            {"Age": age, "Divisor": div, "RMD % of IRA": fmt_pct(1 / div)}
            for age, div in sorted(RMD_DIVISORS.items())
            if age >= hh.your_rmd_start_age
        ]
        st.dataframe(pd.DataFrame(div_rows), hide_index=True)

    # --- Squeeze explanation ---
    st.markdown("---")
    st.markdown("### The RMD Squeeze Explained")
    # Compute Tier-1 surcharge at the IRMAA payment year (base_year + 2). The
    # threshold must be the engine's own (payment-year indexed, $1,000-rounded)
    # value: probing any other threshold (+1) can leave magi below the real
    # Tier-1, so the surcharge renders as $0 (audit C8 / ui-org-3; again after
    # the audit-1008 F-52 rounding). Read it once, then probe and display it.
    _tier1_thresh = irmaa_tier1_threshold(hh.base_year, hh.filing_status, hh.cpi_assumption)
    _tier1_magi = _tier1_thresh + 1  # just above the indexed Tier-1 threshold
    # Surcharge on top of the household's own Part B base premium (audit-1008 F-81).
    _part_b_base = hh.medicare_part_b_base_monthly * 12
    _irmaa_tier1_2p = irmaa_surcharge(
        _tier1_magi,
        2,
        base_part_b=_part_b_base,
        filing_status=hh.filing_status,
        year=hh.base_year + 2,
        cpi=hh.cpi_assumption,
    )
    _irmaa_tier1_1p = irmaa_surcharge(
        _tier1_magi,
        1,
        base_part_b=_part_b_base,
        filing_status=hh.filing_status,
        year=hh.base_year + 2,
        cpi=hh.cpi_assumption,
    )
    _irmaa_surcharge_note = (
        f"{fmt_dollars(_irmaa_tier1_2p)}/yr for a couple"
        if _is_mfj
        else f"{fmt_dollars(_irmaa_tier1_1p)}/yr individually"
    )
    _rmd_age = hh.your_rmd_start_age
    _onset_pct = fmt_pct(1 / RMD_DIVISORS[_rmd_age])
    # IRA growth from today to RMD onset, read off the no-conversion projection.
    _ira_now = no_conv.years[0].your_ira_begin + no_conv.years[0].spouse_ira_begin
    _own_onset = next((y for y in rmd_nc if y.your_age == _rmd_age), yr75_nc)
    _ira_at_rmd = _own_onset.your_ira_begin + _own_onset.spouse_ira_begin
    # md_dollars escapes every "$" so Streamlit does not render text between two
    # dollar signs as LaTeX (audit-1008 F-82).
    st.markdown(
        md_dollars(f"""
- **The problem**: At {_rmd_age}, you *must* take distributions from your IRA — the IRS sets the amount
- **Divisor shrinks**: At {_rmd_age} you withdraw ~{_onset_pct}, by 85 it's ~6.3%, by 95 it's ~11.2%
- **Growth amplifies**: Untouched, your IRAs grow from {fmt_dollars_short(_ira_now)} today to {fmt_dollars_short(_ira_at_rmd)} by {_rmd_age}, and RMDs scale with that balance
- **Bracket escalation**: Large RMDs + SS push you from 12% into 22-24% brackets
- **IRMAA trigger**: MAGI over ${_tier1_thresh / 1000:.0f}K in {hh.base_year} means Medicare surcharges in {hh.base_year + 2} ({_irmaa_surcharge_note} at Tier 1, projected to that payment year)
- **Brokerage overflow**: RMDs exceeding living expenses create taxable investment accounts
- **The fix**: Converting during low-income years (ages {hh.your_age}-{_rmd_age - 1}) shrinks the IRA *before* RMDs start
- **QCD option**: At 70½+, donating up to ${hh.qcd_limit / 1000:.0f}K/yr directly from IRA to charity bypasses taxation
""")
    )
