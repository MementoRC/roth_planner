"""TDD regression tests for audit-0706 wave-2 LTCG findings.

Findings covered
----------------
1. scenario-autofill-2 (BEHAVIORAL): survivor brokerage LTCG stack baseline must use
   RMD-based ordinary income, not zero — understates LTCG tax when RMD pushes gains
   into the 15% band.
2. scenario-autofill-5 (clarity): LTCG_RATES_SINGLE constant is exported from tax.py
   and used in scenario_compare.py instead of LTCG_RATES_MFJ index literals.
3. tax-core-1 (cosmetic/robustness): estimate_ytd_federal_tax uses LTCG_RATES_SINGLE
   for Single filers (rate identical today but semantically correct).
4. tax-core-4 (BEHAVIORAL): deductions() with filing_status='Single' and no explicit
   std_ded must auto-select STD_DEDUCTION_SINGLE, not STD_DEDUCTION_MFJ.
"""

from __future__ import annotations

import pytest

from engine.tax import (
    LTCG_RATES_MFJ,
    LTCG_RATES_SINGLE,
    STD_DEDUCTION_MFJ,
    STD_DEDUCTION_SINGLE,
    deductions,
)

# ---------------------------------------------------------------------------
# Finding 2: LTCG_RATES_SINGLE exported from engine.tax
# ---------------------------------------------------------------------------


class TestLTCGRatesSingleExists:
    """scenario-autofill-5: LTCG_RATES_SINGLE must exist in engine.tax."""

    def test_ltcg_rates_single_is_tuple(self) -> None:
        assert isinstance(LTCG_RATES_SINGLE, tuple)

    def test_ltcg_rates_single_has_three_tiers(self) -> None:
        assert len(LTCG_RATES_SINGLE) == 3

    def test_ltcg_rates_single_values(self) -> None:
        assert LTCG_RATES_SINGLE == (0.0, 0.15, 0.20)

    def test_ltcg_rates_single_same_as_mfj(self) -> None:
        """Rates are currently identical — both are (0.0, 0.15, 0.20) in 2026."""
        assert LTCG_RATES_SINGLE == LTCG_RATES_MFJ


# ---------------------------------------------------------------------------
# Finding 4: deductions() auto-selects std_ded from filing_status
# ---------------------------------------------------------------------------


class TestDeductionsFilingStatusDefault:
    """tax-core-4: deductions() must infer std_ded / senior_extra from filing_status
    when the caller omits them."""

    def test_single_no_explicit_std_ded_uses_single_amount(self) -> None:
        """Core regression: deductions(filing_status='Single') without explicit
        std_ded must return STD_DEDUCTION_SINGLE, not STD_DEDUCTION_MFJ."""
        result = deductions(your_age=50, spouse_age=0, filing_status="Single", year=2026)
        assert result == pytest.approx(STD_DEDUCTION_SINGLE), (
            f"Expected STD_DEDUCTION_SINGLE={STD_DEDUCTION_SINGLE}, got {result}; "
            f"the MFJ default ({STD_DEDUCTION_MFJ}) must not be used for Single filers"
        )

    def test_mfj_no_explicit_std_ded_uses_mfj_amount(self) -> None:
        """Non-regression: deductions() with no filing_status (defaults MFJ) must
        still return STD_DEDUCTION_MFJ."""
        result = deductions(your_age=50, spouse_age=48, year=2026)
        assert result == pytest.approx(STD_DEDUCTION_MFJ)

    def test_single_over_65_no_explicit_args_gets_senior_extra(self) -> None:
        """Single filer 65+ with no explicit std_ded/senior_extra gets both the
        correct base deduction AND the senior extra."""
        from engine.tax import SENIOR_EXTRA_SINGLE

        result = deductions(your_age=70, spouse_age=0, filing_status="Single", year=2026)
        assert result == pytest.approx(STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE)

    def test_explicit_override_still_works_single(self) -> None:
        """Callers that pass explicit std_ded must still get that value (no regression)."""
        custom_ded = 99_000.0
        result = deductions(
            your_age=50,
            spouse_age=0,
            std_ded=custom_ded,
            senior_extra=0.0,
            filing_status="Single",
            year=2026,
        )
        assert result == pytest.approx(custom_ded)

    def test_explicit_override_still_works_mfj(self) -> None:
        """Callers that pass explicit std_ded for MFJ must still get that value."""
        from engine.tax import SENIOR_EXTRA_MFJ

        result = deductions(
            your_age=65,
            spouse_age=60,
            std_ded=STD_DEDUCTION_MFJ,
            senior_extra=SENIOR_EXTRA_MFJ,
            year=2026,
        )
        assert result == pytest.approx(STD_DEDUCTION_MFJ + SENIOR_EXTRA_MFJ)


# ---------------------------------------------------------------------------
# Finding 1: survivor brokerage LTCG stack baseline uses RMD income
# ---------------------------------------------------------------------------


class TestSurvivorBrokerageLTCGStackBaseline:
    """scenario-autofill-2: the brokerage LTCG tax in compute_survivor_snapshot
    must stack on RMD-derived ordinary income, not zero.

    When RMD income is large enough to push realized brokerage gains into the 15%
    LTCG band, the computed tax must be HIGHER than under the zero-baseline assumption,
    and the projected brokerage balance must be LOWER.
    """

    # Fixed synthetic RMD fed to every projection offset (calc_rmd is stubbed in
    # the snapshot module, so the expected balances below need no IRA replay).
    _RMD = 100_000.0

    def _final_year_brok_ltcg_income(
        self, monkeypatch: pytest.MonkeyPatch, rmd: float = _RMD
    ) -> tuple[float, float]:
        """Run the REAL compute_survivor_snapshot and return (the realized
        brokerage LTCG income it handed to survivor_year_tax in the projection
        year, the closing balance the test expects from the year-0 balance)."""
        import engine.scenario_compare as sc
        from engine.scenario import run_no_conversion
        from engine.scenario_compare import compute_survivor_snapshot
        from models.household import Household

        hh = Household(
            base_year=2026,
            your_age=72,
            spouse_age=78,
            your_ira=2_000_000.0,
            spouse_ira=0.0,
            growth_rate=0.05,
            cpi_assumption=0.0,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            brokerage_start=500_000.0,
            brokerage_start_basis=500_000.0,
            brok_turnover=0.10,
            grants=[],
        )
        scenario = run_no_conversion(hh, end_age=95)
        monkeypatch.setattr(sc, "calc_rmd", lambda *_a, **_k: rmd)

        seen: list[float] = []
        original = sc.survivor_year_tax

        def _spy(survivor_age, rmd, survivor_ss, **kwargs):
            seen.append(kwargs["brok_ltcg_income"])
            return original(survivor_age, rmd, survivor_ss, **kwargs)

        monkeypatch.setattr(sc, "survivor_year_tax", _spy)
        compute_survivor_snapshot(hh, [scenario], "you", [72])
        assert len(seen) == 1

        # Independent replay of the 5-year brokerage loop on Single thresholds.
        from engine.tax import LTCG_THRESHOLDS_SINGLE, deductions

        year0 = next(y for y in scenario.years if y.your_age == 72)
        balance = year0.brokerage_balance_end
        appr = hh.brokerage_rate(2027)  # constant growth, no GrowthProfile
        for offset in range(1, 6):
            realized = balance * appr * hh.brok_turnover
            ded = deductions(78 + offset, 0, filing_status="Single", year=2026 + offset, cpi=0.0)
            start = max(rmd - ded, 0.0)
            end = start + realized
            thr = LTCG_THRESHOLDS_SINGLE
            tax = (
                max(0.0, min(end, thr[1]) - max(start, thr[0])) * LTCG_RATES_SINGLE[1]
                + max(0.0, end - max(start, thr[1])) * LTCG_RATES_SINGLE[2]
            )
            balance = balance + balance * appr - tax
        return seen[0], balance * appr * hh.brok_turnover

    def test_rmd_stacking_increases_ltcg_tax(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With RMD income stacking under realized gains, the survivor's brokerage
        balance (hence the projection-year realized income) must be LOWER than a
        zero-baseline run would leave it: the RMD-stacked gains pay 15% tax.

        Zero-baseline expectation: gains of ~12.5K/yr sit below the ~49K 0% ceiling,
        so no tax is ever paid and the balance only compounds.
        """
        captured, expected = self._final_year_brok_ltcg_income(monkeypatch, rmd=self._RMD)
        assert captured == pytest.approx(expected, rel=1e-9)

        zero_baseline, _ = self._final_year_brok_ltcg_income(monkeypatch, rmd=0.0)
        assert captured < zero_baseline

    def test_compute_survivor_snapshot_brokerage_tax_uses_rmd_baseline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Integration: a large inherited IRA's RMD pushes realized gains above the
        LTCG 0% ceiling, and the tax actually reduces the projected balance that
        compute_survivor_snapshot feeds to survivor_year_tax."""
        captured, expected = self._final_year_brok_ltcg_income(monkeypatch)
        assert captured == pytest.approx(expected, rel=1e-9)
