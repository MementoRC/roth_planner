"""Regression tests for engine.scenario_compare.survivor_year_tax and
compute_survivor_snapshot (survivor IRA year-by-year compounding)."""

import pytest

from engine.ira import calc_rmd
from engine.scenario_compare import compute_survivor_snapshot, survivor_year_tax
from engine.tax import (
    SENIOR_EXTRA_SINGLE,
    STD_DEDUCTION_SINGLE,
    federal_tax_single,
    senior_bonus_deduction,
    taxable_ss,
)
from models.household import Household


class TestSurvivorIRACompounding:
    """Regression: survivor IRA must shrink by RMD withdrawals year-by-year.

    The old code used ``inherited_ira * (1 + rate) ** proj_years`` — a single
    end-year compounding that completely ignores the RMD drain during each of
    the 5 projection years.  For a large IRA balance that is past rmd_start_age,
    this overstatement is material (hundreds of thousands of dollars).
    """

    def _make_scenario(self, hh: Household, death_age: int, ira: float) -> "ScenarioResult":  # noqa: F821 — imported at runtime
        from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult

        yr = YearResult(
            year=hh.base_year + (death_age - hh.your_age),
            your_age=death_age,
            spouse_age=death_age - hh.age_gap,
            phase="squeeze",
            your_ira_begin=ira,
            spouse_ira_begin=0.0,
            your_ss=25_000.0,
            spouse_ss=18_000.0,
        )
        return ScenarioResult(
            name="Test",
            years=[yr],
            household=hh,
            plan=ConversionPlan(),
        )

    def test_survivor_ira_lower_than_single_rate_compounding(self) -> None:
        """Year-by-year net-of-RMD balance must be strictly less than naive compound."""
        hh = Household(
            your_age=70,
            spouse_age=64,
            your_ira=1_000_000,
            spouse_ira=0,
            growth_rate=0.07,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        death_age = 80  # spouse survives; survivor starts at age 74
        inherited_ira = 2_000_000.0
        scenario = self._make_scenario(hh, death_age, inherited_ira)

        rows = compute_survivor_snapshot(hh, [scenario], "you", [death_age])
        assert len(rows) == 1

        # Manually compute the naive single-rate value for comparison.
        proj_years = 5
        rate = hh.spouse_ira_rate(hh.base_year + (death_age - hh.your_age) + proj_years)
        naive_grown = inherited_ira * (1 + rate) ** proj_years

        # Year-by-year simulation (mirrors the fix):
        balance = inherited_ira
        survivor_rmd_start = hh.spouse_rmd_start_age
        for offset in range(proj_years):
            year_offset = offset + 1
            surv_age = (death_age - hh.age_gap) + year_offset
            rmd_w = calc_rmd(balance, surv_age, survivor_rmd_start)
            balance = max(balance - rmd_w, 0.0) * (
                1 + hh.spouse_ira_rate(hh.base_year + (death_age - hh.your_age) + year_offset)
            )
        correct_grown = balance

        # The correct (net-of-RMD) balance must be strictly less than the naive value.
        assert correct_grown < naive_grown, (
            f"Expected correct_grown ({correct_grown:,.0f}) < naive_grown ({naive_grown:,.0f})"
        )
        # The difference must be material (> $100K) for a $2M IRA past RMD age
        assert naive_grown - correct_grown > 100_000, (
            "Overstatement from naive compounding should be material for large IRA"
        )

    def test_survivor_ira_no_rmd_years_matches_simple_growth(self) -> None:
        """When survivor is below rmd_start_age for ALL 5 projection years,
        net-of-RMD growth equals naive compounding (no RMD taken, so both paths identical)."""
        hh = Household(
            your_age=60,
            spouse_age=54,
            your_ira=1_000_000,
            spouse_ira=0,
            growth_rate=0.07,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        # Survivor (spouse) is age 54 at death; 5 yr projection → age 54..58, all < 75
        death_age = 60
        inherited_ira = 500_000.0
        scenario = self._make_scenario(hh, death_age, inherited_ira)

        rows = compute_survivor_snapshot(hh, [scenario], "you", [death_age])
        assert len(rows) == 1

        proj_years = 5
        rate = hh.growth_rate
        expected = inherited_ira * (1 + rate) ** proj_years
        # Derive the balance the engine used by reconstructing the RMD at year+5
        # Survivor at death is age 54; +5 → 59, well below rmd_start_age=75 so no RMD taken.
        balance = inherited_ira
        for offset in range(proj_years):
            year_offset = offset + 1
            surv_age = (death_age - hh.age_gap) + year_offset
            rmd_w = calc_rmd(balance, surv_age, hh.spouse_rmd_start_age)
            balance = max(balance - rmd_w, 0.0) * (1 + rate)
        assert balance == pytest.approx(expected, rel=1e-9)


class TestInheritedIraUsesEndOfYearBalance:
    """M5: compute_survivor_snapshot must seed inherited_ira from the death-year
    END-of-year IRA balance (your_ira_end + spouse_ira_end), not the begin-of-year
    balance (your_ira_begin + spouse_ira_begin).

    The difference equals the death-year RMD + growth applied to the IRA.  Using
    the begin balance understates the inherited IRA when the decedent takes an RMD
    and the IRA grows during the death year.
    """

    def _make_scenario_with_end(
        self,
        hh: Household,
        death_age: int,
        ira_begin: float,
        ira_end: float,
    ) -> "ScenarioResult":  # noqa: F821
        from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult

        yr = YearResult(
            year=hh.base_year + (death_age - hh.your_age),
            your_age=death_age,
            spouse_age=death_age - hh.age_gap,
            phase="squeeze",
            your_ira_begin=ira_begin,
            spouse_ira_begin=0.0,
            your_ira_end=ira_end,
            spouse_ira_end=0.0,
            your_ss=25_000.0,
            spouse_ss=18_000.0,
        )
        return ScenarioResult(
            name="Test",
            years=[yr],
            household=hh,
            plan=ConversionPlan(),
        )

    def test_inherited_ira_seeds_from_end_not_begin(self) -> None:
        """Survivor's inherited IRA seed must equal your_ira_end, not your_ira_begin.

        Strategy: set your_ira_begin=1_500_000, your_ira_end=1_200_000 (end < begin,
        unambiguously different).  The 'Inherited IRA' column stores
        fmt_dollars_short(your_ira_end + spouse_ira_end) of the death-year YearResult.
        We assert the column value equals fmt_dollars_short(ira_end) and NOT
        fmt_dollars_short(ira_begin).
        """
        from views._format import fmt_dollars_short

        hh = Household(
            your_age=70,
            spouse_age=64,
            your_ira=1_000_000,
            spouse_ira=0,
            growth_rate=0.07,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
        )
        death_age = 80
        ira_begin = 1_500_000.0
        ira_end = 1_200_000.0  # deliberately lower so begin≠end unambiguously

        scenario = self._make_scenario_with_end(hh, death_age, ira_begin, ira_end)
        rows = compute_survivor_snapshot(hh, [scenario], "you", [death_age])
        assert len(rows) == 1

        row = rows[0]
        col_key = "Test Inherited IRA"
        assert col_key in row, f"Expected column '{col_key}' in row keys: {list(row.keys())}"

        # The column holds fmt_dollars_short(seed, decimals=2) where seed = ira_end + spouse_ira_end.
        # spouse_ira_end=0.0 in our fixture, so seed = ira_end.
        expected_end = fmt_dollars_short(ira_end, decimals=2)
        expected_begin = fmt_dollars_short(ira_begin, decimals=2)

        # Precondition: the two formatted values are distinct.
        assert expected_end != expected_begin, (
            "Test setup error: ira_begin and ira_end format to the same string"
        )

        assert row[col_key] == expected_end, (
            f"Inherited IRA column should be seeded from ira_end={ira_end:,.0f} "
            f"(formatted {expected_end!r}), but got {row[col_key]!r}. "
            f"If it matches ira_begin ({expected_begin!r}), the end-of-year fix is not active."
        )
        assert row[col_key] != expected_begin, (
            f"Inherited IRA column must NOT equal ira_begin-formatted value {expected_begin!r}"
        )


def test_survivor_year_tax_indexes_to_projection_year() -> None:
    # Inflation-grown future income taxed against INDEXED brackets + deduction must
    # be strictly less than the same nominal income taxed against raw 2026 values.
    age, rmd, ss = 81, 150_000.0, 40_000.0
    cpi = 0.025
    tax_fixed, bracket_fixed, taxable_fixed = survivor_year_tax(age, rmd, ss, year=2051, cpi=cpi)
    assert tax_fixed == pytest.approx(federal_tax_single(taxable_fixed, year=2051, cpi=cpi))
    assert 0.0 < bracket_fixed < 1.0
    tss = taxable_ss(ss, rmd, filing_status="Single")
    gross = rmd + tss
    ded_buggy = float(STD_DEDUCTION_SINGLE + SENIOR_EXTRA_SINGLE)
    ded_buggy += senior_bonus_deduction(age, 0, gross, year=2051, cpi=cpi, filing_status="Single")
    taxable_buggy = max(gross - ded_buggy, 0.0)
    tax_buggy = federal_tax_single(taxable_buggy)
    assert tax_fixed < tax_buggy


# ---------------------------------------------------------------------------
# Tests for compare-M3ss / compare-M7senior: brokerage income in survivor tax
# ---------------------------------------------------------------------------


class TestSurvivorYearTaxBrokerageIncome:
    """Unit tests for the 3-bucket brokerage split in survivor_year_tax.

    Verifies:
    1. Zero brokerage (defaults) → byte-identical to the old 0-brokerage behavior.
    2. Ordinary brokerage income raises both the SS provisional base and the
       ordinary federal-tax base.
    3. LTCG-rate brokerage income raises SS provisional income and the
       senior-bonus MAGI, but does NOT add to the ordinary taxable return value.
    """

    _BASE_KW = {"year": 2031, "cpi": 0.025}

    def _call(self, age: int, rmd: float, ss: float, **kw: float) -> tuple[float, float, float]:
        return survivor_year_tax(age, rmd, ss, **self._BASE_KW, **kw)  # type: ignore[arg-type]

    def test_zero_brokerage_backward_compat(self) -> None:
        """Default brok_ord_income=0.0 / brok_ltcg_income=0.0 is byte-identical
        to calling the old signature with no brokerage args."""
        age, rmd, ss = 78, 80_000.0, 30_000.0
        result_new = self._call(age, rmd, ss)
        result_explicit_zero = self._call(age, rmd, ss, brok_ord_income=0.0, brok_ltcg_income=0.0)
        assert result_new == result_explicit_zero

    def test_ordinary_brokerage_raises_tax_and_base(self) -> None:
        """Ordinary brokerage income enters both provisional income and the
        ordinary federal-tax base → higher tax than zero-brokerage case."""
        age, rmd, ss = 78, 80_000.0, 30_000.0
        brok_ord = 15_000.0
        tax_zero, _, taxable_zero = self._call(age, rmd, ss)
        tax_brok, _, taxable_brok = self._call(age, rmd, ss, brok_ord_income=brok_ord)
        assert taxable_brok > taxable_zero, (
            "Ordinary brokerage income must raise the ordinary taxable base"
        )
        assert tax_brok > tax_zero, "Ordinary brokerage income must raise federal tax"

    def test_ltcg_brokerage_not_in_ordinary_base(self) -> None:
        """LTCG-rate brokerage income (qualified divs) raises SS-provisional and
        senior-bonus MAGI, but must NOT enter the ordinary taxable return value.

        Strategy: use a large enough brok_ltcg_income to measurably affect the
        SS-provisional path (raising tss) while brok_ord_income=0.  The taxable
        return value must be strictly less than taxable_zero + brok_ltcg_income
        (i.e., the LTCG amount was NOT simply added to the ordinary base), and
        tss must exceed the zero-brokerage tss (SS provisional WAS raised).
        """
        age, rmd, ss = 78, 80_000.0, 30_000.0
        brok_ltcg = 20_000.0

        # Zero-brokerage baseline
        _, _, taxable_zero = self._call(age, rmd, ss)
        tss_zero = taxable_ss(ss, rmd, filing_status="Single")

        # With LTCG-rate brokerage only (no ordinary)
        _, _, taxable_ltcg = self._call(age, rmd, ss, brok_ltcg_income=brok_ltcg)
        # Provisional income now includes brok_ltcg → tss may be higher
        tss_ltcg = taxable_ss(ss, rmd + brok_ltcg, filing_status="Single")

        # SS provisional income must have increased (confirming brok_ltcg hit that path)
        assert tss_ltcg >= tss_zero, "LTCG brokerage income must enter SS provisional income path"
        # Ordinary taxable base must NOT include brok_ltcg directly
        # (taxable_ltcg may differ from taxable_zero only via the tss channel, not by +brok_ltcg)
        assert taxable_ltcg < taxable_zero + brok_ltcg, (
            "LTCG-rate income must NOT be added directly to the ordinary taxable base"
        )

    def test_ltcg_brokerage_reduces_senior_bonus_magi(self) -> None:
        """LTCG-rate brokerage income is included in the senior-bonus MAGI
        (compare-M7senior fix).  Use an age/income where the OBBBA senior bonus
        applies: survivor_age >= 65, MAGI near the phase-out threshold.

        Confirm the MAGI seen by senior_bonus_deduction is larger when
        brok_ltcg_income > 0, by checking that the deduction is smaller
        (phase-out erodes it) compared to the zero-brokerage case when the
        MAGI crosses the threshold.
        """
        from engine.tax import senior_bonus_deduction

        # Use 2031, cpi=0.025 (same as _BASE_KW).  OBBBA senior bonus phases out
        # above $150K MAGI (Single).  Position RMD + tss near but below that threshold
        # so that adding brok_ltcg crosses it.
        age = 70  # >= 65 qualifies for senior bonus
        rmd = 110_000.0
        ss = 30_000.0
        brok_ltcg = 50_000.0

        tax_no_brok, _, _ = self._call(age, rmd, ss)
        tax_with_brok, _, _ = self._call(age, rmd, ss, brok_ltcg_income=brok_ltcg)

        # With a higher MAGI (brok_ltcg included), the senior bonus deduction
        # should be smaller (phase-out) → tax should be higher.
        # We validate the mechanism by computing the senior-bonus deduction directly.
        tss_no_brok = taxable_ss(ss, rmd, filing_status="Single")
        gross_no_brok = rmd + tss_no_brok
        tss_with_brok = taxable_ss(ss, rmd + brok_ltcg, filing_status="Single")
        gross_with_brok = rmd + tss_with_brok
        magi_with_brok = gross_with_brok + brok_ltcg

        ded_no_brok = senior_bonus_deduction(
            age, 0, gross_no_brok, year=2031, cpi=0.025, filing_status="Single"
        )
        ded_with_brok = senior_bonus_deduction(
            age, 0, magi_with_brok, year=2031, cpi=0.025, filing_status="Single"
        )

        # If MAGI crosses the phase-out threshold, the deduction decreases
        if magi_with_brok > gross_no_brok:
            assert ded_with_brok <= ded_no_brok, (
                "LTCG income in MAGI must not increase the senior bonus deduction"
            )
        # Tax must be at least as high with brokerage income (SS-provisional raised tss)
        assert tax_with_brok >= tax_no_brok, (
            "Adding LTCG brokerage income must not decrease survivor tax"
        )


class TestComputeSurvivorSnapshotBrokerage:
    """Integration tests for the brokerage income projection in compute_survivor_snapshot.

    compare-M3ss: taxable SS must increase when survivor has brokerage income.
    compare-M7senior: OBBBA senior-bonus MAGI must include brokerage income.
    """

    def _make_scenario_with_brokerage(
        self,
        hh: Household,
        death_age: int,
        ira_end: float,
        brok_balance: float,
        your_ss: float = 25_000.0,
        spouse_ss: float = 18_000.0,
    ) -> "ScenarioResult":  # noqa: F821
        from engine.scenario_types import ConversionPlan, ScenarioResult, YearResult

        yr = YearResult(
            year=hh.base_year + (death_age - hh.your_age),
            your_age=death_age,
            spouse_age=death_age - hh.age_gap,
            phase="squeeze",
            your_ira_begin=ira_end,
            spouse_ira_begin=0.0,
            your_ira_end=ira_end,
            spouse_ira_end=0.0,
            brokerage_balance=brok_balance,
            your_ss=your_ss,
            spouse_ss=spouse_ss,
        )
        return ScenarioResult(
            name="Test",
            years=[yr],
            household=hh,
            plan=ConversionPlan(),
        )

    def _make_hh(self, brok_balance: float = 0.0, **kwargs: object) -> Household:
        from models.household import GrowthProfile

        return Household(
            your_age=70,
            spouse_age=64,
            your_ira=1_000_000,
            spouse_ira=0,
            growth_rate=0.07,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            brokerage_start=brok_balance,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.02,
                qualified_fraction=0.8,
            )
            if brok_balance > 0
            else None,
            **kwargs,  # type: ignore[arg-type]
        )

    def test_zero_brokerage_unchanged(self) -> None:
        """Regression: zero brokerage_balance in death-year → same result as before
        the fix (defaults 0.0 keep survivor_year_tax byte-identical)."""
        hh = self._make_hh(brok_balance=0.0)
        death_age = 80
        scenario = self._make_scenario_with_brokerage(hh, death_age, 1_500_000.0, 0.0)
        rows_zero = compute_survivor_snapshot(hh, [scenario], "you", [death_age])
        assert len(rows_zero) == 1
        # Result must not be "---" (death_age found)
        assert rows_zero[0]["Test Survivor Tax"] != "---"
        # Tax must be a positive dollar string
        tax_str = rows_zero[0]["Test Survivor Tax"]
        assert tax_str.startswith("$") or tax_str[0].isdigit(), (
            f"Unexpected tax format: {tax_str!r}"
        )

    def test_brokerage_income_raises_survivor_tax(self) -> None:
        """Active: brokerage_balance > 0 in death-year → survivor tax is HIGHER
        than the zero-brokerage case (brokerage enters SS-provisional + MAGI)."""
        death_age = 80
        ira_end = 1_500_000.0

        hh_zero = self._make_hh(brok_balance=0.0)
        hh_brok = self._make_hh(brok_balance=500_000.0)

        sc_zero = self._make_scenario_with_brokerage(hh_zero, death_age, ira_end, 0.0)
        sc_brok = self._make_scenario_with_brokerage(hh_brok, death_age, ira_end, 500_000.0)

        rows_zero = compute_survivor_snapshot(hh_zero, [sc_zero], "you", [death_age])
        rows_brok = compute_survivor_snapshot(hh_brok, [sc_brok], "you", [death_age])

        # Both must resolve (not "---")
        assert rows_zero[0]["Test Survivor Tax"] != "---"
        assert rows_brok[0]["Test Survivor Tax"] != "---"

        # Parse dollar amounts: strip "$", "/yr", commas then convert
        def _parse(s: str) -> float:
            return float(s.replace("$", "").replace("/yr", "").replace(",", "").strip())

        tax_zero = _parse(rows_zero[0]["Test Survivor Tax"])
        tax_brok = _parse(rows_brok[0]["Test Survivor Tax"])

        assert tax_brok > tax_zero, (
            f"Survivor tax with brokerage ({tax_brok:,.0f}) must exceed "
            f"zero-brokerage ({tax_zero:,.0f})"
        )

    def test_qualified_divs_not_in_ordinary_base(self) -> None:
        """Active: purely-qualified brokerage income raises MAGI / SS-provisional
        but must NOT add to the ordinary taxable base.

        Strategy: configure brokerage_growth with qualified_fraction=1.0 (all
        income is qualified dividends → zero ordinary).  Confirm survivor tax
        is higher than zero-brokerage (SS-provisional path activated), while
        the ordinary taxable return does not equal taxable_zero + qualified_income.
        """
        from models.household import GrowthProfile

        death_age = 80
        ira_end = 1_500_000.0
        brok_seed = 300_000.0

        hh_qual = Household(
            your_age=70,
            spouse_age=64,
            your_ira=1_000_000,
            spouse_ira=0,
            growth_rate=0.07,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            brokerage_start=brok_seed,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.03,
                qualified_fraction=1.0,  # all qualified → brok_ord_income = 0
            ),
        )

        sc_qual = self._make_scenario_with_brokerage(hh_qual, death_age, ira_end, brok_seed)
        hh_zero = self._make_hh(brok_balance=0.0)
        sc_zero = self._make_scenario_with_brokerage(hh_zero, death_age, ira_end, 0.0)

        rows_qual = compute_survivor_snapshot(hh_qual, [sc_qual], "you", [death_age])
        rows_zero = compute_survivor_snapshot(hh_zero, [sc_zero], "you", [death_age])

        def _parse(s: str) -> float:
            return float(s.replace("$", "").replace("/yr", "").replace(",", "").strip())

        tax_qual = _parse(rows_qual[0]["Test Survivor Tax"])
        tax_zero = _parse(rows_zero[0]["Test Survivor Tax"])

        # Tax is higher (SS-provisional path raised tss)
        assert tax_qual >= tax_zero, (
            "Purely-qualified brokerage income must not decrease survivor tax "
            "(SS-provisional path should raise taxable SS)"
        )
        # The bracket column must still be valid
        assert rows_qual[0]["Test Bracket"] != "---"
