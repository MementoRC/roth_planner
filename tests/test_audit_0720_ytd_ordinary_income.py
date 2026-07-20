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
