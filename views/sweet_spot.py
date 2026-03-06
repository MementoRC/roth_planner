"""Sweet Spot Finder — find the optimal Roth conversion amount per year.

Sweeps conversion amounts from $0 to bracket ceiling and plots:
- Marginal all-in cost per additional $1,000 converted
- Cumulative all-in cost (tax + IRMAA + ACA loss + NIIT)
- Bracket boundaries, IRMAA thresholds, ACA cliff, NIIT threshold
- Recommended "sweet spot" zones
"""

import plotly.graph_objects as go
import streamlit as st

from engine.aca import aca_applies, aca_subsidy_loss
from engine.irmaa import IRMAA_TIERS_MFJ, irmaa_for_year
from engine.ira import ss_benefit_at_age, ss_with_cola
from engine.niit import NIIT_THRESHOLD_MFJ, niit
from engine.tax import (
    BRACKETS_MFJ,
    deductions,
    federal_tax,
    room_to_12,
    room_to_22,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import Household

STEP = 1_000  # sweep in $1K increments


def _base_income_for_year(hh: Household, year: int) -> dict:
    """Compute fixed income components for a given year (no conversion)."""
    ya = hh.your_age_in(year)
    sa = hh.spouse_age_in(year)

    opt = hh.option_income(year, early=True)

    your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.ss_start_age)
    spouse_ss_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.ss_start_age)
    your_ss = (
        ss_with_cola(your_ss_base, ya - hh.ss_start_age, hh.ss_cola)
        if ya >= hh.ss_start_age
        else 0.0
    )
    spouse_ss = (
        ss_with_cola(spouse_ss_base, sa - hh.ss_start_age, hh.ss_cola)
        if sa >= hh.ss_start_age
        else 0.0
    )
    combined_ss = your_ss + spouse_ss

    ded = deductions(ya, sa, hh.std_deduction, hh.senior_extra)

    # Base taxable SS (without conversion)
    tss = taxable_ss(combined_ss, opt)

    # Base gross (without conversion)
    base_gross = opt + tss

    # MAGI base (without conversion)
    base_magi = opt + combined_ss

    # Senior bonus deduction
    senior_bonus = senior_bonus_deduction(ya, sa, base_magi)
    total_ded = ded + senior_bonus

    return {
        "ya": ya,
        "sa": sa,
        "opt": opt,
        "combined_ss": combined_ss,
        "base_gross": base_gross,
        "base_magi": base_magi,
        "total_ded": total_ded,
        "ded_base": ded,
    }


def _all_in_at_conversion(hh: Household, base: dict, conv: float,
                           net_inv_income: float) -> dict:
    """Compute all-in costs at a given conversion amount."""
    ya, sa = base["ya"], base["sa"]

    # Recalculate taxable SS with conversion income
    other_inc = base["opt"] + conv
    tss = taxable_ss(base["combined_ss"], other_inc)

    gross = base["opt"] + conv + tss
    magi = base["base_magi"] + conv

    # Recalculate senior bonus deduction at new MAGI
    senior_bonus = senior_bonus_deduction(ya, sa, magi)
    total_ded = base["ded_base"] + senior_bonus

    taxable_inc = max(gross - total_ded, 0)
    tax = federal_tax(taxable_inc)

    # Base tax (no conversion)
    base_tss = taxable_ss(base["combined_ss"], base["opt"])
    base_gross = base["opt"] + base_tss
    base_senior = senior_bonus_deduction(ya, sa, base["base_magi"])
    base_total_ded = base["ded_base"] + base_senior
    base_taxable = max(base_gross - base_total_ded, 0)
    base_tax = federal_tax(base_taxable)

    conv_tax = tax - base_tax

    # IRMAA (2-year lookback)
    irmaa_cost, _ = irmaa_for_year(magi, ya, sa)
    base_irmaa, _ = irmaa_for_year(base["base_magi"], ya, sa)
    irmaa_delta = irmaa_cost - base_irmaa

    # ACA
    anyone_on_aca = (
        aca_applies(ya, hh.your_aca_enrolled)
        or aca_applies(sa, hh.spouse_aca_enrolled)
    )
    if anyone_on_aca:
        aca_loss = aca_subsidy_loss(base["base_magi"], magi)
    else:
        aca_loss = 0.0

    # NIIT
    niit_with = niit(magi, net_inv_income)
    niit_without = niit(base["base_magi"], net_inv_income)
    niit_delta = niit_with - niit_without

    all_in = conv_tax + irmaa_delta + aca_loss + niit_delta

    return {
        "conv": conv,
        "conv_tax": conv_tax,
        "irmaa_delta": irmaa_delta,
        "aca_loss": aca_loss,
        "niit_delta": niit_delta,
        "all_in": all_in,
        "magi": magi,
        "taxable_inc": taxable_inc,
        "room_12": room_to_12(gross, total_ded),
        "room_22": room_to_22(gross, total_ded),
    }


def _find_sweet_spots(results: list[dict]) -> list[dict]:
    """Identify zones where marginal cost jumps significantly."""
    spots = []
    if len(results) < 2:
        return spots

    prev_marginal = 0.0
    for i in range(1, len(results)):
        curr = results[i]
        prev = results[i - 1]
        if curr["conv"] == 0:
            continue
        marginal = (curr["all_in"] - prev["all_in"]) / STEP * 100  # per $100
        if i > 1 and marginal - prev_marginal > 2.0:  # >2% jump per $1K
            spots.append({
                "conv": prev["conv"],
                "label": f"${prev['conv']:,.0f}",
                "reason": _classify_jump(prev, curr),
                "marginal_before": prev_marginal,
                "marginal_after": marginal,
            })
        prev_marginal = marginal

    return spots


def _classify_jump(before: dict, after: dict) -> str:
    """Classify what caused a marginal cost jump."""
    reasons = []
    if after["irmaa_delta"] > before["irmaa_delta"] + 100:
        reasons.append("IRMAA tier")
    if after["aca_loss"] > before["aca_loss"] + 100:
        reasons.append("ACA cliff")
    if after["niit_delta"] > before["niit_delta"] + 10:
        reasons.append("NIIT threshold")
    if not reasons:
        reasons.append("bracket change")
    return " + ".join(reasons)


def render(hh: Household):
    st.title("🎯 Sweet Spot Finder")
    st.caption(
        "Find the optimal Roth conversion amount where marginal cost jumps. "
        "The sweet spot is just before a bracket boundary, IRMAA tier, or ACA cliff."
    )

    # --- Year selector ---
    conv_years = list(range(hh.base_year, hh.base_year + hh.your_conv_window))
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

    # --- Compute base income ---
    base = _base_income_for_year(hh, selected_year)

    # --- Info bar ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Option Income", f"${base['opt']:,.0f}")
    c2.metric("Combined SS", f"${base['combined_ss']:,.0f}")
    c3.metric("Deductions", f"${base['total_ded']:,.0f}")

    base_result = _all_in_at_conversion(hh, base, 0, net_inv_income)
    c4.metric("Base MAGI", f"${base['base_magi']:,.0f}")

    # --- Sweep conversion amounts ---
    max_conv = int(min(
        base["total_ded"] + BRACKETS_MFJ[-2][0],  # up to 35% bracket
        hh.your_ira + hh.spouse_ira,
    ))
    max_conv = min(max_conv, 800_000)  # cap at $800K for performance

    convs = list(range(0, max_conv + STEP, STEP))
    results = [_all_in_at_conversion(hh, base, c, net_inv_income) for c in convs]

    # --- Marginal cost chart ---
    st.markdown("### Marginal All-In Cost per $1,000 Converted")
    st.caption(
        "Shows the cost of converting one more $1,000. "
        "Flat sections are sweet zones; jumps indicate bracket/tier boundaries."
    )

    marginals = [0.0]
    for i in range(1, len(results)):
        m = (results[i]["all_in"] - results[i - 1]["all_in"]) / STEP * 1000
        marginals.append(m)

    # Decompose marginals
    marginal_tax = [0.0]
    marginal_irmaa = [0.0]
    marginal_aca = [0.0]
    marginal_niit = [0.0]
    for i in range(1, len(results)):
        marginal_tax.append(
            (results[i]["conv_tax"] - results[i - 1]["conv_tax"]) / STEP * 1000
        )
        marginal_irmaa.append(
            (results[i]["irmaa_delta"] - results[i - 1]["irmaa_delta"]) / STEP * 1000
        )
        marginal_aca.append(
            (results[i]["aca_loss"] - results[i - 1]["aca_loss"]) / STEP * 1000
        )
        marginal_niit.append(
            (results[i]["niit_delta"] - results[i - 1]["niit_delta"]) / STEP * 1000
        )

    fig_m = go.Figure()
    fig_m.add_trace(go.Scatter(
        x=convs, y=marginal_tax, name="Fed Tax",
        stackgroup="one", line={"color": "#3b82f6"},
        hovertemplate="Fed Tax: $%{y:.0f} per $1K<extra></extra>",
    ))
    fig_m.add_trace(go.Scatter(
        x=convs, y=marginal_irmaa, name="IRMAA",
        stackgroup="one", line={"color": "#ef4444"},
        hovertemplate="IRMAA: $%{y:.0f} per $1K<extra></extra>",
    ))
    if any(v > 0 for v in marginal_aca):
        fig_m.add_trace(go.Scatter(
            x=convs, y=marginal_aca, name="ACA Loss",
            stackgroup="one", line={"color": "#f59e0b"},
            hovertemplate="ACA Loss: $%{y:.0f} per $1K<extra></extra>",
        ))
    if any(v > 0 for v in marginal_niit):
        fig_m.add_trace(go.Scatter(
            x=convs, y=marginal_niit, name="NIIT",
            stackgroup="one", line={"color": "#8b5cf6"},
            hovertemplate="NIIT: $%{y:.0f} per $1K<extra></extra>",
        ))

    # Add bracket boundary lines
    bracket_boundaries = []
    for ceil, rate in BRACKETS_MFJ[:-1]:
        boundary_conv = max(base["total_ded"] + ceil - base["base_gross"] - base["opt"], 0)
        # Adjust for the fact that conversion changes taxable SS
        if 0 < boundary_conv < max_conv:
            bracket_boundaries.append((boundary_conv, rate))
            fig_m.add_vline(
                x=boundary_conv, line_dash="dot", line_color="#94a3b8",
                annotation_text=f"{rate*100:.0f}% bracket",
                annotation_position="top",
            )

    # IRMAA threshold lines
    for threshold, _, _ in IRMAA_TIERS_MFJ:
        irmaa_conv = threshold - base["base_magi"]
        if 0 < irmaa_conv < max_conv:
            fig_m.add_vline(
                x=irmaa_conv, line_dash="dash", line_color="#ef4444",
                annotation_text=f"IRMAA ${threshold/1000:.0f}K",
                annotation_position="bottom",
            )

    # NIIT threshold line
    niit_conv = NIIT_THRESHOLD_MFJ - base["base_magi"]
    if 0 < niit_conv < max_conv and net_inv_income > 0:
        fig_m.add_vline(
            x=niit_conv, line_dash="dash", line_color="#8b5cf6",
            annotation_text="NIIT $250K",
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
    sweet_spots = _find_sweet_spots(results)

    # Also find the room-to-12% and room-to-22% values
    room_12 = base_result["room_12"]
    room_22 = base_result["room_22"]

    st.markdown("### Recommended Conversion Zones")

    z1, z2, z3 = st.columns(3)
    with z1:
        st.markdown("#### Fill to 12%")
        st.metric("Conversion", f"${room_12:,.0f}")
        r12_result = _all_in_at_conversion(hh, base, room_12, net_inv_income)
        avg_rate = r12_result["all_in"] / max(room_12, 1) * 100
        st.metric("All-In Cost", f"${r12_result['all_in']:,.0f}", f"Avg {avg_rate:.1f}%")

    with z2:
        st.markdown("#### Fill to 22%")
        st.metric("Conversion", f"${room_22:,.0f}")
        r22_result = _all_in_at_conversion(hh, base, room_22, net_inv_income)
        avg_rate_22 = r22_result["all_in"] / max(room_22, 1) * 100
        st.metric("All-In Cost", f"${r22_result['all_in']:,.0f}", f"Avg {avg_rate_22:.1f}%")

    with z3:
        st.markdown("#### IRMAA-Safe Max")
        # Find the largest conversion that doesn't trigger IRMAA
        irmaa_safe = max(IRMAA_TIERS_MFJ[0][0] - base["base_magi"], 0)
        st.metric("Conversion", f"${irmaa_safe:,.0f}")
        if irmaa_safe > 0:
            irmaa_result = _all_in_at_conversion(hh, base, irmaa_safe, net_inv_income)
            avg_rate_i = irmaa_result["all_in"] / max(irmaa_safe, 1) * 100
            st.metric("All-In Cost", f"${irmaa_result['all_in']:,.0f}", f"Avg {avg_rate_i:.1f}%")
        else:
            st.metric("All-In Cost", "N/A", "Base MAGI exceeds tier 1")

    # --- Sweet spot alerts ---
    if sweet_spots:
        st.markdown("### Cost Jump Points")
        st.caption("Converting beyond these amounts triggers a significant cost increase.")
        for sp in sweet_spots:
            st.warning(
                f"**{sp['label']}** — marginal cost jumps from "
                f"${sp['marginal_before']:.0f} to ${sp['marginal_after']:.0f} per $1K "
                f"({sp['reason']})"
            )

    # --- Cumulative all-in cost chart ---
    st.markdown("### Cumulative All-In Cost")
    st.caption("Total cost (tax + IRMAA + ACA + NIIT) at each conversion level.")

    fig_c = go.Figure()
    fig_c.add_trace(go.Scatter(
        x=convs,
        y=[r["conv_tax"] for r in results],
        name="Federal Tax",
        stackgroup="one",
        line={"color": "#3b82f6"},
    ))
    fig_c.add_trace(go.Scatter(
        x=convs,
        y=[r["irmaa_delta"] for r in results],
        name="IRMAA",
        stackgroup="one",
        line={"color": "#ef4444"},
    ))
    if any(r["aca_loss"] > 0 for r in results):
        fig_c.add_trace(go.Scatter(
            x=convs,
            y=[r["aca_loss"] for r in results],
            name="ACA Loss",
            stackgroup="one",
            line={"color": "#f59e0b"},
        ))
    if any(r["niit_delta"] > 0 for r in results):
        fig_c.add_trace(go.Scatter(
            x=convs,
            y=[r["niit_delta"] for r in results],
            name="NIIT",
            stackgroup="one",
            line={"color": "#8b5cf6"},
        ))

    # Effective rate overlay
    eff_rates = [
        r["all_in"] / max(r["conv"], 1) * 100 if r["conv"] > 0 else 0
        for r in results
    ]
    fig_c.add_trace(go.Scatter(
        x=convs,
        y=eff_rates,
        name="Avg Eff Rate %",
        yaxis="y2",
        line={"color": "#10b981", "width": 2, "dash": "dot"},
        hovertemplate="Eff Rate: %{y:.1f}%<extra></extra>",
    ))

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

    rows = []
    for yr in conv_years:
        b = _base_income_for_year(hh, yr)
        b_result = _all_in_at_conversion(hh, b, 0, net_inv_income)
        r12 = b_result["room_12"]
        r22 = b_result["room_22"]
        irmaa_max = max(IRMAA_TIERS_MFJ[0][0] - b["base_magi"], 0)

        r12_res = _all_in_at_conversion(hh, b, r12, net_inv_income) if r12 > 0 else None
        r22_res = _all_in_at_conversion(hh, b, r22, net_inv_income) if r22 > 0 else None

        row = {
            "Year": str(yr),
            "You/Sp": f"{b['ya']}/{b['sa']}",
            "Base MAGI": f"${b['base_magi']:,.0f}",
            "Fill 12%": f"${r12:,.0f}",
            "12% Cost": f"${r12_res['all_in']:,.0f}" if r12_res else "---",
            "12% Rate": f"{r12_res['all_in'] / max(r12, 1) * 100:.1f}%" if r12_res else "---",
            "Fill 22%": f"${r22:,.0f}",
            "22% Cost": f"${r22_res['all_in']:,.0f}" if r22_res else "---",
            "22% Rate": f"{r22_res['all_in'] / max(r22, 1) * 100:.1f}%" if r22_res else "---",
            "IRMAA Safe": f"${irmaa_max:,.0f}",
        }
        rows.append(row)

    import pandas as pd

    df = pd.DataFrame(rows)
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
