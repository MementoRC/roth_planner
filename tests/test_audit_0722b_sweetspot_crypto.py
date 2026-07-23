"""Regression test for audit finding (2026-07-22, cross-engine drift, MEDIUM).

engine/sweet_spot_compute.py `estimate_ltcg_eligible()` omitted
`ytd.crypto_ltcg_ytd` from the preferential-rate LTCG stacking base, while the
forward engine (engine/scenario.py `_ytd_ltcg_total`, audit-0720 F3) DOES
include it. crypto_ltcg_ytd is genuine preferential-rate LTCG (0/15/20% stack,
not ordinary brackets — see models/ytd_income.py) and belongs in both engines'
stack-walk input so the two engines agree.
"""

import pytest

from engine.sweet_spot_compute import (
    all_in_at_conversion,
    base_income_for_year,
    estimate_ltcg_eligible,
)
from models.household import Household
from models.ytd_income import YTDSnapshot


class TestEstimateLtcgEligibleIncludesCryptoLtcg:
    """estimate_ltcg_eligible must fold crypto_ltcg_ytd into the LTCG stack base,
    mirroring engine.scenario._ytd_ltcg_total (scenario.py:606-607)."""

    def test_crypto_ltcg_included_in_eligible(self) -> None:
        hh = Household(
            your_age=61,
            spouse_age=59,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
        )
        ytd = YTDSnapshot(
            tax_year=2026,
            crypto_ltcg_ytd=60_000.0,
            ltcg_ytd=0.0,
            qualified_dividends_ytd=0.0,
        )

        eligible = estimate_ltcg_eligible(hh, 2026, ytd=ytd)

        assert eligible == pytest.approx(60_000.0)

    def test_crypto_ltcg_stacks_with_ltcg_and_qual_div(self) -> None:
        hh = Household(
            your_age=61,
            spouse_age=59,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
        )
        ytd = YTDSnapshot(
            tax_year=2026,
            crypto_ltcg_ytd=10_000.0,
            ltcg_ytd=5_000.0,
            qualified_dividends_ytd=2_000.0,
        )

        eligible = estimate_ltcg_eligible(hh, 2026, ytd=ytd)

        assert eligible == pytest.approx(17_000.0)


class TestSweetSpotLtcgStackingMarginalCostNonzero:
    """Integration: with crypto_ltcg_ytd present, a conversion that crosses the
    MFJ 0%->15% LTCG breakpoint ($98,900 for 2026) must show a nonzero
    ltcg_delta in the sweet-spot all-in cost, matching scenario.py's forward
    engine rather than silently reporting $0."""

    def test_ltcg_delta_nonzero_when_crossing_mfj_breakpoint(self) -> None:
        # Given: ages under 65 (no senior bonus, no SS claimed at 70/70 default,
        # no RMDs pre-73) and $0 brokerage/option income, so the only income is
        # the $60K crypto LTCG YTD -- base taxable income (no conversion) is $0,
        # entirely below the $98,900 MFJ 0%->15% breakpoint.
        hh = Household(
            your_age=61,
            spouse_age=59,
            base_year=2026,
            your_ira=0.0,
            spouse_ira=0.0,
        )
        ytd = YTDSnapshot(tax_year=2026, crypto_ltcg_ytd=60_000.0)

        base = base_income_for_year(hh, 2026, ytd=ytd)
        ltcg_eligible = estimate_ltcg_eligible(hh, 2026, ytd=ytd)
        assert base.base_gross == pytest.approx(0.0)

        # A $132,200 conversion lifts taxable income to exactly $100,000
        # (std deduction $32,200), $1,100 above the $98,900 breakpoint --
        # pushing the entire $60K crypto-LTCG stack into the 15% band.
        result = all_in_at_conversion(hh, base, 132_200.0, net_inv_income=0.0, ltcg_eligible=ltcg_eligible)

        assert result.taxable_inc == pytest.approx(100_000.0)
        # Without the fix, ltcg_eligible (and therefore ltcg_delta) is 0.0.
        assert result.ltcg_delta == pytest.approx(9_000.0, abs=1.0)
        assert result.ltcg_delta > 0
