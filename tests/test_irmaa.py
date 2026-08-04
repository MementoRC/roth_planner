"""Tests for engine.irmaa — Medicare premium surcharge tiers."""

import pytest

from engine.irmaa import (
    IRMAA_TIERS_MFJ,
    IRMAA_TIERS_SINGLE,
    MEDICAL_INFLATION,
    irmaa_next_threshold,
    irmaa_surcharge,
)
from engine.scenario import (
    ConversionPlan,
    run_scenario,
)
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestIRMAA:
    def test_below_threshold(self):
        assert irmaa_surcharge(200_000) == 0

    def test_above_tier1(self):
        assert irmaa_surcharge(220_000) > 0

    def test_room_to_next(self):
        assert irmaa_next_threshold(200_000) == approx(18_000)

    def test_part_b_base_default_unchanged(self):
        """Explicit default (202.90/mo) produces the same surcharge as the module constant."""
        magi = 220_000
        assert irmaa_surcharge(magi) == irmaa_surcharge(magi, base_part_b=202.90 * 12)

    def test_part_b_base_higher_reduces_surcharge(self):
        """Higher base_part_b → smaller surcharge: tier premium - higher_base < tier premium - lower_base."""
        magi = 220_000  # above Tier 1 threshold
        default_surcharge = irmaa_surcharge(magi)
        higher_base_surcharge = irmaa_surcharge(magi, base_part_b=300.0 * 12)
        assert higher_base_surcharge < default_surcharge

    def test_household_field_wires_through_scenario(self):
        """medicare_part_b_base_monthly on Household reaches irmaa_for_year via run_scenario.

        Use a large conversion at age 63 so MAGI exceeds the $218K Tier 1 threshold.
        Both spouses are 63, so the 2-year lookback puts them on Medicare at 65.
        With default base ($202.90/mo) a positive surcharge is expected.
        With a raised base ($300/mo) the per-tier delta shrinks, so total IRMAA is lower.
        """
        hh_default = Household(your_age=63, spouse_age=63)
        hh_high_base = Household(your_age=63, spouse_age=63, medicare_part_b_base_monthly=300.0)
        # Conversion large enough to push MAGI above Tier 1 ($218K)
        plan = ConversionPlan(your_conversions={2026: 250_000})
        r_default = run_scenario(hh_default, plan, end_age=68)
        r_high = run_scenario(hh_high_base, plan, end_age=68)
        irmaa_default = sum(yr.irmaa_cost for yr in r_default.years)
        irmaa_high = sum(yr.irmaa_cost for yr in r_high.years)
        assert irmaa_default > 0, "Sanity: default base must trigger IRMAA"
        assert irmaa_high < irmaa_default

    # --- PR5: prior_year_magi anchor + proper temporal accounting ---

    def test_irmaa_default_year_0_unchanged_from_old_engine(self):
        """With no prior_year_magi, year-0 IRMAA falls back to yr.magi (same as pre-PR5).

        The fallback branch is reached because income_year = base_year - 2 is neither
        in prior_year_magi nor in magi_history (which only accumulates during the loop).

        At age 63 (Medicare year) the income-year age is 61; +2 → 63 < 65, so IRMAA = 0
        even with high MAGI.  IRMAA only applies starting Medicare year when income-year
        age >= 63 (i.e., ya >= 65 in the projection year).
        """
        from engine.irmaa import irmaa_for_year

        hh = Household(your_age=63, spouse_age=63)
        plan = ConversionPlan(your_conversions={2026: 250_000})
        result = run_scenario(hh, plan, end_age=66)
        yr0 = result.years[0]

        # scenario passes income-year ages (ya - 2); irmaa_for_year adds +2 internally
        expected_cost, _ = irmaa_for_year(
            yr0.magi,
            yr0.your_age - 2,
            yr0.spouse_age - 2,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
        )
        assert yr0.irmaa_cost == approx(expected_cost)
        # age 63 projection year → income-year age 61 → Medicare age 63 < 65 → no IRMAA
        assert yr0.irmaa_cost == approx(0.0), "Age-63 year-0 must produce zero IRMAA"

    def test_irmaa_year_2_uses_year_0_magi(self):
        """Year-2 IRMAA is anchored to year-0 MAGI (2-year lookback), not year-2 MAGI.

        Build a scenario where year 0 has a large conversion (high MAGI) and
        year 2 has no conversion (low MAGI).  Under the new semantics year-2
        IRMAA must equal irmaa_for_year(year-0 MAGI) and differ from
        irmaa_for_year(year-2 MAGI).
        """
        from engine.irmaa import irmaa_for_year

        hh = Household(your_age=63, spouse_age=63)
        # Large conversion in year 0 only — year 2 has no conversion
        plan = ConversionPlan(your_conversions={2026: 300_000})
        result = run_scenario(hh, plan, end_age=68)

        yr0 = result.years[0]
        yr2 = result.years[2]

        # Year-2 IRMAA should reflect year-0 MAGI (high — above tier 1).
        # scenario passes income-year ages (ya - 2); irmaa_for_year adds +2 internally.
        # yr2.your_age = 65 → income-year age = 63 → Medicare age = 65 → on Medicare.
        expected_from_yr0, _ = irmaa_for_year(
            yr0.magi,
            yr2.your_age - 2,
            yr2.spouse_age - 2,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
            year=2028,  # payment year for yr2 (base_year 2026 + 2)
            cpi=hh.cpi_assumption,
        )
        # Year-2 MAGI (no conversion) should produce a lower IRMAA
        expected_from_yr2, _ = irmaa_for_year(
            yr2.magi,
            yr2.your_age - 2,
            yr2.spouse_age - 2,
            base_part_b=hh.medicare_part_b_base_monthly * 12,
            year=2028,  # payment year for yr2 (base_year 2026 + 2)
            cpi=hh.cpi_assumption,
        )
        assert yr2.irmaa_cost == approx(expected_from_yr0), (
            "PR5: year-2 IRMAA must use year-0 projected MAGI"
        )
        assert expected_from_yr0 > expected_from_yr2, (
            "Sanity: year-0 high-MAGI should produce more IRMAA than year-2 low-MAGI"
        )

    def test_prior_year_magi_anchor_drives_year_0_irmaa(self):
        """prior_year_magi[base_year-2] anchors year-0 IRMAA.

        When the user provides an actual filed MAGI for the lookback year the
        engine must use it instead of the same-year fallback.

        Use age 65 so income-year age is 63 → Medicare age 65 → on Medicare,
        making the anchor observable in year-0 output.
        """
        from engine.irmaa import irmaa_for_year

        base_year = 2026
        filed_magi = 300_000.0  # above IRMAA Tier 1 ($218K)

        hh_no_anchor = Household(your_age=65, spouse_age=65)
        hh_anchored = Household(
            your_age=65,
            spouse_age=65,
            prior_year_magi={base_year - 2: filed_magi},
        )
        plan = ConversionPlan()  # no conversions — year-0 MAGI low without anchor
        r_no = run_scenario(hh_no_anchor, plan, end_age=68)
        r_anc = run_scenario(hh_anchored, plan, end_age=68)

        yr0_no = r_no.years[0]
        yr0_anc = r_anc.years[0]

        # scenario passes income-year ages (ya - 2); irmaa_for_year adds +2 internally
        expected_anchored, _ = irmaa_for_year(
            filed_magi,
            yr0_anc.your_age - 2,
            yr0_anc.spouse_age - 2,
            base_part_b=hh_anchored.medicare_part_b_base_monthly * 12,
        )
        assert yr0_anc.irmaa_cost == approx(expected_anchored), (
            "Anchored IRMAA must equal irmaa_for_year(filed_magi)"
        )
        assert yr0_anc.irmaa_cost != approx(yr0_no.irmaa_cost, tol=1.0), (
            "Anchor must change year-0 IRMAA vs no-anchor baseline"
        )

    def test_prior_year_magi_doesnt_affect_year_2_onwards(self):
        """prior_year_magi anchor only applies to lookback years present in the dict.

        Year-2 IRMAA is based on year-0 projected MAGI (magi_history), not on
        any prior_year_magi value (which keys are base_year-2 and base_year-1,
        both predating the projection window).
        """
        from engine.irmaa import irmaa_for_year

        base_year = 2026
        hh_anchored = Household(
            your_age=63,
            spouse_age=63,
            prior_year_magi={base_year - 2: 300_000.0, base_year - 1: 310_000.0},
        )
        plan = ConversionPlan(your_conversions={2026: 250_000})
        result = run_scenario(hh_anchored, plan, end_age=68)

        yr0 = result.years[0]
        yr2 = result.years[2]

        # Year-2 income_year = 2028 - 2 = 2026 = base_year, which IS in magi_history.
        # scenario passes income-year ages (ya - 2); irmaa_for_year adds +2 internally.
        expected_from_yr0_magi, _ = irmaa_for_year(
            yr0.magi,
            yr2.your_age - 2,
            yr2.spouse_age - 2,
            base_part_b=hh_anchored.medicare_part_b_base_monthly * 12,
            year=2028,  # payment year for yr2 (base_year 2026 + 2)
            cpi=hh_anchored.cpi_assumption,
        )
        assert yr2.irmaa_cost == approx(expected_from_yr0_magi), (
            "Year-2 IRMAA must use year-0 projected MAGI, not prior_year_magi"
        )

    def test_no_irmaa_before_medicare_eligibility(self):
        """Ages 63/61 must produce zero IRMAA even when MAGI is well above Tier-1 ($218K).

        Regression for B-1/E-1: scenario.py was passing current-year ages (ya, sa) to
        irmaa_for_year(), which adds +2 internally.  A 63-year-old was treated as 65 →
        IRMAA charged 2 years before Medicare eligibility.

        With the fix, income-year ages (ya-2, sa-2) are passed; the function adds +2,
        yielding the correct Medicare-year ages (63, 61) which are both < 65 → no IRMAA.
        """
        # MAGI well above 2026 Tier-1 threshold ($218K) — use prior_year_magi anchor
        # so year-0 IRMAA is driven by that filed value rather than the same-year fallback.
        hh_anchored = Household(
            your_age=63,
            spouse_age=61,
            prior_year_magi={2024: 280_000.0},  # above $218K Tier-1; lookback for 2026
        )
        plan = ConversionPlan()  # no conversion — keep it minimal
        result = run_scenario(hh_anchored, plan, end_age=65)

        yr0 = result.years[0]  # year 2026, ya=63, sa=61 — both < Medicare eligibility age
        assert yr0.irmaa_cost == approx(0.0), (
            "IRMAA must be zero at age 63/61: Medicare eligibility requires age 65"
        )

    # --- Fix A: filing_status parameter on irmaa_next_threshold ---

    def test_irmaa_next_threshold_mfj_unchanged_default(self):
        """Backward-compat: omitting filing_status defaults to MFJ table.

        MFJ MAGI $200K is below Tier-1 threshold ($218K) → room = $18K.
        Must be identical when filing_status='MFJ' is passed explicitly.
        """
        assert irmaa_next_threshold(200_000) == approx(18_000)
        assert irmaa_next_threshold(200_000, filing_status="MFJ") == approx(18_000)

    def test_irmaa_next_threshold_single_filer_uses_single_tiers(self):
        """Single filer MAGI $150K: above Single Tier-2 ($137K), below Tier-3 ($171K).

        Next un-crossed threshold is $171K → room = $21K.
        MFJ at same MAGI has room to Tier-1 ($218K) → $68K — confirms different table used.
        """
        single_room = irmaa_next_threshold(150_000, filing_status="Single")
        mfj_room = irmaa_next_threshold(150_000, filing_status="MFJ")
        assert single_room == approx(21_000)
        assert mfj_room == approx(68_000)

    def test_irmaa_for_year_single_filer_uses_single_tiers(self):
        """Single filer MAGI $150K with income-year ages so medicare-year age >= 65.

        $150K is above Single Tier-2 threshold ($137K), below Tier-3 ($171K).
        Annual surcharge per person:
          Part B total $405.80/mo → annual $4,869.60; minus base $202.90/mo * 12 = $2,434.80
          Part B surcharge = $4,869.60 - $2,434.80 = $2,434.80
          Part D surcharge = $37.50 * 12 = $450.00
          Total per person = $2,884.80; 1 person on Medicare → $2,884.80.
        """
        from engine.irmaa import irmaa_for_year

        # income_year ages: 63/55 → payment-year ages 65/57; 1 person on Medicare
        surcharge, medicare_age = irmaa_for_year(
            150_000,
            your_age_income_year=63,
            spouse_age_income_year=55,
            filing_status="Single",
        )
        assert medicare_age == 65
        assert surcharge == approx((405.80 - 202.90) * 12 + 37.50 * 12)

        # Same MAGI under MFJ: $150K < Tier-1 MFJ threshold $218K → no surcharge
        mfj_surcharge, _ = irmaa_for_year(
            150_000,
            your_age_income_year=63,
            spouse_age_income_year=55,
            filing_status="MFJ",
        )
        assert mfj_surcharge == 0.0

    def test_irmaa_for_year_second_element_is_age_not_calendar_year(self):
        """Docstring/naming regression (audit MEDIUM finding).

        irmaa_for_year's second tuple element is your_age_income_year + 2 — an
        AGE — not the payment calendar year (income_year + 2). Pin an income
        year (2026) whose payment year would be 2028: the returned value must
        be the age (65), never the calendar year (2028), and no caller in the
        codebase relies on it as a year (all real call sites discard it via `_`).
        """
        from engine.irmaa import irmaa_for_year

        _, second_element = irmaa_for_year(
            250_000,
            your_age_income_year=63,
            spouse_age_income_year=63,
            filing_status="MFJ",
            year=2028,
        )
        assert second_element == 65
        assert second_element != 2028


class TestIRMAATier5Frozen:
    """Tier 5 (top bracket) is frozen by statute only through 2027 (BBA 2018
    §53109); it resumes CPI indexing for 2028+ (42 U.S.C. §1395r(i)(5)(C),
    audit-0802 F2). See TestIrmaaTopTierResumesIndexing2028 in
    test_audit_irmaa_indexing.py for the 2028+ indexed-value coverage.
    """

    # 2026 base values (directly from module constants)
    _MFJ_TOP = IRMAA_TIERS_MFJ[-1][0]  # 750_000
    _SGL_TOP = IRMAA_TIERS_SINGLE[-1][0]  # 500_000
    # A lower tier that IS indexed (Tier 4)
    _MFJ_T4 = IRMAA_TIERS_MFJ[-2][0]  # 410_000

    def test_base_year_all_tiers_unchanged(self):
        """2026 (base year) — all tier thresholds must equal their base values."""
        from engine.irmaa import _index_irmaa_tiers
        from engine.tax_indexing import BASE_YEAR, DEFAULT_CPI

        tiers_mfj = _index_irmaa_tiers(IRMAA_TIERS_MFJ, BASE_YEAR, DEFAULT_CPI)
        for i, ((base_t, _, _), (indexed_t, _, _)) in enumerate(
            zip(IRMAA_TIERS_MFJ, tiers_mfj, strict=False)
        ):
            assert indexed_t == pytest.approx(base_t, abs=1), (
                f"MFJ Tier {i + 1}: base year must be unchanged"
            )

    def test_forecast_year_top_tier_mfj_frozen(self):
        """2027 (last frozen year): MFJ Tier 5 threshold stays at $750,000."""
        from engine.irmaa import _index_irmaa_tiers
        from engine.tax_indexing import DEFAULT_CPI

        tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2027, DEFAULT_CPI)
        top_threshold = tiers[-1][0]
        assert top_threshold == pytest.approx(self._MFJ_TOP, abs=1), (
            f"MFJ Tier 5 must stay frozen at {self._MFJ_TOP}, got {top_threshold:.0f}"
        )

    def test_forecast_year_top_tier_single_frozen(self):
        """2027 (last frozen year): Single Tier 5 threshold stays at $500,000."""
        from engine.irmaa import _index_irmaa_tiers
        from engine.tax_indexing import DEFAULT_CPI

        tiers = _index_irmaa_tiers(IRMAA_TIERS_SINGLE, 2027, DEFAULT_CPI)
        top_threshold = tiers[-1][0]
        assert top_threshold == pytest.approx(self._SGL_TOP, abs=1), (
            f"Single Tier 5 must stay frozen at {self._SGL_TOP}, got {top_threshold:.0f}"
        )

    def test_forecast_year_lower_tier_does_drift(self):
        """2028: MFJ Tier 4 ($410K base) DOES drift up with CPI — confirms indexing is active."""
        from engine.irmaa import _index_irmaa_tiers
        from engine.tax_indexing import DEFAULT_CPI

        tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2028, DEFAULT_CPI)
        tier4_threshold = tiers[-2][0]  # second-to-last = Tier 4
        expected = self._MFJ_T4 * (1.0 + DEFAULT_CPI) ** 2  # 2 years of CPI
        assert tier4_threshold == pytest.approx(expected, abs=1), (
            f"MFJ Tier 4 must be indexed; expected ~{expected:.0f}, got {tier4_threshold:.0f}"
        )
        assert tier4_threshold > self._MFJ_T4, "Tier 4 must drift above its 2026 base"

    def test_irmaa_surcharge_top_tier_resumes_indexing_2028(self):
        """audit-0802 F2: the top tier resumes CPI indexing for 2028+, so a
        MAGI that was unambiguously Tier 5 in 2026 can drop OUT of Tier 5 by
        2028 once the threshold itself has drifted upward.

        $760K MFJ is Tier 5 in 2026 (frozen base $750K). By 2028 the top
        threshold has drifted to $750K * 1.025 = $768,750, so the SAME $760K
        MAGI now falls to Tier 4 instead — the corrected (non-frozen-forever)
        behavior this reverses the old bug.
        """
        magi = 760_000  # above the 2026 base $750K but below the 2028 indexed ~$768,750
        surcharge_2026 = irmaa_surcharge(magi, year=2026)
        surcharge_2028 = irmaa_surcharge(magi, year=2028)
        genuine_tier5_2028 = irmaa_surcharge(2_000_000, year=2028)
        assert surcharge_2026 > 0, "Sanity: $760K MFJ must be in Tier 5 in 2026"
        assert surcharge_2028 > 0, "Tier 4 surcharge still applies below the indexed top"
        assert surcharge_2028 < genuine_tier5_2028, (
            "$760K must have dropped out of Tier 5 once the top threshold "
            "resumed indexing in 2028"
        )

    def test_irmaa_next_threshold_above_top_tier_returns_inf_in_2028(self):
        """irmaa_next_threshold returns inf when already above the (2028-indexed) top tier."""
        # $800K clears even the 2028-indexed top (~$768,750) — should be inf.
        import math

        result = irmaa_next_threshold(800_000, year=2028)
        assert math.isinf(result)


class TestSurchargeDollarIndexing:
    """Audit A1: IRMAA surcharge dollars are indexed by MEDICAL_INFLATION (not frozen at 2026)."""

    # Top-tier frozen surcharge at 2026 base values (MAGI=1_000_000 always hits Tier 5)
    # (689.90 - 202.90)*12 + 91.00*12
    _BASE_SURCHARGE_1P = (689.90 - 202.90) * 12 + 91.00 * 12

    def test_base_year_surcharge_unchanged(self):
        """year=2026: surcharge equals legacy frozen 2026 dollars (factor=1.0)."""
        result = irmaa_surcharge(1_000_000, num_people=1, year=2026)
        assert result == pytest.approx(self._BASE_SURCHARGE_1P, abs=0.01)

    def test_out_year_2027_grows_by_one_factor(self):
        """year=2027: surcharge = 2026 value * MEDICAL_INFLATION^1."""
        result_2027 = irmaa_surcharge(1_000_000, num_people=1, year=2027)
        expected = self._BASE_SURCHARGE_1P * (1 + MEDICAL_INFLATION) ** 1
        assert result_2027 == pytest.approx(expected, abs=0.01)

    def test_out_year_2036_grows_by_ten_factors(self):
        """year=2036: surcharge = 2026 value * MEDICAL_INFLATION^10."""
        result_2036 = irmaa_surcharge(1_000_000, num_people=1, year=2036)
        expected = self._BASE_SURCHARGE_1P * (1 + MEDICAL_INFLATION) ** 10
        assert result_2036 == pytest.approx(expected, abs=0.10)

    def test_freeze_escape_hatch_medical_cpi_zero(self):
        """medical_cpi=0.0 restores legacy frozen-dollar behavior for any year.

        Uses a MAGI ($2M) safely above the top tier even after it resumes
        CPI indexing in 2028+ (audit-0802 F2) — $1M would drop out of Tier 5
        by 2040 once the indexed top threshold drifts past it, which is
        orthogonal to what this test (medical_cpi freeze) is checking.
        """
        frozen_2040 = irmaa_surcharge(2_000_000, num_people=1, year=2040, medical_cpi=0.0)
        assert frozen_2040 == pytest.approx(self._BASE_SURCHARGE_1P, abs=0.01)

    def test_num_people_scaling_still_holds(self):
        """num_people=2 produces exactly 2× the num_people=1 surcharge (out-year)."""
        s1 = irmaa_surcharge(1_000_000, num_people=1, year=2030)
        s2 = irmaa_surcharge(1_000_000, num_people=2, year=2030)
        assert s2 == pytest.approx(s1 * 2, abs=0.01)


class TestIrmaaFrozenTierMonotonicity:
    """C5 regression: indexed tiers must never overtake the frozen final tier,
    keeping irmaa_tier / irmaa_surcharge / irmaa_next_threshold in agreement."""

    def test_thresholds_monotonic_across_years(self) -> None:
        from engine.irmaa import (
            IRMAA_TIERS_MFJ,
            IRMAA_TIERS_SINGLE,
            _index_irmaa_tiers,
        )

        for base in (IRMAA_TIERS_MFJ, IRMAA_TIERS_SINGLE):
            for year in range(2026, 2081):
                for cpi in (0.025, 0.04):
                    tiers = _index_irmaa_tiers(base, year, cpi)
                    thresholds = [t for t, _, _ in tiers]
                    assert thresholds == sorted(thresholds), (
                        f"non-monotonic at year={year} cpi={cpi}: {thresholds}"
                    )

    def test_tier_and_surcharge_agree_in_inversion_year(self) -> None:
        # audit-0802 F2: the top tier now resumes CPI indexing for 2028+, so
        # the original year=2042/cpi=0.04 scenario no longer inverts (both
        # tier-4 and the top tier grow together). To still exercise the min()
        # clamp we pin to year=2027 (last frozen year) and use an extreme
        # cpi=1.0 (100%; not economically realistic, chosen purely to force
        # unclamped tier-4 = 410_000*2 = 820_000 past the still-frozen $750K
        # top within a single year of compounding).
        from engine.irmaa import irmaa_surcharge, irmaa_tier

        assert irmaa_tier(780_000, "MFJ", year=2027, cpi=1.0) == 5
        # Same tier-5 surcharge as a MAGI unambiguously in tier 5.
        assert irmaa_surcharge(780_000, year=2027, cpi=1.0) == irmaa_surcharge(
            2_000_000, year=2027, cpi=1.0
        )

    def test_next_threshold_inf_above_frozen_floor(self) -> None:
        import math

        from engine.irmaa import irmaa_next_threshold

        # See test_tier_and_surcharge_agree_in_inversion_year for why year=2027/
        # cpi=1.0 (rather than the original 2042/0.04) still forces the clamp.
        assert math.isinf(irmaa_next_threshold(755_000, "MFJ", year=2027, cpi=1.0))
        assert math.isinf(irmaa_next_threshold(780_000, "MFJ", year=2027, cpi=1.0))


class TestSurchargeIndexedThresholdProbe:
    """C8 / ui-org-3: probing just above the INDEXED tier-1 threshold must return a
    positive surcharge; probing the un-indexed base threshold against an indexed-year
    schedule returns 0 (the old RMD-squeeze $0 bug)."""

    def test_positive_just_above_indexed_tier1(self) -> None:
        from engine.irmaa import IRMAA_TIERS_MFJ, irmaa_surcharge
        from engine.tax_indexing import index_value

        y, cpi = 2028, 0.025
        thresh = index_value(IRMAA_TIERS_MFJ[0][0], y, cpi)
        assert irmaa_surcharge(thresh + 1, 2, year=y, cpi=cpi) > 0.0

    def test_unindexed_probe_underflows_to_zero(self) -> None:
        from engine.irmaa import IRMAA_TIERS_MFJ, irmaa_surcharge
        from engine.tax_indexing import index_value

        y, cpi = 2028, 0.025
        # The old ui-org-3 code probed the un-indexed base+1 against the indexed
        # schedule; at cpi>0 that is below the indexed tier-1, so surcharge == 0.
        assert index_value(IRMAA_TIERS_MFJ[0][0], y, cpi) > IRMAA_TIERS_MFJ[0][0] + 1
        assert irmaa_surcharge(IRMAA_TIERS_MFJ[0][0] + 1, 2, year=y, cpi=cpi) == 0.0


class TestIrmaaBeneficiaryGate:
    """Audit 0705 #3 — a Single filer must never get a second IRMAA beneficiary from a phantom
    spouse age (§1839(i) surcharges are per-beneficiary). Mirrors the aca_irmaa_compute guard."""

    def test_single_filer_ignores_phantom_spouse_beneficiary(self):
        """Single filer: a spouse age that reaches Medicare must NOT double the surcharge."""
        import pytest

        from engine.irmaa import irmaa_for_year

        magi = 250_000.0  # above the Single tier-1 threshold → a real surcharge
        # Both ages reach Medicare in the payment year (income year + 2).
        phantom, _ = irmaa_for_year(
            magi,
            your_age_income_year=64,
            spouse_age_income_year=64,
            filing_status="Single",
            year=2026,
            cpi=0.0,
        )
        # Baseline: spouse far from 65. For a Single filer this MUST give the same surcharge.
        baseline, _ = irmaa_for_year(
            magi,
            your_age_income_year=64,
            spouse_age_income_year=40,
            filing_status="Single",
            year=2026,
            cpi=0.0,
        )
        assert phantom > 0  # actually in a surcharge tier
        assert phantom == pytest.approx(baseline)  # spouse age must not affect a Single filer

    def test_mfj_still_counts_both_beneficiaries(self):
        """Regression: MFJ must still count both spouses (fix must not break the MFJ path)."""
        import pytest

        from engine.irmaa import irmaa_for_year

        magi = 250_000.0
        both, _ = irmaa_for_year(
            magi, 64, 64, filing_status="MFJ", year=2026, cpi=0.0
        )
        one, _ = irmaa_for_year(
            magi, 64, 40, filing_status="MFJ", year=2026, cpi=0.0
        )
        assert both == pytest.approx(2 * one)  # two beneficiaries = twice one
