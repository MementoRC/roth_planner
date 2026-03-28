"""Roth IRA Contribution Eligibility — direct contribution & backdoor analysis.

Determines whether you can make a direct Roth IRA contribution based on
MAGI, and whether the backdoor Roth strategy makes sense given existing
Traditional IRA balances (pro-rata rule).

When TurboTax data is available via FinExtract, auto-populates income
figures and IRA contribution amounts.
"""

import streamlit as st

from models.household import Household

# --- 2025 limits ---
CONTRIB_LIMIT = 7_000
CATCHUP_50 = 1_000  # additional if age 50+

# Roth MAGI phase-out (2025)
ROTH_PHASEOUT = {
    "MFJ": (236_000, 246_000),
    "Single": (150_000, 165_000),
}

# Traditional IRA deduction phase-out when covered by workplace plan (2025)
TRAD_DEDUCTION_PHASEOUT = {
    "MFJ_active": (126_000, 146_000),  # you have a workplace plan
    "MFJ_spouse_only": (236_000, 246_000),  # only spouse has workplace plan
    "Single": (79_000, 99_000),
}


def _phase_out(magi: float, lower: float, upper: float, limit: float) -> float:
    """Reduce limit linearly between lower and upper MAGI thresholds."""
    if magi <= lower:
        return limit
    if magi >= upper:
        return 0.0
    # Round up to nearest $10 per IRS rules
    reduced = limit * (upper - magi) / (upper - lower)
    return max(0.0, round(reduced / 10) * 10)


def _render_recharacterization(
    person: str,
    tax_year: int,
    trad_contrib: int,
    roth_allowed: float,
    magi: float,
    filing: str,
    phase_out_lower: float,
    phase_out_upper: float,
    has_workplace_plan: bool,
):
    """Show recharacterization opportunity when Trad was contributed but Roth was available."""
    rechar_amount = min(trad_contrib, int(roth_allowed))
    rechar_deadline = f"October 15, {tax_year + 1}"

    st.markdown("#### Recharacterization Opportunity")
    st.warning(
        f"**{person} contributed ${trad_contrib:,} to Traditional IRA but was eligible for Roth!**\n\n"
        f"MAGI ${magi:,} is {'below' if magi <= phase_out_lower else 'in'} the "
        f"{filing} Roth phase-out range (${phase_out_lower:,} – ${phase_out_upper:,}).\n\n"
        f"You can recharacterize **${rechar_amount:,}** to Roth IRA before **{rechar_deadline}**."
    )

    with st.expander("Recharacterization Action Plan", expanded=True):
        # Step-by-step guide
        st.markdown(f"""
**What is recharacterization?**

Recharacterization moves a contribution (plus attributable earnings) from one IRA type
to another. It's treated as if the Roth contribution was made originally — it is NOT a
conversion, so the pro-rata rule does NOT apply.

**Steps to recharacterize:**

1. **Call your IRA custodian** (the institution holding the Traditional IRA)
   - Request a recharacterization of your {tax_year} Traditional IRA contribution to a Roth IRA
   - They will calculate the attributable earnings and transfer both to a Roth IRA
   - If you don't have a Roth IRA there yet, they'll open one

2. **Tax filing**
""")

        if has_workplace_plan and magi > TRAD_DEDUCTION_PHASEOUT.get(
            f"{filing}_active" if filing == "MFJ" else filing, (0, 0)
        )[1]:
            st.markdown(
                f"   - Your Traditional contribution was **not deductible** anyway "
                f"(MAGI ${magi:,} exceeds the ${filing} deduction limit with a workplace plan), "
                f"so the 1040-X amendment is straightforward — remove Form 8606 non-deductible "
                f"reporting and report as a Roth contribution instead."
            )
        else:
            st.markdown(
                f"   - If you claimed a deduction for the Traditional contribution on your "
                f"{tax_year} return, file **Form 1040-X** to remove that deduction.\n"
                f"   - TurboTax can generate the amended return."
            )

        st.markdown(f"""
3. **Deadline: {rechar_deadline}**
   - This is the extended filing deadline for {tax_year} returns
   - After this date, recharacterization is no longer available for {tax_year}

**Key facts:**
- Recharacterization is **not** a conversion — pro-rata rule does NOT apply
- The custodian calculates earnings attributable to the contribution
- Both the contribution and earnings move to the Roth IRA
- Earnings in the Roth grow tax-free going forward
- You can recharacterize a partial amount if you prefer
""")

        # Countdown
        from datetime import date

        deadline = date(tax_year + 1, 10, 15)
        today = date.today()
        days_left = (deadline - today).days

        if days_left > 0:
            if days_left > 180:
                st.success(f"**{days_left} days remaining** until recharacterization deadline.")
            elif days_left > 60:
                st.warning(f"**{days_left} days remaining** — schedule this soon.")
            else:
                st.error(f"**Only {days_left} days remaining!** Act now.")
        else:
            st.error(f"Recharacterization deadline for {tax_year} has **passed**.")


def render(hh: Household):
    st.title("Roth IRA Contribution Eligibility")
    st.caption(
        "Check whether you can make a direct Roth IRA contribution, "
        "and whether a backdoor Roth makes sense given your IRA balances."
    )

    # --- TurboTax data sync ---
    tax_snap = st.session_state.get("tax_return_snapshot")

    col_sync, col_status = st.columns([1, 3])
    with col_sync:
        sync_tax = st.button(
            "Sync TurboTax Data",
            help="Pull income & deduction data from FinExtract ingestion server",
        )
    if sync_tax:
        from engine.portfolio_sync import fetch_tax_return, save_tax_snapshot

        tax_snap = fetch_tax_return()
        if tax_snap.server_available:
            st.session_state.tax_return_snapshot = tax_snap
            save_tax_snapshot(tax_snap)
            with col_status:
                st.success("Synced TurboTax data from FinExtract")
        else:
            with col_status:
                st.error(f"Server unavailable: {tax_snap.error}")

    # Load cached on first visit
    if tax_snap is None and "tax_return_snapshot" not in st.session_state:
        from engine.portfolio_sync import load_tax_snapshot

        tax_snap = load_tax_snapshot()
        if tax_snap is not None:
            st.session_state.tax_return_snapshot = tax_snap

    # --- Income summary from TurboTax ---
    if tax_snap and tax_snap.server_available:
        st.markdown("### Income Summary (from TurboTax)")
        inc_cols = st.columns(4)
        inc_cols[0].metric("W-2 Wages", f"${tax_snap.wages:,.0f}")
        inc_cols[1].metric("1099-NEC", f"${tax_snap.nec_income:,.0f}")
        inc_cols[2].metric("Investments", f"${tax_snap.investment_income:,.0f}")
        inc_cols[3].metric("Est. MAGI", f"${tax_snap.estimated_magi:,.0f}")

        if tax_snap.ira_distributions > 0:
            st.write(f"IRA/Pension Distributions (1099-R): **${tax_snap.ira_distributions:,.0f}**")
        if tax_snap.ira_contributions > 0:
            st.write(f"IRA Contributions (Form 5498): **${tax_snap.ira_contributions:,.0f}**")
        st.markdown("---")

    # --- Inputs ---
    st.markdown("### Tax Year Info")
    col1, col2 = st.columns(2)

    # Default MAGI from TurboTax if available
    default_magi = int(tax_snap.estimated_magi) if tax_snap and tax_snap.server_available else 200_000

    with col1:
        tax_year = st.selectbox("Tax Year", [2025, 2026], index=0)
        filing = st.selectbox("Filing Status", ["MFJ", "Single"])
    with col2:
        magi = st.number_input(
            "Modified AGI" + (" (from TurboTax)" if tax_snap and tax_snap.server_available else ""),
            value=default_magi, step=5_000, format="%d",
            help="Form 1040 line 11 adjusted for Roth eligibility. "
            + ("Auto-populated from TurboTax — adjust if needed." if tax_snap and tax_snap.server_available else ""),
        )

    st.markdown("### Your Situation")
    col1, col2, col3 = st.columns(3)

    # Default IRA contributions from TurboTax (split evenly for MFJ as approximation)
    default_ira_contrib = int(tax_snap.ira_contributions) if tax_snap and tax_snap.server_available else 0
    # If MFJ and total is $8K+, likely both spouses contributed
    if filing == "MFJ" and default_ira_contrib >= 14_000:
        default_you = default_ira_contrib // 2
        default_spouse = default_ira_contrib - default_you
    elif filing == "MFJ" and default_ira_contrib > 0:
        # Assume primary contributed, spouse did not (user can adjust)
        default_you = min(default_ira_contrib, 8_000)
        default_spouse = max(0, default_ira_contrib - default_you)
    else:
        default_you = default_ira_contrib
        default_spouse = 0

    with col1:
        your_age = st.number_input("Your Age (end of tax year)", value=hh.your_age, step=1)
        spouse_age = st.number_input("Spouse Age (end of tax year)", value=hh.spouse_age, step=1)
    with col2:
        has_workplace_plan = st.checkbox("You have workplace plan (403b/401k)", value=True)
        spouse_workplace = st.checkbox("Spouse has workplace plan", value=False)
    with col3:
        trad_contrib_you = st.number_input(
            "Your Trad IRA contribution (this year)"
            + (" *" if tax_snap and tax_snap.server_available else ""),
            value=default_you, step=500, format="%d",
            help="From TurboTax Form 5498. Includes Traditional + Roth combined — "
            "adjust to show only Traditional." if tax_snap and tax_snap.server_available else None,
        )
        trad_contrib_spouse = st.number_input(
            "Spouse Trad IRA contribution (this year)",
            value=default_spouse, step=500, format="%d",
        )

    if tax_snap and tax_snap.server_available and default_ira_contrib > 0:
        st.caption(
            f"\\* TurboTax reports ${default_ira_contrib:,} total IRA contributions (Form 5498). "
            "This includes both Traditional and Roth — adjust above to reflect only Traditional contributions."
        )

    st.markdown("### IRA Balances (Dec 31)")
    st.caption("Needed for pro-rata calculation. Include ALL Traditional, SEP, and SIMPLE IRA balances.")
    col1, col2 = st.columns(2)
    with col1:
        your_trad_balance = st.number_input(
            "Your Total Trad IRA Balance", value=int(hh.your_ira), step=50_000, format="%d",
            help="All Traditional IRA accounts combined (Dec 31 of tax year)",
        )
    with col2:
        spouse_trad_balance = st.number_input(
            "Spouse Total Trad IRA Balance", value=int(hh.spouse_ira), step=50_000, format="%d",
        )

    # --- Calculations ---
    st.markdown("---")

    for person, age, trad_contrib, trad_balance, workplace in [
        ("You", your_age, trad_contrib_you, your_trad_balance, has_workplace_plan),
        ("Spouse", spouse_age, trad_contrib_spouse, spouse_trad_balance, spouse_workplace),
    ]:
        st.markdown(f"### {person}")

        # Contribution limit
        limit = CONTRIB_LIMIT + (CATCHUP_50 if age >= 50 else 0)
        remaining = max(0, limit - trad_contrib)

        st.write(f"**IRA contribution limit**: ${limit:,} ({'includes $1,000 catch-up' if age >= 50 else 'under 50'})")
        if trad_contrib > 0:
            st.write(f"**Already contributed to Trad IRA**: ${trad_contrib:,} → **${remaining:,} remaining** for Roth")

        if remaining == 0:
            # Check if they COULD have done Roth instead
            lower, upper = ROTH_PHASEOUT.get(filing, ROTH_PHASEOUT["MFJ"])
            roth_allowed = _phase_out(magi, lower, upper, float(limit))
            if roth_allowed > 0:
                st.error(f"No room left — full ${limit:,} already contributed to Traditional IRA.")
                _render_recharacterization(
                    person, tax_year, trad_contrib, roth_allowed, magi, filing,
                    lower, upper, workplace,
                )
            else:
                st.error(f"No room left — full ${limit:,} already contributed to Traditional IRA.")
            continue

        # Direct Roth eligibility
        lower, upper = ROTH_PHASEOUT.get(filing, ROTH_PHASEOUT["MFJ"])
        allowed = _phase_out(magi, lower, upper, float(remaining))

        if allowed >= remaining:
            st.success(f"**Eligible for full direct Roth contribution**: ${remaining:,}")
            st.write(f"MAGI ${magi:,} is below ${filing} phase-out start (${lower:,})")
        elif allowed > 0:
            st.warning(f"**Partial Roth contribution allowed**: ${allowed:,.0f}")
            st.write(f"MAGI ${magi:,} is in phase-out range (${lower:,} – ${upper:,})")
        else:
            st.error(f"**No direct Roth contribution** — MAGI ${magi:,} exceeds ${filing} limit (${upper:,})")

        # Backdoor Roth analysis
        st.markdown("#### Backdoor Roth Analysis")

        if trad_balance == 0:
            st.success(
                "**Backdoor Roth is clean!** No existing Traditional IRA balance means "
                "no pro-rata tax. Contribute non-deductible to Traditional, then convert immediately."
            )
            if allowed < remaining:
                backdoor_amount = remaining - int(allowed)
                st.write(f"Recommended: contribute ${backdoor_amount:,} non-deductible to Trad IRA, convert to Roth.")
        else:
            # Pro-rata calculation
            # Non-deductible basis = what you contribute now (assuming prior was deductible)
            nondeductible = remaining  # max you could contribute and convert
            total_trad = trad_balance + nondeductible
            taxable_pct = trad_balance / total_trad if total_trad > 0 else 0
            tax_on_convert = nondeductible * taxable_pct

            st.error(
                f"**Pro-rata rule makes backdoor Roth expensive.**\n\n"
                f"Your Traditional IRA balance: **${trad_balance:,}**\n\n"
                f"If you contribute ${nondeductible:,} non-deductible and convert:"
            )

            col1, col2, col3 = st.columns(3)
            col1.metric("Taxable %", f"{taxable_pct * 100:.1f}%")
            col2.metric("Tax on Conversion", f"${tax_on_convert:,.0f}")
            col3.metric("Tax-Free Portion", f"${nondeductible - tax_on_convert:,.0f}")

            st.write(
                f"Of the ${nondeductible:,} converted, **${tax_on_convert:,.0f}** would be taxable "
                f"because {taxable_pct * 100:.1f}% of your total IRA is pre-tax money."
            )

            st.info(
                "**Recommendation**: With a large Traditional IRA balance, backdoor Roth "
                "contributions are not worthwhile. Focus on strategic Roth *conversions* "
                "to fill tax brackets instead — that's what the Conversion Planner page does."
            )

        # Traditional IRA deduction
        st.markdown("#### Traditional IRA Deduction")
        if workplace:
            key = f"{filing}_active" if filing == "MFJ" else filing
            ded_lower, ded_upper = TRAD_DEDUCTION_PHASEOUT.get(key, (0, 0))
            deductible = _phase_out(magi, ded_lower, ded_upper, float(limit))
            if deductible >= limit:
                st.write(f"Traditional IRA contributions are **fully deductible** (MAGI below ${ded_lower:,})")
            elif deductible > 0:
                st.write(f"Partial deduction: **${deductible:,.0f}** of ${limit:,} (MAGI in phase-out)")
            else:
                st.write(f"**Not deductible** — MAGI ${magi:,} exceeds ${filing} limit with workplace plan (${ded_upper:,})")
        elif filing == "MFJ" and spouse_workplace:
            ded_lower, ded_upper = TRAD_DEDUCTION_PHASEOUT["MFJ_spouse_only"]
            deductible = _phase_out(magi, ded_lower, ded_upper, float(limit))
            if deductible >= limit:
                st.write("Traditional IRA contributions are **fully deductible** (spouse has plan, your MAGI OK)")
            elif deductible > 0:
                st.write(f"Partial deduction: **${deductible:,.0f}** (spouse-plan phase-out)")
            else:
                st.write(f"**Not deductible** — MAGI exceeds spouse-plan limit (${ded_upper:,})")
        else:
            st.write("Traditional IRA contributions are **fully deductible** (no workplace plan)")

    # --- Key dates ---
    st.markdown("---")
    st.markdown("### Key Dates")
    st.markdown(f"""
| Deadline | Action |
|----------|--------|
| **Apr 15, {tax_year + 1}** | Last day for {tax_year} IRA contributions (Traditional or Roth) |
| **Dec 31, {tax_year}** | Last day for {tax_year} Roth *conversions* |
| **Oct 15, {tax_year + 1}** | Last day to *recharacterize* a {tax_year} contribution |
""")

    st.info(
        "**Conversions ≠ Contributions ≠ Recharacterizations**: "
        "You can *contribute* to a Roth IRA until April 15 for the prior year. "
        f"Roth *conversions* must be done by Dec 31, {tax_year}. "
        f"*Recharacterizations* (moving a contribution between IRA types) are allowed until Oct 15, {tax_year + 1}. "
        "These are three separate actions with different rules, tax treatment, and deadlines."
    )
