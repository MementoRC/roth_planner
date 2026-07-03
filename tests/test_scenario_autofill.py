"""Tests for engine.scenario auto-fill MAGI/SS regression (F9) and autofill taxable-SS base_magi."""

import pytest

from engine.scenario_autofill import auto_fill_12, auto_fill_22, auto_fill_irmaa_safe
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
