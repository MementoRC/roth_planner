"""Re-price one projected year at extra income (audit-0823 AF-2; #123 slice 3).

A YearResult's taxable Social Security and OBBBA senior bonus were priced at the
year's own income. Sizing an ADDITIONAL amount must re-price both: the amount
raises provisional income (more SS becomes taxable, IRC 86) and MAGI (the senior
bonus phases out, IRC 151(d)(5)). Pure: no Streamlit, no projection.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.scenario_types import YearResult
from engine.tax import senior_bonus_deduction, taxable_ss


@dataclass(frozen=True)
class Repriced:
    """Year figures re-priced after an income change; taxable_income is ORDINARY taxable income only."""

    taxable_ss: float
    taxable_income: float  # max(gross_at - deductions_at, 0); ordinary only
    total_deductions: float  # deductions after the senior bonus
    magi_phaseout_basis: float


def reprice(yr: YearResult, ordinary_delta: float, other_agi_delta: float, cpi: float) -> Repriced:
    """``yr`` re-priced with ``ordinary_delta`` more ordinary income (it enters
    provisional income, gross and AGI -- the bracket fill's withdrawal) and
    ``other_agi_delta`` more any non-ordinary AGI (e.g. long-term capital gain). It
    raises provisional income and MAGI but is not ordinary income; callers must price
    any preferential-rate tax separately (e.g. _pref_stack_tax). ``cpi`` is
    ``hh.cpi_assumption``.

    Uses the inputs the year was priced from (``ss_provisional_base``,
    ``deductions_before_senior_bonus``, ``senior_bonus_*_eff``,
    ``magi_phaseout_basis``), so both deltas at 0 reproduce the year's own figures.
    """
    tss_at = taxable_ss(
        yr.combined_ss,
        yr.ss_provisional_base + ordinary_delta + other_agi_delta,
        filing_status=yr.filing_status,
    )
    gross_at = yr.combined_gross - yr.taxable_ss_amt + tss_at + ordinary_delta
    # The senior bonus falls as MAGI rises; MAGI moves with both deltas AND with
    # the extra taxable SS they dragged in.
    magi_at = yr.magi_phaseout_basis - yr.taxable_ss_amt + tss_at + ordinary_delta + other_agi_delta
    ded_at = yr.deductions_before_senior_bonus + senior_bonus_deduction(
        yr.senior_bonus_ya_eff,
        yr.senior_bonus_sa_eff,
        magi_at,
        year=yr.year,
        cpi=cpi,
        filing_status=yr.filing_status,
    )
    return Repriced(
        taxable_ss=tss_at,
        taxable_income=max(gross_at - ded_at, 0.0),
        total_deductions=ded_at,
        magi_phaseout_basis=magi_at,
    )
