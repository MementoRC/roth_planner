"""Data-only types for the scenario engine.

Pure dataclasses with no dependency on engine.scenario — extracted so
scenario_autofill.py can import them without a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.household import Household


@dataclass
class YearResult:
    """All computed values for a single year."""

    year: int
    your_age: int
    spouse_age: int
    phase: str  # "options", "clean", "ss_conv", "squeeze", "rmd"
    filing_status: str = ""  # per-year filing status ("MFJ"/"Single"); "Single" after a survivor transition (U2)

    # IRA balances (beginning of year)
    your_ira_begin: float = 0.0
    spouse_ira_begin: float = 0.0
    your_roth_begin: float = 0.0
    spouse_roth_begin: float = 0.0

    # Income sources
    option_income: float = 0.0
    your_conversion: float = 0.0
    spouse_conversion: float = 0.0
    your_rmd: float = 0.0
    qcd: float = 0.0
    taxable_rmd: float = 0.0
    spouse_rmd: float = 0.0
    spouse_qcd: float = 0.0
    spouse_taxable_rmd: float = 0.0
    your_ss: float = 0.0
    spouse_ss: float = 0.0
    combined_ss: float = 0.0
    taxable_ss_amt: float = 0.0

    extra_withdrawal: float = (
        0.0  # voluntary excess withdrawal from your IRA (post-RMD bracket fill)
    )
    spouse_extra_withdrawal: float = (
        0.0  # voluntary excess withdrawal from spouse IRA (post-RMD bracket fill)
    )

    # YTD actuals (base year only, when ytd snapshot provided)
    ytd_wages: float = 0.0
    ytd_ltcg: float = 0.0
    ytd_stcg: float = 0.0
    ytd_dividends: float = 0.0  # aggregate (qualified + ordinary); backward compat
    ytd_qualified_dividends: float = 0.0
    ytd_ordinary_dividends: float = 0.0
    ytd_interest: float = 0.0
    ytd_conversions_done: float = 0.0
    ytd_ltcg_tax: float = 0.0  # LTCG tax computed separately

    # Aggregates
    combined_gross: float = 0.0
    total_deductions: float = 0.0
    taxable_income: float = 0.0
    magi: float = 0.0  # for IRMAA/ACA (uses full RMD, full SS)
    niit_magi: float = 0.0  # NIIT MAGI per IRC §1411(d)(3): excludes muni interest (vs. yr.magi which is IRMAA-compatible)
    aca_magi: float = 0.0  # ACA MAGI per IRC §36B(d)(2)(B): yr.magi + non-taxable SS portion

    # Tax & costs
    federal_tax_amt: float = 0.0
    marginal_bracket: float = 0.0
    conversion_tax: float = 0.0
    irmaa_cost: float = 0.0
    aca_loss: float = 0.0
    aca_clawback: float = 0.0  # Form 8962 excess-APTC repayment (positive = owed, negative = refund); added to federal_tax_amt
    niit_cost: float = 0.0
    conversion_ltcg_cost: float = 0.0  # C2: conversion-induced LTCG bracket-stacking cost; added to all_in_cost only (NOT conversion_tax) to avoid double-counting cum_brok_tax
    all_in_cost: float = 0.0

    # Bracket room
    room_12: float = 0.0
    room_22: float = 0.0
    irmaa_room: float = 0.0

    # Brokerage (excess RMD tracking)
    living_expenses: float = 0.0
    income_needed: float = 0.0
    excess_rmd: float = 0.0
    # audit-0805 C8: the portion of income_needed that the brokerage balance
    # could NOT cover this year (shortfall > available brokerage). Falling
    # back to an IRA withdrawal is DELIBERATELY out of scope -- see the debit
    # comment in engine/scenario.py's brokerage-update block.
    unfunded_need: float = 0.0
    # brokerage_balance is the OPENING (begin-of-year) balance -- set before
    # this year's growth, reinvested dividends, excess_rmd contribution, and
    # the living-expense debit are applied (engine/scenario.py:824).
    brokerage_balance: float = 0.0
    # brokerage_balance_end is the CLOSING (end-of-year) balance -- after
    # this year's growth, reinvested dividends, excess_rmd contribution, and
    # the living-expense debit (engine/scenario.py, set alongside
    # brokerage_basis). brokerage_basis is ALSO a closing-of-year quantity,
    # so it must be compared against brokerage_balance_end, never against
    # brokerage_balance -- comparing basis to the opening balance is an
    # apples-to-oranges footgun this field exists to remove.
    brokerage_balance_end: float = 0.0
    brokerage_basis: float = 0.0  # derived cost basis of brokerage_balance_end (bookkeeping only; see Household.brokerage_start_basis)
    brokerage_growth: float = 0.0
    brokerage_gain_tax: float = 0.0
    brokerage_qual_div: float = 0.0  # qualified dividends (MAGI-only / LTCG rate)
    brokerage_ord_div: float = 0.0  # ordinary dividends (ordinary income stack)

    # IRA-withdrawal-waterfall (stage 3a plumbing only -- not yet activated;
    # engine/withdrawal_waterfall.py solves the fixed-point, wiring lands in
    # stage 3b). All default to inert no-op values.
    forced_brokerage_draw: float = 0.0
    forced_your_ira_draw: float = 0.0
    forced_spouse_ira_draw: float = 0.0
    forced_your_roth_draw: float = 0.0
    forced_spouse_roth_draw: float = 0.0
    forced_early_withdrawal_penalty: float = 0.0
    # False ONLY when the tax gross-up iteration failed to settle (it
    # oscillated, or pinned against the IRA ceiling and still left the
    # household short). It is NOT a resource-exhaustion signal: test
    # `unfunded_need > 0` for that.
    waterfall_converged: bool = True
    # The IRA leg hit its ceiling and the waterfall moved on to the Roth.
    # Routine and informational -- true in every year funded partly from the
    # Roth, which for a decumulating household is most late years. Kept
    # separate from `waterfall_converged`, which previously conflated the two
    # and made 17 fully-funded years report solver failure.
    waterfall_ira_leg_saturated: bool = False

    # Inherited IRA distributions (SECURE Act 10-year rule)
    your_inherited_distribution: float = 0.0
    spouse_inherited_distribution: float = 0.0
    your_inherited_balance_end: float = 0.0  # sum of inherited balances for "you" at end of year
    spouse_inherited_balance_end: float = (
        0.0  # sum of inherited balances for "spouse" at end of year
    )

    # IRA end of year
    your_ira_end: float = 0.0
    spouse_ira_end: float = 0.0
    your_roth_end: float = 0.0
    spouse_roth_end: float = 0.0


@dataclass
class ConversionPlan:
    """User-specified conversion amounts per year."""

    your_conversions: dict[int, float] = field(default_factory=dict)  # year -> amount
    spouse_conversions: dict[int, float] = field(default_factory=dict)
    qcds: dict[int, float] = field(default_factory=dict)  # year -> QCD amount
    spouse_qcds: dict[int, float] = field(default_factory=dict)  # year -> spouse QCD amount
    extra_withdrawals: dict[int, float] = field(
        default_factory=dict
    )  # year -> voluntary excess (your IRA)
    spouse_extra_withdrawals: dict[int, float] = field(
        default_factory=dict
    )  # year -> voluntary excess (spouse IRA)


@dataclass
class ScenarioResult:
    """Complete multi-year projection output."""

    name: str
    years: list[YearResult]
    household: Household
    plan: ConversionPlan

    # Summary
    total_your_conv: float = 0.0
    total_spouse_conv: float = 0.0
    total_conv_tax: float = 0.0
    total_irmaa: float = 0.0
    total_aca_loss: float = 0.0
    total_niit: float = 0.0
    total_rmd_tax: float = 0.0  # cumulative tax during RMD years
    total_brok_tax: float = 0.0  # cumulative brokerage capital gains tax

    def years_as_dicts(self) -> list[dict]:
        """Convert to list of dicts for DataFrame creation."""
        return [yr.__dict__ for yr in self.years]
