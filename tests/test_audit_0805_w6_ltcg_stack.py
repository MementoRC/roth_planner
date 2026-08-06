"""TDD regression tests for audit-0805 W6 findings C78 and C1 in
engine/tax.py::estimate_ytd_federal_tax.

C78 (crypto LTCG omitted from preferential stack)
--------------------------------------------------
engine/tax.py:524 builds ``ltcg_taxable = ltcg_ytd + qualified_dividends_ytd``,
dropping ``crypto_ltcg_ytd``. Crypto long-term capital gains ARE long-term
capital gains (no statutory carve-out distinguishes the asset class -- see
IRC §1222(3)/(11) and Notice 2014-21) and belong in the same §1(h)
preferential-rate stack as ltcg_ytd/qualified_dividends_ytd.

C1 (unused standard deduction not offsetting LTCG)
----------------------------------------------------
engine/tax.py:512 clamps ``taxable_ordinary`` at 0 using ONLY ordinary
income, then lines 525-526 stack the FULL LTCG amount on top of that
floor. Per IRC §1(h) (the Qualified Dividends and Capital Gain Tax
Worksheet, Form 1040 Instructions), taxable income is TOTAL income
(ordinary + net capital gain) minus ALL deductions, floored at 0 -- a
standard deduction unused by ordinary income must offset LTCG too.
"""

from __future__ import annotations

import pytest

from engine.tax import estimate_ytd_federal_tax
from models.household import Household
from models.ytd_income import YTDSnapshot


def approx(expected: float, tol: float = 0.01) -> object:
    return pytest.approx(expected, abs=tol)


def _hh_mfj(your_age: int = 60, spouse_age: int = 60) -> Household:
    """MFJ household, base_year=2026, no CPI inflation, neither spouse 65+."""
    return Household(
        your_age=your_age,
        spouse_age=spouse_age,
        base_year=2026,
        cpi_assumption=0.0,
        filing_status="MFJ",
    )


class TestC78CryptoLTCGOmittedFromStack:
    """audit-0805 C78: engine/tax.py:524 drops crypto_ltcg_ytd from the
    preferential-rate stack. Crypto LTCG is long-term capital gain and must
    be taxed at the §1(h) preferential rate, not excluded entirely.
    """

    def test_crypto_ltcg_taxed_at_preferential_rate(self) -> None:
        """MFJ, wages=$200K + crypto_ltcg=$500K.

        taxable_ordinary = 200_000 - 32_200 = 167_800.
        Correct ltcg_tax = 445_900 * 0.15 + 54_100 * 0.20 = 77_705.00
        (445_900 = 613_700 - 167_800; 54_100 = 667_800 - 613_700).
        Current (defective) code omits crypto_ltcg_ytd entirely -> 0.00.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=200_000, crypto_ltcg_ytd=500_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        assert result.ltcg_tax == approx(77_705.00), (
            f"Expected ltcg_tax=77705.00 (crypto LTCG taxed via §1(h) stack), "
            f"got {result.ltcg_tax:.2f} -- crypto_ltcg_ytd is being dropped "
            f"from the preferential-rate stack (engine/tax.py:524)"
        )

    def test_crypto_ltcg_equivalent_to_regular_ltcg(self) -> None:
        """Equivalence: crypto_ltcg_ytd=X must yield the same ltcg_tax as
        ltcg_ytd=X (all else equal) -- both are long-term capital gain and
        the tax code draws no distinction between the two for §1(h) purposes.
        """
        hh = _hh_mfj()
        ytd_regular = YTDSnapshot(tax_year=2026, wages_ytd=200_000, ltcg_ytd=500_000)
        ytd_crypto = YTDSnapshot(tax_year=2026, wages_ytd=200_000, crypto_ltcg_ytd=500_000)

        result_regular = estimate_ytd_federal_tax(ytd_regular, hh)
        result_crypto = estimate_ytd_federal_tax(ytd_crypto, hh)

        assert result_crypto.ltcg_tax == approx(result_regular.ltcg_tax), (
            f"crypto_ltcg_ytd=500000 produced ltcg_tax={result_crypto.ltcg_tax:.2f} but "
            f"ltcg_ytd=500000 (same dollar amount, same asset class treatment under "
            f"IRC §1222) produced ltcg_tax={result_regular.ltcg_tax:.2f} -- these must match"
        )


class TestC1UnusedStdDeductionOffsetsLTCG:
    """audit-0805 C1: engine/tax.py:512 clamps taxable_ordinary at 0 using
    only ordinary income, then stacks the FULL LTCG amount on top. Per IRC
    §1(h)'s Qualified Dividends and Capital Gain Tax Worksheet, the standard
    deduction applies against TOTAL income (ordinary + LTCG) before the
    ordinary/preferential split -- unused deduction capacity must offset LTCG.
    """

    def test_unused_deduction_offsets_ltcg_zeroing_ltcg_tax(self) -> None:
        """MFJ, wages=$10K, ltcg=$110K.

        Unused standard deduction = 32_200 - 10_000 = 22_200.
        Correct ltcg_taxable (after full deduction absorption) = 110_000 - 22_200
        = 87_800, entirely below the 98_900 0%-rate threshold -> correct
        ltcg_tax = 0.00.
        Current (defective) code stacks the FULL 110_000 on top of a
        zero-clamped ordinary floor -> incorrectly returns 1_665.00.
        """
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=10_000, ltcg_ytd=110_000)
        result = estimate_ytd_federal_tax(ytd, _hh_mfj())
        assert result.ltcg_tax == approx(0.00), (
            f"Expected ltcg_tax=0.00 (unused std deduction of 22200 offsets LTCG, "
            f"leaving 87800 entirely below the 98900 0%-rate threshold), got "
            f"{result.ltcg_tax:.2f} -- the deduction is being clamped away at the "
            f"ordinary-income floor instead of offsetting LTCG (engine/tax.py:512)"
        )
