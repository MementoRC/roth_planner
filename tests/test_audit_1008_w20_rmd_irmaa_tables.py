"""Audit-1008 Wave 20: RMD tables, inherited-IRA window, IRMAA indexing, FEIE.

F-49  Joint & Last Survivor table cells (86,74) and (90,76) mis-transcribed.
F-50  Deferred first-year RMD read Table II with the current-year beneficiary age.
F-51  Inherited-IRA 10-year rule emptied the account one year early.
F-52  Indexed IRMAA thresholds skipped the statutory $1,000 rounding.
F-53  Top IRMAA tier is "at least" its threshold, not "more than".
F-59  IRMAA MAGI must add the foreign earned income exclusion back.

All figures are synthetic.
"""

from __future__ import annotations

import pytest

from engine.ira import (
    calc_rmd,
    inherited_ira_drain_for_year,
    inherited_ira_years_remaining,
    joint_life_divisor,
    rmd_divisor,
)
from engine.irmaa import (
    IRMAA_TIERS_MFJ,
    IRMAA_TIERS_SINGLE,
    _index_irmaa_tiers,
    irmaa_next_threshold,
    irmaa_surcharge,
    irmaa_tier,
    irmaa_tier1_threshold,
)
from engine.tax_return_pdf import compute_irmaa_magi, compute_magi

# ---------------------------------------------------------------------------
# F-49 — Table II cells, checked against 26 CFR §1.401(a)(9)-9(d)
# ---------------------------------------------------------------------------


class TestJointTableCells:
    def test_owner_86_beneficiary_74(self) -> None:
        assert joint_life_divisor(86, 74) == 16.5

    def test_owner_90_beneficiary_76(self) -> None:
        assert joint_life_divisor(90, 76) == 14.7

    def test_rmd_uses_corrected_cell(self) -> None:
        assert calc_rmd(1_000_000.0, 86, beneficiary_age=74) == pytest.approx(1_000_000.0 / 16.5)


# ---------------------------------------------------------------------------
# F-50 — deferred first-year RMD uses the beneficiary's PRIOR-year age
# ---------------------------------------------------------------------------


class TestDeferredFirstRmdBeneficiaryAge:
    def test_prior_year_rmd_reads_table_ii_with_prior_year_ages(self) -> None:
        # Owner first RMD at 73, deferred to 74. Spouse is 11 years younger:
        # 62 in the deferred year, 63 now. The deferred RMD is the age-73 RMD,
        # so it uses (73, 62) — an 11-year gap, Table II — not (73, 63).
        got = calc_rmd(
            1_000_000.0,
            74,
            rmd_start_age=73,
            first_year_deferred=True,
            prior_year_balance=1_000_000.0,
            beneficiary_age=63,
        )
        expected = 1_000_000.0 / rmd_divisor(74, 63) + 1_000_000.0 / joint_life_divisor(73, 62)
        assert got == pytest.approx(expected)

    def test_no_beneficiary_unchanged(self) -> None:
        got = calc_rmd(
            1_000_000.0,
            74,
            rmd_start_age=73,
            first_year_deferred=True,
            prior_year_balance=1_000_000.0,
        )
        assert got == pytest.approx(1_000_000.0 / 25.5 + 1_000_000.0 / 26.5)


# ---------------------------------------------------------------------------
# F-51 — 10-year window runs through Dec 31 of death year + 10
# ---------------------------------------------------------------------------


class TestInheritedIraWindow:
    def test_years_remaining_counts_eleven_calendar_years(self) -> None:
        assert inherited_ira_years_remaining(2026, 2026) == 11
        assert inherited_ira_years_remaining(2026, 2036) == 1
        assert inherited_ira_years_remaining(2026, 2037) == 0

    def test_zero_growth_even_drain_over_eleven_years(self) -> None:
        balance = 550_000.0
        draws = [inherited_ira_drain_for_year(balance, 2026, y, 0.0) for y in range(2026, 2038)]
        assert draws[0] == pytest.approx(50_000.0)
        # Death year + 10 is still a draw year; the year after is empty.
        assert draws[10] == pytest.approx(50_000.0)
        assert draws[11] == 0.0
        assert sum(draws) == pytest.approx(balance)


# ---------------------------------------------------------------------------
# F-52 — indexed thresholds round to the nearest $1,000
# ---------------------------------------------------------------------------


class TestIrmaaRounding:
    def test_base_year_thresholds_unchanged(self) -> None:
        assert [t for t, _, _ in _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2026, 0.025)] == [
            t for t, _, _ in IRMAA_TIERS_MFJ
        ]

    @pytest.mark.parametrize("year", [2027, 2028, 2031, 2040])
    def test_single_thresholds_are_multiples_of_1000(self, year: int) -> None:
        for t, _, _ in _index_irmaa_tiers(IRMAA_TIERS_SINGLE, year, 0.025):
            assert t % 1000 == 0

    @pytest.mark.parametrize("year", [2027, 2028, 2031, 2040])
    def test_joint_thresholds_derive_from_rounded_single(self, year: int) -> None:
        single = _index_irmaa_tiers(IRMAA_TIERS_SINGLE, year, 0.025)
        joint = _index_irmaa_tiers(IRMAA_TIERS_MFJ, year, 0.025)
        for i in range(4):
            assert joint[i][0] == 2 * single[i][0]
        assert joint[4][0] == 1.5 * single[4][0]

    def test_2027_single_tier1_rounds(self) -> None:
        # 109,000 * 1.025 = 111,725 -> 112,000.
        assert _index_irmaa_tiers(IRMAA_TIERS_SINGLE, 2027, 0.025)[0][0] == 112_000.0

    def test_magi_between_raw_and_rounded_threshold(self) -> None:
        # Single tier 1 in 2027: raw 111,725, statutory 112,000. MAGI 111,900
        # is below the statutory threshold -> no surcharge.
        assert irmaa_tier(111_900.0, "Single", year=2027, cpi=0.025) == 0
        # Single tier 1 in 2029 (cpi 2.5%): raw 109,000*1.025**3 = 117,380.4,
        # statutory 117,000. MAGI 117,200 is IN tier 1.
        assert irmaa_tier(117_200.0, "Single", year=2029, cpi=0.025) == 1
        assert irmaa_surcharge(117_200.0, 1, filing_status="Single", year=2029, cpi=0.025) > 0

    def test_tier1_threshold_helper_rounds(self) -> None:
        # income year 2025 -> payment year 2027.
        assert irmaa_tier1_threshold(2025, "Single", 0.025) == 112_000.0
        assert irmaa_tier1_threshold(2025, "MFJ", 0.025) == 224_000.0


# ---------------------------------------------------------------------------
# F-53 — top tier is inclusive
# ---------------------------------------------------------------------------


class TestTopTierInclusive:
    def test_mfj_exactly_750k_is_tier_5(self) -> None:
        assert irmaa_tier(750_000.0, "MFJ") == 5
        top = irmaa_surcharge(750_000.0, 2, filing_status="MFJ", medical_cpi=0.0)
        tier4 = irmaa_surcharge(749_999.0, 2, filing_status="MFJ", medical_cpi=0.0)
        assert top > tier4

    def test_single_exactly_500k_is_tier_5(self) -> None:
        assert irmaa_tier(500_000.0, "Single") == 5

    def test_no_room_reported_to_an_applied_top_tier(self) -> None:
        assert irmaa_next_threshold(750_000.0, "MFJ") == float("inf")

    def test_lower_tiers_stay_more_than(self) -> None:
        # Exactly on the tier-1 threshold is still tier 0 ("more than").
        assert irmaa_tier(218_000.0, "MFJ") == 0
        assert irmaa_surcharge(218_000.0, 2, filing_status="MFJ") == 0.0


# ---------------------------------------------------------------------------
# F-59 — IRMAA MAGI adds back the FEIE (42 U.S.C. §1395r(i)(4)(A))
# ---------------------------------------------------------------------------


class TestIrmaaMagiFeie:
    def test_feie_added_back(self) -> None:
        assert compute_irmaa_magi(200_000.0, 0.0, 20_000.0) == 220_000.0

    def test_matches_compute_magi(self) -> None:
        assert compute_irmaa_magi(150_000.0, 1_500.0, 10_000.0) == compute_magi(
            150_000.0, 1_500.0, 10_000.0
        )

    def test_negative_parsed_feie_still_adds_back(self) -> None:
        # Schedule 1 prints the exclusion as a negative adjustment.
        assert compute_irmaa_magi(200_000.0, 0.0, -20_000.0) == 220_000.0
        assert compute_magi(200_000.0, 0.0, -20_000.0) == 220_000.0

    def test_feie_pushes_into_tier_1(self) -> None:
        magi = compute_irmaa_magi(200_000.0, 0.0, 20_000.0)
        assert irmaa_tier(magi, "MFJ") == 1
