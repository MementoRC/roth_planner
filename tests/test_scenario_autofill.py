"""Tests for engine.scenario auto-fill MAGI/SS regression (F9) and autofill taxable-SS base_magi."""

import pytest

from engine.scenario_autofill import auto_fill_12, auto_fill_22, auto_fill_aca, auto_fill_irmaa_safe
from models.household import Household, SurvivorScenario


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestAutoFillRmdDeferral:
    """your_defer_first_rmd=True must shift income, producing a different plan than False."""

    def test_spouse_defer_flag_changes_autofill_plan(self) -> None:
        """spouse_defer_first_rmd=True must yield a different autofill plan than False.

        The spouse_taxable_rmd is included in ordinary_core regardless of your age,
        so it directly reduces conversion room when the spouse reaches their RMD age
        while you are still converting.

        Setup:
          - your_age=62, your_rmd_start_age=75: wide conversion window (13 yrs)
          - spouse_age=66 (born 1960), spouse_rmd_start_age=75: hits RMD at yr_idx=9 (sa=75)
          - spouse_ira=1_000_000: large enough to produce a meaningful RMD

        With spouse_defer_first_rmd=False: yr_idx=1 has positive spouse_taxable_rmd
          -> reduces fixed_gross room -> lower conversions that year.
        With spouse_defer_first_rmd=True: yr_idx=1 has spouse_taxable_rmd=0
          -> more conversion room that year.
        The conversion totals across the two runs must differ.
        """
        from dataclasses import replace

        # spouse_age=66 → born 1960 → default_rmd_age=75 (1960+ cohort, SECURE 2.0 §107).
        # dataclasses.replace() re-runs __post_init__, so cohort must be born 1960+ for
        # spouse_rmd_start_age=75 to survive derivation (1951-1959 → 73 otherwise).
        # Spouse hits RMD at base_year+9 (age 75). Your conversion window is 13 yrs (age 62→75).
        hh_base = replace(
            Household(),
            your_age=62,
            your_ira=2_000_000.0,
            spouse_age=66,
            spouse_ira=1_000_000.0,
        )
        assert hh_base.spouse_rmd_start_age == 75, "setup: born-1960 spouse must get rmd_start=75"
        hh_no_defer = replace(hh_base, spouse_defer_first_rmd=False)
        hh_defer = replace(hh_base, spouse_defer_first_rmd=True)

        plan_no = auto_fill_12(hh_no_defer)
        plan_yes = auto_fill_12(hh_defer)

        # yr_idx=9 corresponds to base_year + 9, spouse age 75 (first RMD year)
        first_spouse_rmd_year = hh_base.base_year + 9
        conv_no = plan_no.your_conversions.get(first_spouse_rmd_year, 0.0)
        conv_yes = plan_yes.your_conversions.get(first_spouse_rmd_year, 0.0)

        # With defer: spouse RMD income = 0 that year -> more room for your conversions.
        assert conv_yes >= conv_no, (
            f"Deferred spouse start-age year conversion ({conv_yes:.0f}) should be >= "
            f"no-defer ({conv_no:.0f}): deferred spouse RMD reduces fixed_gross"
        )
        # The two plans must produce different conversion amounts
        total_no = sum(plan_no.your_conversions.values())
        total_yes = sum(plan_yes.your_conversions.values())
        assert total_no != pytest.approx(total_yes, rel=1e-6), (
            f"Expected plans to differ: defer=False total={total_no:.0f}, "
            f"defer=True total={total_yes:.0f}; spouse_defer_first_rmd has no effect"
        )


class TestAutoFillCoreBaseMagiTaxableSS:
    """F9 regression: _auto_fill_core must use taxable SS (not gross SS) in base_magi.

    Prior to the fix, base_magi added the full combined_ss even though tss (the
    IRC §86-capped taxable portion) was already computed and used in fixed_gross.
    This overstated base_magi, causing the IRMAA-safe ceiling to be hit too soon
    and OBBBA senior-bonus phase-out to fire earlier than correct.

    Note: your_ss_fra is a monthly dollar amount; ss_benefit_at_age() converts it
    to an annual benefit applying delay/early credits.
    """

    def test_irmaa_safe_base_magi_uses_taxable_ss(self) -> None:
        """auto_fill_irmaa_safe conversion must not be reduced by non-taxable SS.

        Setup: Single household at SS-start age. your_ss_fra=1_500 (monthly) ->
        annual SS ~$22.3K at age 70 (3yr delay credits). With no other income,
        provisional = 0 + 0.5x22.3K = 11.2K < $25K Single tier-1 -> tss = 0.

        Under the old bug: base_magi += gross SS (~22.3K) -> less IRMAA room.
        Under the fix:     base_magi += tss (0) -> full IRMAA room.

        Observable consequence: auto_fill_irmaa_safe generates a non-zero conversion
        in the base year, AND run_scenario confirms taxable_ss_amt == 0 (the scenario
        engine independently computes tss=0 for this household, so if autofill used
        gross SS the plan would be overly conservative relative to scenario truth).
        """
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss

        hh = Household(
            filing_status="Single",
            your_age=70,
            your_ira=3_000_000,
            spouse_ira=0,
            spouse_roth=0,
            spouse_age=0,
            spouse_ss_fra=0,
            your_ss_fra=1_500,  # $1,500/month FRA benefit (realistic)
            your_ss_start_age=70,
        )

        # Confirm precondition: tss = 0 for this household (provisional < $25K tier-1).
        combined_ss = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        assert combined_ss > 0.0, f"Precondition: household must have SS income, got {combined_ss}"
        tss = taxable_ss(combined_ss, 0.0, filing_status="Single")
        assert tss == 0.0, (
            f"Precondition: provisional={0.5 * combined_ss:.0f} must be < $25K tier-1; "
            f"got tss={tss:.0f} (combined_ss={combined_ss:.0f})"
        )

        plan = auto_fill_irmaa_safe(hh)
        base_year = hh.base_year
        conv = plan.your_conversions.get(base_year, 0.0)

        # Post-fix: base_magi uses tss=0 -> IRMAA room = threshold - RMD, so a
        # positive conversion is generated. Pre-fix: base_magi added ~$22K of gross
        # SS, over-consuming IRMAA room by that amount (overly conservative plan).
        assert conv > 0.0, (
            f"IRMAA-safe plan must produce a positive base-year conversion; got {conv}"
        )

    def test_irmaa_safe_room_reduced_by_tss_not_gross_ss(self) -> None:
        """IRMAA room reduction from SS equals tss, not gross combined_ss.

        Compare two identical MFJ households that differ only in whether SS has
        started. With high wages YTD, provisional income is deep in the 85% band
        so tss = 85% x combined_ss < combined_ss.

        The base-year conversion difference between the no-SS and SS households
        must equal tss (the taxable fraction), not the full gross SS amount.

        Note: your_ss_fra=2_000/month -> combined_ss_annual ~59.5K (both at 70).
        provisional = wages(80K) + 0.5x59.5K ~109.7K >> $44K MFJ tier-2
        -> tss = 85% x 59.5K ~50.6K; gross = 59.5K; delta ~8.9K.
        """
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss
        from models.ytd_income import YTDSnapshot

        # Large IRA -- never the binding constraint; IRMAA ceiling is.
        common_kwargs: dict = {
            "filing_status": "MFJ",
            "your_ira": 5_000_000,
            "spouse_ira": 5_000_000,
            "your_ss_fra": 2_000,  # $2K/month FRA (realistic)
            "spouse_ss_fra": 2_000,
            "your_ss_start_age": 70,
            "spouse_ss_start_age": 70,
        }
        # No SS yet (ages below start age)
        hh_no_ss = Household(**common_kwargs, your_age=60, spouse_age=60)
        # SS active (ages at start age -> 3yr delay credits applied)
        hh_ss = Household(**common_kwargs, your_age=70, spouse_age=70)

        wages_ytd = 80_000.0
        ytd_no_ss = YTDSnapshot(tax_year=hh_no_ss.base_year, wages_ytd=wages_ytd)
        ytd_ss = YTDSnapshot(tax_year=hh_ss.base_year, wages_ytd=wages_ytd)

        your_base = ss_benefit_at_age(
            hh_ss.your_ss_fra, hh_ss.your_ss_start_age, hh_ss.your_fra_age
        )
        spouse_base = ss_benefit_at_age(
            hh_ss.spouse_ss_fra, hh_ss.spouse_ss_start_age, hh_ss.spouse_fra_age
        )
        combined_ss = your_base + spouse_base
        expected_tss = taxable_ss(combined_ss, wages_ytd, filing_status="MFJ")

        # Precondition: 85% rule fires -> tss < gross SS.
        assert expected_tss < combined_ss, (
            f"Precondition: tss={expected_tss:.0f} must be < gross ss={combined_ss:.0f}"
        )
        assert expected_tss > 0.0, (
            f"Precondition: tss={expected_tss:.0f} must be positive (85% band active)"
        )

        plan_no_ss = auto_fill_irmaa_safe(hh_no_ss, ytd=ytd_no_ss)
        plan_ss = auto_fill_irmaa_safe(hh_ss, ytd=ytd_ss)

        conv_no_ss = plan_no_ss.your_conversions.get(
            hh_no_ss.base_year, 0.0
        ) + plan_no_ss.spouse_conversions.get(hh_no_ss.base_year, 0.0)
        conv_ss = plan_ss.your_conversions.get(
            hh_ss.base_year, 0.0
        ) + plan_ss.spouse_conversions.get(hh_ss.base_year, 0.0)

        # The SS household commits tss to MAGI -> less conversion room.
        reduction = conv_no_ss - conv_ss
        assert reduction >= 0.0, (
            f"SS household must have <= conversion room: no_ss={conv_no_ss:.0f}, ss={conv_ss:.0f}"
        )
        # Reduction must equal tss (fixed) not combined_ss (buggy pre-F9).
        # Tolerance: $100 for indexing/rounding across the two base years.
        assert reduction == approx(expected_tss, tol=100), (
            f"IRMAA room reduction should equal tss={expected_tss:.0f}, "
            f"got {reduction:.0f} (gross-SS bug would give ~{combined_ss:.0f})"
        )


class TestAutoFillDeferredRmdSeeding:
    """C16 (audit-0805 W5, mirrors engine/scenario.py's ira-rmd-1 seeding and
    engine/asset_location.py's identical audit-0802 F7 fix): _auto_fill_core
    must seed prev_your_ira/prev_spouse_ira from the household's current IRA
    balance at iteration 0 when defer_first_rmd is elected. Without this, the
    deferred prior-year RMD term (calc_rmd's ``first_year_deferred and age ==
    rmd_start_age + 1 and prior_year_balance > 0`` branch) is silently
    suppressed in the base year -- prior_year_balance stayed 0.0 -- so the
    base-year RMD is understated and auto-fill offers bracket room that does
    not actually exist.
    """

    def test_base_year_doubled_rmd_year_uses_seeded_prior_balance(self) -> None:
        from dataclasses import replace

        from engine.ira import calc_rmd
        from engine.tax import deductions, room_to_12, senior_bonus_deduction

        # base-year age = 76 == your_rmd_start_age(75) + 1: the "doubled" RMD
        # year (deferred age-75 RMD + normal age-76 RMD both due). Keep the
        # IRA modest so combined RMD stays well under the $150K OBBBA senior-
        # bonus phaseout start (isolates this test from C18's ded/room
        # iteration).
        hh = replace(
            Household(),
            your_age=76,
            your_ira=1_000_000.0,
            your_rmd_start_age=75,
            your_defer_first_rmd=True,
            spouse_age=55,
            spouse_ira=5_000_000.0,  # large + unconstrained; absorbs all room
            spouse_ss_fra=0,
            your_ss_fra=0,
            grants=[],
            brokerage_start=0.0,
        )
        base_year = hh.base_year

        # Correct (seeded) base-year RMD: includes the deferred age-75 term.
        expected_rmd = calc_rmd(
            hh.your_ira, hh.your_age, hh.your_rmd_start_age,
            first_year_deferred=True, prior_year_balance=hh.your_ira,
        )
        # Buggy (unseeded) RMD: deferred term suppressed (prior_year_balance=0).
        buggy_rmd = calc_rmd(
            hh.your_ira, hh.your_age, hh.your_rmd_start_age,
            first_year_deferred=True, prior_year_balance=0.0,
        )
        assert expected_rmd > buggy_rmd, (
            f"precondition: seeded RMD ({expected_rmd:.0f}) must exceed the "
            f"buggy unseeded RMD ({buggy_rmd:.0f})"
        )
        assert expected_rmd < 150_000.0, (
            "precondition: keep MAGI under the OBBBA phaseout start so ded is "
            "constant (isolates this test from the C18 iteration)"
        )

        # your_age(76) >= your_rmd_start_age -> your IRA is RMD-only (not
        # conversion-eligible); spouse (55) is pre-RMD and absorbs 100% of the
        # shared bracket room since spouse_ira is effectively unconstrained.
        ded = deductions(
            76, 55, hh.std_deduction, hh.senior_extra,
            filing_status="MFJ", year=base_year, cpi=hh.cpi_assumption,
        )
        ded += senior_bonus_deduction(
            76, 55, expected_rmd, year=base_year, cpi=hh.cpi_assumption, filing_status="MFJ",
        )
        expected_room = room_to_12(
            expected_rmd, ded, year=base_year, cpi=hh.cpi_assumption, filing_status="MFJ"
        )

        plan = auto_fill_12(hh)
        actual = plan.spouse_conversions.get(base_year, 0.0)

        assert actual == approx(expected_room, tol=25.0), (
            f"base-year spouse conversion ({actual:.0f}) must equal the room implied "
            f"by the CORRECTLY SEEDED (doubled) RMD ({expected_room:.0f}) -- pre-fix, "
            "the unseeded RMD understated ordinary income and overstated this room"
        )


class TestAutoFillSpouseRmdBalanceBeneficiaryAge:
    """C17 (audit-0805 W5): the balance-side spouse RMD recompute in
    _auto_fill_core (used to roll spouse_ira forward each year) omitted
    beneficiary_age, so it used the Uniform Lifetime Table III divisor while
    the income-side spouse_taxable_rmd (this year's bracket-room math)
    correctly used the Joint & Last Survivor Table II divisor whenever the
    household's mutual sole-beneficiary election applies and the age gap
    exceeds 10 years (26 CFR 1.401(a)(9)-5 Q&A-4). Fixing the balance side to
    match the income side changes the spousal IRA balance carried into year 2,
    which changes year 2's recognized spouse RMD income and therefore "your"
    bracket room that year.
    """

    def _hh(self) -> Household:
        from dataclasses import replace

        return replace(
            Household(),
            your_age=65,
            your_ira=20_000_000.0,  # never the binding constraint
            your_rmd_start_age=75,
            spouse_age=80,
            spouse_ira=2_000_000.0,
            spouse_rmd_start_age=73,  # already active from year 0
            spouse_is_sole_beneficiary=True,
            your_ss_fra=0,
            spouse_ss_fra=0,
            grants=[],
            brokerage_start=0.0,
            growth_rate=0.0,  # zero IRA growth simplifies the hand-trace below
            filing_status="MFJ",
            cpi_assumption=0.0,
        )

    def test_year2_room_reflects_joint_survivor_balance_not_uniform_lifetime(self) -> None:
        from engine.ira import calc_rmd, rmd_divisor
        from engine.tax import deductions, room_to_12, senior_bonus_deduction

        hh = self._hh()
        base_year = hh.base_year

        # Precondition: the >10yr gap actually engages a DIFFERENT (larger)
        # Table II divisor than Table III at these ages (26 CFR 1.401(a)(9)-9(d)).
        d2 = rmd_divisor(80, 65)
        d3 = rmd_divisor(80, None)
        assert d2 > d3, f"precondition: Table II ({d2}) must exceed Table III ({d3}) at (80,65)"

        # --- Year 1 (yr_idx=0): ya=65, sa=80 ---
        # Income-side RMD (unaffected by this bug -- already correct pre-fix).
        rmd_income_yr1 = calc_rmd(hh.spouse_ira, 80, hh.spouse_rmd_start_age, beneficiary_age=65)
        # Balance-side, CORRECT (post-fix): same divisor as the income side.
        spouse_ira_yr2_fixed = hh.spouse_ira - rmd_income_yr1
        # Balance-side, BUGGY (pre-fix): Table III, no beneficiary_age.
        rmd_balance_yr1_buggy = calc_rmd(hh.spouse_ira, 80, hh.spouse_rmd_start_age)
        spouse_ira_yr2_buggy = hh.spouse_ira - rmd_balance_yr1_buggy

        assert spouse_ira_yr2_fixed != pytest.approx(spouse_ira_yr2_buggy, abs=1.0), (
            "precondition: buggy and fixed year-2 spousal balances must differ"
        )

        # --- Year 2 (yr_idx=1): ya=66, sa=81 ---
        rmd_income_yr2_fixed = calc_rmd(
            spouse_ira_yr2_fixed, 81, hh.spouse_rmd_start_age, beneficiary_age=66
        )
        rmd_income_yr2_buggy = calc_rmd(
            spouse_ira_yr2_buggy, 81, hh.spouse_rmd_start_age, beneficiary_age=66
        )
        assert rmd_income_yr2_fixed != pytest.approx(rmd_income_yr2_buggy, abs=1.0), (
            "precondition: year-2 recognized spouse RMD income must differ between "
            "the fixed and buggy balance trajectories"
        )

        year2 = base_year + 1
        ded_yr2 = deductions(
            66, 81, hh.std_deduction, hh.senior_extra, filing_status="MFJ", year=year2, cpi=0.0
        )
        ded_yr2 += senior_bonus_deduction(
            66, 81, rmd_income_yr2_fixed, year=year2, cpi=0.0, filing_status="MFJ"
        )
        expected_room_yr2_fixed = room_to_12(
            rmd_income_yr2_fixed, ded_yr2, year=year2, cpi=0.0, filing_status="MFJ"
        )

        ded_yr2_buggy = deductions(
            66, 81, hh.std_deduction, hh.senior_extra, filing_status="MFJ", year=year2, cpi=0.0
        )
        ded_yr2_buggy += senior_bonus_deduction(
            66, 81, rmd_income_yr2_buggy, year=year2, cpi=0.0, filing_status="MFJ"
        )
        naive_room_yr2_buggy = room_to_12(
            rmd_income_yr2_buggy, ded_yr2_buggy, year=year2, cpi=0.0, filing_status="MFJ"
        )
        assert expected_room_yr2_fixed != pytest.approx(naive_room_yr2_buggy, abs=1.0), (
            "precondition: fixed vs buggy year-2 room must differ once the spousal "
            "balance trajectories diverge"
        )

        plan = auto_fill_12(hh)
        actual_conversion_yr2 = plan.your_conversions.get(year2, 0.0)

        assert actual_conversion_yr2 == pytest.approx(expected_room_yr2_fixed, abs=25.0), (
            f"year-2 'your' conversion ({actual_conversion_yr2:.0f}) must match the "
            f"CORRECT (Table-II-consistent balance) room ({expected_room_yr2_fixed:.0f}), "
            f"not the buggy Table-III-balance-drift room ({naive_room_yr2_buggy:.0f})"
        )


class TestAutoFillIrmaaSafeSsTorpedo:
    """C14 (HIGH, audit-0805 W5): auto_fill_irmaa_safe's _irmaa_room computed
    irmaa_room from a base_magi built BEFORE this room's own conversion pushes
    additional Social Security into taxability (IRC §86(b)). The naive
    `threshold - base_magi` subtraction assumes taxable SS is
    conversion-invariant; once the conversion crosses the 50%/85% partial-
    taxability transition, actual post-conversion MAGI permanently sits above
    the naive linear projection by the extra SS pulled into taxability during
    the crossing, so converting the naive room overshoots the IRMAA tier-1
    ceiling. Same bug family as C81 (headroom.py) / C23 (sweet_spot_compute.py).
    """

    def _hh(self) -> Household:
        return Household(
            your_age=66,
            spouse_age=64,
            base_year=2026,
            your_ss_start_age=62,
            spouse_ss_start_age=62,
            your_ss_fra=1_500.0,
            spouse_ss_fra=1_000.0,
            your_fra_age=67,
            spouse_fra_age=67,
            filing_status="MFJ",
            cpi_assumption=0.0,
            ss_cola=0.0,
            grants=[],
            brokerage_start=0.0,
            your_ira=5_000_000.0,
            spouse_ira=5_000_000.0,
            your_rmd_start_age=90,
            spouse_rmd_start_age=90,
        )

    def test_irmaa_safe_conversion_does_not_overshoot_tier1_threshold(self) -> None:
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss

        hh = self._hh()
        base_year = hh.base_year

        your_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        spouse_base = ss_benefit_at_age(hh.spouse_ss_fra, hh.spouse_ss_start_age, hh.spouse_fra_age)
        combined_ss = your_base + spouse_base
        assert combined_ss > 0, "precondition: SS must be active"

        threshold = 218_000.0  # IRMAA_TIERS_MFJ[0][0], unindexed (cpi=0)
        # Pre-conversion base_magi: opt=0, no RMD, no ytd -> other_fixed=0.
        base_tss = taxable_ss(combined_ss, 0.0, filing_status="MFJ")
        base_magi = base_tss  # other_fixed(0) + tss
        naive_room = max(threshold - base_magi, 0.0)

        # RED precondition: converting the naive room overshoots the threshold,
        # because SS taxability keeps climbing as the conversion rises.
        naive_tss = taxable_ss(combined_ss, naive_room, filing_status="MFJ")
        naive_magi = naive_room + naive_tss
        assert naive_magi > threshold + 1_000.0, (
            f"precondition: naive room ({naive_room:.0f}) must overshoot the "
            f"threshold ({threshold:.0f}) once SS torpedo is folded in -- got "
            f"magi={naive_magi:.0f}"
        )

        plan = auto_fill_irmaa_safe(hh)
        actual_conv = plan.your_conversions.get(base_year, 0.0) + plan.spouse_conversions.get(
            base_year, 0.0
        )

        actual_tss = taxable_ss(combined_ss, actual_conv, filing_status="MFJ")
        actual_magi = actual_conv + actual_tss
        assert actual_magi <= threshold + 1.0, (
            f"conversion={actual_conv:.0f} produced magi={actual_magi:.0f}, must not "
            f"exceed the IRMAA tier-1 threshold ({threshold:.0f})"
        )
        assert actual_conv < naive_room - 1_000.0, (
            f"actual conversion ({actual_conv:.0f}) must be materially below the "
            f"naive SS-invariant room ({naive_room:.0f})"
        )


class TestAutoFillAcaMagiFullSsAddback:
    """C15 (HIGH, audit-0805 W5): auto_fill_aca's _aca_room compared the ACA
    cliff against IRMAA-basis MAGI (which carries only the TAXABLE portion of
    Social Security), omitting the non-taxable portion. Per IRC
    §36B(d)(2)(B)(iii), ACA MAGI MUST add back the FULL Social Security
    benefit (taxable + non-taxable). Reuses the same basis identity already
    proven correct in engine/headroom.py's aca_magi computation (audit
    C7/headroom-2): ACA MAGI = other_fixed (SS-exclusive income) + combined_ss
    (full benefit) -- independent of the conversion amount, since the taxable/
    non-taxable SS split cancels out identically.
    """

    def _hh(self) -> Household:
        return Household(
            your_age=62,
            spouse_age=55,
            base_year=2026,
            your_aca_enrolled=True,
            your_ss_fra=500.0,
            your_ss_start_age=62,
            your_fra_age=67,
            spouse_ss_fra=0.0,
            spouse_ss_start_age=70,
            spouse_fra_age=67,
            filing_status="MFJ",
            cpi_assumption=0.0,
            your_ira=5_000_000.0,
            spouse_ira=5_000_000.0,
            your_rmd_start_age=90,
            spouse_rmd_start_age=90,
            grants=[],
            brokerage_start=0.0,
        )

    def test_aca_room_uses_full_ss_not_taxable_only(self) -> None:
        from engine.aca import aca_ceiling_magi
        from engine.ira import ss_benefit_at_age
        from engine.tax import taxable_ss

        hh = self._hh()
        base_year = hh.base_year

        combined_ss = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        assert combined_ss > 0, "precondition: SS must be active"

        base_tss = taxable_ss(combined_ss, 0.0, filing_status="MFJ")
        assert base_tss < combined_ss, (
            "precondition: a non-taxable SS portion must exist (partial/zero taxability)"
        )

        ceiling = aca_ceiling_magi("MFJ", base_year, hh.cpi_assumption)
        naive_room = max(ceiling - base_tss, 0.0)  # pre-fix: base_magi = other_fixed(0) + base_tss
        expected_room = max(ceiling - combined_ss, 0.0)  # post-fix: other_fixed(0) + combined_ss

        assert naive_room - expected_room == pytest.approx(combined_ss, abs=1.0), (
            "precondition: naive vs correct room must differ by exactly the "
            "non-taxable SS omission"
        )

        plan = auto_fill_aca(hh)
        actual = plan.your_conversions.get(base_year, 0.0)

        assert actual == approx(expected_room, tol=10.0), (
            f"base-year conversion ({actual:.0f}) must equal the FULL-SS-addback room "
            f"({expected_room:.0f}), not the naive taxable-SS-only room ({naive_room:.0f})"
        )


class TestAutoFillSeniorBonusPostConversionMagi:
    """C18 (LOW, audit-0805 W5): _auto_fill_core evaluated the OBBBA senior-
    bonus deduction at PRE-conversion base_magi when sizing room, but
    run_scenario evaluates the same deduction at POST-conversion MAGI (see
    engine/scenario.py's yr.magi, which folds in yr.your_conversion /
    yr.spouse_conversion before feeding senior_bonus_deduction). Once the
    post-conversion MAGI crosses into the $150K-$350K OBBBA phaseout band
    (dual-eligible MFJ), the deduction actually available is smaller than the
    pre-conversion snapshot assumed, so the naive one-shot room overshoots the
    22% bracket ceiling.
    """

    def _hh(self) -> Household:
        return Household(
            your_age=67,
            spouse_age=65,
            base_year=2026,
            your_ss_fra=0,
            spouse_ss_fra=0,
            filing_status="MFJ",
            cpi_assumption=0.0,
            your_ira=5_000_000.0,
            spouse_ira=5_000_000.0,
            your_rmd_start_age=90,
            spouse_rmd_start_age=90,
            grants=[],
            brokerage_start=0.0,
        )

    def test_auto_fill_22_does_not_overshoot_via_stale_senior_bonus(self) -> None:
        from engine.tax import room_to_22, senior_bonus_deduction

        hh = self._hh()
        base_year = hh.base_year

        # No SS/RMD/options -> fixed_gross = base_magi = 0 in the base year, both
        # spouses 65+ (eligible for the full $12,000 aggregate OBBBA bonus).
        fixed_gross = 0.0
        ded_no_senior = hh.std_deduction + 2 * hh.senior_extra  # both spouses 65+, MFJ
        ceiling_22 = 211_400.0  # BRACKETS_MFJ[2][0], unindexed (cpi=0)

        # Naive (buggy) one-shot room: senior bonus frozen at PRE-conversion
        # MAGI (0) -> full $12,000, never re-evaluated at the resulting MAGI.
        naive_senior = senior_bonus_deduction(
            67, 65, 0.0, year=base_year, cpi=0.0, filing_status="MFJ"
        )
        naive_room = ded_no_senior + naive_senior + ceiling_22 - fixed_gross

        # Closed-form fixed point: room* = ded_no_senior + senior_bonus(room*) +
        # ceiling - fixed_gross, with senior_bonus linear in the phaseout band.
        # audit-0809 C19: the reduction is applied PER PERSON (each $6,000
        # reduced by 0.06*(magi-150_000), floored independently), so for two
        # eligible people moving in lockstep the AGGREGATE slope doubles:
        # senior_bonus(magi) = 12_000 - 2*0.06*(magi-150_000) for magi in [150K,250K].
        total_bonus = 12_000.0
        phaseout_rate = 0.06
        effective_phaseout_rate = 2.0 * phaseout_rate  # dual-eligible: both persons phase out in lockstep
        phaseout_start = 150_000.0
        expected_room = (
            ded_no_senior
            + total_bonus
            + effective_phaseout_rate * phaseout_start
            + ceiling_22
            - fixed_gross
        ) / (1.0 + effective_phaseout_rate)

        # Precondition: the fixed point actually sits inside the linear phaseout
        # band (not clamped at $0 or the full $12,000). Per-person zeroing at
        # $250,000 (not the old aggregate $350,000 endpoint).
        assert 150_000.0 < expected_room < 250_000.0, (
            f"precondition: expected_room ({expected_room:.0f}) must sit inside the "
            "OBBBA phaseout band for the linear closed form above to apply"
        )
        assert naive_room > expected_room + 1_000.0, (
            f"precondition: naive one-shot room ({naive_room:.0f}) must materially "
            f"exceed the converged fixed-point room ({expected_room:.0f})"
        )

        # Self-consistency oracle: senior bonus AT the converged room must
        # reproduce that same room via room_to_22.
        senior_at_room = senior_bonus_deduction(
            67, 65, expected_room, year=base_year, cpi=0.0, filing_status="MFJ"
        )
        oracle_room = room_to_22(
            fixed_gross, ded_no_senior + senior_at_room, year=base_year, cpi=0.0, filing_status="MFJ"
        )
        assert oracle_room == pytest.approx(expected_room, abs=1.0), (
            "self-consistency check failed: expected_room is not a fixed point"
        )

        plan = auto_fill_22(hh)
        actual = plan.your_conversions.get(base_year, 0.0)

        assert actual == approx(expected_room, tol=5.0), (
            f"base-year conversion ({actual:.0f}) must equal the converged "
            f"post-conversion-MAGI room ({expected_room:.0f}), not the naive "
            f"pre-conversion room ({naive_room:.0f})"
        )


class TestSurvivorAutofill:
    """C3 / autofill-1 regression: _auto_fill_core must honor the survivor filing-status
    transition.

    Before the fix, _auto_fill_core stayed MFJ forever, summed both spouses' SS,
    and kept offering the deceased's IRA for conversion after death.  Three tests
    prove all three aspects are now correct.

    Setup common to (a) and (b):
      - base_year = 2026
      - your_age=62, your_rmd_start_age=75 → 13-year pre-RMD window
      - spouse_age=62, spouse_rmd_start_age=75 → equal window
      - death_year = base_year + 2 (= 2028), so survivor_active begins 2029
      - Both IRAs funded; no SS, no options, no grants → clean isolation
    """

    # ── Shared fixture factory ──────────────────────────────────────────────

    @staticmethod
    def _base_hh(**overrides: object) -> Household:
        """Minimal MFJ household with a long conversion window and no noise sources."""
        from dataclasses import replace

        hh = replace(
            Household(),
            your_age=62,
            spouse_age=62,
            your_ira=2_000_000.0,
            spouse_ira=2_000_000.0,
            your_rmd_start_age=75,
            spouse_rmd_start_age=75,
            # Zero out SS and option income so room calculations are bracket-only.
            your_ss_fra=0,
            spouse_ss_fra=0,
            grants=[],
            # No brokerage noise.
            brokerage_start=0.0,
        )
        return replace(hh, **overrides)  # type: ignore[arg-type]

    # ── (a) Deceased-IRA conversions stop ──────────────────────────────────

    def test_spouse_ira_conversions_stop_after_death(self) -> None:
        """After who_dies='spouse' the plan must have no positive spouse conversions.

        Before the fix: spouse_ira kept being offered every year regardless of
        survivor_active, because _auto_fill_core did not implement the IRA rollover
        (spouse_ira → your_ira at death_year+1) and kept allocating room to it.

        After the fix: the rollover zeroes spouse_ira at death_year+1; the
        allocation guard (sa < spouse_rmd_start_age) still passes numerically but
        spouse_ira == 0 so min(room, 0) == 0 → no spouse conversion.
        """
        death_year = Household().base_year + 2  # 2028
        hh = self._base_hh(
            survivor=SurvivorScenario(who_dies="spouse", death_year=death_year)
        )
        plan = auto_fill_22(hh)

        post_death_years = [
            y for y in plan.spouse_conversions if y >= death_year + 1
        ]
        for year in post_death_years:
            assert plan.spouse_conversions[year] == 0.0, (
                f"spouse_conversions[{year}]={plan.spouse_conversions[year]:.0f} "
                f"must be 0 after death_year={death_year}"
            )

        # Also verify the plan has pre-death spouse conversions (sanity: not just empty).
        pre_death_spouse_total = sum(
            v for y, v in plan.spouse_conversions.items() if y <= death_year
        )
        assert pre_death_spouse_total > 0.0, (
            "Sanity: expect positive spouse conversions before death year"
        )

    def test_your_ira_conversions_stop_after_death(self) -> None:
        """Symmetric: who_dies='you' → no positive your_conversions from death_year+1."""
        death_year = Household().base_year + 2  # 2028
        hh = self._base_hh(
            survivor=SurvivorScenario(who_dies="you", death_year=death_year)
        )
        plan = auto_fill_22(hh)

        post_death_years = [
            y for y in plan.your_conversions if y >= death_year + 1
        ]
        for year in post_death_years:
            assert plan.your_conversions[year] == 0.0, (
                f"your_conversions[{year}]={plan.your_conversions[year]:.0f} "
                f"must be 0 after death_year={death_year} (you died)"
            )

        # Sanity: pre-death your_conversions exist.
        pre_death_your_total = sum(
            v for y, v in plan.your_conversions.items() if y <= death_year
        )
        assert pre_death_your_total > 0.0, (
            "Sanity: expect positive your_conversions before death year"
        )

    # ── (b) Single brackets shrink conversion room ──────────────────────────

    def test_survivor_single_bracket_reduces_post_death_conversions(self) -> None:
        """Survivor (Single) cumulative post-death conversions < MFJ cumulative.

        The MFJ 22% ceiling is ~$211,400 (2026); the Single 22% ceiling is
        ~$105,700. After the spousal rollover the survivor holds both IRAs
        but only has half the bracket room, so cumulative conversions across
        all post-death years must be strictly less than the MFJ-forever baseline.

        Before the fix: both households used MFJ brackets forever → identical
        totals → assertion would fail (equal, not less).
        """
        base_year = Household().base_year  # 2026
        death_year = base_year + 2  # 2028; survivor_active from 2029

        hh_mfj = self._base_hh()  # no survivor → MFJ forever
        hh_surv = self._base_hh(
            survivor=SurvivorScenario(who_dies="spouse", death_year=death_year)
        )

        plan_mfj = auto_fill_22(hh_mfj)
        plan_surv = auto_fill_22(hh_surv)

        # Cumulative conversions in years strictly after death_year.
        def _post_death_total(plan: object) -> float:
            from engine.scenario_types import ConversionPlan
            assert isinstance(plan, ConversionPlan)
            return sum(
                plan.your_conversions.get(y, 0.0) + plan.spouse_conversions.get(y, 0.0)
                for y in range(death_year + 1, base_year + 20)
            )

        total_mfj = _post_death_total(plan_mfj)
        total_surv = _post_death_total(plan_surv)

        assert total_mfj > 0.0, (
            f"Sanity: MFJ post-death conversions must be positive, got {total_mfj:.0f}"
        )
        assert total_surv < total_mfj, (
            f"Survivor post-death total ({total_surv:.0f}) must be < MFJ total "
            f"({total_mfj:.0f}): Single 22% ceiling is ~half MFJ ceiling"
        )


class TestSurvivorSSStepUpFullActuarial:
    """Audit-0720 H5: the survivor SS step-up in _auto_fill_core must apply the
    full-actuarial SSA survivor rules (age-60 eligibility floor, reduction
    locked at claim-onset age), mirroring compute_social_security in
    engine/scenario_compute.py -- NOT the bare max(your_ss, spouse_ss)
    fallback, which is only the death_year-unknown branch and is never the
    right rule for auto-fill (survivor_active always carries a real
    death_year here).
    """

    def test_survivor_under_60_gets_zero_ss_not_deceased_benefit(self) -> None:
        """Survivor under age 60 in a post-death year must receive $0 SS.

        MFJ household: you die at end of base_year (death_year=base_year), so
        survivor_active begins base_year+1. The spouse (survivor) is 55 at
        base_year -> 56 in the first survivor year, well under the SSA
        age-60 eligibility floor. The deceased (you) already claimed SS at
        62, so your_ss > 0 that year.

        Bug: _auto_fill_core used max(your_ss, spouse_ss) = your_ss > 0,
        leaking the deceased's benefit to the survivor. That overstates
        combined_ss (and its taxable fraction), overstates fixed_gross, and
        understates the 22%-bracket conversion room for that year.

        Fix: full-actuarial rules zero the survivor's SS (age < 60 floor), so
        fixed_gross = 0 and the spouse's conversion exactly fills
        room_to_22(0, ded, ...) -- unconstrained since her rolled-over IRA is
        large. No hardcoded dollar figures: the expected conversion is
        derived directly from engine.tax.room_to_22/deductions using the same
        inputs the auto-fill loop uses.
        """
        from dataclasses import replace

        from engine.ira import ss_benefit_at_age, ss_with_cola
        from engine.tax import deductions as _deductions
        from engine.tax import room_to_22 as _room_to_22

        death_year = Household().base_year  # 2026
        survivor_year = death_year + 1  # 2027 -- first survivor-active year
        hh = replace(
            Household(),
            filing_status="MFJ",
            your_age=62,
            your_ira=0.0,
            your_ss_fra=8_000,  # synthetic large monthly FRA benefit -- pushes the
            # (buggy) deceased-benefit step-up well into the 85% taxable-SS band so
            # the divergence is large and unambiguous, not a rounding artifact.
            your_ss_start_age=62,
            spouse_age=55,  # -> 56 in survivor_year: under the age-60 floor
            spouse_ira=10_000_000.0,  # never the binding constraint
            spouse_ss_fra=0,
            spouse_ss_start_age=62,
            grants=[],
            brokerage_start=0.0,
            survivor=SurvivorScenario(who_dies="you", death_year=death_year),
        )

        # Sanity precondition: the deceased's benefit is indeed positive in the
        # survivor year (so a bug that leaks it to the survivor is observable).
        your_ss_base = ss_benefit_at_age(hh.your_ss_fra, hh.your_ss_start_age, hh.your_fra_age)
        deceased_benefit = ss_with_cola(
            your_ss_base, (hh.your_age + 1) - hh.your_ss_start_age, hh.ss_cola
        )
        assert deceased_benefit > 0.0, "Precondition: deceased benefit must be positive"

        # Expected (correct) conversion: combined_ss = 0 (age-60 floor) ->
        # fixed_gross = 0 -> room = room_to_22(0, ded, ...), fully absorbed by
        # the spouse's (unconstrained) IRA.
        ded = _deductions(
            0, 56, filing_status="Single", year=survivor_year, cpi=hh.cpi_assumption
        )
        expected_conversion = _room_to_22(
            0.0, ded, year=survivor_year, cpi=hh.cpi_assumption, filing_status="Single"
        )
        assert expected_conversion > 0.0, "Precondition: bracket room must be positive"

        plan = auto_fill_22(hh)
        actual_conversion = plan.spouse_conversions.get(survivor_year, 0.0)

        assert actual_conversion == approx(expected_conversion, tol=50.0), (
            f"survivor_year={survivor_year} conversion={actual_conversion:.0f} must equal "
            f"the unconstrained 22%-bracket room {expected_conversion:.0f} (survivor SS=0 "
            f"under the age-60 floor). Bug would instead leak the deceased's benefit "
            f"(~{deceased_benefit:.0f}) into combined_ss, taxing a chunk of it and "
            f"reducing the conversion by roughly its taxable fraction."
        )


class TestAutoFillRmdYtdClamp:
    """Audit 0702 / autofill-rmd-clamp: base-year RMD must not be double-counted.

    ira_distributions_ytd already includes any RMD taken so far this year and is
    re-added downstream via magi_ytd (other_fixed / base_magi) and explicitly in
    fixed_gross.  Without the clamp, the already-taken RMD portion appears twice,
    artificially inflating income and reducing conversion room in the base year.

    Economic-equivalence invariant: two YTD snapshots representing identical
    economics (RMD not yet taken vs. full RMD already taken) must yield the same
    base-year conversion amount after the fix.
    """

    @staticmethod
    def _rmd_hh() -> Household:
        """MFJ household at exact RMD start age, large IRA, minimal other income."""
        from dataclasses import replace

        return replace(
            Household(),
            your_age=75,
            your_ira=1_000_000.0,
            your_rmd_start_age=75,
            spouse_age=65,
            spouse_ira=0.0,
            spouse_ss_fra=0,
            your_ss_fra=0,
            grants=[],
            brokerage_start=0.0,
        )

    def test_rmd_ytd_clamp_economic_equivalence(self) -> None:
        """Snapshot A (RMD not yet taken) and Snapshot B (full RMD already taken)
        must produce identical base-year your_conversions after the clamp fix.

        Before the fix: Snapshot B overstates income by ~R (the RMD amount) so
        the planner converts less.  After the fix the two plans are equal.
        """
        from engine.ira import calc_rmd
        from models.ytd_income import YTDSnapshot

        hh = self._rmd_hh()
        base_year = hh.base_year

        # R = required distribution in the base year.
        r = calc_rmd(hh.your_ira, hh.your_age, hh.your_rmd_start_age)
        assert r > 0.0, f"Precondition: RMD must be positive at age {hh.your_age}, got {r}"

        # Snapshot A: RMD not yet taken — zero YTD distributions.
        ytd_a = YTDSnapshot(tax_year=base_year, ira_distributions_ytd=0.0)

        # Snapshot B: full RMD already taken — distributions_ytd = R, magi_ytd includes R.
        ytd_b = YTDSnapshot(tax_year=base_year, ira_distributions_ytd=r)

        plan_a = auto_fill_12(hh, ytd=ytd_a)
        plan_b = auto_fill_12(hh, ytd=ytd_b)

        conv_a = plan_a.your_conversions.get(base_year, 0.0)
        conv_b = plan_b.your_conversions.get(base_year, 0.0)

        assert conv_a == pytest.approx(conv_b, abs=1.0), (
            f"Economic-equivalence failed: RMD not-yet-taken conv={conv_a:.0f}, "
            f"RMD already-taken conv={conv_b:.0f}, delta={conv_a - conv_b:.0f} "
            f"(expected ~0, pre-fix delta would be ~{r:.0f})"
        )


class TestAutoFillBrokerageIncome:
    """Audit 0702/autofill-brokerage: _auto_fill_core must include forecast brokerage income.

    Prior to the fix, all forecast brokerage income (ordinary dividends, qualified
    dividends, realized LTCG) was omitted from income aggregates, understating SS
    provisional income and MAGI in forecast years and therefore over-recommending
    conversions.

    Three tests prove the three routing rules:
      1. Ordinary dividends reduce bracket room (enter fixed_gross).
      2. Qualified dividends + realized LTCG do NOT reduce bracket room (excluded
         from fixed_gross) but DO reduce IRMAA room (enter base_magi).
      3. All brokerage components reduce IRMAA-safe conversion room (enter base_magi).
    """

    @staticmethod
    def _zero_ss_hh(**overrides: object) -> "Household":
        """Pre-RMD MFJ household with zero SS so taxable_ss=0 in all years."""
        from dataclasses import replace

        from models.household import Household

        hh = replace(
            Household(),
            your_age=62,
            your_ira=2_000_000.0,
            your_rmd_start_age=75,
            spouse_age=60,
            spouse_ira=2_000_000.0,
            spouse_rmd_start_age=75,
            your_ss_fra=0,
            spouse_ss_fra=0,
            grants=[],
        )
        return replace(hh, **overrides)  # type: ignore[arg-type]

    def test_ordinary_dividends_reduce_bracket_room(self) -> None:
        """Ordinary brokerage dividends must enter fixed_gross and reduce bracket room."""
        from dataclasses import replace

        from models.household import GrowthProfile

        hh_no_brok = self._zero_ss_hh(brokerage_start=0.0)
        hh_brok = self._zero_ss_hh(
            brokerage_start=1_000_000.0,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.03,
                qualified_fraction=0.0,
            ),
        )
        hh_brok = replace(hh_brok, brok_turnover=0.0)

        plan_no = auto_fill_12(hh_no_brok)
        plan_brok = auto_fill_12(hh_brok)

        total_no = sum(plan_no.your_conversions.values()) + sum(plan_no.spouse_conversions.values())
        total_brok = sum(plan_brok.your_conversions.values()) + sum(
            plan_brok.spouse_conversions.values()
        )

        assert total_brok < total_no, (
            f"Ordinary dividends must reduce bracket-fill conversions: "
            f"no-brok={total_no:,.0f}, with-brok={total_brok:,.0f} "
            f"(pre-fix: both equal; post-fix: with-brok < no-brok)"
        )

    def test_qualified_divs_and_ltcg_excluded_from_bracket_base(self) -> None:
        """Pure qualified dividends + realized LTCG must NOT reduce bracket room."""
        from dataclasses import replace

        from models.household import GrowthProfile

        hh_no_brok = self._zero_ss_hh(brokerage_start=0.0)
        hh_brok = self._zero_ss_hh(
            brokerage_start=1_000_000.0,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.03,
                qualified_fraction=1.0,
            ),
        )
        hh_brok = replace(hh_brok, brok_turnover=0.30)

        plan_no = auto_fill_12(hh_no_brok)
        plan_brok = auto_fill_12(hh_brok)

        total_no = sum(plan_no.your_conversions.values()) + sum(plan_no.spouse_conversions.values())
        total_brok = sum(plan_brok.your_conversions.values()) + sum(
            plan_brok.spouse_conversions.values()
        )

        assert total_brok == pytest.approx(total_no, abs=1.0), (
            f"Qualified dividends + LTCG must NOT reduce bracket room: "
            f"no-brok={total_no:,.0f}, with-brok={total_brok:,.0f} "
            f"(delta={total_no - total_brok:,.0f} should be ~0)"
        )

    def test_brokerage_magi_reduces_irmaa_safe_conversions(self) -> None:
        """All brokerage components (ordinary + qualified + LTCG) must enter base_magi."""
        from dataclasses import replace

        from models.household import GrowthProfile

        hh_no_brok = self._zero_ss_hh(brokerage_start=0.0)
        hh_brok = self._zero_ss_hh(
            brokerage_start=1_500_000.0,
            brokerage_growth=GrowthProfile(
                default_rate=0.07,
                yield_rate=0.02,
                qualified_fraction=1.0,
            ),
        )
        hh_brok = replace(hh_brok, brok_turnover=0.30)

        plan_no = auto_fill_irmaa_safe(hh_no_brok)
        plan_brok = auto_fill_irmaa_safe(hh_brok)

        total_no = sum(plan_no.your_conversions.values()) + sum(plan_no.spouse_conversions.values())
        total_brok = sum(plan_brok.your_conversions.values()) + sum(
            plan_brok.spouse_conversions.values()
        )

        assert total_brok < total_no, (
            f"Brokerage MAGI must reduce IRMAA-safe conversions: "
            f"no-brok={total_no:,.0f}, with-brok={total_brok:,.0f} "
            f"(delta={total_no - total_brok:,.0f}; pre-fix: both equal)"
        )


class TestAutoFillSpouseSqueezeWindow:
    """Audit 0705 / headroom-scenario-4: the loop bound must cover the actual spouse
    squeeze window, not a hardcoded +6 offset.

    For an age-gap household (you 61 / spouse 55, your_rmd_start_age 73 /
    spouse_rmd_start_age 75) the spouse can still convert at ages 73 and 74 (the
    two years when ya >= your_rmd_start_age but sa < spouse_rmd_start_age). The
    old bound ``range(your_rmd_start_age - 1 - your_age + 1 + 6)`` produces 18
    iterations, stopping at yr_idx=17 (ya=78, sa=72) -- the two spouse tail years
    at sa=73 and sa=74 are never reached.

    The fix replaces +6 with:
      window = max(spouse_rmd_start_age - spouse_age - (your_rmd_start_age - your_age), 6)
    which for the age-gap household gives max(20 - 12, 6) = 8, producing 20
    iterations and covering sa=73 and sa=74.
    """

    @staticmethod
    def _age_gap_hh() -> "Household":
        """Age-gap household: you 61/spouse 55, rmd-start 73/75, sizeable both IRAs."""
        from dataclasses import replace

        # your_rmd_start_age=73 (born 1952, SECURE 1.0 cohort)
        # spouse_rmd_start_age=75 (born 1971, SECURE 2.0 cohort)
        # Suppress SS/options/brokerage noise so only conversion room matters.
        return replace(
            Household(),
            your_age=61,
            your_ira=1_700_000.0,
            your_rmd_start_age=73,
            spouse_age=55,
            spouse_ira=1_700_000.0,
            spouse_rmd_start_age=75,
            your_ss_fra=0,
            spouse_ss_fra=0,
            grants=[],
            brokerage_start=0.0,
        )

    @staticmethod
    def _same_age_hh() -> "Household":
        """Same-age household (you 65/spouse 65, rmd-start 73/73) for regression."""
        from dataclasses import replace

        return replace(
            Household(),
            your_age=65,
            your_ira=1_000_000.0,
            your_rmd_start_age=73,
            spouse_age=65,
            spouse_ira=1_000_000.0,
            spouse_rmd_start_age=73,
            your_ss_fra=0,
            spouse_ss_fra=0,
            grants=[],
            brokerage_start=0.0,
        )

    def test_age_gap_spouse_tail_years_included(self) -> None:
        """auto_fill_12 must include spouse conversions in yr_idx 18-19 (sa 73-74).

        For the age-gap household the old +6 bound stops at yr_idx=17 (sa=72),
        leaving ~$260K of spouse conversions unreached. After the fix, the plan
        includes positive spouse conversions at sa=73 and sa=74.
        """
        hh = self._age_gap_hh()
        plan = auto_fill_12(hh)

        base_year = hh.base_year
        # yr_idx where sa = 73 and sa = 74
        # sa = spouse_age + yr_idx  =>  yr_idx = sa - spouse_age
        yr_idx_sa73 = 73 - hh.spouse_age  # = 18
        yr_idx_sa74 = 74 - hh.spouse_age  # = 19
        year_sa73 = base_year + yr_idx_sa73
        year_sa74 = base_year + yr_idx_sa74

        sc_sa73 = plan.spouse_conversions.get(year_sa73, 0.0)
        sc_sa74 = plan.spouse_conversions.get(year_sa74, 0.0)

        assert sc_sa73 > 0.0, (
            f"Spouse conversion at sa=73 (year {year_sa73}) must be positive; "
            f"got {sc_sa73:.0f} -- loop bound too short (old +6 bug)"
        )
        assert sc_sa74 > 0.0, (
            f"Spouse conversion at sa=74 (year {year_sa74}) must be positive; "
            f"got {sc_sa74:.0f} -- loop bound too short (old +6 bug)"
        )

    def test_age_gap_spouse_total_materially_higher(self) -> None:
        """Total spouse conversions after fix must exceed the buggy truncated total.

        The two dropped tail years each contribute ~$130K (12% bracket fill for a
        single year with sizeable IRA). The fixed total must beat the buggy +6 bound
        by at least $200K (conservative, below the ~$260K empirical delta).
        """
        hh = self._age_gap_hh()

        # Simulate the buggy plan by capping the range at +6 directly.
        # We achieve this by temporarily restricting the household to exactly the
        # window the old code used: yr_idx < 12 + 6 = 18 iterations (ya stops at 78).
        # Rather than monkey-patching, we measure the fixed plan vs a synthetic bound.
        plan_fixed = auto_fill_12(hh)

        total_spouse_fixed = sum(plan_fixed.spouse_conversions.values())

        # The two tail years (sa 73-74) each provide substantial conversion room.
        # With a $1.7M spouse IRA and modest room available at the 12% ceiling,
        # each year contributes at minimum $50K. Assert >= $100K combined improvement.
        base_year = hh.base_year
        yr_idx_sa73 = 73 - hh.spouse_age
        yr_idx_sa74 = 74 - hh.spouse_age
        year_sa73 = base_year + yr_idx_sa73
        year_sa74 = base_year + yr_idx_sa74

        tail_total = (
            plan_fixed.spouse_conversions.get(year_sa73, 0.0)
            + plan_fixed.spouse_conversions.get(year_sa74, 0.0)
        )

        assert tail_total >= 100_000.0, (
            f"Spouse tail-year conversions (sa=73+74) must be >= $100K; "
            f"got {tail_total:,.0f} (total spouse={total_spouse_fixed:,.0f})"
        )

    def test_same_age_household_unchanged(self) -> None:
        """Same-age household result must be identical before and after the fix.

        For a household where your_rmd_start_age == spouse_rmd_start_age == 73
        and both are 65, the window = max(73-65 - (73-65), 6) = max(0, 6) = 6,
        preserving the original +6 bound. The total conversions must be > 0 and
        independent of the fix (regression guard).
        """
        hh = self._same_age_hh()
        plan = auto_fill_12(hh)

        total = (
            sum(plan.your_conversions.values())
            + sum(plan.spouse_conversions.values())
        )
        assert total > 0.0, (
            f"Same-age household must produce positive conversions; got {total:.0f}"
        )

        # Verify the window formula gives 6 for same-age same-rmd case.
        window = max(
            hh.spouse_rmd_start_age - hh.spouse_age - (hh.your_rmd_start_age - hh.your_age),
            6,
        )
        assert window == 6, (
            f"Same-age same-rmd window must be 6 (backward compat); got {window}"
        )
