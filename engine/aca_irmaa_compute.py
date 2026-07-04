"""Pure compute for the ACA + IRMAA Explorer view.

Functions in this module take a Household + scalar inputs and return
plain dataclasses with all curves the view needs. No Streamlit, no plotly.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.aca import (
    aca_applies,
    aca_net_cost,
    aca_subsidy,
    aca_subsidy_loss,
    effective_benchmark_premium,
)
from engine.ira import ss_benefit_at_age, ss_with_cola
from engine.irmaa import _index_irmaa_tiers, irmaa_next_threshold, irmaa_surcharge, irmaa_tier
from engine.niit import niit
from engine.tax import (
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    deductions,
    federal_tax,
    federal_tax_single,
    marginal_rate,
    marginal_rate_single,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import Household


def _nontaxable_ss(
    hh: Household,
    ya: int,
    sa: int | None,
    *,
    other_income: float,
    filing_status: str,
) -> float:
    """Non-taxable Social Security for the ACA MAGI add-back (IRC §36B(d)(2)(B)(iii)).

    IRMAA MAGI (§1839(i)(4)) excludes the non-taxable portion of Social Security;
    ACA MAGI must add it back. Returns ``combined_ss - taxable_ss`` floored at 0.

    Returns ``0.0`` when no SS is claimed at these ages — e.g. the default
    household claims at 70, after the ACA window closes at 65 — so this is a
    no-op for households not drawing SS during the ACA years.

    ``other_income`` is the non-SS provisional-income proxy (the Explorer's base
    MAGI). At ACA-relevant incomes SS inclusion is pinned at the 85% cap, so the
    proxy's slight over-count of provisional income is immaterial. Mirrors the
    canonical add-back in engine/scenario_compute.compute_aca and
    engine/sweet_spot_compute. The SSA survivor step-up (survivor keeps the
    larger benefit) is not separately modeled here — a second-order effect on
    the already-narrow SS-while-on-ACA case, tracked under the deferred
    survivor/ACA interaction.
    """
    your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
    your_ss = (
        ss_with_cola(your_ss_base, ya - hh.your_ss_start_age, hh.ss_cola)
        if ya >= hh.your_ss_start_age
        else 0.0
    )
    if filing_status == "MFJ" and sa is not None:
        spouse_ss_base = ss_benefit_at_age(
            hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age
        )
        spouse_ss = (
            ss_with_cola(spouse_ss_base, sa - hh.spouse_ss_start_age, hh.ss_cola)
            if sa >= hh.spouse_ss_start_age
            else 0.0
        )
    else:
        spouse_ss = 0.0
    combined_ss = your_ss + spouse_ss
    if combined_ss <= 0:
        return 0.0
    taxable = taxable_ss(combined_ss, other_income, filing_status=filing_status)
    return max(combined_ss - taxable, 0.0)


@dataclass
class CostCurves:
    """Parallel-list cost decomposition curves over a MAGI range.

    All lists share the same length and align with `magi_points`.
    """

    magi_points: list[float]
    aca_subsidy_vals: list[float]
    aca_net_cost_vals: list[float]
    aca_subsidy_loss_vals: list[float]
    irmaa_vals: list[float]
    irmaa_tier_vals: list[int]
    niit_vals: list[float]
    fed_tax_vals: list[float]
    marginal_rate_vals: list[float]
    total_hidden_cost_vals: list[float]
    irmaa_increase_vals: list[float]
    niit_increase_vals: list[float]
    # Base-state scalars (computed once, hoisted from inside the original loop)
    base_irmaa: float
    base_niit: float
    # ACA MAGI add-back (IRC §36B); 0 unless SS drawn in ACA years. Used to align
    # the cliff marker (audit C7 / aca-4).
    nontaxable_ss: float = 0.0


def compute_cost_curves(
    magi_points: list[float],
    base_magi: float,
    net_inv_income: float,
    hh: Household,
    *,
    year: int,
    cpi: float,
) -> CostCurves:
    """Build cost curves for ACA, IRMAA, NIIT, federal tax, and total hidden cost.

    Computes base-state ACA/IRMAA/NIIT once (was previously recomputed inside the
    loop in views/aca_irmaa.py) and reuses for the hidden-cost decomposition.
    """
    _your_on_aca = aca_applies(hh.your_age, hh.your_aca_enrolled)
    _spouse_on_aca = aca_applies(hh.spouse_age, hh.spouse_aca_enrolled)
    anyone_on_aca = _your_on_aca or _spouse_on_aca

    effective_benchmark = effective_benchmark_premium(
        hh.aca_benchmark_premium_annual,
        your_age=hh.your_age,
        your_on_aca=_your_on_aca,
        spouse_age=hh.spouse_age,
        spouse_on_aca=_spouse_on_aca,
        filing_status=hh.filing_status,
    )

    if hh.filing_status == "Single":
        ded = deductions(
            hh.your_age,
            hh.spouse_age,
            STD_DEDUCTION_SINGLE,
            SENIOR_EXTRA_SINGLE,
            year=year,
            cpi=cpi,
        )
    else:
        ded = deductions(
            hh.your_age, hh.spouse_age, hh.std_deduction, hh.senior_extra, year=year, cpi=cpi
        )

    # Hoist base-state computations outside the loop
    # Single filers have only one Medicare beneficiary (42 U.S.C. §1395r(i)).
    # Mirror the is_mfj guard used in compute_year_by_year_timeline so a
    # phantom default-age spouse cannot inflate on_medicare to 2 for Single HHs.
    _is_mfj_curves = hh.filing_status == "MFJ"

    # ACA MAGI adds back non-taxable SS (IRC §36B(d)(2)(B)(iii)); IRMAA MAGI does
    # not. Constant offset at the base point applied to every swept MAGI below.
    # 0.0 unless someone is drawing SS during the ACA years (no-op for default HH).
    nontaxable_ss = _nontaxable_ss(
        hh,
        hh.your_age_in(year),
        hh.spouse_age_in(year) if _is_mfj_curves else None,
        other_income=base_magi,
        filing_status=hh.filing_status,
    )

    # IRMAA 2-year lookback: income realized in `year` is judged against the
    # thresholds published for, and paid in, year + 2. ACA (below) stays on `year`
    # because it is a same-year effect.
    _irmaa_year = year + 2
    if _is_mfj_curves:
        on_medicare = sum(
            1 for a in (hh.your_age_in(_irmaa_year), hh.spouse_age_in(_irmaa_year)) if a >= 65
        )
    else:
        on_medicare = 1 if hh.your_age_in(_irmaa_year) >= 65 else 0
    base_irmaa = irmaa_surcharge(
        base_magi,
        num_people=on_medicare,
        base_part_b=hh.medicare_part_b_base_monthly * 12,
        filing_status=hh.filing_status,
        year=_irmaa_year,
        cpi=cpi,
    )
    base_niit = niit(base_magi, net_inv_income, filing_status=hh.filing_status)

    aca_subsidy_vals: list[float] = []
    aca_net_cost_vals: list[float] = []
    aca_loss_vals: list[float] = []
    irmaa_vals: list[float] = []
    irmaa_tier_vals: list[int] = []
    niit_vals: list[float] = []
    fed_tax_vals: list[float] = []
    marginal_vals: list[float] = []
    total_hidden_cost: list[float] = []

    for magi in magi_points:
        # ACA (only meaningful if enrolled and pre-65)
        if anyone_on_aca:
            sub = aca_subsidy(
                magi + nontaxable_ss,
                benchmark=effective_benchmark,
                enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
                filing_status=hh.filing_status,
                year=year,
                cpi=cpi,
            )
            aca_subsidy_vals.append(sub)
            aca_net_cost_vals.append(
                aca_net_cost(
                    magi + nontaxable_ss,
                    benchmark=effective_benchmark,
                    enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
                    filing_status=hh.filing_status,
                    year=year,
                    cpi=cpi,
                )
            )
            aca_loss_vals.append(
                aca_subsidy_loss(
                    base_magi + nontaxable_ss,
                    magi + nontaxable_ss,
                    benchmark=effective_benchmark,
                    enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
                    filing_status=hh.filing_status,
                    year=year,
                    cpi=cpi,
                )
            )
        else:
            aca_subsidy_vals.append(0)
            aca_net_cost_vals.append(0)
            aca_loss_vals.append(0)

        # IRMAA
        surcharge = irmaa_surcharge(
            magi,
            num_people=on_medicare,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
            filing_status=hh.filing_status,
            year=_irmaa_year,
            cpi=cpi,
        )
        irmaa_vals.append(surcharge)
        irmaa_tier_vals.append(
            irmaa_tier(magi, filing_status=hh.filing_status, year=_irmaa_year, cpi=cpi)
        )

        # NIIT
        niit_vals.append(niit(magi, net_inv_income, filing_status=hh.filing_status))

        # Tax
        bonus_ded = senior_bonus_deduction(
            hh.your_age,
            hh.spouse_age,
            magi,
            year=year,
            cpi=cpi,
            filing_status=hh.filing_status,
        )
        taxable = max(magi - ded - bonus_ded, 0)
        if hh.filing_status == "Single":
            fed_tax_vals.append(federal_tax_single(taxable, year=year, cpi=cpi))
            marginal_vals.append(marginal_rate_single(taxable, year=year, cpi=cpi))
        else:
            fed_tax_vals.append(federal_tax(taxable, year=year, cpi=cpi))
            marginal_vals.append(marginal_rate(taxable, year=year, cpi=cpi))

        # Combined hidden cost (ACA loss + IRMAA beyond base + NIIT increase)
        hidden = (
            aca_subsidy_loss(
                base_magi + nontaxable_ss,
                magi + nontaxable_ss,
                benchmark=effective_benchmark,
                enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
                filing_status=hh.filing_status,
                year=year,
                cpi=cpi,
            )
            + max(surcharge - base_irmaa, 0)
            + max(niit(magi, net_inv_income, filing_status=hh.filing_status) - base_niit, 0)
        )
        total_hidden_cost.append(hidden)

    irmaa_increase = [max(v - base_irmaa, 0) for v in irmaa_vals]
    niit_increase = [max(v - base_niit, 0) for v in niit_vals]

    return CostCurves(
        magi_points=magi_points,
        aca_subsidy_vals=aca_subsidy_vals,
        aca_net_cost_vals=aca_net_cost_vals,
        aca_subsidy_loss_vals=aca_loss_vals,
        irmaa_vals=irmaa_vals,
        irmaa_tier_vals=irmaa_tier_vals,
        niit_vals=niit_vals,
        fed_tax_vals=fed_tax_vals,
        marginal_rate_vals=marginal_vals,
        total_hidden_cost_vals=total_hidden_cost,
        irmaa_increase_vals=irmaa_increase,
        niit_increase_vals=niit_increase,
        base_irmaa=base_irmaa,
        base_niit=base_niit,
        nontaxable_ss=nontaxable_ss,
    )


@dataclass
class TimelineRow:
    """One year in the ACA → IRMAA timeline. Raw values; view formats."""

    year: int
    you_age: int | None  # None if you are deceased (handle inherited-IRA case if applicable)
    spouse_age: int | None
    system: str  # human-readable e.g. "ACA (you) + Medicare (sp)" or "Medicare"
    irmaa_tier: int | None  # None if no one on Medicare yet
    irmaa_room: float | None  # None if no one on Medicare yet
    aca_subsidy: float | None  # None if ACA does not apply
    aca_you_pay: float | None  # None if ACA does not apply


def compute_year_by_year_timeline(
    hh: Household,
    base_magi: float,
    *,
    years: int = 20,
    cpi: float,
) -> list[TimelineRow]:
    """Build the 20-year ACA → IRMAA timeline.

    For each year:
    - Compute You/Spouse ages
    - Determine which system applies (ACA / Medicare / both)
    - If anyone on Medicare: compute IRMAA tier + room to next threshold
    - If ACA applies: compute current ACA subsidy + net cost
    """
    surv = hh.survivor
    is_mfj_base = hh.filing_status == "MFJ"
    rows: list[TimelineRow] = []
    for yr_idx in range(years):
        year = hh.base_year + yr_idx

        # === Survivor scenario: determine filing status for this year ===
        survivor_active = surv is not None and year >= surv.death_year + 1
        current_filing_status = "Single" if survivor_active else hh.filing_status
        is_mfj = is_mfj_base and not survivor_active

        ya = hh.your_age_in(year)
        sa = hh.spouse_age_in(year) if is_mfj else None

        you_on_aca = aca_applies(ya, hh.your_aca_enrolled)
        sp_on_aca = aca_applies(sa, hh.spouse_aca_enrolled) if sa is not None else False
        on_medicare_you = ya >= 65
        on_medicare_sp = sa >= 65 if sa is not None else False

        # When survivor_active, only the SURVIVOR's Medicare status counts.
        if survivor_active and surv is not None:
            if surv.who_dies == "spouse":
                # survivor is "you" — drop the deceased spouse
                on_medicare_sp = False
            else:
                # survivor is the spouse — base Medicare on the surviving
                # spouse's actual age (sa is None here because is_mfj is False)
                on_medicare_you = False
                on_medicare_sp = hh.spouse_age_in(year) >= 65
        medicare_count = (1 if on_medicare_you else 0) + (1 if on_medicare_sp else 0)

        # Determine system per person
        parts = []
        if you_on_aca:
            parts.append("ACA (you)")
        elif on_medicare_you:
            parts.append("Medicare (you)")
        else:
            parts.append("Employer (you)")
        if is_mfj:
            if sp_on_aca:
                parts.append("ACA (sp)")
            elif on_medicare_sp:
                parts.append("Medicare (sp)")
            elif hh.spouse_aca_enrolled:
                parts.append("ACA (sp)")
            else:
                parts.append("Uninsured/Other (sp)")
        system = " + ".join(parts)

        _yr_cpi = cpi
        # A2: IRMAA 2-year lookback — income realized in `year` is judged against the
        # thresholds published for, and paid in, year + 2. Mirror compute_cost_curves
        # (`_irmaa_year`) and engine.irmaa.irmaa_for_year so the timeline and cost-curve
        # views no longer disagree on tier/room for the same MAGI by ~2 CPI-years. ACA
        # stays on `year` below (a same-year effect), as does the medicare_count gate
        # and the system label (income-year insurance status).
        _irmaa_year = year + 2
        irmaa_room = (
            irmaa_next_threshold(
                base_magi, filing_status=current_filing_status, year=_irmaa_year, cpi=_yr_cpi
            )
            if medicare_count > 0
            else None
        )

        num_on_aca_yr = (1 if you_on_aca else 0) + (1 if sp_on_aca else 0)
        eff_bench_yr = hh.aca_benchmark_premium_annual * (num_on_aca_yr / 2)
        aca_sub: float | None = None
        aca_pay: float | None = None
        if you_on_aca or sp_on_aca:
            # ACA MAGI adds back non-taxable SS (IRC §36B(d)(2)(B)(iii)). Survivor
            # years keep the existing behavior (offset 0): the survivor-SS step-up
            # interaction with ACA is deferred to a separate design pass.
            nontaxable_ss_yr = (
                _nontaxable_ss(
                    hh, ya, sa, other_income=base_magi, filing_status=current_filing_status
                )
                if not survivor_active
                else 0.0
            )
            aca_sub = aca_subsidy(
                base_magi + nontaxable_ss_yr,
                benchmark=eff_bench_yr,
                enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
                filing_status=current_filing_status,
                year=year,
                cpi=_yr_cpi,
            )
            aca_pay = aca_net_cost(
                base_magi + nontaxable_ss_yr,
                benchmark=eff_bench_yr,
                enhanced_subsidies_active=hh.aca_enhanced_subsidies_active,
                filing_status=current_filing_status,
                year=year,
                cpi=_yr_cpi,
            )

        rows.append(
            TimelineRow(
                year=year,
                you_age=ya,
                spouse_age=sa,
                system=system,
                irmaa_tier=irmaa_tier(
                    base_magi, filing_status=current_filing_status, year=_irmaa_year, cpi=_yr_cpi
                )
                if medicare_count > 0
                else None,
                irmaa_room=irmaa_room,
                aca_subsidy=aca_sub,
                aca_you_pay=aca_pay,
            )
        )

    return rows


def index_irmaa_tier_thresholds(
    tiers: list[tuple[float, float, float]],
    *,
    year: int,
    cpi: float,
) -> list[tuple[float, float, float]]:
    """CPI-index IRMAA tier thresholds (first tuple slot) to a target year.

    Delegates to _index_irmaa_tiers so the frozen Tier 5 threshold is never
    inflated (Tiers 1-4 indexed; Tier 5 preserved at statute-frozen base value).
    """
    return _index_irmaa_tiers(tiers, year=year, cpi=cpi)
