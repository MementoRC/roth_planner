"""Sweet Spot Finder — find the optimal Roth conversion amount per year.

Sweeps conversion amounts from $0 to bracket ceiling and plots:
- Marginal all-in cost per additional $1,000 converted
- Cumulative all-in cost (tax + IRMAA + ACA loss + NIIT)
- Bracket boundaries, IRMAA thresholds, ACA cliff, NIIT threshold
- Recommended "sweet spot" zones
"""

import plotly.graph_objects as go
import streamlit as st

from engine.irmaa import IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE, _index_irmaa_tiers
from engine.niit import NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE
from engine.sweet_spot_compute import (
    STEP,
    all_in_at_conversion,
    base_income_for_year,
    bracket_boundary_conversion,
    compute_marginal_costs,
    compute_multi_year_summary,
    estimate_ltcg_eligible,
    find_sweet_spots,
)
from engine.tax import BRACKETS_MFJ, BRACKETS_SINGLE
from engine.tax_indexing import index_bracket_list as _index_brackets
from models.household import Household
from views._format import FORM_8606_CAPTION, fmt_dollars, fmt_pct


def render(hh: Household) -> None:
    st.title("🎯 Sweet Spot Finder")
    st.caption(
        "Find the optimal Roth conversion amount where marginal cost jumps. "
        "The sweet spot is just before a bracket boundary, IRMAA tier, or ACA cliff."
    )
    st.caption(FORM_8606_CAPTION)

    # Filing-status-aware constants for chart annotations (indexed for selected year)
    _base_irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
    niit_threshold = NIIT_THRESHOLD_SINGLE if hh.filing_status == "Single" else NIIT_THRESHOLD_MFJ
    # irmaa_tiers resolved after year selection below

    # --- Year selector ---
    conv_window = max(hh.your_conv_window, hh.spouse_conv_window)
    conv_years = list(range(hh.base_year, hh.base_year + conv_window))
    if not conv_years:
        st.warning("No conversion window remaining.")
        return

    col_yr, col_inv = st.columns(2)
    with col_yr:
        selected_year = st.selectbox(
            "Analysis Year",
            conv_years,
            format_func=lambda y: f"{y} (age {hh.your_age_in(y)}/{hh.spouse_age_in(y)})",
        )
    with col_inv:
        net_inv_income = st.number_input(
            "Net Investment Income (est.)",
            value=0,
            step=5_000,
            format="%d",
            help="Capital gains + dividends + interest from brokerage. "
            "Used to estimate NIIT impact.",
        )

    # Index IRMAA tiers and brackets for the selected year.
    # IRMAA: this year's MAGI is measured against payment-year (selected_year + 2) thresholds.
    # Ordinary brackets: no lookback — index to the income year itself.
    _cpi = hh.cpi_assumption
    irmaa_tiers = _index_irmaa_tiers(_base_irmaa_tiers, selected_year + 2, _cpi)  # +2: payment-year indexing
    _base_brackets = BRACKETS_SINGLE if hh.filing_status == "Single" else BRACKETS_MFJ
    indexed_brackets = _index_brackets(_base_brackets, selected_year, _cpi)

    # --- Compute base income ---
    # Inject base-year realized YTD income into MAGI when the user opted in (niit-5).
    _apply_ytd = st.session_state.get("apply_ytd_to_projection", False)
    _ytd = st.session_state.get("ytd_snapshot") if _apply_ytd else None
    base = base_income_for_year(
        hh, selected_year, ytd=_ytd if selected_year == hh.base_year else None
    )

    # --- Info bar ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Option Income", fmt_dollars(base.opt))
    c2.metric("Combined SS", fmt_dollars(base.combined_ss))
    c3.metric("Deductions", fmt_dollars(base.total_ded))

    _ltcg_eligible = estimate_ltcg_eligible(hh, selected_year)
    base_result = all_in_at_conversion(hh, base, 0, net_inv_income, ltcg_eligible=_ltcg_eligible)
    c4.metric("Base MAGI", fmt_dollars(base.base_magi))

    # Prior-year MAGI anchor for IRMAA 2-year lookback
    prior_magi = st.session_state.get("prior_year_magi") or {}
    if prior_magi:
        sorted_years = sorted(prior_magi.keys(), reverse=True)
        most_recent = sorted_years[0]
        st.caption(
            f"Prior-year MAGI anchor ({most_recent}): {fmt_dollars(prior_magi[most_recent])}"
            " — used for IRMAA 2-year lookback"
        )

    # --- Sweep conversion amounts ---
    max_conv = int(
        min(
            base.total_ded + indexed_brackets[-2][0],  # up to 35% bracket
            hh.your_ira + hh.spouse_ira,
        )
    )
    max_conv = min(max_conv, 800_000)  # cap at $800K for performance

    convs = list(range(0, max_conv + STEP, STEP))
    results = [
        all_in_at_conversion(hh, base, c, net_inv_income, ltcg_eligible=_ltcg_eligible)
        for c in convs
    ]

    # --- Marginal cost chart ---
    st.markdown("### Marginal All-In Cost per $1,000 Converted")
    st.caption(
        "Shows the cost of converting one more $1,000. "
        "Flat sections are sweet zones; jumps indicate bracket/tier boundaries."
    )

    marginal_data = compute_marginal_costs(results)

    fig_m = go.Figure()
    fig_m.add_trace(
        go.Scatter(
            x=convs,
            y=marginal_data.marginal_tax,
            name="Fed Tax",
            stackgroup="one",
            line={"color": "#3b82f6"},
            hovertemplate="Fed Tax: $%{y:.0f} per $1K<extra></extra>",
        )
    )
    fig_m.add_trace(
        go.Scatter(
            x=convs,
            y=marginal_data.marginal_irmaa,
            name="IRMAA",
            stackgroup="one",
            line={"color": "#ef4444"},
            hovertemplate="IRMAA: $%{y:.0f} per $1K<extra></extra>",
        )
    )
    if any(v > 0 for v in marginal_data.marginal_aca):
        fig_m.add_trace(
            go.Scatter(
                x=convs,
                y=marginal_data.marginal_aca,
                name="ACA Loss",
                stackgroup="one",
                line={"color": "#f59e0b"},
                hovertemplate="ACA Loss: $%{y:.0f} per $1K<extra></extra>",
            )
        )
    if any(v > 0 for v in marginal_data.marginal_niit):
        fig_m.add_trace(
            go.Scatter(
                x=convs,
                y=marginal_data.marginal_niit,
                name="NIIT",
                stackgroup="one",
                line={"color": "#8b5cf6"},
                hovertemplate="NIIT: $%{y:.0f} per $1K<extra></extra>",
            )
        )

    # Add bracket boundary lines
    bracket_boundaries = []
    for ceil, rate in indexed_brackets[:-1]:
        boundary_conv = bracket_boundary_conversion(base, ceil)
        # Adjust for the fact that conversion changes taxable SS
        if 0 < boundary_conv < max_conv:
            bracket_boundaries.append((boundary_conv, rate))
            fig_m.add_vline(
                x=boundary_conv,
                line_dash="dot",
                line_color="#94a3b8",
                annotation_text=f"{fmt_pct(rate, 0)} bracket",
                annotation_position="top",
            )

    # IRMAA threshold lines
    for threshold, _, _ in irmaa_tiers:
        irmaa_conv = threshold - base.base_magi
        if 0 < irmaa_conv < max_conv:
            fig_m.add_vline(
                x=irmaa_conv,
                line_dash="dash",
                line_color="#ef4444",
                annotation_text=f"IRMAA ${threshold / 1000:.0f}K",
                annotation_position="bottom",
            )

    # NIIT threshold line
    niit_conv = niit_threshold - base.base_magi
    if 0 < niit_conv < max_conv and net_inv_income > 0:
        fig_m.add_vline(
            x=niit_conv,
            line_dash="dash",
            line_color="#8b5cf6",
            annotation_text=f"NIIT ${niit_threshold // 1000:.0f}K",
            annotation_position="top",
        )

    fig_m.update_layout(
        xaxis_title="Conversion Amount ($)",
        yaxis_title="Marginal Cost per $1,000",
        xaxis_tickformat="$,.0s",
        yaxis_tickformat="$,.0f",
        height=450,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_m, width="stretch")

    # --- Sweet spots ---
    sweet_spots = find_sweet_spots(results)

    # Also find the room-to-12% and room-to-22% values
    room_12 = base_result.room_12
    room_22 = base_result.room_22

    st.markdown("### Recommended Conversion Zones")

    z1, z2, z3 = st.columns(3)
    with z1:
        st.markdown("#### Fill to 12%")
        st.metric("Conversion", fmt_dollars(room_12))
        r12_result = all_in_at_conversion(
            hh, base, room_12, net_inv_income, ltcg_eligible=_ltcg_eligible
        )
        avg_rate = r12_result.all_in / max(room_12, 1)
        st.metric("All-In Cost", fmt_dollars(r12_result.all_in), f"Avg {fmt_pct(avg_rate)}")

    with z2:
        st.markdown("#### Fill to 22%")
        st.metric("Conversion", fmt_dollars(room_22))
        r22_result = all_in_at_conversion(
            hh, base, room_22, net_inv_income, ltcg_eligible=_ltcg_eligible
        )
        avg_rate_22 = r22_result.all_in / max(room_22, 1)
        st.metric("All-In Cost", fmt_dollars(r22_result.all_in), f"Avg {fmt_pct(avg_rate_22)}")

    with z3:
        st.markdown("#### IRMAA-Safe Max")
        # Find the largest conversion that doesn't trigger IRMAA
        irmaa_safe = max(irmaa_tiers[0][0] - base.base_magi, 0)
        st.metric("Conversion", fmt_dollars(irmaa_safe))
        if irmaa_safe > 0:
            irmaa_result = all_in_at_conversion(
                hh, base, irmaa_safe, net_inv_income, ltcg_eligible=_ltcg_eligible
            )
            avg_rate_i = irmaa_result.all_in / max(irmaa_safe, 1)
            st.metric("All-In Cost", fmt_dollars(irmaa_result.all_in), f"Avg {fmt_pct(avg_rate_i)}")
        else:
            st.metric("All-In Cost", "N/A", "Base MAGI exceeds tier 1")

    # --- Sweet spot alerts ---
    if sweet_spots:
        st.markdown("### Cost Jump Points")
        st.caption("Converting beyond these amounts triggers a significant cost increase.")
        for sp in sweet_spots:
            st.warning(
                f"**{sp.label}** — marginal cost jumps from "
                f"{sp.marginal_before:.0f}% to {sp.marginal_after:.0f}% of each $1K converted "
                f"({sp.reason})"
            )

    # --- Cumulative all-in cost chart ---
    st.markdown("### Cumulative All-In Cost")
    st.caption("Total cost (tax + IRMAA + ACA + NIIT) at each conversion level.")

    fig_c = go.Figure()
    fig_c.add_trace(
        go.Scatter(
            x=convs,
            y=[r.conv_tax for r in results],
            name="Federal Tax",
            stackgroup="one",
            line={"color": "#3b82f6"},
        )
    )
    fig_c.add_trace(
        go.Scatter(
            x=convs,
            y=[r.irmaa_delta for r in results],
            name="IRMAA",
            stackgroup="one",
            line={"color": "#ef4444"},
        )
    )
    if any(r.aca_loss > 0 for r in results):
        fig_c.add_trace(
            go.Scatter(
                x=convs,
                y=[r.aca_loss for r in results],
                name="ACA Loss",
                stackgroup="one",
                line={"color": "#f59e0b"},
            )
        )
    if any(r.niit_delta > 0 for r in results):
        fig_c.add_trace(
            go.Scatter(
                x=convs,
                y=[r.niit_delta for r in results],
                name="NIIT",
                stackgroup="one",
                line={"color": "#8b5cf6"},
            )
        )

    # Effective rate overlay
    eff_rates = [r.all_in / max(r.conv, 1) * 100 if r.conv > 0 else 0 for r in results]
    fig_c.add_trace(
        go.Scatter(
            x=convs,
            y=eff_rates,
            name="Avg Eff Rate %",
            yaxis="y2",
            line={"color": "#10b981", "width": 2, "dash": "dot"},
            hovertemplate="Eff Rate: %{y:.1f}%<extra></extra>",
        )
    )

    fig_c.update_layout(
        xaxis_title="Conversion Amount ($)",
        yaxis_title="Cumulative Cost ($)",
        yaxis2={
            "title": "Effective Rate (%)",
            "overlaying": "y",
            "side": "right",
            "range": [0, 40],
        },
        xaxis_tickformat="$,.0s",
        yaxis_tickformat="$,.0s",
        height=450,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_c, width="stretch")

    # --- Multi-year comparison ---
    st.markdown("### Multi-Year Sweet Spot Summary")
    st.caption("Quick comparison of recommended zones across all conversion years.")

    summary_rows = compute_multi_year_summary(
        hh, net_inv_income=net_inv_income, ytd=_ytd, include_ltcg_stacking=True
    )

    import pandas as pd

    rows_fmt = []
    for s in summary_rows:
        row = {
            "Year": str(s.year),
            "You/Sp": f"{s.you_age}/{s.spouse_age}",
            "Base MAGI": fmt_dollars(s.base_magi),
            "Fill 12%": fmt_dollars(s.fill_12),
            "12% Cost": fmt_dollars(s.cost_12) if s.fill_12 > 0 else "---",
            "12% Rate": fmt_pct(s.rate_12) if s.fill_12 > 0 else "---",
            "Fill 22%": fmt_dollars(s.fill_22),
            "22% Cost": fmt_dollars(s.cost_22) if s.fill_22 > 0 else "---",
            "22% Rate": fmt_pct(s.rate_22) if s.fill_22 > 0 else "---",
            "IRMAA Safe": fmt_dollars(s.irmaa_safe) if s.irmaa_safe is not None else fmt_dollars(0),
        }
        rows_fmt.append(row)

    df = pd.DataFrame(rows_fmt)
    st.dataframe(df, hide_index=True, width="stretch")

    # --- Guidance ---
    st.markdown("---")
    st.markdown("### How to Use This")
    st.markdown("""
- **Flat sections** in the marginal chart are "sweet zones" — low marginal cost per dollar converted
- **Jumps** indicate bracket boundaries, IRMAA tier crossings, ACA cliffs, or NIIT thresholds
- **Fill to 12%** is typically the safest conversion — low tax rate with no hidden costs
- **Fill to 22%** converts more aggressively but may trigger IRMAA in 2 years
- **IRMAA-Safe Max** is the most you can convert without triggering Medicare surcharges
- Compare the **average effective rate** against your expected RMD-era marginal rate (often 22-24%)
- If the all-in rate is **below your future marginal rate**, the conversion saves money long-term
""")
