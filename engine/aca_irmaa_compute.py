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
    resolve_couple_benchmark_annual,
)
from engine.ira import ss_benefit_at_age, ss_with_cola
from engine.irmaa import _index_irmaa_tiers, irmaa_next_threshold, irmaa_surcharge, irmaa_tier
from engine.niit import niit
from engine.sweet_spot_compute import base_income_for_year
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
from models.ytd_income import YTDSnapshot


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

    ``other_income`` is the caller's SS-inclusive base/swept MAGI (e.g. the
    Explorer's "base MAGI" input) — every call site adds only this function's
    return value back on top (``magi + nontaxable_ss``), so ``other_income``
    already embeds ``combined_ss``'s taxable portion whenever SS is being
    drawn. IRC §86 taxable SS is a function of income OTHER than SS, so
    feeding the SS-inclusive ``other_income`` straight into ``taxable_ss()``
    over-counts §86 provisional income by that already-embedded taxable-SS
    amount.

    That over-count used to be dismissed here as "immaterial" on the theory
    that ACA-relevant incomes pin SS inclusion at the 85% cap — true only
    once ``other_income`` is comfortably above the SS taxability phase-in
    band, where the cap absorbs the perturbation. Inside the phase-in band
    (50%/85% marginal taxability) the over-count is NOT immaterial: an MFJ
    household with $40,000 combined SS and a $24,000 SS-inclusive base MAGI
    understated the add-back by $2,000 (5.6% of the correct $36,000) under
    the old one-shot computation. Fixed-point iterate instead to solve
    ``x = other_income - taxable_ss(combined_ss, x)`` for the true
    non-SS income ``x`` (contraction mapping, Lipschitz <= 0.85 — converges
    to sub-cent precision well within the fixed iteration budget below).
    This keeps the add-back exact across the whole SS taxability curve, not
    just the phase-in band, without touching the (already-correct) IRMAA
    MAGI path, which never adds this value back.

    Mirrors the canonical add-back in engine/scenario_compute.compute_aca and
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
    # Solve x = other_income - taxable_ss(combined_ss, x) by fixed-point
    # iteration (see docstring) instead of the one-shot
    # taxable_ss(combined_ss, other_income, ...) that double-counted the
    # taxable-SS portion already embedded in other_income.
    non_ss_income = other_income
    for _ in range(50):
        non_ss_income = other_income - taxable_ss(
            combined_ss, non_ss_income, filing_status=filing_status
        )
    taxable = taxable_ss(combined_ss, non_ss_income, filing_status=filing_status)
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
    # W4: auto-derived net investment income (forecast div/gains + YTD investment
    # income, if applied) folded into every NIIT computation below. Exposed so the
    # view can show the manual field's "on top of X auto-detected" meaning.
    auto_nii: float = 0.0


def compute_cost_curves(
    magi_points: list[float],
    base_magi: float,
    net_inv_income: float,
    hh: Household,
    *,
    year: int,
    cpi: float,
    ytd: YTDSnapshot | None = None,
) -> CostCurves:
    """Build cost curves for ACA, IRMAA, NIIT, federal tax, and total hidden cost.

    Computes base-state ACA/IRMAA/NIIT once (was previously recomputed inside the
    loop in views/aca_irmaa.py) and reuses for the hidden-cost decomposition.

    W4: `net_inv_income` (the manual widget value) is treated as "additional NII
    not otherwise modeled" -- the SAME semantics Sweet Spot uses -- and is added
    to the auto-derived `net_investment_income_addl` (forecast div/gains + YTD
    investment income, if `ytd` is supplied) before every NIIT computation below.
    Pre-fix, this function fed the raw manual value only, under-counting NIIT
    whenever the household has forecast brokerage income or opted-in YTD actuals.
    """
    auto_nii = base_income_for_year(hh, year, ytd=ytd).net_investment_income_addl
    total_nii = net_inv_income + auto_nii
    _your_on_aca = aca_applies(hh.your_age_in(year), hh.your_aca_enrolled)
    _spouse_on_aca = aca_applies(hh.spouse_age_in(year), hh.spouse_aca_enrolled)
    anyone_on_aca = _your_on_aca or _spouse_on_aca

    resolved_couple_benchmark = resolve_couple_benchmark_annual(
        hh.aca_benchmark_premium_annual,
        your_age=hh.your_age,
        spouse_age=hh.spouse_age,
        filing_status=hh.filing_status,
        year=year,
        cpi=cpi,
    )
    effective_benchmark = effective_benchmark_premium(
        resolved_couple_benchmark,
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
            filing_status="Single",
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
    base_niit = niit(base_magi, total_nii, filing_status=hh.filing_status)

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
        niit_vals.append(niit(magi, total_nii, filing_status=hh.filing_status))

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
            + max(niit_vals[-1] - base_niit, 0)
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
        auto_nii=auto_nii,
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

        # audit-0805 C3: mirror the Medicare gate above for ACA. Without this,
        # is_mfj=False forces sa=None every survivor year regardless of who
        # died, so sp_on_aca was unconditionally False (denying a subsidy to a
        # genuinely enrolled, under-65 surviving spouse) while you_on_aca kept
        # reading the deceased "you"'s continuing age/enrollment flag (crediting
        # a subsidy to someone who died years earlier). Only the SURVIVOR's ACA
        # enrollment may count in survivor years.
        if survivor_active and surv is not None:
            if surv.who_dies == "spouse":
                # survivor is "you" — deceased spouse can't be on ACA
                sp_on_aca = False
            else:
                # survivor is the spouse — base ACA eligibility on the surviving
                # spouse's actual age (sa is None here because is_mfj is False),
                # not the deceased primary's continuing age/enrollment flag.
                you_on_aca = False
                sp_on_aca = aca_applies(hh.spouse_age_in(year), hh.spouse_aca_enrolled)
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

        if survivor_active and surv is not None:
            # C5 fix: hh.aca_benchmark_premium_annual is the ORIGINAL COUPLE'S
            # benchmark. effective_benchmark_premium's filing_status="Single"
            # branch assumes couple_benchmark is ALREADY an individual rate
            # (true for a genuinely-Single household), so passing
            # current_filing_status="Single" here would return the FULL couple
            # rate to the survivor instead of their age-rated share. Force the
            # MFJ partial-enrollment blend path instead — deceased's age still
            # counts toward the blend (not enrolled), survivor gets the
            # age-rated share of the couple benchmark.
            _deceased_age = (
                hh.spouse_age_in(year) if surv.who_dies == "spouse" else hh.your_age_in(year)
            )
            resolved_couple_bench_yr = resolve_couple_benchmark_annual(
                hh.aca_benchmark_premium_annual,
                your_age=ya,
                spouse_age=_deceased_age,
                filing_status="MFJ",
                year=year,
                cpi=_yr_cpi,
            )
            eff_bench_yr = effective_benchmark_premium(
                resolved_couple_bench_yr,
                your_age=ya,
                your_on_aca=you_on_aca,
                spouse_age=_deceased_age,
                spouse_on_aca=False,
                filing_status="MFJ",
            )
        else:
            resolved_couple_bench_yr = resolve_couple_benchmark_annual(
                hh.aca_benchmark_premium_annual,
                your_age=ya,
                spouse_age=sa if sa is not None else 0,
                filing_status=current_filing_status,
                year=year,
                cpi=_yr_cpi,
            )
            eff_bench_yr = effective_benchmark_premium(
                resolved_couple_bench_yr,
                your_age=ya,
                your_on_aca=you_on_aca,
                spouse_age=sa if sa is not None else 0,
                spouse_on_aca=sp_on_aca,
                filing_status=current_filing_status,
            )
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

    Delegates to _index_irmaa_tiers: Tiers 1-4 are indexed every year; Tier 5
    is frozen at its base value for 2020-2027, then resumes CPI indexing for
    2028+ (audit-0802 F2).
    """
    return _index_irmaa_tiers(tiers, year=year, cpi=cpi)
