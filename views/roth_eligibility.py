"""Roth IRA Contribution Eligibility — direct contribution & backdoor analysis.

Determines whether you can make a direct Roth IRA contribution based on
MAGI, and whether the backdoor Roth strategy makes sense given existing
Traditional IRA balances (pro-rata rule).
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


def render(hh: Household):
    st.title("Roth IRA Contribution Eligibility")
    st.caption(
        "Check whether you can make a direct Roth IRA contribution, "
        "and whether a backdoor Roth makes sense given your IRA balances."
    )

    # --- Inputs ---
    st.markdown("### Tax Year Info")
    col1, col2 = st.columns(2)
    with col1:
        tax_year = st.selectbox("Tax Year", [2025, 2026], index=0)
        filing = st.selectbox("Filing Status", ["MFJ", "Single"])
    with col2:
        magi = st.number_input(
            "Modified AGI (from TurboTax)", value=200_000, step=5_000, format="%d",
            help="Form 1040 line 11 adjusted for Roth eligibility",
        )

    st.markdown("### Your Situation")
    col1, col2, col3 = st.columns(3)
    with col1:
        your_age = st.number_input("Your Age (end of tax year)", value=hh.your_age, step=1)
        spouse_age = st.number_input("Spouse Age (end of tax year)", value=hh.spouse_age, step=1)
    with col2:
        has_workplace_plan = st.checkbox("You have workplace plan (403b/401k)", value=True)
        spouse_workplace = st.checkbox("Spouse has workplace plan", value=False)
    with col3:
        trad_contrib_you = st.number_input(
            "Your Trad IRA contribution (this year)", value=0, step=500, format="%d",
        )
        trad_contrib_spouse = st.number_input(
            "Spouse Trad IRA contribution (this year)", value=0, step=500, format="%d",
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
        "**Conversions ≠ Contributions**: You can contribute to a Roth IRA until April 15 "
        f"for the prior year. But Roth *conversions* must be done by Dec 31, {tax_year}. "
        "These are separate actions with different rules and deadlines."
    )
