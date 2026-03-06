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
    ACA_CAP_SCHEDULE,
    BENCHMARK_PREMIUM_ANNUAL,
    ENHANCED_SUBSIDIES_ACTIVE,
    FPL_2,
    aca_applies,
    aca_net_cost,
    aca_subsidy,
    aca_subsidy_loss,
)
from engine.irmaa import (
    BASE_PART_B,
    IRMAA_TIERS_MFJ,
    irmaa_next_threshold,
    irmaa_surcharge,
    irmaa_tier,
)
from engine.niit import NIIT_THRESHOLD_MFJ, niit
from engine.tax import deductions, federal_tax, marginal_rate, senior_bonus_deduction
from models.household import Household


def render(hh: Household):
    st.title("🏥 ACA + IRMAA Explorer")
    anyone_on_aca = (
        aca_applies(hh.your_age, hh.your_aca_enrolled)
        or aca_applies(hh.spouse_age, hh.spouse_aca_enrolled)
    )
    aca_status = "Enrolled" if anyone_on_aca else "Not enrolled"
    st.caption(
        f"You {hh.your_age} / Spouse {hh.spouse_age} · "
        f"ACA: {aca_status} · IRMAA lookback: 2 years · "
        f"Enhanced subsidies: {'Active' if ENHANCED_SUBSIDIES_ACTIVE else 'Expired (pre-ARP rules)'}"
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
        net_inv_income = st.number_input(
            "Net investment income ($/yr)",
            value=0,
            step=5_000,
            format="%d",
            help="Capital gains + dividends + interest. NIIT = 3.8% when MAGI > $250K",
        )

    # --- Generate cost curves ---
    magi_points = list(range(magi_range[0], magi_range[1] + 1, 1_000))

    aca_subsidy_vals = []
    aca_net_cost_vals = []
    aca_loss_vals = []
    irmaa_vals = []
    irmaa_tier_vals = []
    niit_vals = []
    fed_tax_vals = []
    marginal_vals = []
    total_hidden_cost = []

    ded = deductions(hh.your_age, hh.spouse_age, hh.std_deduction, hh.senior_extra)

    for magi in magi_points:
        # ACA (only meaningful if enrolled and pre-65)
        if anyone_on_aca:
            sub = aca_subsidy(magi)
            aca_subsidy_vals.append(sub)
            aca_net_cost_vals.append(aca_net_cost(magi))
            aca_loss_vals.append(aca_subsidy_loss(base_magi, magi))
        else:
            aca_subsidy_vals.append(0)
            aca_net_cost_vals.append(0)
            aca_loss_vals.append(0)

        # IRMAA
        surcharge = irmaa_surcharge(magi, num_people=2)
        irmaa_vals.append(surcharge)
        irmaa_tier_vals.append(irmaa_tier(magi))

        # NIIT
        niit_vals.append(niit(magi, net_inv_income))

        # Tax
        bonus_ded = senior_bonus_deduction(hh.your_age, hh.spouse_age, magi)
        taxable = max(magi - ded - bonus_ded, 0)
        fed_tax_vals.append(federal_tax(taxable))
        marginal_vals.append(marginal_rate(taxable))

        # Combined hidden cost (ACA loss + IRMAA beyond base + NIIT increase)
        base_irmaa = irmaa_surcharge(base_magi, num_people=2)
        base_niit = niit(base_magi, net_inv_income)
        hidden = (
            aca_subsidy_loss(base_magi, magi)
            + max(surcharge - base_irmaa, 0)
            + max(niit(magi, net_inv_income) - base_niit, 0)
        )
        total_hidden_cost.append(hidden)

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
                    y=aca_subsidy_vals,
                    name="Subsidy",
                    line={"color": "#22c55e", "width": 2},
                    hovertemplate="MAGI: $%{x:,.0f}<br>Subsidy: $%{y:,.0f}<extra></extra>",
                )
            )
            fig_aca.add_trace(
                go.Scatter(
                    x=magi_points,
                    y=aca_net_cost_vals,
                    name="You Pay",
                    line={"color": "#ef4444", "width": 2},
                    hovertemplate="MAGI: $%{x:,.0f}<br>You Pay: $%{y:,.0f}<extra></extra>",
                )
            )
            fig_aca.add_hline(
                y=BENCHMARK_PREMIUM_ANNUAL,
                line_dash="dot",
                line_color="gray",
                annotation_text=f"Full premium: ${BENCHMARK_PREMIUM_ANNUAL:,.0f}",
            )

            # Mark FPL thresholds
            if not ENHANCED_SUBSIDIES_ACTIVE:
                cliff_magi = 4.0 * FPL_2
                fig_aca.add_vline(
                    x=cliff_magi,
                    line_dash="dash",
                    line_color="#ef4444",
                    annotation_text=f"400% FPL cliff: ${cliff_magi:,.0f}",
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
            if hh.your_age >= 65:
                st.info(
                    f"You are {hh.your_age} — on Medicare. See IRMAA section."
                )
            else:
                st.info(
                    "ACA not enrolled. Toggle 'You/Spouse on ACA Marketplace' "
                    "in the sidebar to model ACA subsidies."
                )

    # --- Chart 2: IRMAA Tiers ---
    with col_irmaa:
        st.markdown("### IRMAA Medicare Surcharges (65+)")
        fig_irmaa = go.Figure()
        fig_irmaa.add_trace(
            go.Scatter(
                x=magi_points,
                y=irmaa_vals,
                name="IRMAA Surcharge (2 people)",
                line={"color": "#f59e0b", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(245,158,11,0.15)",
                hovertemplate="MAGI: $%{x:,.0f}<br>Surcharge: $%{y:,.0f}/yr<extra></extra>",
            )
        )

        # Mark tier thresholds
        for threshold, _part_b, _ in IRMAA_TIERS_MFJ:
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
        f"Base MAGI: ${base_magi:,.0f} · Net investment income: ${net_inv_income:,.0f} — "
        "shows ACA subsidy loss + IRMAA increase + NIIT as you add conversion income"
    )

    fig_hidden = go.Figure()

    # Stacked area: ACA loss + IRMAA increase + NIIT increase
    base_irmaa = irmaa_surcharge(base_magi, num_people=2)
    base_niit = niit(base_magi, net_inv_income)
    irmaa_increase = [max(v - base_irmaa, 0) for v in irmaa_vals]
    niit_increase = [max(v - base_niit, 0) for v in niit_vals]

    fig_hidden.add_trace(
        go.Scatter(
            x=magi_points,
            y=aca_loss_vals,
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
            y=irmaa_increase,
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
            y=niit_increase,
            name="NIIT (3.8%)",
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
            y=[r * 100 for r in marginal_vals],
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

    timeline = []
    for yr_idx in range(20):
        year = hh.base_year + yr_idx
        ya = hh.your_age_in(year)
        sa = hh.spouse_age_in(year)

        you_on_aca = aca_applies(ya, hh.your_aca_enrolled)
        sp_on_aca = aca_applies(sa, hh.spouse_aca_enrolled)
        on_medicare_you = ya >= 65
        on_medicare_sp = sa >= 65
        medicare_count = sum(1 for a in [ya, sa] if a >= 65)

        # Determine system per person
        parts = []
        if you_on_aca:
            parts.append("ACA (you)")
        elif on_medicare_you:
            parts.append("Medicare (you)")
        else:
            parts.append("Employer (you)")
        if sp_on_aca:
            parts.append("ACA (sp)")
        elif on_medicare_sp:
            parts.append("Medicare (sp)")
        elif hh.spouse_aca_enrolled:
            parts.append("ACA (sp)")
        else:
            parts.append("Uninsured/Other (sp)")
        system = " + ".join(parts)

        irmaa_room = irmaa_next_threshold(base_magi) if medicare_count > 0 else None

        row = {
            "Year": year,
            "You": ya,
            "Spouse": sa,
            "System": system,
            "IRMAA Tier": str(irmaa_tier(base_magi)) if medicare_count > 0 else "—",
            "IRMAA Room": f"${irmaa_room:,.0f}" if irmaa_room is not None else "—",
        }

        if you_on_aca or sp_on_aca:
            row["ACA Subsidy"] = f"${aca_subsidy(base_magi):,.0f}"
            row["ACA You Pay"] = f"${aca_net_cost(base_magi):,.0f}"
        else:
            row["ACA Subsidy"] = "—"
            row["ACA You Pay"] = "—"

        timeline.append(row)

    df = pd.DataFrame(timeline)
    st.dataframe(df, width="stretch", hide_index=True)

    # --- Reference Tables ---
    st.markdown("---")
    col_ref1, col_ref2 = st.columns(2)

    with col_ref1:
        st.markdown("### IRMAA 2026 Thresholds (MFJ)")
        irmaa_data = []
        for i, (threshold, part_b, part_d) in enumerate(IRMAA_TIERS_MFJ):
            surcharge_pp = (part_b - BASE_PART_B) + part_d
            irmaa_data.append({
                "Tier": i + 1,
                "MAGI >": f"${threshold:,.0f}",
                "Part B/mo": f"${part_b / 12:,.2f}",
                "Part D/mo": f"${part_d / 12:,.2f}",
                "Surcharge/yr (×2)": f"${surcharge_pp * 2:,.0f}",
            })
        st.dataframe(pd.DataFrame(irmaa_data), width="stretch", hide_index=True)

    with col_ref2:
        st.markdown(
            f"### ACA Premium Schedule ({'Enhanced' if ENHANCED_SUBSIDIES_ACTIVE else 'Pre-ARP'})"
        )
        aca_data = []
        for upper_fpl, cap_rate in ACA_CAP_SCHEDULE:
            fpl_label = "400%+" if upper_fpl == float("inf") else f"≤{upper_fpl:.0%}"
            aca_data.append({
                "FPL Range": fpl_label,
                "MAGI ≤": f"${upper_fpl * FPL_2:,.0f}" if upper_fpl != float("inf") else "No limit",
                "Premium Cap": f"{cap_rate:.1%} of income",
            })
        st.dataframe(pd.DataFrame(aca_data), width="stretch", hide_index=True)

        st.caption(
            f"FPL (family of 2): ${FPL_2:,.0f} · "
            f"Benchmark silver plan: ${BENCHMARK_PREMIUM_ANNUAL:,.0f}/yr"
        )

    st.markdown("---")
    st.markdown("### NIIT — Net Investment Income Tax")
    st.markdown(
        f"**3.8% surtax** on the lesser of net investment income or MAGI above "
        f"**${NIIT_THRESHOLD_MFJ:,}** (MFJ). Applies to capital gains, dividends, "
        "interest, and rental income. Roth conversions are *not* investment income, "
        "but they raise MAGI, which can expose more investment income to the tax."
    )
