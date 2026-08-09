"""Tests for engine.scenario RMD phase, brokerage accumulation, and brokerage_start seeding."""

import pytest

from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from models.household import GrowthProfile, Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestScenarioDividendProjection:
    """Tests for brokerage dividend projection in scenario engine."""

    def _rmd_household(self, **kwargs) -> Household:
        """Household at RMD age so excess RMD seeds brokerage in year 1."""
        return Household(
            your_age=75,
            spouse_age=69,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=1_000_000,
            growth_rate=0.07,
            **kwargs,
        )

    def test_zero_yield_is_backward_compatible(self):
        """GrowthProfile with yield_rate=0 → identical outputs to no GrowthProfile."""
        hh_default = self._rmd_household()
        hh_explicit = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        r_default = run_scenario(hh_default, ConversionPlan(), "default", end_age=80)
        r_explicit = run_scenario(hh_explicit, ConversionPlan(), "explicit", end_age=80)

        for yr_d, yr_e in zip(r_default.years, r_explicit.years, strict=True):
            assert yr_d.magi == pytest.approx(yr_e.magi, abs=1.0)
            assert yr_d.combined_gross == pytest.approx(yr_e.combined_gross, abs=1.0)
            assert yr_d.brokerage_balance == pytest.approx(yr_e.brokerage_balance, abs=1.0)

    def test_yield_pushes_qualified_to_magi(self):
        """qualified_fraction=1.0 → qualified dividends increment MAGI but not combined_gross."""
        # Use brokerage_growth with yield but all-qualified; run two years so brokerage is seeded.
        hh_no_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        hh_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.03, qualified_fraction=1.0
            ),
        )
        r_no = run_scenario(hh_no_yield, ConversionPlan(), "no_yield", end_age=80)
        r_yes = run_scenario(hh_yield, ConversionPlan(), "with_yield", end_age=80)

        # Find a year where brokerage has accumulated (age 77, 2 years of excess)
        yr_no = next(yr for yr in r_no.years if yr.your_age == 77)
        yr_yes = next(yr for yr in r_yes.years if yr.your_age == 77)

        # With qualified dividends: MAGI should be higher
        assert yr_yes.magi > yr_no.magi
        # combined_gross should be equal (qualified divs don't stack into ordinary brackets)
        assert yr_yes.combined_gross == pytest.approx(yr_no.combined_gross, abs=1.0)
        # Qualified div field should be nonzero in yield scenario
        assert yr_yes.brokerage_qual_div > 0.0
        assert yr_yes.brokerage_ord_div == pytest.approx(0.0)

    def test_yield_pushes_ordinary_to_combined_gross(self):
        """qualified_fraction=0.0 → ordinary dividends increment both MAGI and combined_gross."""
        hh_no_yield = self._rmd_household(
            brokerage_growth=GrowthProfile(default_rate=0.07, yield_rate=0.0),
        )
        hh_ord = self._rmd_household(
            brokerage_growth=GrowthProfile(
                default_rate=0.07, yield_rate=0.03, qualified_fraction=0.0
            ),
        )
        r_no = run_scenario(hh_no_yield, ConversionPlan(), "no_yield", end_age=80)
        r_ord = run_scenario(hh_ord, ConversionPlan(), "ord_yield", end_age=80)

        yr_no = next(yr for yr in r_no.years if yr.your_age == 77)
        yr_ord = next(yr for yr in r_ord.years if yr.your_age == 77)

        # With ordinary dividends: both MAGI and combined_gross should be higher
        assert yr_ord.magi > yr_no.magi
        assert yr_ord.combined_gross > yr_no.combined_gross
        # Ordinary div field should be nonzero; qualified should be zero
        assert yr_ord.brokerage_ord_div > 0.0
        assert yr_ord.brokerage_qual_div == pytest.approx(0.0)


class TestSpouseIsSoleBeneficiaryRmd:
    """M3 (audit-0720): spouse_is_sole_beneficiary threads through the multi-year
    projection's RMD phase (engine/scenario.py -> compute_rmds -> calc_rmd)."""

    def _qualifying_household(self, **kwargs) -> Household:
        """Owner at RMD age, spouse-beneficiary 15 years younger (qualifies for
        Table II: 80 - 65 = 15 > 10)."""
        return Household(
            your_age=80,
            spouse_age=65,
            base_year=2026,
            your_ira=4_000_000,
            spouse_ira=1_000_000,
            growth_rate=0.07,
            **kwargs,
        )

    def test_flag_off_matches_todays_table_iii_rmd(self):
        """Default-off: RMD identical to the pre-M3 Table-III-only behavior."""
        hh = self._qualifying_household(spouse_is_sole_beneficiary=False)
        result = run_scenario(hh, ConversionPlan(), "flag_off", end_age=81)
        yr0 = result.years[0]
        assert yr0.your_rmd == approx(4_000_000 / 20.2, tol=1.0)

    def test_flag_on_lowers_rmd_vs_flag_off(self):
        """Flag on for a qualifying household: Table II's larger divisor produces
        a strictly smaller projected RMD than flag-off (Table III)."""
        hh_off = self._qualifying_household(spouse_is_sole_beneficiary=False)
        hh_on = self._qualifying_household(spouse_is_sole_beneficiary=True)
        r_off = run_scenario(hh_off, ConversionPlan(), "flag_off", end_age=81)
        r_on = run_scenario(hh_on, ConversionPlan(), "flag_on", end_age=81)
        yr_off = r_off.years[0]
        yr_on = r_on.years[0]
        assert yr_on.your_rmd < yr_off.your_rmd
        assert yr_on.your_rmd == approx(4_000_000 / 23.8, tol=1.0)

    def test_default_household_age_gap_does_not_qualify_flag_on_is_golden_stable(self):
        """Default household (your_age 61 / spouse_age 55, gap 6) does not qualify
        (gap must be > 10) — flag-on RMDs must be UNCHANGED vs flag-off."""
        hh_off = Household(spouse_is_sole_beneficiary=False)
        hh_on = Household(spouse_is_sole_beneficiary=True)
        r_off = run_scenario(hh_off, ConversionPlan(), "default_off", end_age=95)
        r_on = run_scenario(hh_on, ConversionPlan(), "default_on", end_age=95)
        for yr_off, yr_on in zip(r_off.years, r_on.years, strict=True):
            assert yr_on.your_rmd == pytest.approx(yr_off.your_rmd, abs=0.01)
            assert yr_on.spouse_rmd == pytest.approx(yr_off.spouse_rmd, abs=0.01)


class TestSpouseRMDBrokerageAccumulation:
    """Regression: available_income must include spouse RMD and spouse extra_withdrawal.

    Bug (audit C-2): lines 561-562 of engine/scenario.py computed after_tax_rmd and
    available_income using only the "your" side — omitting yr.spouse_taxable_rmd and
    yr.spouse_extra_withdrawal.  When both spouses are in RMD, the spouse contribution
    can exceed $60K/yr, causing brokerage accumulation to be understated by $500K+
    over a 10-year window.
    """

    def _rmd_household(self, your_ira: float, spouse_ira: float) -> Household:
        """Both spouses already 75 (in RMD), no conversions, modest SS."""
        from dataclasses import replace

        return replace(
            Household(grants=[]),
            your_age=75,
            spouse_age=75,
            your_ira=your_ira,
            spouse_ira=spouse_ira,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            living_expenses=60_000.0,
        )

    def test_spouse_rmd_increases_brokerage_balance(self):
        """With spouse IRA active, year-10 brokerage must exceed the no-spouse baseline.

        Setup: both spouses 75 with $1.5M trad IRAs each (~$60K RMD/yr each at 75,
        divisor ≈25).  No conversions.  Living expenses $60K.  With fix, both RMDs
        flow into available_income; excess accumulates in brokerage.
        """
        plan = ConversionPlan()

        # Baseline: your IRA only (spouse IRA zeroed out → spouse_taxable_rmd ≈ 0)
        hh_yours_only = self._rmd_household(your_ira=1_500_000.0, spouse_ira=0.0)
        result_yours = run_scenario(hh_yours_only, plan, end_age=85)

        # With spouse: both IRAs $1.5M → spouse_taxable_rmd ≈ $60K extra each year
        hh_both = self._rmd_household(your_ira=1_500_000.0, spouse_ira=1_500_000.0)
        result_both = run_scenario(hh_both, plan, end_age=85)

        brok_yours = result_yours.years[-1].brokerage_balance
        brok_both = result_both.years[-1].brokerage_balance

        # The spouse RMD (~$60K/yr after-tax) compounded over 10 years at a
        # brokerage rate ≈7% produces well over $800K extra.  A conservative
        # floor of $500K guards against this regression without being brittle.
        assert brok_both > brok_yours + 500_000, (
            f"Expected brokerage with spouse RMD to exceed baseline by >$500K; "
            f"got brok_both={brok_both:,.0f}, brok_yours={brok_yours:,.0f}, "
            f"delta={brok_both - brok_yours:,.0f}"
        )

    def test_spouse_rmd_zero_equals_baseline(self):
        """When spouse IRA is zero, available_income must match the pre-fix behaviour.

        Ensures the fix is additive and does not corrupt the single-earner path.
        """
        plan = ConversionPlan()
        hh = self._rmd_household(your_ira=1_500_000.0, spouse_ira=0.0)
        result = run_scenario(hh, plan, end_age=85)

        for yr in result.years:
            if yr.your_age >= 75:
                assert yr.spouse_taxable_rmd == pytest.approx(0.0), (
                    f"year {yr.year}: spouse_taxable_rmd should be 0 when spouse IRA=0"
                )


class TestAuditF7ComputePhaseRmdStartAge:
    """F7: compute_phase must respect hh.your_rmd_start_age / hh.spouse_rmd_start_age,
    not hardcoded 74/75 literals."""

    def test_f7_rmd_phase_at_73_when_rmd_start_age_73(self):
        """F7: user at age 73 with rmd_start_age=73 must get 'rmd' or 'squeeze', not 'ss_conv'."""
        from engine.scenario_compute import compute_phase

        hh = Household(
            your_age=73,
            spouse_age=73,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            grants=[],  # isolate RMD-phase logic from option income / 'options' phase
        )
        phase = compute_phase(ya=73, sa=73, year=hh.base_year, hh=hh)
        assert phase in ("rmd", "squeeze"), (
            f"Expected 'rmd' or 'squeeze' at age 73 with rmd_start_age=73, got '{phase}'"
        )

    def test_f7_ss_conv_before_rmd_start_age_73(self):
        """F7: user at age 72 with rmd_start_age=73 must still get 'ss_conv'."""
        from engine.scenario_compute import compute_phase

        hh = Household(
            your_age=72,
            spouse_age=67,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            grants=[],  # isolate RMD-phase logic from option income / 'options' phase
        )
        phase = compute_phase(ya=72, sa=67, year=hh.base_year, hh=hh)
        assert phase == "ss_conv", (
            f"Expected 'ss_conv' at age 72 with rmd_start_age=73, got '{phase}'"
        )

    def test_f7_squeeze_when_only_user_hits_rmd(self):
        """F7: user at rmd_start_age but spouse below theirs → 'squeeze', not 'rmd'."""
        from engine.scenario_compute import compute_phase

        hh = Household(
            your_age=73,
            spouse_age=67,
            your_rmd_start_age=73,
            spouse_rmd_start_age=75,
            grants=[],  # isolate RMD-phase logic from option income / 'options' phase
        )
        phase = compute_phase(ya=73, sa=67, year=hh.base_year, hh=hh)
        assert phase == "squeeze", (
            f"Expected 'squeeze' when your_age==rmd_start_age but spouse below theirs, got '{phase}'"
        )

    def test_rmd_phase_takes_priority_over_options_past_rmd_age(self):
        """MEDIUM audit fix: 'rmd' must win over 'options' once BOTH spouses are
        past RMD age, even with concurrent nonzero option/NQO exercise income.

        RMD computation itself (compute_rmds) is age-gated independently of the
        phase label, so this was a display/label bug, not a functional one — but
        the label must still be correct.
        """
        from engine.scenario_compute import compute_phase
        from models.grants import StockGrant

        year = 2026
        hh = Household(
            your_age=76,
            spouse_age=76,
            base_year=year,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            grants=[StockGrant(year=2019, strike=104, shares=1000, expiry_year=year, grant_id="")],
            txn_price_now=200.0,
        )
        assert hh.option_income(year) > 0, "setup: grant must produce nonzero option income"
        phase = compute_phase(ya=76, sa=76, year=year, hh=hh)
        assert phase == "rmd", (
            f"Both spouses past RMD age with concurrent option income must be 'rmd', got '{phase}'"
        )

    def test_f7_rmd_phase_at_75_with_default_rmd_start_age(self):
        """F7: default rmd_start_age=75 — phase must be 'rmd'/'squeeze' only at age 75+.

        Use ages born 1960 (age 66 in 2026) so __post_init__ derives rmd_start_age=75.
        Born 1951-1959 → 73; born 1960+ → 75 (SECURE 2.0 §107).
        """
        from engine.scenario_compute import compute_phase

        # age 66 in base_year 2026 → born 1960 → default_rmd_age = 75
        hh = Household(
            your_age=66,
            spouse_age=66,
            grants=[],  # isolate RMD-phase logic from option income / 'options' phase
        )
        assert hh.your_rmd_start_age == 75, "setup: born-1960 cohort must get rmd_start=75"
        phase_74 = compute_phase(ya=74, sa=74, year=hh.base_year + 8, hh=hh)
        phase_75 = compute_phase(ya=75, sa=75, year=hh.base_year + 9, hh=hh)
        assert phase_74 == "ss_conv", (
            f"Age 74 with rmd_start=75 should be ss_conv, got '{phase_74}'"
        )
        assert phase_75 in ("rmd", "squeeze"), (
            f"Age 75 with rmd_start=75 should be rmd/squeeze, got '{phase_75}'"
        )

    def test_f7_run_scenario_phase_73_rmd_start_73(self):
        """F7: run_scenario must label age-73 year as 'rmd' when rmd_start_age=73."""
        hh = Household(
            your_age=70,
            spouse_age=70,
            your_rmd_start_age=73,
            spouse_rmd_start_age=73,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=74)
        yr73 = next(yr for yr in result.years if yr.your_age == 73)
        assert yr73.phase in ("rmd", "squeeze"), (
            f"run_scenario year at age 73 (rmd_start_age=73) must be rmd/squeeze, got '{yr73.phase}'"
        )


class TestCumRmdTaxSpouseOlderGate:
    """Fix #2: cum_rmd_tax must accumulate in years where EITHER spouse is in RMD.

    Bug: the gate was `ya >= hh.your_rmd_start_age` only, so households where
    the spouse reaches RMD age before the primary planner had their RMD-phase
    federal tax excluded from total_rmd_tax in those spouse-only-RMD years.
    """

    def _hh_spouse_older_rmd(self) -> "Household":
        """Spouse is 75 (in RMD), primary is 65 (NOT yet in RMD at rmd_start_age=75).

        Primary age 65 → born 1961 → default_rmd_age=75 (born 1960+).
        Spouse age 75 → born 1951 → default_rmd_age=73; already past RMD start.
        dataclasses.replace() re-runs __post_init__, so ages must be born 1960+
        for rmd_start_age=75 to stick; spouse's derived 73 is fine since sa≥73.
        """
        from dataclasses import replace

        return replace(
            Household(grants=[]),
            your_age=65,
            spouse_age=75,
            your_ira=500_000.0,
            spouse_ira=1_500_000.0,  # large so spouse RMD is material
            living_expenses=40_000.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )

    def test_total_rmd_tax_nonzero_when_only_spouse_in_rmd(self):
        """total_rmd_tax must be > 0 in years where only the spouse is in RMD.

        With spouse IRA $1.5M and spouse in RMD from base year, year-1 (primary age 65)
        the spouse has a material RMD that drives federal tax.  The buggy gate
        (ya >= your_rmd_start_age only) would exclude these years → total_rmd_tax=0
        for the first 10 years.  The fix (or sa >= spouse_rmd_start_age) must
        accumulate them.
        """
        from engine.scenario import ConversionPlan, run_scenario

        hh = self._hh_spouse_older_rmd()
        plan = ConversionPlan()
        # Run only to age 74 — primary still below rmd_start_age=75 throughout
        result = run_scenario(hh, plan, end_age=74)

        # All years are spouse-in-RMD, primary-NOT-in-RMD
        for yr in result.years:
            assert yr.your_age < hh.your_rmd_start_age, "test setup: primary not in RMD"
            assert yr.spouse_age >= hh.spouse_rmd_start_age, "test setup: spouse in RMD"

        # Without fix total_rmd_tax == 0; with fix it must be > 0
        assert result.total_rmd_tax > 0, (
            f"total_rmd_tax should be > 0 when spouse is in RMD but primary is not; "
            f"got {result.total_rmd_tax:.0f}"
        )

    def test_total_rmd_tax_larger_with_spouse_gate(self):
        """Household where BOTH eventually reach RMD: total_rmd_tax with the fix
        must be >= the buggy result (only primary's RMD years counted).

        We compare two households: one with spouse in RMD before primary
        (spouse 10 years older) vs. same household with spouse IRA=0 so spouse
        RMD doesn't matter.  The fixed version should accumulate more total_rmd_tax.
        """
        from dataclasses import replace

        from engine.scenario import ConversionPlan, run_scenario

        # your_age=65 → born 1961 → rmd_start_age=75 (born 1960+, so __post_init__ keeps 75)
        # spouse_age=75 → born 1951 → rmd_start_age=73 (already past RMD, in RMD from year 0)
        base = replace(
            Household(grants=[]),
            your_age=65,
            spouse_age=75,
            your_ira=800_000.0,
            living_expenses=40_000.0,
        )
        hh_with_spouse_ira = replace(base, spouse_ira=1_000_000.0)
        hh_no_spouse_ira = replace(base, spouse_ira=0.0)

        plan = ConversionPlan()
        result_with = run_scenario(hh_with_spouse_ira, plan, end_age=80)
        result_no = run_scenario(hh_no_spouse_ira, plan, end_age=80)

        assert result_with.total_rmd_tax >= result_no.total_rmd_tax, (
            "total_rmd_tax with active spouse IRA should be >= no-spouse-IRA baseline"
        )


class TestBrokerageStart:
    """Regression: brokerage_start must seed year-0 brokerage_balance.

    Prior to the fix, run_scenario initialised `brokerage = 0.0`, ignoring
    any existing taxable-account balance. Households with a non-zero brokerage
    produced zero brokerage_balance / brokerage_growth / brokerage_gain_tax in
    year 0, under-projecting MAGI, NIIT, and LTCG tax from the first year.
    """

    def _hh(self, brokerage_start: float = 0.0, **kwargs) -> Household:
        return Household(
            your_age=61,
            spouse_age=55,
            base_year=2026,
            your_ira=500_000,
            spouse_ira=0,
            grants=[],
            brok_turnover=0.30,
            brokerage_start=brokerage_start,
            **kwargs,
        )

    def test_zero_start_default_is_backward_compatible(self):
        """INVARIANT: default brokerage_start=0.0 produces zero year-0 brokerage figures."""
        hh = self._hh(brokerage_start=0.0)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age)
        yr0 = result.years[0]

        assert yr0.brokerage_balance == pytest.approx(0.0)
        assert yr0.brokerage_growth == pytest.approx(0.0)
        assert yr0.brokerage_gain_tax == pytest.approx(0.0)

    def test_nonzero_start_seeds_year0_balance(self):
        """BEHAVIORAL: brokerage_start=500_000 must appear as year-0 brokerage_balance."""
        start = 500_000.0
        hh = self._hh(brokerage_start=start)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age)
        yr0 = result.years[0]

        assert yr0.brokerage_balance == pytest.approx(start), (
            f"brokerage_balance in year 0 should equal brokerage_start={start:,.0f}; "
            f"got {yr0.brokerage_balance:,.0f}"
        )

    def test_nonzero_start_produces_nonzero_growth_and_magi(self):
        """BEHAVIORAL: non-zero starting balance must produce positive growth and feed MAGI.

        Note: brokerage_gain_tax may be 0 if ordinary income + realized gains fall in the
        0% LTCG band.  Instead assert brokerage_growth > 0 and that realized gains enter
        MAGI (the two unconditional consequences of a non-zero starting balance).
        """
        start = 500_000.0
        rate = 0.07
        hh = self._hh(brokerage_start=start, growth_rate=rate, living_expenses=0.0)
        plan = ConversionPlan()
        result = run_scenario(hh, plan, end_age=hh.your_age)
        yr0 = result.years[0]

        expected_growth = start * rate  # appreciation_rate == rate when no GrowthProfile
        expected_realized = expected_growth * hh.brok_turnover
        assert yr0.brokerage_growth == pytest.approx(expected_growth, rel=1e-6), (
            f"brokerage_growth should be {expected_growth:,.0f}; got {yr0.brokerage_growth:,.0f}"
        )
        # realized_gains from brokerage_start must appear in MAGI
        assert yr0.magi >= expected_realized, (
            f"MAGI must include brokerage realized gains >= {expected_realized:,.0f}; "
            f"got magi={yr0.magi:,.0f}"
        )

    def test_zero_start_vs_nonzero_start_magi_delta(self):
        """BEHAVIORAL: brokerage_start delta drives a matching MAGI delta via realized gains."""
        start = 200_000.0
        rate = 0.07
        hh_zero = self._hh(brokerage_start=0.0, growth_rate=rate, living_expenses=0.0)
        hh_with = self._hh(brokerage_start=start, growth_rate=rate, living_expenses=0.0)
        plan = ConversionPlan()

        yr_zero = run_scenario(hh_zero, plan, end_age=hh_zero.your_age).years[0]
        yr_with = run_scenario(hh_with, plan, end_age=hh_with.your_age).years[0]

        expected_realized_gain = start * rate * hh_with.brok_turnover
        magi_delta = yr_with.magi - yr_zero.magi
        assert magi_delta == pytest.approx(expected_realized_gain, rel=1e-6), (
            f"MAGI delta should equal realized gains from brokerage_start: "
            f"expected {expected_realized_gain:,.0f}, got {magi_delta:,.0f}"
        )


class TestApril1DeferralScenario:
    """IRC §401(a)(9)(C)(ii) April-1 deferral — integration tests via run_scenario.

    Member reaches rmd_start_age within the window (starts 3 years before).
    With deferral=False: year at start_age has your_rmd > 0.
    With deferral=True:  year at start_age has your_rmd == 0;
                         year at start_age+1 has your_rmd strictly greater than
                         the corresponding non-deferred year (doubled RMD).
    """

    def _hh(self, defer: bool) -> Household:
        from dataclasses import replace

        # your_age=66 → born 1960 → default_rmd_age=75 (1960+ cohort).
        # dataclasses.replace() re-runs __post_init__; born-1960+ ensures rmd_start stays 75.
        return replace(
            Household(grants=[]),
            your_age=66,
            spouse_age=59,
            your_ira=2_000_000.0,
            spouse_ira=0.0,
            your_defer_first_rmd=defer,
        )

    def test_no_deferral_rmd_positive_at_start_age(self):
        """Without deferral, RMD is positive in the start-age year."""
        result = run_scenario(self._hh(False), ConversionPlan(), end_age=78)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        assert yr75.your_rmd > 0.0, (
            f"Expected positive RMD at start_age=75 without deferral; got {yr75.your_rmd}"
        )

    def test_deferral_rmd_zero_at_start_age(self):
        """With deferral, RMD is zero in the start-age year."""
        result = run_scenario(self._hh(True), ConversionPlan(), end_age=78)
        yr75 = next(yr for yr in result.years if yr.your_age == 75)
        assert yr75.your_rmd == pytest.approx(0.0), (
            f"Expected zero RMD at start_age=75 with deferral; got {yr75.your_rmd}"
        )

    def test_deferral_doubles_rmd_in_year_two(self):
        """With deferral, year start_age+1 RMD exceeds the non-deferred year's RMD."""
        result_no = run_scenario(self._hh(False), ConversionPlan(), end_age=78)
        result_yes = run_scenario(self._hh(True), ConversionPlan(), end_age=78)

        yr76_no = next(yr for yr in result_no.years if yr.your_age == 76)
        yr76_yes = next(yr for yr in result_yes.years if yr.your_age == 76)

        assert yr76_yes.your_rmd > yr76_no.your_rmd, (
            f"Deferred year-2 RMD ({yr76_yes.your_rmd:,.0f}) should exceed "
            f"non-deferred year-2 RMD ({yr76_no.your_rmd:,.0f})"
        )
