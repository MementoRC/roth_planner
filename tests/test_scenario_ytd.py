"""Tests for engine.scenario YTD injection — autofill bracket math and run_scenario base-year wiring."""

import pytest

from engine.headroom import compute_headroom
from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from engine.scenario_autofill import auto_fill_12, auto_fill_22
from models.grants import StockGrant
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestA2AutoFillSS86ProvisionalIncomeMagi:
    """A2 — autofill taxable-SS provisional base must include LTCG and QD from YTD snapshot.

    IRC §86(b)(2): provisional income = AGI + tax-exempt interest + 0.5*SS.
    Pre-fix code summed only ordinary YTD fields (wages, NEC, STCG, ordinary_dividends,
    interest, conversions, distributions), omitting ltcg_ytd, qualified_dividends_ytd,
    and tax_exempt_interest_ytd.  A household with meaningful LTCG/QD should have a
    higher taxable SS and therefore less bracket room in the base year.
    """

    def _hh(self) -> "Household":
        return Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=300_000.0,
            your_ss_fra=2_500.0,
            your_ss_start_age=70,
            spouse_ss_fra=0.0,
            grants=[],
        )

    def test_ltcg_in_ytd_raises_taxable_ss_reduces_bracket_room(self) -> None:
        """LTCG in base-year YTD raises §86 provisional income → more taxable SS
        → autofill finds less bracket room in the base year.

        Scenario: household with small SS ($15K combined) and small wages ($10K).
        Without LTCG: provisional = 10K + 0.5*15K = 17.5K < tier1 ($32K) → tss = 0.
        With LTCG $30K: provisional = 40K + 0.5*15K = 47.5K > tier2 ($44K) → tss > 0.
        The LTCG household must have a lower conversion amount because tss shrinks room.
        """
        from models.ytd_income import YTDSnapshot

        # Small SS so that provisional crosses the tier1 threshold only with LTCG.
        # your_ss_start_age=61 so SS is active in the base year (ya=61 >= start=61).
        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=300_000.0,
            your_ss_fra=625.0,  # reduced early: ~$7.5K/yr (combined ~$7.5K, no spouse SS)
            your_ss_start_age=61,  # claiming at 61: active in base year
            spouse_ss_fra=0.0,
            grants=[],
        )
        ytd_no_ltcg = YTDSnapshot(tax_year=2026, wages_ytd=10_000)
        ytd_with_ltcg = YTDSnapshot(tax_year=2026, wages_ytd=10_000, ltcg_ytd=30_000)

        plan_no_ltcg = auto_fill_12(hh, early_exercise=False, ytd=ytd_no_ltcg)
        plan_with_ltcg = auto_fill_12(hh, early_exercise=False, ytd=ytd_with_ltcg)

        base_year = hh.base_year
        conv_no_ltcg = plan_no_ltcg.your_conversions.get(base_year, 0.0)
        conv_with_ltcg = plan_with_ltcg.your_conversions.get(base_year, 0.0)

        # LTCG raises taxable SS → more ordinary income stacked → less room to 12% ceiling
        assert conv_with_ltcg < conv_no_ltcg, (
            f"Expected less conversion room with LTCG in YTD (SS taxed more), "
            f"but got conv_with_ltcg={conv_with_ltcg:.0f} >= conv_no_ltcg={conv_no_ltcg:.0f}."
        )

    def test_qualified_dividends_in_ytd_raise_taxable_ss(self) -> None:
        """QD in base-year YTD raises §86 provisional income → more taxable SS
        → autofill finds less bracket room in the base year.

        Scenario: household with small SS ($15K combined) and small wages ($10K).
        Without QD: provisional = 10K + 7.5K = 17.5K < tier1 → tss = 0.
        With QD $30K: provisional = 40K + 7.5K = 47.5K → tss > 0.
        """
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            cpi_assumption=0.0,
            your_ira=500_000.0,
            spouse_ira=300_000.0,
            your_ss_fra=625.0,
            your_ss_start_age=61,
            spouse_ss_fra=0.0,
            grants=[],
        )
        ytd_no_qd = YTDSnapshot(tax_year=2026, wages_ytd=10_000)
        ytd_with_qd = YTDSnapshot(tax_year=2026, wages_ytd=10_000, qualified_dividends_ytd=30_000)

        plan_no_qd = auto_fill_12(hh, early_exercise=False, ytd=ytd_no_qd)
        plan_with_qd = auto_fill_12(hh, early_exercise=False, ytd=ytd_with_qd)

        base_year = hh.base_year
        conv_no_qd = plan_no_qd.your_conversions.get(base_year, 0.0)
        conv_with_qd = plan_with_qd.your_conversions.get(base_year, 0.0)

        assert conv_with_qd < conv_no_qd, (
            f"Expected less conversion room with QD in YTD (SS taxed more), "
            f"but got conv_with_qd={conv_with_qd:.0f} >= conv_no_qd={conv_no_qd:.0f}."
        )


class TestAutoFillCoreOrdinaryDividendsYTD:
    """Regression tests: _auto_fill_core must include ordinary_dividends_ytd in fixed_gross.

    Prior to the fix (math audit 2026-06-12 Priority 3), _auto_fill_core added only
    wages_ytd and stcg_ytd from the YTD snapshot, omitting ordinary_dividends_ytd
    (and nec_income_ytd, ira_conversions_ytd, ira_distributions_ytd). This caused
    bracket room to be overstated by the omitted ordinary income amount.
    """

    def _base_hh(self) -> Household:
        return Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=1_700_000,
            spouse_ira=1_700_000,
        )

    def test_ordinary_dividends_reduce_room_base_year(self):
        """ordinary_dividends_ytd must reduce base-year bracket room and conversion amount."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_div = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ordinary_dividends_ytd=10_000,
        )

        plan_no_div = auto_fill_12(hh, ytd=ytd_no_div)
        plan_with_div = auto_fill_12(hh, ytd=ytd_with_div)

        base_conv = plan_no_div.your_conversions.get(2026, 0.0)
        div_conv = plan_with_div.your_conversions.get(2026, 0.0)

        # ordinary_dividends_ytd consumes bracket room → fewer conversions in base year
        assert div_conv < base_conv, (
            f"Expected ordinary_dividends_ytd to reduce base-year conversion, "
            f"got no_div={base_conv:.0f} vs with_div={div_conv:.0f}"
        )
        # Difference should match the dividend amount (ordinary income fills bracket space)
        assert base_conv - div_conv == approx(10_000, tol=200)

    def test_nec_income_reduces_room_base_year(self):
        """nec_income_ytd (1099-NEC) must also reduce base-year bracket room."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_nec = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_nec = YTDSnapshot(tax_year=2026, wages_ytd=50_000, nec_income_ytd=8_000)

        plan_no_nec = auto_fill_12(hh, ytd=ytd_no_nec)
        plan_with_nec = auto_fill_12(hh, ytd=ytd_with_nec)

        base_conv = plan_no_nec.your_conversions.get(2026, 0.0)
        nec_conv = plan_with_nec.your_conversions.get(2026, 0.0)

        assert nec_conv < base_conv
        assert base_conv - nec_conv == approx(8_000, tol=200)

    def test_ira_conversions_done_reduce_room_base_year(self):
        """ira_conversions_ytd already done must reduce remaining planned room."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_done = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_done = YTDSnapshot(tax_year=2026, wages_ytd=50_000, ira_conversions_ytd=15_000)

        plan_no_done = auto_fill_12(hh, ytd=ytd_no_done)
        plan_done = auto_fill_12(hh, ytd=ytd_done)

        base_conv = plan_no_done.your_conversions.get(2026, 0.0)
        done_conv = plan_done.your_conversions.get(2026, 0.0)

        assert done_conv < base_conv
        assert base_conv - done_conv == approx(15_000, tol=200)

    def test_future_years_unaffected(self):
        """YTD snapshot only applies to base year; future years must be identical."""
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ordinary_dividends_ytd=10_000,
        )

        plan_no_ytd = auto_fill_12(hh)
        plan_with_ytd = auto_fill_12(hh, ytd=ytd)

        # All years after 2026 must be identical
        future_years_no = {y: v for y, v in plan_no_ytd.your_conversions.items() if y > 2026}
        future_years_with = {y: v for y, v in plan_with_ytd.your_conversions.items() if y > 2026}
        assert future_years_no == pytest.approx(future_years_with, abs=1.0)

    def test_total_subtract_grant_id_empty_uses_total(self):
        """Total subtract applies even when StockGrant.grant_id is empty (legacy fixture)."""
        from models.ytd_income import YTDSnapshot

        hh = Household(
            base_year=2026,
            grants=[StockGrant(year=2019, strike=104, shares=2000, expiry_year=2026, grant_id="")],
            txn_price_now=200.0,
        )
        ytd = YTDSnapshot(tax_year=2026, nqo_exercise_ytd=80_000)
        ytd._option_exercises_by_grant = {"GR-2019": 80_000}  # noqa: SLF001
        result = compute_headroom(hh, ytd, early_exercise=True)
        # Total subtract: realized = ytd.nqo_exercise_ytd regardless of grant_id
        assert result.realized_option_income_ytd == approx(80_000)
        assert result.planned_option_income == approx(192_000 - 80_000)

    def test_magi_ytd_includes_tax_exempt_interest(self):
        """Tax-exempt (muni) interest must appear in IRMAA MAGI even though it is federally exempt."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=80_000, tax_exempt_interest_ytd=5_000)
        # MAGI = wages + tax_exempt_interest
        assert ytd.magi_ytd == approx(85_000)

    def test_tax_exempt_interest_not_in_total_ordinary_income(self):
        """Tax-exempt interest is federally exempt — it must NOT stack into ordinary brackets."""
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(wages_ytd=80_000, tax_exempt_interest_ytd=5_000)
        # ordinary income = wages only; muni interest is excluded
        assert ytd.total_ordinary_income == approx(80_000)

    def test_interest_ytd_reduces_room_base_year(self):
        """Regression C-4: interest_ytd must reduce base-year bracket room and conversion amount.

        Prior to the fix, _auto_fill_core omitted interest_ytd from both other_fixed
        (provisional income for SS taxability) and fixed_gross (bracket math), causing
        conversion room to be overstated by the full interest amount.
        """
        from models.ytd_income import YTDSnapshot

        hh = self._base_hh()

        ytd_no_int = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_int = YTDSnapshot(tax_year=2026, wages_ytd=50_000, interest_ytd=12_000)

        plan_no_int = auto_fill_22(hh, ytd=ytd_no_int)
        plan_with_int = auto_fill_22(hh, ytd=ytd_with_int)

        base_conv = plan_no_int.your_conversions.get(2026, 0.0)
        int_conv = plan_with_int.your_conversions.get(2026, 0.0)

        # interest_ytd is fully taxable ordinary income → consumes bracket room → fewer conversions
        assert int_conv < base_conv, (
            f"Expected interest_ytd=12_000 to reduce base-year conversion, "
            f"got no_int={base_conv:.0f} vs with_int={int_conv:.0f}"
        )
        # Difference should be approximately the interest amount
        assert base_conv - int_conv == approx(12_000, tol=300)


class TestScenarioWithYTD:
    """Test scenario engine with YTD injection."""

    def test_ltcg_in_magi_not_gross(self):
        """LTCG appears in base-year MAGI but NOT in combined_gross."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000)
        plan = ConversionPlan(your_conversions={2026: 50_000})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # MAGI should include LTCG
        assert yr2026.magi > 200_000

        # combined_gross should NOT include LTCG
        # (only option income + conversion + taxable SS)
        assert yr2026.combined_gross < 200_000

    def test_scenario_combined_gross_includes_ytd_ordinary_dividends(self):
        """Ordinary dividends in YTD snapshot must stack into combined_gross (ordinary income)."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd_no_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000)
        ytd_with_div = YTDSnapshot(tax_year=2026, wages_ytd=50_000, ordinary_dividends_ytd=4_000)
        plan = ConversionPlan()

        result_no_div = run_scenario(hh, plan, "no_div", end_age=65, ytd=ytd_no_div)
        result_with_div = run_scenario(hh, plan, "with_div", end_age=65, ytd=ytd_with_div)

        yr_no_div = result_no_div.years[0]
        yr_with_div = result_with_div.years[0]

        # combined_gross in the dividend scenario should be exactly 4_000 higher
        assert yr_with_div.combined_gross - yr_no_div.combined_gross == approx(4_000)

    def test_ytd_does_not_affect_future_years(self):
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ltcg_ytd=200_000, wages_ytd=100_000)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "test", end_age=70, ytd=ytd)

        yr2026 = next(yr for yr in result.years if yr.year == 2026)
        yr2027 = next(yr for yr in result.years if yr.year == 2027)

        # 2026 should have YTD fields populated
        assert yr2026.ytd_ltcg == approx(200_000)
        assert yr2026.ytd_wages == approx(100_000)

        # 2027 should have zero YTD fields
        assert yr2027.ytd_ltcg == 0
        assert yr2027.ytd_wages == 0

    def test_conversions_done_subtracted(self):
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=30_000)
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result = run_scenario(hh, plan, "test", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # Planned $100K minus $30K already done = $70K
        assert yr2026.your_conversion == approx(70_000)

    def test_run_scenario_includes_ytd_conversions_in_base_magi(self):
        """ira_conversions_ytd must appear in base-year MAGI even though it
        reduces the remaining planned conversion amount."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        conversions_done = 35_000
        ytd = YTDSnapshot(tax_year=2026, ira_conversions_ytd=conversions_done)
        # Plan more than what's already done so yr.your_conversion > 0
        plan = ConversionPlan(your_conversions={2026: 100_000})
        result_with = run_scenario(hh, plan, "with_conv", end_age=65, ytd=ytd)
        result_without = run_scenario(hh, plan, "without_conv", end_age=65, ytd=None)

        yr_with = result_with.years[0]
        yr_without = result_without.years[0]

        # Invariant: both scenarios plan the same $100K total conversion; ira_conversions_ytd
        # merely shifts income from yr.your_conversion (planned remaining) to magi_ytd (already
        # done). The SUM must be equal — the absolute value depends on default Household
        # option_income which varies with tax-year defaults (e.g. TXN NQO grants in 2026).
        assert yr_with.magi == approx(yr_without.magi)

    def test_run_scenario_includes_nec_in_base_magi(self):
        """nec_income_ytd (1099-NEC) must appear in base-year MAGI."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        nec = 28_000
        ytd_with = YTDSnapshot(tax_year=2026, nec_income_ytd=nec)
        ytd_none = YTDSnapshot(tax_year=2026)
        plan = ConversionPlan()

        yr_with = run_scenario(hh, plan, "nec", end_age=65, ytd=ytd_with).years[0]
        yr_none = run_scenario(hh, plan, "no_nec", end_age=65, ytd=ytd_none).years[0]

        assert yr_with.magi - yr_none.magi == approx(nec)

    def test_run_scenario_includes_distributions_in_base_magi(self):
        """ira_distributions_ytd (non-conversion IRA withdrawals) must appear
        in base-year MAGI."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        distrib = 42_000
        ytd_with = YTDSnapshot(tax_year=2026, ira_distributions_ytd=distrib)
        ytd_none = YTDSnapshot(tax_year=2026)
        plan = ConversionPlan()

        yr_with = run_scenario(hh, plan, "dist", end_age=65, ytd=ytd_with).years[0]
        yr_none = run_scenario(hh, plan, "no_dist", end_age=65, ytd=ytd_none).years[0]

        assert yr_with.magi - yr_none.magi == approx(distrib)

    def test_run_scenario_matches_canonical_magi_ytd(self):
        """Base-year MAGI must equal canonical YTDSnapshot.magi_ytd plus the
        projected income components (remaining planned conversion, option
        income, SS, RMD) — verifying parity with _auto_fill_core."""
        from models.ytd_income import YTDSnapshot

        hh = Household()
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=60_000,
            nec_income_ytd=10_000,
            ira_conversions_ytd=25_000,
            ira_distributions_ytd=15_000,
            ltcg_ytd=50_000,
            stcg_ytd=5_000,
            qualified_dividends_ytd=3_000,
            ordinary_dividends_ytd=2_000,
            interest_ytd=1_000,
        )
        planned_conversion = 80_000
        plan = ConversionPlan(your_conversions={2026: planned_conversion})
        result = run_scenario(hh, plan, "canonical", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        # Projected components not in magi_ytd.
        # D-1: uses taxable_ss_amt (not full combined_ss) — per §1395r(i)(4).
        # C-7: option_income contribution is net of nqo_exercise_ytd (no NQO in this ytd → zero).
        # E-3: includes realized_gains (brokerage_growth * brok_turnover).
        projected_components = (
            yr2026.option_income  # no nqo_exercise_ytd in this ytd, so no dedup delta
            + yr2026.your_conversion  # remaining after subtracting ira_conversions_ytd
            + yr2026.spouse_conversion
            + yr2026.taxable_rmd
            + yr2026.spouse_taxable_rmd
            + yr2026.extra_withdrawal
            + yr2026.spouse_extra_withdrawal
            + yr2026.taxable_ss_amt  # D-1: was combined_ss; zero here (age 61, no SS)
            + yr2026.your_inherited_distribution
            + yr2026.spouse_inherited_distribution
            + yr2026.brokerage_qual_div
            + yr2026.brokerage_ord_div
            + yr2026.brokerage_growth * hh.brok_turnover  # E-3: realized_gains
        )
        expected_magi = projected_components + ytd.magi_ytd
        assert yr2026.magi == approx(expected_magi)

    def test_ytd_save_load_roundtrip(self, tmp_path, monkeypatch):
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import RealizedGainEvent, YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd.json")

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000,
            ltcg_ytd=200_000,
            stcg_ytd=10_000,
            ordinary_dividends_ytd=5_000,
            interest_ytd=3_000,
            ira_conversions_ytd=20_000,
            snapshot_date="2026-06-15",
            gain_events=[
                RealizedGainEvent(
                    date="2026-03-15",
                    description="TXN stop-loss",
                    proceeds=250_000,
                    cost_basis=50_000,
                    holding_period="long",
                    account_name="Schwab",
                ),
            ],
        )
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.wages_ytd == 50_000
        assert loaded.ltcg_ytd == 200_000
        assert loaded.stcg_ytd == 10_000
        assert loaded.dividends_ytd == 5_000
        assert loaded.interest_ytd == 3_000
        assert loaded.ira_conversions_ytd == 20_000
        assert len(loaded.gain_events) == 1
        assert loaded.gain_events[0].gain_loss == approx(200_000)

    def test_spouse_conversions_done_subtracted(self):
        """Fix #1: spouse_ira_conversions_ytd must reduce effective spouse_conversion
        symmetrically to how ira_conversions_ytd reduces your_conversion.

        Plan spouse $80K, already done $30K → effective spouse_conversion must be $50K.
        """
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=1_000_000.0,
            spouse_ira=1_000_000.0,
            grants=[],
        )
        ytd = YTDSnapshot(tax_year=2026, spouse_ira_conversions_ytd=30_000)
        plan = ConversionPlan(spouse_conversions={2026: 80_000})
        result = run_scenario(hh, plan, "spouse_clamp", end_age=65, ytd=ytd)
        yr2026 = result.years[0]

        assert yr2026.spouse_conversion == pytest.approx(50_000, abs=1.0), (
            f"Expected spouse_conversion=50_000 (80K planned - 30K done); "
            f"got {yr2026.spouse_conversion:.0f}"
        )

    def test_spouse_conversions_done_zero_is_symmetric_to_your_side(self):
        """Symmetry: zero spouse_ira_conversions_ytd leaves spouse_conversion unchanged."""
        from models.ytd_income import YTDSnapshot

        hh = Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=1_000_000.0,
            spouse_ira=1_000_000.0,
            grants=[],
        )
        ytd_no_done = YTDSnapshot(tax_year=2026, spouse_ira_conversions_ytd=0)
        ytd_no_ytd = None
        plan = ConversionPlan(spouse_conversions={2026: 60_000})

        result_no_done = run_scenario(hh, plan, "sp_no_done", end_age=65, ytd=ytd_no_done)
        result_no_ytd = run_scenario(hh, plan, "sp_no_ytd", end_age=65, ytd=ytd_no_ytd)

        assert result_no_done.years[0].spouse_conversion == pytest.approx(
            result_no_ytd.years[0].spouse_conversion, abs=1.0
        ), "Zero spouse_ira_conversions_ytd must not alter spouse_conversion"

    def test_ytd_ltcg_bracket_walk_zero_percent_band(self):
        """YTD LTCG fully inside the 0% band must produce zero LTCG tax.

        Regression for audit A-5/D-5: flat-rate hh.ltcg_rate was applied,
        yielding $6,000 instead of $0 for a household with taxable_ordinary
        well below the MFJ 0%-band ceiling (~$96,700 for 2026).
        """
        from models.ytd_income import YTDSnapshot

        # Wages $30K → std deduction $30,000 (MFJ both <65) → taxable ~$0,
        # well below LTCG_THRESHOLDS_MFJ[0] (~$96,700). $40K LTCG stays in 0% band.
        hh = Household(your_age=61, spouse_age=55, base_year=2026)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=30_000, ltcg_ytd=40_000)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, "ltcg_bracket", end_age=62, ytd=ytd)
        yr2026 = result.years[0]

        # taxable_income (ordinary) should be well below ~$98,900 threshold
        assert yr2026.taxable_income < 98_900
        # All $40K LTCG sits in the 0% band — no LTCG tax owed
        assert yr2026.ytd_ltcg_tax == approx(0.0)

    def test_base_year_realized_gains_not_double_counted_with_ytd(self):
        """B1+B2: base-year forecast realized gains must not stack on top of YTD actuals.

        With a YTD snapshot present, the base-year row's MAGI, NIIT cost, and LTCG tax
        must be invariant to the forecast realized-gains config (brok_turnover), and the
        forecast LTCG stack (brokerage_gain_tax) must be 0 in the base year.
        """
        from models.ytd_income import YTDSnapshot

        # $400K brokerage + large income pushes MAGI above $250K NIIT threshold so
        # niit_cost is meaningful (not trivially zero) and variance would be detectable.
        # Two households differ ONLY in brok_turnover (30% vs 0%).
        common_kwargs = {
            "your_age": 61,
            "spouse_age": 55,
            "base_year": 2026,
            "your_ira": 500_000.0,
            "spouse_ira": 500_000.0,
            "brokerage_start": 400_000.0,
            "your_ss_fra": 2_500.0,
            "your_ss_start_age": 67,
            "grants": [],
        }
        hh_high = Household(**common_kwargs, brok_turnover=0.30)
        hh_zero = Household(**common_kwargs, brok_turnover=0.0)

        # YTD snapshot with meaningful LTCG ($50K) and enough other income to clear
        # the $250K NIIT threshold once conversions are added.
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=50_000.0,
            ltcg_ytd=50_000.0,
        )
        plan = ConversionPlan(your_conversions={2026: 100_000})

        results_high = run_scenario(hh_high, plan, "b1b2_high", end_age=62, ytd=ytd)
        results_zero = run_scenario(hh_zero, plan, "b1b2_zero", end_age=62, ytd=ytd)

        yr_high = results_high.years[0]
        yr_zero = results_zero.years[0]

        # MAGI must be identical regardless of brok_turnover — YTD actuals are the sole source
        assert yr_high.magi == pytest.approx(yr_zero.magi), (
            f"Base-year MAGI differs: high={yr_high.magi}, zero={yr_zero.magi}"
        )
        # NIIT cost must be identical — forecast NII term must be suppressed in base year
        assert yr_high.niit_cost == pytest.approx(yr_zero.niit_cost), (
            f"Base-year niit_cost differs: high={yr_high.niit_cost}, zero={yr_zero.niit_cost}"
        )
        # Forecast LTCG stack (brokerage_gain_tax) must be zero in the base year for both —
        # the YTD LTCG tax (ytd_ltcg_tax) is the sole source via the existing ytd_ltcg_tax path
        assert yr_high.brokerage_gain_tax == pytest.approx(0.0), (
            f"Base-year brokerage_gain_tax should be 0 with ytd, got {yr_high.brokerage_gain_tax}"
        )
        assert yr_zero.brokerage_gain_tax == pytest.approx(0.0), (
            f"Base-year brokerage_gain_tax should be 0 with ytd, got {yr_zero.brokerage_gain_tax}"
        )
