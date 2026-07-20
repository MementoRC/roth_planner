"""Regression test for audit Finding F1.

Finding F1: engine/scenario.py `run_scenario`'s `combined_gross` YTD block
enumerates 8 YTDSnapshot ordinary-income fields but omits `crypto_stcg_ytd`
and `crypto_income_ytd`. Both are ordinary-bracket income per
`YTDSnapshot.total_ordinary_income` and already flow into MAGI (via
`compute_magi` -> `magi_ytd`), but `combined_gross` (and therefore
`taxable_income` and `federal_tax_amt`) silently drops them.

Rule: crypto STCG YTD is ordinary-bracket income and must flow into
combined_gross / taxable_income / federal tax, consistent with its
inclusion in MAGI.
"""

import pytest

from engine.scenario import ConversionPlan, run_scenario
from engine.tax import taxable_ss
from models.household import Household
from models.ytd_income import YTDSnapshot


class TestF1CryptoStcgYtdInCombinedGross:
    """F1 — crypto_stcg_ytd must reach combined_gross, not just MAGI."""

    def test_f1_combined_gross_includes_crypto_stcg_ytd(self) -> None:
        # Given: a household with no other income sources, and a YTD snapshot
        # whose only income is $300K of crypto short-term capital gains.
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_stcg_ytd=300_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "f1_crypto_stcg", end_age=61, ytd=ytd)
        yr = result.years[0]

        # Then:
        # MAGI already routes crypto_stcg_ytd correctly (guard — was already correct).
        assert yr.magi == pytest.approx(300_000.0)

        # combined_gross must include the same $300K of ordinary-bracket income
        # (the bug: pre-fix code silently drops crypto_stcg_ytd here).
        assert yr.combined_gross == pytest.approx(300_000.0)

        # With $300K of ordinary income and no offsetting deductions beyond the
        # standard deduction, federal tax must be strictly positive.
        assert yr.federal_tax_amt > 0


class TestF2CryptoYtdInSocialSecurityProvisionalIncome:
    """F2 — crypto YTD income must reach the SS provisional-income base.

    Finding F2: `engine/scenario_compute.py::compute_social_security`'s
    YTD ordinary-income block (used to build IRC §86(b)(2) provisional
    income) enumerates wages/NEC/STCG/dividends/conversions/distributions/
    interest but omits `crypto_stcg_ytd`, `crypto_income_ytd`, and
    `crypto_ltcg_ytd`. All three are AGI items (crypto STCG/income are
    ordinary; crypto LTCG is a capital gain, same AGI-inclusion treatment
    as `ltcg_ytd`/`qualified_dividends_ytd` already in the block) and must
    count toward provisional income, understating taxable Social Security
    when omitted.

    Rule: provisional income = AGI items (incl. crypto STCG/income/LTCG) +
    tax-exempt interest + 1/2 combined SS (IRC §86(b)(2)).
    """

    def test_f2_taxable_ss_reflects_crypto_ytd_provisional_income(self) -> None:
        # Given: a household where SS has already started (age 70, default
        # your_ss_start_age=70), no other income sources, and a YTD snapshot
        # with $100K of crypto STCG — deliberately chosen (with the default
        # SS benefit) to land provisional income in the 85%-taxable tier.
        hh = Household(
            your_age=70,
            spouse_age=65,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_stcg_ytd=100_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "f2_crypto_ss", end_age=70, ytd=ytd)
        yr = result.years[0]

        # Then: the only non-SS AGI item is the $100K crypto STCG, so the
        # correct provisional income is exactly crypto_stcg_ytd + 1/2 combined_ss,
        # and taxable_ss_amt must match the formula applied to that base.
        expected_taxable_ss = taxable_ss(
            yr.combined_ss, 100_000.0, filing_status=hh.filing_status
        )
        assert expected_taxable_ss > 0, "test setup must land in a taxable SS tier"
        assert yr.taxable_ss_amt == pytest.approx(expected_taxable_ss)


class TestF3CryptoLtcgYtdInLtcgStackWalk:
    """F3 — crypto_ltcg_ytd must reach the LTCG preferential-rate stack-walk.

    Finding F3: `engine/scenario.py`'s LTCG stack-walk guard/base
    (`_ytd_ltcg_total`, ~line 578) sums only `ltcg_ytd` +
    `qualified_dividends_ytd`, omitting `crypto_ltcg_ytd`. Crypto LTCG
    already flows into MAGI (`magi_ytd`) and NIIT (`total_investment_income`)
    but never reaches the 0%/15%/20% LTCG stack-walk, so a household whose
    only realized gain is crypto LTCG pays $0 LTCG tax while NIIT correctly
    fires on the same dollars.

    Rule: crypto LTCG is taxed at the same preferential 0/15/20% rates as
    ordinary LTCG (IRC §1(h)) and must be included in the stack-walk base.
    """

    def test_f3_ytd_ltcg_tax_positive_when_only_gain_is_crypto_ltcg(self) -> None:
        # Given: a household with zero ordinary income and a YTD snapshot
        # whose only investment activity is $200K of crypto long-term
        # capital gains — large enough to cross the 0%->15% LTCG threshold
        # even starting from $0 ordinary taxable income.
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
            living_expenses=60_000.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_ltcg_ytd=200_000.0)
        plan = ConversionPlan()

        # When: the scenario is projected for the base year only.
        result = run_scenario(hh, plan, "f3_crypto_ltcg", end_age=61, ytd=ytd)
        yr = result.years[0]

        # Then:
        # MAGI already routes crypto_ltcg_ytd correctly (guard — was already correct).
        assert yr.magi == pytest.approx(200_000.0)

        # The bug: the stack-walk guard/base omits crypto_ltcg_ytd entirely,
        # so ytd_ltcg_tax (and its fold-in to federal_tax_amt) is $0.
        assert yr.ytd_ltcg_tax > 0
