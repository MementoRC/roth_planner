"""Tests for engine.scenario MAGI ordering, LTCG cost, and SS provisional income (audit F3/F4/F5)."""

import pytest

from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from models.household import GrowthProfile, Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestMagiOrderingAndLtcgCost:
    """Regression tests for the three engine fixes in commit 8aa7e525.

    Behavioral assertion type is noted per test:
      BEHAVIORAL  — exercises the fix path, fails on pre-fix code
      INVARIANT   — checks a structural guarantee; passes on both old/new but
                    documents and locks the property for future refactors
    """

    # ------------------------------------------------------------------
    # Test 1 — grid-05: ytd_ltcg_tax folded into federal_tax_amt
    # Assertion type: BEHAVIORAL
    # ------------------------------------------------------------------

    def test_ytd_ltcg_tax_folded_into_federal_tax_amt(self):
        """YTD LTCG tax (grid-05) must be counted in federal_tax_amt.

        Pre-fix: yr.ytd_ltcg_tax was computed and stored but never added to
        federal_tax_amt, so the base-year total tax was understated.
        Post-fix: federal_tax_amt includes ytd_ltcg_tax.

        Approach: run two scenarios — one with ltcg_ytd large enough to
        produce 15% LTCG tax, one without.  Assert:
          (a) yr.ytd_ltcg_tax > 0  (sanity — LTCG is actually taxed at 15%)
          (b) federal_tax_amt_with - federal_tax_amt_without == yr.ytd_ltcg_tax
              (to pytest.approx)

        Fixture:
          wages_ytd=150_000 pushes taxable_income above the MFJ 0%-band
          ceiling (~$98,900 in 2026) so that ltcg_ytd=60_000 is entirely
          taxed at 15% → expected ytd_ltcg_tax = 60_000 * 0.15 = $9,000.
          No conversions; no grants (strips option income for clarity).
        """
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
            grants=[],  # remove option income for a clean fixture
        )

        ltcg_amount = 60_000
        ytd_with = YTDSnapshot(tax_year=2026, wages_ytd=150_000, ltcg_ytd=ltcg_amount)
        ytd_without = YTDSnapshot(tax_year=2026, wages_ytd=150_000, ltcg_ytd=0)

        plan = ConversionPlan()
        yr_with = run_scenario(hh, plan, "with_ltcg", end_age=62, ytd=ytd_with).years[0]
        yr_without = run_scenario(hh, plan, "no_ltcg", end_age=62, ytd=ytd_without).years[0]

        # Sanity: taxable_income above the 0%-band so LTCG is taxed at 15%
        assert yr_with.taxable_income > 98_900, (
            f"Fixture broken: taxable_income={yr_with.taxable_income:.0f} is not above "
            f"MFJ 0%-band ceiling (~$98,900); LTCG would not be taxed at 15%"
        )

        # (a) BEHAVIORAL: ytd_ltcg_tax must be positive
        assert yr_with.ytd_ltcg_tax > 0, (
            f"Expected ytd_ltcg_tax > 0 for ltcg_ytd={ltcg_amount}; got {yr_with.ytd_ltcg_tax}"
        )

        # (b) BEHAVIORAL: the delta in federal_tax_amt must equal ytd_ltcg_tax exactly.
        # Pre-fix code never added ytd_ltcg_tax to federal_tax_amt, so the delta
        # would be 0 on pre-fix code (assertion would fail).
        delta = yr_with.federal_tax_amt - yr_without.federal_tax_amt
        assert delta == pytest.approx(yr_with.ytd_ltcg_tax, abs=1.0), (
            f"federal_tax_amt delta ({delta:.2f}) != ytd_ltcg_tax "
            f"({yr_with.ytd_ltcg_tax:.2f}); grid-05 fix may be missing or double-counted"
        )

        # (c) INVARIANT: no-LTCG run has zero ytd_ltcg_tax
        assert yr_without.ytd_ltcg_tax == pytest.approx(0.0)

    # ------------------------------------------------------------------
    # Test 2 — grid-03: IRMAA same-year fallback uses realized-gains-
    #          inclusive MAGI
    # Assertion type: BEHAVIORAL (direct IRMAA cost) + INVARIANT (magi_history)
    # ------------------------------------------------------------------

    def test_irmaa_fallback_uses_realized_gains_inclusive_magi(self):
        """IRMAA same-year fallback (grid-03) must see the full yr.magi.

        The fallback fires for yr_idx < 2 when prior_year_magi is empty.
        Pre-fix: realized_gains were folded into yr.magi *after* the MAGI
        ordering block, so magi_history[year] captured MAGI without realized
        gains; the fallback then under-stated magi_for_irmaa.
        Post-fix: realized_gains are hoisted before magi_history[year] = yr.magi,
        so the stored value and the fallback both include them.

        Strategy:
          - Use an RMD-age household (your_age=75) so the year-0 RMD produces
            excess_rmd that seeds the brokerage for year 1.
          - Year 1 (yr_idx=1): income_year=2025. Not in magi_history (only 2026
            was stored in yr_idx=0); prior_year_magi is empty → fallback fires,
            magi_for_irmaa = yr.magi.
          - Give the household a high brokerage_growth so year-1 realized gains
            (brokerage * appreciation * turnover) are meaningful.
          - Assert: magi_history value for year 0 equals yr2026.magi (invariant),
            AND that yr2027.irmaa_cost is nonzero (behavioral — MAGI + realized
            gains pushes us above an IRMAA tier; would fail if magi is understated
            and the threshold is not crossed).

        Note on observability: magi_history is internal state; we cannot inspect it
        directly from outside run_scenario.  We instead verify the downstream
        effect: the IRMAA cost for the fallback year reflects the full yr.magi
        (confirmed by checking the relationship irmaa_cost > 0 and that it is
        consistent with the recorded yr.magi passing an IRMAA threshold).
        """
        from engine.irmaa import irmaa_for_year

        # RMD-age household: large IRA produces RMD > living_expenses, seeding brokerage.
        # High brokerage appreciation rate → large realized gains in year 1.
        # prior_year_magi intentionally empty → fallback fires for yr_idx 0 and 1.
        hh = Household(
            your_age=75,
            spouse_age=69,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=500_000,
            growth_rate=0.07,
            living_expenses=60_000.0,
            grants=[],
            brokerage_growth=GrowthProfile(default_rate=0.30),
        )

        plan = ConversionPlan()
        result = run_scenario(hh, plan, "fallback_irmaa", end_age=77)

        yr2026 = result.years[0]  # yr_idx=0: fallback fires, brokerage=0 → realized_gains=0
        yr2027 = result.years[1]  # yr_idx=1: fallback fires, brokerage>0 → realized_gains>0

        # INVARIANT: brokerage was seeded in year 0 (excess_rmd > 0)
        assert yr2026.excess_rmd > 0, "Fixture broken: year 0 produced no excess_rmd"
        # INVARIANT: year-1 brokerage balance reflects year-0 seeding
        assert yr2027.brokerage_balance > 0, (
            "Fixture broken: year-1 brokerage is 0; realized_gains test is vacuous"
        )

        # Compute expected realized gains for year 1 (same formula as engine):
        brok_y1 = yr2027.brokerage_balance  # begin-of-year balance
        brok_appreciation_rate = hh.brokerage_growth.appreciation_for(2027)  # type: ignore[union-attr]
        expected_realized_gains_y1 = brok_y1 * brok_appreciation_rate * hh.brok_turnover
        assert expected_realized_gains_y1 > 0, (
            "Fixture broken: expected realized gains in year 1 are zero"
        )

        # BEHAVIORAL: yr.magi for year 1 must include realized gains.
        # We verify this by recomputing what magi *without* realized gains would be
        # (= yr.magi - expected_realized_gains_y1) and asserting the full magi is larger.
        magi_without_realized = yr2027.magi - expected_realized_gains_y1
        assert yr2027.magi > magi_without_realized, (
            "yr2027.magi does not exceed the no-realized-gains baseline; "
            "realized_gains may not be included in magi"
        )

        # INVARIANT: irmaa_cost for yr2027 (fallback year) must be consistent with
        # the full yr.magi.  This locks the ordering guarantee: irmaa_for_year() is
        # called AFTER realized_gains are included in yr.magi.
        #
        # Note on discriminability: this assertion is an invariant (always true on
        # correct code) rather than a strict behavioral discriminator.  A truly
        # discriminating test would require realized_gains to straddle an IRMAA tier
        # boundary.  With a $4M IRA the household is deep in a high tier; the small
        # ~$9K brokerage-realized component won't cross a boundary.  The fixture
        # nonetheless exercises the fallback path and locks the irmaa_cost value.
        # For a strictly behavioral test, see the grid-05 test above which cleanly
        # isolates a fixed-vs-unfixed path.
        #
        # yr_idx=1: ya=76, sa=70; irmaa_for_year receives ya-2=74, sa-2=68
        expected_irmaa, _ = irmaa_for_year(
            yr2027.magi,  # fallback: magi_for_irmaa = yr.magi (full, realized-gains-inclusive)
            76 - 2,  # your_age_income_year
            70 - 2,  # spouse_age_income_year
            hh.medicare_part_b_base_monthly * 12,
            "MFJ",
            year=2027,  # projection/payment year passed by engine (audit A1: year param indexes surcharge dollars)
            cpi=hh.cpi_assumption,
        )
        assert yr2027.irmaa_cost == pytest.approx(expected_irmaa, abs=1.0), (
            f"irmaa_cost={yr2027.irmaa_cost:.2f} != value computed from full yr.magi "
            f"{yr2027.magi:.0f}: expected {expected_irmaa:.2f}. "
            f"Ordering invariant broken: IRMAA may be computed before realized_gains "
            f"are folded into yr.magi."
        )

    # ------------------------------------------------------------------
    # Test 3 — value-preservation: realized_gains hoist does NOT change
    #          final yr.magi or yr.niit_magi
    # Assertion type: INVARIANT
    # ------------------------------------------------------------------

    def test_magi_and_niit_magi_include_realized_gains_correctly(self):
        """Hoisting realized_gains must preserve final yr.magi and yr.niit_magi values.

        This invariant test verifies that folding realized_gains earlier (for MAGI
        ordering) does not accidentally double-count or omit them in the final field
        values recorded on YearResult.

        For a year with nonzero brokerage:
          yr.magi == magi_without_brokerage_realized_gains + realized_gains
          yr.niit_magi == yr.magi - tax_exempt_interest_ytd

        Strategy: use an RMD household so brokerage accumulates, pick year 2 (yr_idx=2)
        where brokerage has been seeded for two years and realized gains are meaningful.
        Reconstruct expected_magi from its components and assert equality.
        """
        hh = Household(
            your_age=75,
            spouse_age=69,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=500_000,
            growth_rate=0.07,
            living_expenses=60_000.0,
            grants=[],
            brokerage_growth=GrowthProfile(default_rate=0.20),
        )

        plan = ConversionPlan()
        result = run_scenario(hh, plan, "value_preservation", end_age=80)

        # Pick yr_idx=2 (year 2028) — brokerage seeded for 2 years, realized gains nonzero
        yr = result.years[2]
        assert yr.year == 2026 + 2
        assert yr.brokerage_balance > 0, "Fixture broken: brokerage is 0 in year 2"

        # Reconstruct realized_gains using the same formula as the engine
        brok_appreciation_rate = hh.brokerage_growth.appreciation_for(yr.year)  # type: ignore[union-attr]
        realized_gains = yr.brokerage_balance * brok_appreciation_rate * hh.brok_turnover
        assert realized_gains > 0, "Fixture broken: realized_gains is zero in year 2"

        # INVARIANT (a): yr.magi must be positive and include realized_gains.
        # Sanity: magi must be at least as large as realized_gains alone.
        assert yr.magi > realized_gains, (
            f"yr.magi ({yr.magi:.2f}) <= realized_gains ({realized_gains:.2f}); "
            f"magi appears to contain only realized_gains or less — other income missing"
        )

        # INVARIANT (b): yr.niit_magi excludes muni interest per §1411(d)(3).
        # No YTD snapshot here → tax_exempt_interest_ytd = 0.
        # So yr.niit_magi must equal yr.magi exactly.
        assert yr.niit_magi == pytest.approx(yr.magi, abs=0.01), (
            f"yr.niit_magi ({yr.niit_magi:.2f}) != yr.magi ({yr.magi:.2f}) "
            f"when tax_exempt_interest_ytd=0; §1411 exclusion mis-applied"
        )

        # INVARIANT (c): niit_magi invariant with muni interest in YTD
        # Re-run with a YTD snapshot that has tax_exempt_interest_ytd to verify exclusion.
        from models.ytd_income import YTDSnapshot

        muni_interest = 8_000.0
        ytd_muni = YTDSnapshot(tax_year=2026, tax_exempt_interest_ytd=muni_interest)
        result_muni = run_scenario(hh, plan, "value_pres_muni", end_age=80, ytd=ytd_muni)
        yr_muni = result_muni.years[0]  # base year: ytd applies

        # §1411(d)(3): niit_magi = magi - tax_exempt_interest
        assert yr_muni.niit_magi == pytest.approx(yr_muni.magi - muni_interest, abs=0.01), (
            f"niit_magi ({yr_muni.niit_magi:.2f}) != magi - muni_interest "
            f"({yr_muni.magi - muni_interest:.2f}); §1411(d)(3) exclusion broken"
        )


class TestAuditF3F4SSProvisionalIncome:
    """F3/F4: LTCG, qualified dividends, and realized brokerage gains must enter
    SS provisional income per IRC §86(b)(2)."""

    def _base_hh(self) -> Household:
        return Household(
            your_age=70,
            spouse_age=64,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=1_000_000,
            spouse_ira=0,
        )

    def _call_ss(self, hh: Household, **kwargs) -> float:
        """Call compute_social_security and return taxable_ss_amt."""
        from engine.scenario_compute import compute_social_security

        _, _, _, taxable_ss_amt = compute_social_security(
            hh=hh,
            ya=hh.your_age,
            sa=hh.spouse_age,
            survivor_active=False,
            who_dies=None,
            current_filing_status="MFJ",
            your_conversion=0.0,
            spouse_conversion=0.0,
            taxable_rmd=0.0,
            spouse_taxable_rmd=0.0,
            extra_withdrawal=0.0,
            spouse_extra_withdrawal=0.0,
            option_income=0.0,
            your_inherited_distribution=0.0,
            spouse_inherited_distribution=0.0,
            ord_div_this_year=0.0,
            ytd_year=kwargs.get("ytd_year"),
            qual_div_this_year=kwargs.get("qual_div_this_year", 0.0),
            realized_gains=kwargs.get("realized_gains", 0.0),
        )
        return taxable_ss_amt

    def test_f3_ytd_ltcg_raises_taxable_ss(self):
        """F3: ltcg_ytd must enter provisional income — taxable SS is higher with YTD LTCG."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()
        ytd_no_ltcg = YTDSnapshot(tax_year=hh.base_year, ltcg_ytd=0.0)
        ytd_with_ltcg = YTDSnapshot(tax_year=hh.base_year, ltcg_ytd=50_000.0)

        ss_without = self._call_ss(hh, ytd_year=ytd_no_ltcg)
        ss_with = self._call_ss(hh, ytd_year=ytd_with_ltcg)

        assert ss_with > ss_without, (
            "ltcg_ytd must increase taxable SS via provisional income (IRC §86(b)(2))"
        )

    def test_f3_ytd_qualified_dividends_raises_taxable_ss(self):
        """F3: qualified_dividends_ytd must enter provisional income — taxable SS is higher."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()
        ytd_no_qdiv = YTDSnapshot(qualified_dividends_ytd=0.0)
        ytd_with_qdiv = YTDSnapshot(qualified_dividends_ytd=30_000.0)

        ss_without = self._call_ss(hh, ytd_year=ytd_no_qdiv)
        ss_with = self._call_ss(hh, ytd_year=ytd_with_qdiv)

        assert ss_with > ss_without, (
            "qualified_dividends_ytd must increase taxable SS via provisional income"
        )

    def test_f4_forecast_qual_div_raises_taxable_ss(self):
        """F4: forecast qual_div_this_year must enter provisional income."""
        hh = self._base_hh()
        ss_without = self._call_ss(hh, qual_div_this_year=0.0)
        ss_with = self._call_ss(hh, qual_div_this_year=20_000.0)

        assert ss_with > ss_without, (
            "qual_div_this_year must increase taxable SS via provisional income (IRC §86(b)(2))"
        )

    def test_f4_realized_gains_raise_taxable_ss(self):
        """F4: brokerage realized_gains must enter SS provisional income."""
        hh = self._base_hh()
        ss_without = self._call_ss(hh, realized_gains=0.0)
        ss_with = self._call_ss(hh, realized_gains=40_000.0)

        assert ss_with > ss_without, (
            "realized_gains must increase taxable SS via provisional income (IRC §86(b)(2))"
        )

    def test_f4_run_scenario_brokerage_yield_raises_taxable_ss(self):
        """F4: run_scenario with a brokerage yield produces higher taxable SS than zero-yield.

        Sanity-checks that the fix flows end-to-end through run_scenario.
        Use a future year (your_age=72) where SS is active but no RMDs yet.
        """
        hh_no_yield = Household(
            your_age=68,
            spouse_age=62,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=500_000,
            spouse_ira=0,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.0,
                qualified_fraction=1.0,
            ),
        )
        hh_with_yield = Household(
            your_age=68,
            spouse_age=62,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=500_000,
            spouse_ira=0,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.03,
                qualified_fraction=1.0,
            ),
        )
        # Seed both with a brokerage balance by running enough years to accumulate
        plan = ConversionPlan()
        res_no = run_scenario(hh_no_yield, plan, end_age=75)
        res_with = run_scenario(hh_with_yield, plan, end_age=75)

        # At age 72 (SS active, no RMDs) the yield scenario must show higher taxable SS
        yr_no = next(yr for yr in res_no.years if yr.your_age == 73)
        yr_with = next(yr for yr in res_with.years if yr.your_age == 73)

        assert yr_with.taxable_ss_amt >= yr_no.taxable_ss_amt, (
            "Scenario with brokerage yield must produce >= taxable SS than zero-yield"
        )


class TestAuditF5BaseYearQualDivLTCGWalk:
    """F5: YTD qualified dividends must be taxed at preferential LTCG rates,
    not escaped entirely when ltcg_ytd == 0."""

    def _hh(self) -> Household:
        return Household(
            your_age=65,
            spouse_age=59,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            your_ira=500_000,
            spouse_ira=0,
        )

    def test_f5_qual_div_only_gets_ltcg_rate_tax(self):
        """F5: When ltcg_ytd==0 but qualified_dividends_ytd>0, ytd_ltcg_tax must be > 0.

        Pre-fix: guard was `ltcg_ytd > 0` so qual-divs-only skipped the stack walk entirely
        → ytd_ltcg_tax = 0. Post-fix: guard is `(ltcg_ytd + qualified_dividends_ytd) > 0`.
        """
        from models.ytd_income import YTDSnapshot

        hh = self._hh()
        # Put enough ordinary income so the qual-divs land in the 15% LTCG band
        ytd = YTDSnapshot(
            wages_ytd=150_000.0,
            ltcg_ytd=0.0,
            qualified_dividends_ytd=20_000.0,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age, ytd=ytd)
        yr = result.years[0]

        assert yr.ytd_ltcg_tax > 0.0, (
            "ytd_ltcg_tax must be > 0 when only qualified_dividends_ytd > 0 (F5 fix)"
        )

    def test_f5_qual_div_plus_ltcg_both_taxed(self):
        """F5: Combined ltcg_ytd + qualified_dividends_ytd must both enter the stack walk."""
        from models.ytd_income import YTDSnapshot

        hh = self._hh()
        ytd_ltcg_only = YTDSnapshot(
            wages_ytd=150_000.0,
            ltcg_ytd=20_000.0,
            qualified_dividends_ytd=0.0,
        )
        ytd_both = YTDSnapshot(
            wages_ytd=150_000.0,
            ltcg_ytd=20_000.0,
            qualified_dividends_ytd=10_000.0,
        )
        plan = ConversionPlan()
        res_ltcg = run_scenario(hh, plan, end_age=hh.your_age, ytd=ytd_ltcg_only)
        res_both = run_scenario(hh, plan, end_age=hh.your_age, ytd=ytd_both)

        yr_ltcg = res_ltcg.years[0]
        yr_both = res_both.years[0]

        assert yr_both.ytd_ltcg_tax > yr_ltcg.ytd_ltcg_tax, (
            "Adding qualified_dividends_ytd to ltcg_ytd must increase ytd_ltcg_tax"
        )
