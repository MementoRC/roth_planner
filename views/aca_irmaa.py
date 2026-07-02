"""ACA + IRMAA Explorer — interactive cost visualizer.

Shows how conversion amounts affect:
1. ACA subsidy loss (ages 61-64, pre-Medicare)
2. IRMAA surcharges (ages 65+, 2-year lookback)
3. Combined "hidden cost" zones where conversions trigger cliffs
4. Year-by-year timeline of which system applies
"""

import plotly.graph_objects as go
import streamlit as st

from engine.aca import (
    FPL_1,
    FPL_2,
    _aca_cap_schedule,
    aca_applies,
)
from engine.aca_irmaa_compute import (
    compute_cost_curves,
    compute_year_by_year_timeline,
    index_irmaa_tier_thresholds,
)
from engine.irmaa import (
    BASE_PART_B,
    IRMAA_TIERS_MFJ,
    IRMAA_TIERS_SINGLE,
)
from engine.niit import NIIT_RATE, NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE
from engine.tax_indexing import index_value as _index_value
from models.household import Household
from views._format import fmt_dollars, fmt_pct


def render(hh: Household):
    st.title("🏥 ACA + IRMAA Explorer")
    anyone_on_aca = aca_applies(hh.your_age, hh.your_aca_enrolled) or aca_applies(
        hh.spouse_age, hh.spouse_aca_enrolled
    )
    aca_status = "Enrolled" if anyone_on_aca else "Not enrolled"
    st.caption(
        f"You {hh.your_age} / Spouse {hh.spouse_age} · "
        f"ACA: {aca_status} · IRMAA lookback: 2 years · "
        f"Enhanced subsidies: {'Active' if hh.aca_enhanced_subsidies_active else 'Expired (pre-ARP rules)'}"
    )

    # --- Interactive MAGI slider ---
    st.markdown("### Explore: Cost of Additional Income")
    col_slider, col_info = st.columns([3, 1])

    with col_slider:
        magi_range = st.slider(
            "MAGI range to explore ($)",
            min_value=0,
            max_value=500_000,
            value=(20_000, 300_000),
            step=5_000,
            format="$%d",
        )

    with col_info:
        base_magi = st.number_input(
            "Your base MAGI (no conversion)",
            value=int(hh.option_income(hh.base_year, True)),
            step=5_000,
            format="%d",
            help="Income before any Roth conversion (options, SS, RMD, etc.)",
        )
        _niit_threshold = (
            NIIT_THRESHOLD_SINGLE if hh.filing_status == "Single" else NIIT_THRESHOLD_MFJ
        )
        net_inv_income = st.number_input(
            "Net investment income ($/yr)",
            value=0,
            step=5_000,
            format="%d",
            help=f"Capital gains + dividends + interest. NIIT = {fmt_pct(NIIT_RATE)} when MAGI > ${_niit_threshold // 1000:.0f}K",
        )

    # --- Generate cost curves ---
    magi_points = list(range(magi_range[0], magi_range[1] + 1, 1_000))

    _view_year = hh.base_year
    _view_cpi = hh.cpi_assumption

    curves = compute_cost_curves(
        magi_points, base_magi, net_inv_income, hh, year=_view_year, cpi=_view_cpi
    )

    # --- Chart 1: ACA Subsidy & Net Premium ---
    st.markdown("---")
    col_aca, col_irmaa = st.columns(2)

    with col_aca:
        st.markdown("### ACA Marketplace (Ages 61-64)")
        if anyone_on_aca:
            fig_aca = go.Figure()
            fig_aca.add_trace(
                go.Scatter(
                    x=magi_points,
                    y=curves.aca_subsidy_vals,
                    name="Subsidy",
                    line={"color": "#22c55e", "width": 2},
                    hovertemplate="MAGI: $%{x:,.0f}<br>Subsidy: $%{y:,.0f}<extra></extra>",
                )
            )
            fig_aca.add_trace(
                go.Scatter(
                    x=magi_points,
                    y=curves.aca_net_cost_vals,
                    name="You Pay",
                    line={"color": "#ef4444", "width": 2},
                    hovertemplate="MAGI: $%{x:,.0f}<br>You Pay: $%{y:,.0f}<extra></extra>",
                )
            )
            num_on_aca = (1 if aca_applies(hh.your_age, hh.your_aca_enrolled) else 0) + (
                1 if aca_applies(hh.spouse_age, hh.spouse_aca_enrolled) else 0
            )
            effective_benchmark = hh.aca_benchmark_premium_annual * (num_on_aca / 2)
            fig_aca.add_hline(
                y=effective_benchmark,
                line_dash="dot",
                line_color="gray",
                annotation_text=f"Full premium: {fmt_dollars(effective_benchmark)}",
            )

            # Mark FPL thresholds
            if not hh.aca_enhanced_subsidies_active:
                cliff_fpl = FPL_1 if hh.filing_status == "Single" else FPL_2
                cliff_magi = 4.0 * _index_value(cliff_fpl, _view_year, _view_cpi)
                # The x-axis is raw (IRMAA) MAGI, but ACA MAGI adds back non-taxable SS
                # (IRC §36B), so the subsidy cliff falls at raw MAGI = 4*FPL −
                # nontaxable_ss on this axis. Place the marker there so it lines up with
                # the ACA curve's actual drop (audit C7 / aca-4). nontaxable_ss is 0
                # unless SS is drawn during the ACA years → no-op for the default HH.
                cliff_x = cliff_magi - curves.nontaxable_ss
                fig_aca.add_vline(
                    x=cliff_x,
                    line_dash="dash",
                    line_color="#ef4444",
                    annotation_text=f"400% FPL cliff: {fmt_dollars(cliff_x)}",
                )

            fig_aca.update_layout(
                title="ACA Subsidy vs What You Pay",
                xaxis_title="MAGI ($)",
                xaxis_tickformat="$,.0s",
                yaxis_title="Annual ($)",
                yaxis_tickformat="$,.0s",
                height=400,
                legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
            )
            st.plotly_chart(fig_aca, width="stretch")
        else:
            if hh.your_age >= 65 or hh.spouse_age >= 65:
                st.info("On Medicare. See IRMAA section below.")
            else:
                st.info(
                    "ACA not enrolled. Go to ⚙️ Setup → 📊 Parameters → Me (or Spouse) "
                    "and toggle 'On ACA Marketplace' to model ACA subsidies."
                )

    # --- Chart 2: IRMAA Tiers ---
    with col_irmaa:
        st.markdown("### IRMAA Medicare Surcharges (65+)")
        fig_irmaa = go.Figure()
        fig_irmaa.add_trace(
            go.Scatter(
                x=magi_points,
                y=curves.irmaa_vals,
                name="IRMAA Surcharge (2 people)",
                line={"color": "#f59e0b", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(245,158,11,0.15)",
                hovertemplate="MAGI: $%{x:,.0f}<br>Surcharge: $%{y:,.0f}/yr<extra></extra>",
            )
        )

        # Mark tier thresholds (IRMAA 2-yr lookback: indexed to payment year = base_year + 2)
        _base_irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
        _irmaa_tiers = index_irmaa_tier_thresholds(
            _base_irmaa_tiers, year=_view_year + 2, cpi=_view_cpi
        )
        for threshold, _part_b, _ in _irmaa_tiers:
            if magi_range[0] <= threshold <= magi_range[1]:
                fig_irmaa.add_vline(
                    x=threshold,
                    line_dash="dot",
                    line_color="rgba(245,158,11,0.5)",
                )

        fig_irmaa.update_layout(
            title="Annual IRMAA Surcharge (Both Spouses)",
            xaxis_title="MAGI ($)",
            xaxis_tickformat="$,.0s",
            yaxis_title="Surcharge ($/yr)",
            yaxis_tickformat="$,.0s",
            height=400,
        )
        st.plotly_chart(fig_irmaa, width="stretch")

    # --- Chart 3: Combined Hidden Cost ---
    st.markdown("### Total Hidden Cost of Conversion Income")
    st.caption(
        f"Base MAGI: {fmt_dollars(base_magi)} · Net investment income: {fmt_dollars(net_inv_income)} — "
        "shows ACA subsidy loss + IRMAA increase + NIIT as you add conversion income"
    )

    fig_hidden = go.Figure()

    # Stacked area: ACA loss + IRMAA increase + NIIT increase
    fig_hidden.add_trace(
        go.Scatter(
            x=magi_points,
            y=curves.aca_subsidy_loss_vals,
            name="ACA Subsidy Lost",
            stackgroup="cost",
            line={"color": "#22c55e"},
            fillcolor="rgba(34,197,94,0.3)",
            hovertemplate="MAGI: $%{x:,.0f}<br>ACA Lost: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_hidden.add_trace(
        go.Scatter(
            x=magi_points,
            y=curves.irmaa_increase_vals,
            name="IRMAA Increase",
            stackgroup="cost",
            line={"color": "#f59e0b"},
            fillcolor="rgba(245,158,11,0.3)",
            hovertemplate="MAGI: $%{x:,.0f}<br>IRMAA: $%{y:,.0f}<extra></extra>",
        )
    )
    fig_hidden.add_trace(
        go.Scatter(
            x=magi_points,
            y=curves.niit_increase_vals,
            name=f"NIIT ({fmt_pct(NIIT_RATE)})",
            stackgroup="cost",
            line={"color": "#8b5cf6"},
            fillcolor="rgba(139,92,246,0.3)",
            hovertemplate="MAGI: $%{x:,.0f}<br>NIIT: $%{y:,.0f}<extra></extra>",
        )
    )

    # Overlay marginal tax rate as secondary axis
    fig_hidden.add_trace(
        go.Scatter(
            x=magi_points,
            y=[r * 100 for r in curves.marginal_rate_vals],
            name="Marginal Tax Rate",
            yaxis="y2",
            line={"color": "#3b82f6", "width": 1, "dash": "dot"},
            hovertemplate="MAGI: $%{x:,.0f}<br>Marginal: %{y:.0f}%<extra></extra>",
        )
    )

    fig_hidden.add_vline(
        x=base_magi,
        line_dash="dash",
        line_color="gray",
        annotation_text="Base MAGI",
    )

    fig_hidden.update_layout(
        title="Hidden Costs Above Base MAGI (ACA Loss + IRMAA + NIIT)",
        xaxis_title="MAGI ($)",
        xaxis_tickformat="$,.0s",
        yaxis_title="Hidden Cost ($/yr)",
        yaxis_tickformat="$,.0s",
        yaxis2={
            "title": "Marginal Rate (%)",
            "overlaying": "y",
            "side": "right",
            "range": [0, 40],
            "showgrid": False,
        },
        height=450,
        legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
    )
    st.plotly_chart(fig_hidden, width="stretch")

    # --- Year-by-Year Timeline ---
    st.markdown("---")
    st.markdown("### Your ACA → IRMAA Timeline")
    st.caption("Which system applies each year and key thresholds to watch")

    import pandas as pd

    _yr_cpi = hh.cpi_assumption
    timeline_rows = compute_year_by_year_timeline(hh, base_magi, years=20, cpi=_yr_cpi)

    timeline = []
    for row in timeline_rows:
        entry = {
            "Year": row.year,
            "You": row.you_age,
            "Spouse": row.spouse_age,
            "System": row.system,
            "IRMAA Tier": str(row.irmaa_tier) if row.irmaa_tier is not None else "—",
            "IRMAA Room": fmt_dollars(row.irmaa_room) if row.irmaa_room is not None else "—",
        }
        if row.aca_subsidy is not None:
            entry["ACA Subsidy"] = fmt_dollars(row.aca_subsidy)
            entry["ACA You Pay"] = fmt_dollars(row.aca_you_pay)
        else:
            entry["ACA Subsidy"] = "—"
            entry["ACA You Pay"] = "—"
        timeline.append(entry)

    df = pd.DataFrame(timeline)
    st.dataframe(df, width="stretch", hide_index=True)

    # --- Reference Tables ---
    st.markdown("---")
    col_ref1, col_ref2 = st.columns(2)

    with col_ref1:
        _fs_label = "Single" if hh.filing_status == "Single" else "MFJ"
        _ref_irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
        st.markdown(f"### IRMAA 2026 Thresholds ({_fs_label})")
        irmaa_data = []
        for i, (threshold, part_b, part_d) in enumerate(_ref_irmaa_tiers):
            surcharge_pp = (part_b - BASE_PART_B) + part_d
            irmaa_data.append(
                {
                    "Tier": i + 1,
                    "MAGI >": fmt_dollars(threshold),
                    "Part B/mo": fmt_dollars(part_b / 12, decimals=2),
                    "Part D/mo": fmt_dollars(part_d / 12, decimals=2),
                    "Surcharge/yr (×2)": fmt_dollars(surcharge_pp * 2),
                }
            )
        st.dataframe(pd.DataFrame(irmaa_data), width="stretch", hide_index=True)

    with col_ref2:
        st.markdown(
            f"### ACA Premium Schedule ({'Enhanced' if hh.aca_enhanced_subsidies_active else 'Pre-ARP'})"
        )
        aca_data = []
        for upper_fpl, cap_rate in _aca_cap_schedule(hh.aca_enhanced_subsidies_active):
            fpl_label = "400%+" if upper_fpl == float("inf") else f"≤{fmt_pct(upper_fpl, 0)}"
            aca_data.append(
                {
                    "FPL Range": fpl_label,
                    "MAGI ≤": fmt_dollars(
                        upper_fpl
                        * _index_value(
                            FPL_1 if hh.filing_status == "Single" else FPL_2,
                            _view_year,
                            _view_cpi,
                        )
                    )
                    if upper_fpl != float("inf")
                    else "No limit",
                    "Premium Cap": f"{fmt_pct(cap_rate)} of income",
                }
            )
        st.dataframe(pd.DataFrame(aca_data), width="stretch", hide_index=True)

        _fpl_label = "family of 1" if hh.filing_status == "Single" else "family of 2"
        _fpl_val = FPL_1 if hh.filing_status == "Single" else FPL_2
        st.caption(
            f"FPL ({_fpl_label}): {fmt_dollars(_fpl_val)} · "
            f"Benchmark silver plan: {fmt_dollars(hh.aca_benchmark_premium_annual)}/yr"
        )

    _niit_thr = NIIT_THRESHOLD_SINGLE if hh.filing_status == "Single" else NIIT_THRESHOLD_MFJ
    st.markdown("---")
    st.markdown("### NIIT — Net Investment Income Tax")
    st.markdown(
        f"**{fmt_pct(NIIT_RATE)} surtax** on the lesser of net investment income or MAGI above "
        f"**${_niit_thr:,}** ({hh.filing_status}). Applies to capital gains, dividends, "
        "interest, and rental income. Roth conversions are *not* investment income, "
        "but they raise MAGI, which can expose more investment income to the tax."
    )
