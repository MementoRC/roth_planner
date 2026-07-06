"""Edit-tripwire + citation record.

Pins every hardcoded 2026 tax constant to its authoritative value,
web-verified 2026-07-05 (audit #11, 0 defects). Fails loudly if a constant
is edited without re-verifying against the cited source. NOT a correctness
test — formula correctness is covered by test_engine.py and related suites.

Sources referenced inline on each assert:
  - IRS Rev. Proc. 2025-32: https://www.irs.gov/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-2026-including-amendments-from-the-one-big-beautiful-bill
  - Tax Foundation 2026 brackets: https://taxfoundation.org/data/all/federal/2026-tax-brackets/
  - OBBBA Pub.L.119-21: https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors
  - OBBBA Tax Foundation: https://taxfoundation.org/research/all/federal/one-big-beautiful-bill-act-tax-changes/
  - CMS 2026 IRMAA/Part B fact sheet: https://www.cms.gov/newsroom/fact-sheets/2026-medicare-parts-b-premiums-deductibles
  - Federal Register 2025-20251: https://www.federalregister.gov/documents/2025/11/19/2025-20251/medicare-program-medicare-part-b-monthly-actuarial-rates-premium-rates-and-annual-deductible
  - IRS Rev. Proc. 2025-25 (ACA): https://www.irs.gov/pub/irs-drop/rp-25-25.pdf
  - HHS Default Standard Age Curve: https://www.law.cornell.edu/cfr/text/45/147.102
  - HHS 2025 FPL: https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines/prior-hhs-poverty-guidelines-federal-register-references/2025-poverty-guidelines-computations
  - SECURE 2.0 QCD / IRS Rev. Proc. 2025-32: https://www.irs.gov/pub/irs-drop/rp-25-32.pdf
"""

import math

import pytest

# ---------------------------------------------------------------------------
# IRS Rev. Proc. 2025-32 — 2026 Ordinary Income Brackets
# https://www.irs.gov/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-2026-including-amendments-from-the-one-big-beautiful-bill
# https://taxfoundation.org/data/all/federal/2026-tax-brackets/
# ---------------------------------------------------------------------------


class TestOrdinaryBrackets:
    """2026 MFJ and Single ordinary-income bracket thresholds (IRS Rev. Proc. 2025-32)."""

    def test_brackets_mfj_rate_breakpoints(self):
        from engine.tax import BRACKETS_MFJ

        thresholds = [b[0] for b in BRACKETS_MFJ[:-1]]  # exclude inf sentinel
        assert thresholds == [24_800, 100_800, 211_400, 403_550, 512_450, 768_700]  # IRS Rev. Proc. 2025-32

    def test_brackets_mfj_rates(self):
        from engine.tax import BRACKETS_MFJ

        rates = [b[1] for b in BRACKETS_MFJ]
        assert rates == [0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37]  # IRS Rev. Proc. 2025-32

    def test_brackets_mfj_top_tier_is_inf(self):
        from engine.tax import BRACKETS_MFJ

        assert math.isinf(BRACKETS_MFJ[-1][0])  # sentinel — no upper bound on 37% tier

    def test_brackets_single_rate_breakpoints(self):
        from engine.tax import BRACKETS_SINGLE

        thresholds = [b[0] for b in BRACKETS_SINGLE[:-1]]  # exclude inf sentinel
        assert thresholds == [12_400, 50_400, 105_700, 201_775, 256_225, 640_600]  # IRS Rev. Proc. 2025-32

    def test_brackets_single_rates(self):
        from engine.tax import BRACKETS_SINGLE

        rates = [b[1] for b in BRACKETS_SINGLE]
        assert rates == [0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37]  # IRS Rev. Proc. 2025-32

    def test_brackets_single_top_tier_is_inf(self):
        from engine.tax import BRACKETS_SINGLE

        assert math.isinf(BRACKETS_SINGLE[-1][0])  # sentinel — no upper bound on 37% tier


# ---------------------------------------------------------------------------
# IRS Rev. Proc. 2025-32 — 2026 Standard Deductions and Senior Extras
# ---------------------------------------------------------------------------


class TestStandardDeductions:
    """2026 standard deductions and IRC §63(f) senior extras (IRS Rev. Proc. 2025-32)."""

    def test_std_deduction_single(self):
        from engine.tax import STD_DEDUCTION_SINGLE

        assert STD_DEDUCTION_SINGLE == 16_100  # IRS Rev. Proc. 2025-32

    def test_std_deduction_mfj(self):
        from engine.tax import STD_DEDUCTION_MFJ

        assert STD_DEDUCTION_MFJ == 32_200  # IRS Rev. Proc. 2025-32

    def test_senior_extra_single(self):
        from engine.tax import SENIOR_EXTRA_SINGLE

        assert SENIOR_EXTRA_SINGLE == 2_050  # IRS Rev. Proc. 2025-32; IRC §63(f) single filer 65+

    def test_senior_extra_mfj(self):
        from engine.tax import SENIOR_EXTRA_MFJ

        assert SENIOR_EXTRA_MFJ == 1_650  # IRS Rev. Proc. 2025-32; IRC §63(f) per spouse 65+


# ---------------------------------------------------------------------------
# IRS Rev. Proc. 2025-32 §3.03 — 2026 LTCG/Qualified-Dividend Thresholds
# ---------------------------------------------------------------------------


class TestLTCGThresholds:
    """2026 LTCG bracket thresholds (taxable income upper bounds) per IRS Rev. Proc. 2025-32 §3.03."""

    def test_ltcg_thresholds_mfj(self):
        from engine.tax import LTCG_THRESHOLDS_MFJ

        assert LTCG_THRESHOLDS_MFJ == (98_900, 613_700)  # IRS Rev. Proc. 2025-32 §3.03; 0%/15%/20% tiers

    def test_ltcg_thresholds_single(self):
        from engine.tax import LTCG_THRESHOLDS_SINGLE

        assert LTCG_THRESHOLDS_SINGLE == (49_450, 545_500)  # IRS Rev. Proc. 2025-32 §3.03; 0%/15%/20% tiers


# ---------------------------------------------------------------------------
# OBBBA Pub.L.119-21 §70103 — Senior Bonus Deduction
# https://www.irs.gov/newsroom/one-big-beautiful-bill-act-tax-deductions-for-working-americans-and-seniors
# https://taxfoundation.org/research/all/federal/one-big-beautiful-bill-act-tax-changes/
# ---------------------------------------------------------------------------


class TestOBBBAConstants:
    """OBBBA (Pub. L. 119-21 §70103) senior bonus deduction constants."""

    def test_obbba_bonus_per_person(self):
        from engine.tax import OBBBA_BONUS_PER_PERSON

        assert OBBBA_BONUS_PER_PERSON == 6_000  # Pub. L. 119-21 §70103; $6,000 per person age 65+

    def test_obbba_phaseout_start_mfj(self):
        from engine.tax import OBBBA_PHASEOUT_START_MFJ

        assert OBBBA_PHASEOUT_START_MFJ == 150_000  # Pub. L. 119-21 §70103; IRC §151(d)(5)(C) MFJ phaseout start

    def test_obbba_phaseout_start_single(self):
        from engine.tax import OBBBA_PHASEOUT_START_SINGLE

        assert OBBBA_PHASEOUT_START_SINGLE == 75_000  # Pub. L. 119-21 §70103; Single/HoH phaseout start

    def test_obbba_phaseout_rate(self):
        from engine.tax import OBBBA_PHASEOUT_RATE

        assert pytest.approx(0.06) == OBBBA_PHASEOUT_RATE  # Pub. L. 119-21 §70103; $0.06 per $1 excess MAGI


# ---------------------------------------------------------------------------
# CMS 2026 IRMAA / Medicare Part B — Tiers and Base Premium
# https://www.cms.gov/newsroom/fact-sheets/2026-medicare-parts-b-premiums-deductibles
# https://www.federalregister.gov/documents/2025/11/19/2025-20251/medicare-program-medicare-part-b-monthly-actuarial-rates-premium-rates-and-annual-deductible
# ---------------------------------------------------------------------------
# Note: irmaa.py stores Part B totals and Part D surcharges as annual (monthly * 12).
# These asserts validate the stored annual values by comparing against monthly * 12.


class TestIRMAAConstants:
    """2026 IRMAA MAGI thresholds and Part B/D premium amounts (CMS 2026)."""

    # --- MFJ tier MAGI thresholds ---

    def test_irmaa_tiers_mfj_thresholds(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        thresholds = [t[0] for t in IRMAA_TIERS_MFJ]
        assert thresholds == [218_000, 274_000, 342_000, 410_000, 750_000]  # CMS 2026; Tier 5 frozen since 2020

    # --- MFJ Part B annual totals (monthly * 12) ---

    def test_irmaa_tiers_mfj_part_b_tier1(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[0][1] == pytest.approx(284.10 * 12)  # CMS 2026; $284.10/mo Part B Tier 1

    def test_irmaa_tiers_mfj_part_b_tier2(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[1][1] == pytest.approx(405.80 * 12)  # CMS 2026; $405.80/mo Part B Tier 2

    def test_irmaa_tiers_mfj_part_b_tier3(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[2][1] == pytest.approx(527.50 * 12)  # CMS 2026; $527.50/mo Part B Tier 3

    def test_irmaa_tiers_mfj_part_b_tier4(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[3][1] == pytest.approx(649.20 * 12)  # CMS 2026; $649.20/mo Part B Tier 4

    def test_irmaa_tiers_mfj_part_b_tier5(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[4][1] == pytest.approx(689.90 * 12)  # CMS 2026; $689.90/mo Part B Tier 5

    # --- MFJ Part D annual surcharges (monthly * 12) ---

    def test_irmaa_tiers_mfj_part_d_tier1(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[0][2] == pytest.approx(14.50 * 12)  # CMS 2026; $14.50/mo Part D Tier 1

    def test_irmaa_tiers_mfj_part_d_tier2(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[1][2] == pytest.approx(37.50 * 12)  # CMS 2026; $37.50/mo Part D Tier 2

    def test_irmaa_tiers_mfj_part_d_tier3(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[2][2] == pytest.approx(60.40 * 12)  # CMS 2026; $60.40/mo Part D Tier 3

    def test_irmaa_tiers_mfj_part_d_tier4(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[3][2] == pytest.approx(83.30 * 12)  # CMS 2026; $83.30/mo Part D Tier 4

    def test_irmaa_tiers_mfj_part_d_tier5(self):
        from engine.irmaa import IRMAA_TIERS_MFJ

        assert IRMAA_TIERS_MFJ[4][2] == pytest.approx(91.00 * 12)  # CMS 2026; $91.00/mo Part D Tier 5

    # --- Single tier MAGI thresholds ---

    def test_irmaa_tiers_single_thresholds(self):
        from engine.irmaa import IRMAA_TIERS_SINGLE

        thresholds = [t[0] for t in IRMAA_TIERS_SINGLE]
        assert thresholds == [109_000, 137_000, 171_000, 205_000, 500_000]  # CMS 2026; Tier 5 frozen since 2020

    # --- Base Part B premium (no surcharge) ---

    def test_base_part_b_annual(self):
        from engine.irmaa import BASE_PART_B

        assert pytest.approx(202.90 * 12) == BASE_PART_B  # CMS 2026; $202.90/mo standard Part B premium


# ---------------------------------------------------------------------------
# IRS Rev. Proc. 2025-25 — ACA §36B Pre-ARP Applicable-Percentage Schedule
# https://www.irs.gov/pub/irs-drop/rp-25-25.pdf
# ---------------------------------------------------------------------------


class TestACASchedule:
    """2026 ACA pre-ARP §36B applicable-percentage schedule (IRS Rev. Proc. 2025-25)."""

    def test_aca_pre_arp_schedule_fpl_breakpoints(self):
        from engine.aca import ACA_PRE_ARP_SCHEDULE

        fpl_multiples = [entry[0] for entry in ACA_PRE_ARP_SCHEDULE]
        assert fpl_multiples == [1.33, 1.50, 2.00, 2.50, 3.00, 4.00]  # IRS Rev. Proc. 2025-25

    def test_aca_pre_arp_schedule_applicable_pcts(self):
        from engine.aca import ACA_PRE_ARP_SCHEDULE

        pcts = [entry[1] for entry in ACA_PRE_ARP_SCHEDULE]
        # IRS Rev. Proc. 2025-25: 2.10%/3.14%/4.19%/6.60%/8.44%/9.96%
        expected = [0.0210, 0.0314, 0.0419, 0.0660, 0.0844, 0.0996]
        assert pcts == pytest.approx(expected, abs=1e-5)  # IRS Rev. Proc. 2025-25


# ---------------------------------------------------------------------------
# HHS 2025 Federal Poverty Level — for CY2026 ACA Coverage
# https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines/prior-hhs-poverty-guidelines-federal-register-references/2025-poverty-guidelines-computations
# ---------------------------------------------------------------------------


class TestFPLConstants:
    """2025 HHS Federal Poverty Level guidelines (used for 2026 ACA coverage year)."""

    def test_fpl_single(self):
        from engine.aca import FPL_1

        assert FPL_1 == 15_650  # HHS 2025 FPL; continental US household of 1

    def test_fpl_family_of_2(self):
        from engine.aca import FPL_2

        assert FPL_2 == 21_150  # HHS 2025 FPL; continental US household of 2 ($5,500 per-person increment)


# ---------------------------------------------------------------------------
# HHS Default Standard Age Curve — 45 CFR 147.102
# https://www.law.cornell.edu/cfr/text/45/147.102
# Effective plan years 2018+; stable regulatory table; CMS PDFs 403-blocked.
# Corroborated via authoritative search extraction (confidence: high).
# ---------------------------------------------------------------------------


class TestHHSAgeCurve:
    """HHS Default Standard Age Curve key anchors (45 CFR 147.102)."""

    def test_hhs_age_curve_anchor_age40(self):
        from engine.aca import _HHS_AGE_CURVE

        assert _HHS_AGE_CURVE[40] == pytest.approx(1.278)  # 45 CFR 147.102; age-40 factor (lower clamp)

    def test_hhs_age_curve_anchor_age64(self):
        from engine.aca import _HHS_AGE_CURVE

        assert _HHS_AGE_CURVE[64] == pytest.approx(3.000)  # 45 CFR 147.102; age-64 factor = 3:1 cap

    def test_aca_age_factor_clamp_above_64(self):
        from engine.aca import aca_age_factor

        assert aca_age_factor(65) == pytest.approx(3.000)  # 45 CFR 147.102; ≥64 clamps to 3.000

    def test_aca_age_factor_clamp_below_40(self):
        from engine.aca import aca_age_factor

        assert aca_age_factor(35) == pytest.approx(1.278)  # 45 CFR 147.102; ≤40 clamps to age-40 factor


# ---------------------------------------------------------------------------
# Household model defaults — mirror engine constants (models/household.py)
# Sources same as engine constants above.
# ---------------------------------------------------------------------------


class TestHouseholdDefaults:
    """Household dataclass defaults mirror 2026 engine constants."""

    def test_household_std_deduction_default(self):
        from models.household import Household

        hh = Household(grants=[])
        assert hh.std_deduction == 32_200  # IRS Rev. Proc. 2025-32; MFJ standard deduction 2026

    def test_household_senior_extra_default(self):
        from models.household import Household

        hh = Household(grants=[])
        assert hh.senior_extra == 1_650  # IRS Rev. Proc. 2025-32; per spouse 65+ additional deduction 2026

    def test_household_part_b_base_monthly_default(self):
        from models.household import Household

        hh = Household(grants=[])
        assert hh.medicare_part_b_base_monthly == pytest.approx(202.90)  # CMS 2026; standard Part B monthly

    def test_household_qcd_limit_default(self):
        from models.household import Household

        hh = Household(grants=[])
        assert hh.qcd_limit == 111_000  # IRS Rev. Proc. 2025-32; 2026 per-person QCD limit (SECURE 2.0 §307)
