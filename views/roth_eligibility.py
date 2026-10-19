"""Roth IRA Contribution Eligibility — direct contribution & backdoor analysis.

Determines whether you can make a direct Roth IRA contribution based on
MAGI, and whether the backdoor Roth strategy makes sense given existing
Traditional IRA balances (pro-rata rule).

MAGI defaults from the most recent imported 1040 when available. Roth
eligibility uses that AGI less any Roth conversion income (IRC
§408A(c)(3)(B)(i)); the Traditional IRA deduction test uses the full AGI.
"""

import math

import streamlit as st

from engine.tax_indexing import DEFAULT_CPI, index_tuple, index_value
from models.household import Household
from views._format import fmt_dollars, fmt_pct
from views._shared import render_canonical_field, render_completeness_badge
from views._style import render_page_header

# Per-year IRA contribution limits.
# 2025 source: IRS Notice 2024-80.
# 2026 source: IRS IR-2025-111 / Notice 2025-67 (Nov 13 2025).
CONTRIB_LIMIT_BY_YEAR: dict[int, int] = {
    2025: 7_000,
    2026: 7_500,
}
CATCHUP_50_BY_YEAR: dict[int, int] = {
    2025: 1_000,  # total 50+ = $8,000
    2026: 1_100,  # total 50+ = $8,600
}

# Roth MAGI phase-out by tax year.
ROTH_PHASEOUT_BY_YEAR: dict[int, dict[str, tuple[int, int]]] = {
    2025: {
        "MFJ": (236_000, 246_000),
        "Single": (150_000, 165_000),
    },
    2026: {
        "MFJ": (242_000, 252_000),
        "Single": (153_000, 168_000),
    },
}


def contrib_limit_for_year(tax_year: int, cpi: float = DEFAULT_CPI) -> float:
    """Roth/IRA elective contribution limit (IRC §219(b)(5)). Exact for published years;
    CPI-indexed past 2026 (the IRS inflation-adjusts this limit) rather than frozen."""
    if tax_year in CONTRIB_LIMIT_BY_YEAR:
        return CONTRIB_LIMIT_BY_YEAR[tax_year]
    if tax_year > 2026:
        return index_value(CONTRIB_LIMIT_BY_YEAR[2026], tax_year, cpi)
    return CONTRIB_LIMIT_BY_YEAR[min(CONTRIB_LIMIT_BY_YEAR)]


def catchup_50_for_year(tax_year: int, cpi: float = DEFAULT_CPI) -> float:
    """Age-50 catch-up contribution (IRC §414(v)). Exact for published years; CPI-indexed past 2026."""
    if tax_year in CATCHUP_50_BY_YEAR:
        return CATCHUP_50_BY_YEAR[tax_year]
    if tax_year > 2026:
        return index_value(CATCHUP_50_BY_YEAR[2026], tax_year, cpi)
    return CATCHUP_50_BY_YEAR[min(CATCHUP_50_BY_YEAR)]


def roth_phaseout_for_year(
    tax_year: int, filing_status: str, cpi: float = DEFAULT_CPI
) -> tuple[float, float]:
    """Roth contribution MAGI phase-out range (IRC §408A(c)(3)). Exact for published years;
    CPI-indexed past 2026 rather than frozen."""
    if tax_year in ROTH_PHASEOUT_BY_YEAR:
        low, high = ROTH_PHASEOUT_BY_YEAR[tax_year][filing_status]
        return (float(low), float(high))
    if tax_year > 2026:
        low, high = index_tuple(ROTH_PHASEOUT_BY_YEAR[2026][filing_status], tax_year, cpi)
        return (float(low), float(high))
    low, high = ROTH_PHASEOUT_BY_YEAR[min(ROTH_PHASEOUT_BY_YEAR)][filing_status]
    return (float(low), float(high))


# Convenience aliases for the current default year (2026) — kept so that
# any external references and the test suite that pins 2026 values still work.
CONTRIB_LIMIT = CONTRIB_LIMIT_BY_YEAR[2026]
CATCHUP_50 = CATCHUP_50_BY_YEAR[2026]
ROTH_PHASEOUT = ROTH_PHASEOUT_BY_YEAR[2026]

# Traditional IRA deduction phase-out when covered by workplace plan, keyed by tax year.
# 2025 source: IRS Notice 2024-80.
# 2026 source: IRS IR-2025-111 / Notice 2025-67 (Nov 13 2025).
TRAD_DEDUCTION_PHASEOUT_BY_YEAR: dict[int, dict[str, tuple[int, int]]] = {
    2025: {
        "MFJ_active": (126_000, 146_000),  # you have a workplace plan
        "MFJ_spouse_only": (236_000, 246_000),  # only spouse has workplace plan
        "Single": (79_000, 89_000),
    },
    2026: {
        "MFJ_active": (129_000, 149_000),  # you have a workplace plan
        "MFJ_spouse_only": (242_000, 252_000),  # only spouse has workplace plan
        "Single": (81_000, 91_000),
    },
}

# Convenience alias for the current default year (2026) — keeps existing importers working.
TRAD_DEDUCTION_PHASEOUT = TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2026]


def trad_deduction_phaseout_for_year(
    tax_year: int, key: str, cpi: float = DEFAULT_CPI
) -> tuple[float, float]:
    """Traditional IRA deduction phase-out bounds for *key* in the given *tax_year*.

    Returns the (lower, upper) MAGI thresholds for the supplied key
    (``"MFJ_active"``, ``"MFJ_spouse_only"``, or ``"Single"``).
    Falls back to the earliest published year for any year before the table.
    CPI-indexed past 2026 rather than frozen (mirrors roth_phaseout_for_year).
    """
    if tax_year in TRAD_DEDUCTION_PHASEOUT_BY_YEAR:
        low, high = TRAD_DEDUCTION_PHASEOUT_BY_YEAR[tax_year].get(key, (0, 0))
        return (float(low), float(high))
    if tax_year > 2026:
        low, high = index_tuple(
            TRAD_DEDUCTION_PHASEOUT_BY_YEAR[2026].get(key, (0, 0)), tax_year, cpi
        )
        return (float(low), float(high))
    year_data = TRAD_DEDUCTION_PHASEOUT_BY_YEAR[min(TRAD_DEDUCTION_PHASEOUT_BY_YEAR)]
    low, high = year_data.get(key, (0, 0))
    return (float(low), float(high))


def _phase_out(magi: float, lower: float, upper: float, limit: float) -> float:
    """Reduce limit linearly between lower and upper MAGI thresholds.

    Rounding per IRS Pub 590-A worksheet:
    - Round UP to the next $10 (math.ceil, not banker's round).
    - If the result is positive but less than $200, raise it to $200 (minimum floor).
    - A fully-phased-out result of $0.0 stays $0 (floor does not apply).
    """
    if magi <= lower:
        return limit
    if magi >= upper:
        return 0.0
    reduced = limit * (upper - magi) / (upper - lower)
    rounded = math.ceil(reduced / 10) * 10
    # Apply $200 floor only for a positive (partially-phased-out) result.
    if 0 < rounded < 200:
        rounded = 200
    return float(rounded)


def _roth_allowed(
    magi: float,
    lower: float,
    upper: float,
    limit: float,
    remaining: float,
) -> float:
    """Allowable direct Roth contribution after phase-out and prior Trad IRA contributions.

    Per IRC §408A(c)(3) the phase-out fraction is applied to the FULL IRA
    contribution limit, then capped at the room still available after any
    Traditional IRA contribution made for the same year.
    """
    return min(_phase_out(magi, lower, upper, limit), remaining)


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
        f"**{person} contributed {fmt_dollars(trad_contrib)} to Traditional IRA but was eligible for Roth!**\n\n"
        f"MAGI {fmt_dollars(magi)} is {'below' if magi <= phase_out_lower else 'in'} the "
        f"{filing} Roth phase-out range ({fmt_dollars(phase_out_lower)} – {fmt_dollars(phase_out_upper)}).\n\n"
        f"You can recharacterize **{fmt_dollars(rechar_amount)}** to Roth IRA before **{rechar_deadline}**."
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

        if (
            has_workplace_plan
            and magi
            > trad_deduction_phaseout_for_year(
                tax_year, f"{filing}_active" if filing == "MFJ" else filing
            )[1]
        ):
            st.markdown(
                f"   - Your Traditional contribution was **not deductible** anyway "
                f"(MAGI {fmt_dollars(magi)} exceeds the {filing} deduction limit with a workplace plan), "
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


def workplace_plan_balance(snap: object, owner: str) -> float:
    """Sum of *owner*'s 403(b) accounts in the portfolio snapshot (0 if none).

    ``Household.your_ira`` / ``spouse_ira`` hold the owner's whole pre-tax
    total, which includes 403(b) accounts. The backdoor pro-rata rule counts
    IRAs only (IRC §408(d)(2); Form 8606 line 6), so this amount comes out.
    """
    accounts = getattr(snap, "accounts", None) or []
    return float(
        sum(a.total_value for a in accounts if a.account_type == "403b" and a.owner == owner)
    )


def ira_only_balance(pretax_total: float, snap: object, owner: str) -> float:
    """The owner's IRA balance for the pro-rata rule: pre-tax total minus 403(b)."""
    return max(0.0, float(pretax_total) - workplace_plan_balance(snap, owner))


def roth_magi_default(scanned_1040s: dict, prior_magi_anchor: dict) -> tuple[int, int | None]:
    """Default AGI for the Roth MAGI input, and the 1040 year it came from.

    Prefers the most recent scanned 1040: AGI plus any foreign earned income
    exclusion (IRC §219(g)(3)(A) computes AGI without regard to §911). Tax-
    exempt interest is NOT added (unlike the IRMAA anchor). Falls back to the
    IRMAA anchor (AGI + tax-exempt interest) when no 1040 record is available,
    and to a placeholder when neither is.
    """
    if scanned_1040s:
        year = max(scanned_1040s)
        rec = scanned_1040s[year]
        return int(rec.agi + rec.feie), year
    if prior_magi_anchor:
        year = max(prior_magi_anchor)
        return int(prior_magi_anchor[year]), year
    return 200_000, None


def render(hh: Household):
    render_page_header("elig", "IRA Contribution Eligibility")
    st.caption(
        "Check whether you can make a direct Roth IRA contribution, "
        "and whether a backdoor Roth makes sense given your IRA balances."
    )
    render_completeness_badge(hh)

    # Prior-year MAGI anchor (from the 1040 PDF import) seeds the MAGI default
    # and the IRMAA 2-year lookback.
    prior_magi_anchor = st.session_state.get("prior_year_magi") or {}
    most_recent_year = (
        sorted(prior_magi_anchor.keys(), reverse=True)[0] if prior_magi_anchor else None
    )

    # --- Inputs ---
    st.markdown("### Tax Year Info")
    col1, col2 = st.columns(2)

    # Default from the most recent imported 1040: its AGI (+ FEIE) when the
    # scanned record is available, else the IRMAA anchor, else a placeholder.
    scanned_1040s = st.session_state.get("_pdf_1040_scanned") or {}
    default_magi, magi_year = roth_magi_default(scanned_1040s, prior_magi_anchor)
    from_record = bool(scanned_1040s)

    with col1:
        tax_year = st.selectbox(
            "Tax Year",
            [2025, 2026],
            index=[2025, 2026].index(hh.base_year) if hh.base_year in [2025, 2026] else 1,
            key="elig_tax_year",
        )
        render_canonical_field("Filing status", hh.filing_status, key="filing")
        filing = hh.filing_status
    with col2:
        agi = st.number_input(
            "Modified AGI" + (f" (from {magi_year} 1040)" if magi_year else ""),
            value=default_magi,
            step=5_000,
            format="%d",
            key="elig_magi",
            help="Form 1040 line 11 (AGI), plus any foreign earned income exclusion."
            + (
                f" Defaulted from your {magi_year} 1040 import — adjust if needed."
                if from_record
                else (
                    f" Defaulted from your {magi_year} IRMAA anchor, which also includes "
                    "tax-exempt interest — subtract that interest for Roth purposes."
                    if magi_year
                    else ""
                )
            ),
        )
        conversions = st.number_input(
            "Roth conversions included in that AGI",
            value=0,
            min_value=0,
            step=5_000,
            format="%d",
            key="elig_conversions",
            help="Conversion income does not count toward Roth contribution eligibility "
            "(IRC §408A(c)(3)(B)(i)). It still counts for the Traditional IRA "
            "deduction phase-out.",
        )
    # Roth MAGI excludes conversion income; the deduction test uses the full AGI.
    magi = agi
    roth_magi = max(0, agi - conversions)

    # Prior-year MAGI anchor for IRMAA 2-year lookback
    if most_recent_year:
        st.caption(
            f"Prior-year MAGI anchor ({most_recent_year}): "
            f"{fmt_dollars(prior_magi_anchor[most_recent_year])}"
            " — used for IRMAA 2-year lookback"
        )

    st.markdown("### Your Situation")
    col1, col2, col3 = st.columns(3)

    # IRA contributions are entered by the user (no TurboTax pre-fill).
    default_you = 0
    default_spouse = 0

    # Catch-up eligibility depends on age at the end of the SELECTED tax year,
    # not the household's base-year age.
    with col1:
        your_age = hh.your_age_in(tax_year)
        render_canonical_field("Your Age (end of tax year)", your_age, key="your_age")
        if filing != "Single":
            spouse_age = hh.spouse_age_in(tax_year)
            render_canonical_field("Spouse Age (end of tax year)", spouse_age, key="spouse_age")
        else:
            spouse_age = 0
    with col2:
        render_canonical_field(
            "You have a workplace plan (401k/403b)",
            hh.your_has_workplace_plan,
            key="your_workplace_plan",
            fmt=lambda b: "Yes" if b else "No",
        )
        has_workplace_plan = hh.your_has_workplace_plan
        if filing != "Single":
            render_canonical_field(
                "Spouse has a workplace plan",
                hh.spouse_has_workplace_plan,
                key="spouse_workplace_plan",
                fmt=lambda b: "Yes" if b else "No",
            )
            spouse_workplace = hh.spouse_has_workplace_plan
        else:
            spouse_workplace = False
    with col3:
        trad_contrib_you = st.number_input(
            "Your Trad IRA contribution (this year)",
            value=default_you,
            min_value=0,
            step=500,
            format="%d",
        )
        if filing != "Single":
            trad_contrib_spouse = st.number_input(
                "Spouse Trad IRA contribution (this year)",
                value=default_spouse,
                min_value=0,
                step=500,
                format="%d",
            )
        else:
            trad_contrib_spouse = 0

    st.markdown("### IRA Balances (Dec 31)")
    st.caption(
        "Needed for pro-rata calculation. Counts ALL Traditional, SEP, and SIMPLE IRA "
        "balances; 403(b)/401(k) balances do not count."
    )
    # The balance fields hold each owner's whole pre-tax total, which includes
    # 403(b) accounts; the pro-rata rule counts IRAs only, so those come out.
    snap = st.session_state.get("portfolio_snapshot")
    col1, col2 = st.columns(2)
    with col1:
        render_canonical_field(
            "Your Total Pre-tax Balance", hh.your_ira, key="your_trad_balance", fmt=fmt_dollars
        )
        your_403b = workplace_plan_balance(snap, "you")
        your_trad_balance = ira_only_balance(hh.your_ira, snap, "you")
        if your_403b > 0:
            st.caption(
                f"Excludes {fmt_dollars(your_403b)} in 403(b) — IRA balance for pro-rata: "
                f"{fmt_dollars(your_trad_balance)}".replace("$", "\\$")
            )
    with col2:
        if filing != "Single":
            render_canonical_field(
                "Spouse Total Pre-tax Balance",
                hh.spouse_ira,
                key="spouse_trad_balance",
                fmt=fmt_dollars,
            )
            spouse_403b = workplace_plan_balance(snap, "spouse")
            spouse_trad_balance = ira_only_balance(hh.spouse_ira, snap, "spouse")
            if spouse_403b > 0:
                st.caption(
                    f"Excludes {fmt_dollars(spouse_403b)} in 403(b) — IRA balance for "
                    f"pro-rata: {fmt_dollars(spouse_trad_balance)}".replace("$", "\\$")
                )
        else:
            spouse_trad_balance = 0

    # --- Calculations ---
    st.markdown("---")

    # Resolve per-year constants for the selected tax_year.
    _contrib_limit = contrib_limit_for_year(tax_year, cpi=hh.cpi_assumption)
    _catchup_50 = catchup_50_for_year(tax_year, cpi=hh.cpi_assumption)

    persons = [
        ("You", your_age, trad_contrib_you, your_trad_balance, has_workplace_plan, spouse_workplace)
    ]
    if filing != "Single":
        persons.append(
            (
                "Spouse",
                spouse_age,
                trad_contrib_spouse,
                spouse_trad_balance,
                spouse_workplace,
                has_workplace_plan,
            )
        )
    for person, age, trad_contrib, trad_balance, workplace, other_workplace in persons:
        st.markdown(f"### {person}")

        # Contribution limit
        limit = _contrib_limit + (_catchup_50 if age >= 50 else 0)
        remaining = max(0, limit - trad_contrib)

        st.write(
            f"**IRA contribution limit**: {fmt_dollars(limit)} ({f'includes {fmt_dollars(_catchup_50)} catch-up' if age >= 50 else 'under 50'})"
        )
        if trad_contrib > 0:
            st.write(
                f"**Already contributed to Trad IRA**: {fmt_dollars(trad_contrib)} → **{fmt_dollars(remaining)} remaining** for Roth"
            )

        if remaining == 0:
            # Check if they COULD have done Roth instead
            lower, upper = roth_phaseout_for_year(tax_year, filing, cpi=hh.cpi_assumption)
            roth_allowed = _phase_out(roth_magi, lower, upper, float(limit))
            if roth_allowed > 0:
                st.error(
                    f"No room left — full {fmt_dollars(limit)} already contributed to Traditional IRA."
                )
                _render_recharacterization(
                    person,
                    tax_year,
                    trad_contrib,
                    roth_allowed,
                    roth_magi,
                    filing,
                    lower,
                    upper,
                    workplace,
                )
            else:
                st.error(
                    f"No room left — full {fmt_dollars(limit)} already contributed to Traditional IRA."
                )
            continue

        # Direct Roth eligibility
        lower, upper = roth_phaseout_for_year(tax_year, filing, cpi=hh.cpi_assumption)
        allowed = _roth_allowed(roth_magi, lower, upper, float(limit), float(remaining))

        if allowed >= remaining:
            st.success(f"**Eligible for full direct Roth contribution**: {fmt_dollars(remaining)}")
            st.write(
                f"MAGI {fmt_dollars(roth_magi)} is below {filing} phase-out start ({fmt_dollars(lower)})"
            )
        elif allowed > 0:
            st.warning(f"**Partial Roth contribution allowed**: {fmt_dollars(allowed)}")
            st.write(
                f"MAGI {fmt_dollars(roth_magi)} is in phase-out range ({fmt_dollars(lower)} – {fmt_dollars(upper)})"
            )
        else:
            st.error(
                f"**No direct Roth contribution** — MAGI {fmt_dollars(roth_magi)} exceeds {filing} limit ({fmt_dollars(upper)})"
            )

        # Backdoor Roth analysis
        st.markdown("#### Backdoor Roth Analysis")

        if trad_balance == 0:
            st.success(
                "**Backdoor Roth is clean!** No existing Traditional IRA balance means "
                "no pro-rata tax. Contribute non-deductible to Traditional, then convert immediately."
            )
            if allowed < remaining:
                backdoor_amount = remaining - int(allowed)
                st.write(
                    f"Recommended: contribute {fmt_dollars(backdoor_amount)} non-deductible to Trad IRA, convert to Roth."
                )
        else:
            # Pro-rata calculation
            # Non-deductible basis = what you contribute now (assuming prior was deductible)
            nondeductible = remaining  # max you could contribute and convert
            total_trad = trad_balance + nondeductible
            taxable_pct = trad_balance / total_trad if total_trad > 0 else 0
            # Taxable income from the conversion (not the tax on it).
            taxable_amount = nondeductible * taxable_pct

            st.error(
                f"**Pro-rata rule makes backdoor Roth expensive.**\n\n"
                f"Your Traditional IRA balance: **{fmt_dollars(trad_balance)}**\n\n"
                f"If you contribute {fmt_dollars(nondeductible)} non-deductible and convert:"
            )

            col1, col2, col3 = st.columns(3)
            col1.metric("Taxable %", fmt_pct(taxable_pct))
            col2.metric("Taxable Amount", fmt_dollars(taxable_amount))
            col3.metric("Tax-Free Portion", fmt_dollars(nondeductible - taxable_amount))

            st.write(
                f"Of the {fmt_dollars(nondeductible)} converted, **{fmt_dollars(taxable_amount)}** would be taxable "
                f"because {fmt_pct(taxable_pct)} of your total IRA is pre-tax money."
            )

            st.info(
                "**Recommendation**: With a large Traditional IRA balance, backdoor Roth "
                "contributions are not worthwhile. Focus on strategic Roth *conversions* "
                "to fill tax brackets instead — that's what the Conversion Plan page does."
            )

        # Traditional IRA deduction
        st.markdown("#### Traditional IRA Deduction")
        if workplace:
            key = f"{filing}_active" if filing == "MFJ" else filing
            ded_lower, ded_upper = trad_deduction_phaseout_for_year(tax_year, key)
            deductible = _phase_out(magi, ded_lower, ded_upper, float(limit))
            if deductible >= limit:
                st.write(
                    f"Traditional IRA contributions are **fully deductible** (MAGI below {fmt_dollars(ded_lower)})"
                )
            elif deductible > 0:
                st.write(
                    f"Partial deduction: **{fmt_dollars(deductible)}** of {fmt_dollars(limit)} (MAGI in phase-out)"
                )
            else:
                st.write(
                    f"**Not deductible** — MAGI {fmt_dollars(magi)} exceeds {filing} limit with workplace plan ({fmt_dollars(ded_upper)})"
                )
        elif filing == "MFJ" and other_workplace:
            ded_lower, ded_upper = trad_deduction_phaseout_for_year(tax_year, "MFJ_spouse_only")
            deductible = _phase_out(magi, ded_lower, ded_upper, float(limit))
            if deductible >= limit:
                st.write(
                    "Traditional IRA contributions are **fully deductible** (spouse has plan, your MAGI OK)"
                )
            elif deductible > 0:
                st.write(
                    f"Partial deduction: **{fmt_dollars(deductible)}** (spouse-plan phase-out)"
                )
            else:
                st.write(
                    f"**Not deductible** — MAGI exceeds spouse-plan limit ({fmt_dollars(ded_upper)})"
                )
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
