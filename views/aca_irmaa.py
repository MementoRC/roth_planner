"""ACA + IRMAA Explorer — interactive cost visualizer.

Shows how conversion amounts affect:
1. ACA subsidy loss (ages 61-64, pre-Medicare)
2. IRMAA surcharges (ages 65+, 2-year lookback)
3. Combined "hidden cost" zones where conversions trigger cliffs
4. Year-by-year timeline of which system applies
"""

import plotly.graph_objects as go
import streamlit as st

from config.scalar_registry import scalar_default
from engine.aca import (
    aca_applies,
    aca_cap_bands,
    effective_benchmark_premium,
    fpl_for,
    resolve_couple_benchmark_annual,
)
from engine.aca_irmaa_compute import (
    compute_cost_curves,
    compute_year_by_year_timeline,
    default_base_magi,
    index_irmaa_tier_thresholds,
)
from engine.irmaa import (
    BASE_PART_B,
    IRMAA_TIERS_MFJ,
    IRMAA_TIERS_SINGLE,
)
from engine.niit import NIIT_RATE, NIIT_THRESHOLD_MFJ, NIIT_THRESHOLD_SINGLE
from models.household import Household
from views._format import fmt_dollars, fmt_pct, md_dollars
from views._shared import render_completeness_badge, render_readonly_input
from views._style import render_page_header

# d3 "~s" with 3 significant digits: "$250k", "$1.25M". ".0s" kept one digit,
# so neighbouring ticks repeated ("$200k", "$200k") and $0.50 showed as "$500m".
DOLLAR_TICKS = "$,.3~s"

IRMAA_ROOM_CAPTION = (
    "IRMAA Room is for income earned that year (premiums two years later); "
    "from the year after a death the survivor files Single."
)


def hidden_cost_is_flat(*series: list[float]) -> bool:
    """True when every stacked hidden-cost series is zero across the range."""
    return all(abs(v) < 0.5 for s in series for v in s)


def _age_cell(age: int | None) -> str:
    """Timeline age cell: "—" for a person no longer alive (engine gives None).

    Always a string, so the column stays one Arrow type.
    """
    return "—" if age is None else str(age)


def render(hh: Household):
    render_page_header("aca")
    anyone_on_aca = aca_applies(hh.your_age, hh.your_aca_enrolled) or aca_applies(
        hh.spouse_age, hh.spouse_aca_enrolled
    )
    aca_status = "Enrolled" if anyone_on_aca else "Not enrolled"
    _age_part = (
        f"You {hh.your_age}"
        if hh.filing_status == "Single"
        else f"You {hh.your_age} / Spouse {hh.spouse_age}"
    )
    st.caption(
        f"{_age_part} · "
        f"ACA: {aca_status} · IRMAA lookback: 2 years · "
        f"Enhanced subsidies: {'Active' if hh.aca_enhanced_subsidies_active else 'Expired (pre-ARP rules)'}"
    )
    render_completeness_badge(hh)

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

    # Inject base-year realized YTD investment income when the user opted in
    # (mirrors Sweet Spot's apply_ytd_to_projection gating, views/sweet_spot.py).
    _apply_ytd = st.session_state.get("apply_ytd_to_projection", False)
    _ytd = st.session_state.get("ytd_snapshot") if _apply_ytd else None
    net_inv_income = int(st.session_state.get("net_inv_income", scalar_default("net_inv_income")))

    with col_info:
        base_magi = st.number_input(
            "Your base MAGI (no conversion)",
            # audit-1008 F-28: the household's no-conversion MAGI (Sweet Spot's
            # "Base MAGI"), not option income alone.
            value=int(default_base_magi(hh, ytd=_ytd, net_inv_income=net_inv_income)),
            step=5_000,
            format="%d",
            help=(
                "Income before any Roth conversion. Defaults to this year's base from "
                "your profile: options, taxable Social Security, RMDs, forecast "
                "dividends and gains, YTD actuals when applied, and the IRA withdrawal "
                "that funds living expenses (same as Sweet Spot's Base MAGI)."
            ),
        )
        render_readonly_input(
            "Additional net investment income (NIIT estimate)",
            f"${net_inv_income:,.0f}/yr",
            key="nii",
            page="assumptions",
            where="Profile › Assumptions",
            stacked=True,
        )

    # --- Generate cost curves ---
    magi_points = list(range(magi_range[0], magi_range[1] + 1, 1_000))

    _view_year = hh.base_year
    _view_cpi = hh.cpi_assumption

    curves = compute_cost_curves(
        magi_points, base_magi, net_inv_income, hh, year=_view_year, cpi=_view_cpi, ytd=_ytd
    )

    # --- Chart 1: ACA Subsidy & Net Premium ---
    st.markdown("---")
    col_aca, col_irmaa = st.columns(2)

    with col_aca:
        st.markdown("### ACA Marketplace (pre-Medicare, under 65)")
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
            _resolved_couple_benchmark = resolve_couple_benchmark_annual(
                hh.aca_benchmark_premium_annual,
                your_age=hh.your_age,
                spouse_age=hh.spouse_age,
                filing_status=hh.filing_status,
                year=_view_year,
                cpi=_view_cpi,
            )
            effective_benchmark = effective_benchmark_premium(
                _resolved_couple_benchmark,
                your_age=hh.your_age,
                your_on_aca=aca_applies(hh.your_age, hh.your_aca_enrolled),
                spouse_age=hh.spouse_age,
                spouse_on_aca=aca_applies(hh.spouse_age, hh.spouse_aca_enrolled),
                filing_status=hh.filing_status,
            )
            fig_aca.add_hline(
                y=effective_benchmark,
                line_dash="dot",
                line_color="gray",
                annotation_text=f"Full premium: {fmt_dollars(effective_benchmark)}",
            )

            # Mark FPL thresholds
            if not hh.aca_enhanced_subsidies_active:
                cliff_magi = 4.0 * fpl_for(hh.filing_status, _view_year, _view_cpi)
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
                xaxis_title="MAGI ($)",
                xaxis_tickformat=DOLLAR_TICKS,
                yaxis_title="Annual ($)",
                yaxis_tickformat=DOLLAR_TICKS,
                height=400,
                legend={"yanchor": "top", "y": 0.99, "xanchor": "right", "x": 0.99},
            )
            st.plotly_chart(fig_aca, width="stretch")
        else:
            if hh.your_age >= 65 or hh.spouse_age >= 65:
                st.info("On Medicare. See IRMAA section below.")
            else:
                st.info(
                    "ACA not enrolled. Go to Profile › Household & Accounts → Me (or Spouse) "
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
                name=f"IRMAA Surcharge ({'1 person' if hh.filing_status == 'Single' else '2 people'})",
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
            xaxis_title="MAGI ($)",
            xaxis_tickformat=DOLLAR_TICKS,
            yaxis_title="Surcharge ($/yr)",
            yaxis_tickformat=DOLLAR_TICKS,
            height=400,
        )
        st.plotly_chart(fig_irmaa, width="stretch")

    # --- Chart 3: Combined Hidden Cost ---
    st.markdown("### Total Hidden Cost of Conversion Income")
    st.caption(
        md_dollars(
            f"Base MAGI: {fmt_dollars(base_magi)} · Net investment income: "
            f"{fmt_dollars(net_inv_income + curves.auto_nii)} "
            f"(manual {fmt_dollars(net_inv_income)} + auto-detected "
            f"{fmt_dollars(curves.auto_nii)}) — "
            "shows ACA subsidy loss + IRMAA increase + NIIT as you add conversion income"
        )
    )

    fig_hidden = go.Figure()

    # Stacked area: ACA loss + IRMAA increase + NIIT increase
    fig_hidden.add_trace(
        go.Scatter(
            x=magi_points,
            y=curves.aca_subsidy_loss_vals,
            name="ACA Subsidy Lost",
            stackgroup="cost",
            line={"color": "#ef4444"},
            fillcolor="rgba(239,68,68,0.3)",
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

    _flat = hidden_cost_is_flat(
        curves.aca_subsidy_loss_vals, curves.irmaa_increase_vals, curves.niit_increase_vals
    )
    if _flat:
        # An all-zero series autoranges to about ±1, which drew "-$1" and
        # "$500m" ticks. Pin a readable $0-$1k range instead.
        fig_hidden.update_yaxes(range=[0, 1_000])
    fig_hidden.update_layout(
        xaxis_title="MAGI ($)",
        xaxis_tickformat=DOLLAR_TICKS,
        yaxis_title="Hidden Cost ($/yr)",
        yaxis_tickformat=DOLLAR_TICKS,
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
    if _flat:
        st.caption("No ACA, IRMAA or NIIT cost anywhere in this MAGI range.")

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
            "You": _age_cell(row.you_age),
        }
        if hh.filing_status == "MFJ":
            entry["Spouse"] = _age_cell(row.spouse_age)
        entry.update(
            {
                "System": row.system,
                "IRMAA Tier": "—"
                if row.irmaa_tier is None
                else ("Base" if row.irmaa_tier == 0 else str(row.irmaa_tier)),
                "IRMAA Room": fmt_dollars(row.irmaa_room) if row.irmaa_room is not None else "—",
            }
        )
        if row.aca_subsidy is not None:
            entry["ACA Subsidy"] = fmt_dollars(row.aca_subsidy)
            entry["ACA You Pay"] = fmt_dollars(row.aca_you_pay)
        else:
            entry["ACA Subsidy"] = "—"
            entry["ACA You Pay"] = "—"
        timeline.append(entry)

    df = pd.DataFrame(timeline)
    st.dataframe(df, width="stretch", hide_index=True)
    st.caption(IRMAA_ROOM_CAPTION)

    # --- Reference Tables ---
    st.markdown("---")
    col_ref1, col_ref2 = st.columns(2)

    with col_ref1:
        _fs_label = "Single" if hh.filing_status == "Single" else "MFJ"
        _ref_irmaa_tiers = IRMAA_TIERS_SINGLE if hh.filing_status == "Single" else IRMAA_TIERS_MFJ
        _medicare_count = 1 if hh.filing_status == "Single" else 2
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
                    f"Surcharge/yr (×{_medicare_count})": fmt_dollars(
                        surcharge_pp * _medicare_count
                    ),
                }
            )
        st.dataframe(pd.DataFrame(irmaa_data), width="stretch", hide_index=True)
        st.caption(
            "2026 statutory base. The chart above projects these forward — thresholds "
            "CPI-indexed and surcharge dollars rising with medical inflation — to the "
            "IRMAA payment year (income year + 2), so its numbers run higher."
        )

    with col_ref2:
        st.markdown(
            f"### ACA Premium Schedule ({'Enhanced' if hh.aca_enhanced_subsidies_active else 'Pre-ARP'})"
        )
        aca_data = []
        # audit-0823 const/APPLICABLE-PCT: pass the same _view_year used for the
        # FPL indexing just below, so the displayed pre-ARP applicable-% table
        # matches the year this reference panel is actually showing instead of
        # being frozen at the 2026 published table.
        # Each row is one band with the applicable % at its lower and upper FPL
        # edge; within a band the percentage rises linearly between the two
        # (pre-ARP and ARPA alike), so "a → b" reads as a ramp, not a step.
        _view_fpl = fpl_for(hh.filing_status, _view_year, _view_cpi)
        for lower_fpl, upper_fpl, rate_lo, rate_hi in aca_cap_bands(
            hh.aca_enhanced_subsidies_active, year=_view_year
        ):
            if upper_fpl == float("inf"):
                fpl_label = f"{fmt_pct(lower_fpl, 0)}+"
            else:
                fpl_label = f"{lower_fpl * 100:.0f}–{fmt_pct(upper_fpl, 0)}"
            cap_label = (
                f"{fmt_pct(rate_lo)} of income"
                if rate_lo == rate_hi
                else f"{fmt_pct(rate_lo)} → {fmt_pct(rate_hi)} of income"
            )
            aca_data.append(
                {
                    "FPL Range": fpl_label,
                    "MAGI ≤": fmt_dollars(upper_fpl * _view_fpl)
                    if upper_fpl != float("inf")
                    else "No limit",
                    "Premium Cap": cap_label,
                }
            )
        if not hh.aca_enhanced_subsidies_active:
            aca_data.append(
                {"FPL Range": "400%+", "MAGI ≤": "No limit", "Premium Cap": "No subsidy"}
            )
        st.dataframe(pd.DataFrame(aca_data), width="stretch", hide_index=True)

        _fpl_label = "family of 1" if hh.filing_status == "Single" else "family of 2"
        _fpl_val = fpl_for(hh.filing_status, _view_year, _view_cpi)
        _caption_couple_benchmark = resolve_couple_benchmark_annual(
            hh.aca_benchmark_premium_annual,
            your_age=hh.your_age,
            spouse_age=hh.spouse_age,
            filing_status=hh.filing_status,
            year=_view_year,
            cpi=_view_cpi,
        )
        st.caption(
            md_dollars(
                f"FPL ({_fpl_label}): {fmt_dollars(_fpl_val)} · "
                f"Benchmark silver plan: {fmt_dollars(_caption_couple_benchmark)}/yr"
            )
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
