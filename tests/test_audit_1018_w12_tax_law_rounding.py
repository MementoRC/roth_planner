"""audit-1018 W12: tax-law correctness and rounding (F-13, F-36, F-38, F-39, F-40,
F-41, F-55, F-56). Synthetic inputs only."""

from __future__ import annotations

import inspect
import math

import pytest

from engine import aca, irmaa, scenario, sweet_spot_compute, tax_indexing
from engine.tax import (
    STD_DEDUCTION_SINGLE,
    _next_quarterly_due,
    deductions,
    safe_harbor_payment,
    taxable_ss,
)
from engine.tax_indexing import index_bracket_list, index_value
from views import roth_eligibility as re_view

# ---------------------------------------------------------------------------
# F-38: statute rounds the CPI INCREASE DOWN to a multiple of $50
# ---------------------------------------------------------------------------


class TestRound50RoundsDown:
    def test_small_increase_rounds_down_not_to_nearest(self):
        # 10_000 * 1.0049 = 10_049: nearest-$50 gave 10_050 (above the CPI-scaled
        # value); the statute rounds the $49 increase down to $0.
        assert index_value(10_000.0, 2027, 0.0049, round50=True) == 10_000.0

    def test_result_never_exceeds_scaled_and_within_50(self):
        for base in (16_100.0, 32_200.0, 24_800.0):
            for cpi in (0.011, 0.025, 0.034, 0.049):
                for year in (2027, 2030, 2036):
                    raw = base * (1 + cpi) ** (year - 2026)
                    got = index_value(base, year, cpi, round50=True)
                    assert got <= raw + 1e-6
                    assert raw - got < 50.0
                    assert (got - base) % 50.0 == 0.0

    def test_exact_multiple_survives_float_noise(self):
        # 10_000 * 1.005 evaluates to 10049.999999999998 in binary float
        assert index_value(10_000.0, 2027, 0.005, round50=True) == 10_050.0

    def test_infinity_and_base_year_unchanged(self):
        assert math.isinf(index_value(float("inf"), 2030, 0.025, round50=True))
        assert index_value(16_100.0, 2026, 0.5, round50=True) == 16_100.0

    def test_bracket_list_ceilings_round_down(self):
        out = index_bracket_list(
            [(10_000.0, 0.10), (float("inf"), 0.37)], 2027, 0.0049, round50=True
        )
        assert out[0] == (10_000.0, 0.10)
        assert math.isinf(out[1][0])

    def test_std_deduction_rounds_down(self):
        # 32_200 * 1.034 = 33_294.8: increase 1_094.8 -> 1_050, not nearest (33_300)
        assert deductions(40, 40, filing_status="MFJ", year=2027, cpi=0.034) == 33_250.0

    def test_docstring_cites_f7_not_f6(self):
        doc = inspect.getsource(tax_indexing)
        assert "§1(f)(6)" not in doc
        assert "§1(f)(7)" in doc


# ---------------------------------------------------------------------------
# F-36: IRA limit / catch-up floor, Roth + Trad phase-out thresholds
# ---------------------------------------------------------------------------


class TestIraProjections:
    def test_contribution_limit_rounds_down_to_500(self):
        # 7_500 * 1.03 = 7_725 -> increase 225 -> floors to 0
        assert re_view.contrib_limit_for_year(2027, cpi=0.03) == 7_500.0
        # 7_500 * 1.10 = 8_250 -> increase 750 -> 500
        assert re_view.contrib_limit_for_year(2027, cpi=0.10) == 8_000.0

    def test_catchup_rounds_down_to_100(self):
        # 1_100 * 1.10 = 1_210 -> increase 110 -> 100
        assert re_view.catchup_50_for_year(2027, cpi=0.10) == 1_200.0

    def test_published_years_stay_exact(self):
        assert re_view.contrib_limit_for_year(2026) == 7_500
        assert re_view.catchup_50_for_year(2026) == 1_100
        assert re_view.roth_phaseout_for_year(2026, "MFJ") == (242_000.0, 252_000.0)

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("MFJ", (249_000.0, 259_000.0)),  # 242_000*1.03=249_260 -> 249_000, +10k
            ("Single", (158_000.0, 173_000.0)),  # 153_000*1.03=157_590 -> 158_000, +15k
        ],
    )
    def test_roth_phaseout_fixed_width_rounded_threshold(self, status, expected):
        assert re_view.roth_phaseout_for_year(2027, status, cpi=0.03) == expected

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("MFJ_active", (133_000.0, 153_000.0)),  # 129_000*1.03=132_870 -> 133_000, +20k
            ("MFJ_spouse_only", (249_000.0, 259_000.0)),
            ("Single", (83_000.0, 93_000.0)),  # 81_000*1.03=83_430 -> 83_000, +10k
        ],
    )
    def test_trad_phaseout_fixed_width_rounded_threshold(self, key, expected):
        assert re_view.trad_deduction_phaseout_for_year(2027, key, cpi=0.03) == expected


# ---------------------------------------------------------------------------
# F-39: IRMAA $1,000 rounding is half-up even with float error
# ---------------------------------------------------------------------------


class TestIrmaaHalfUp:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (2_499.9999999999995, 3_000.0),  # exact 2_500 carried as float noise
            (1_499.9999999999998, 2_000.0),
            (500.0, 1_000.0),
            (499.99, 0.0),
            (1_500.0, 2_000.0),
        ],
    )
    def test_round_to_1000(self, value, expected):
        assert irmaa._round_to_1000(value) == expected


# ---------------------------------------------------------------------------
# F-40: HoH standard deduction and SS base amounts
# ---------------------------------------------------------------------------


class TestFilingStatusConstants:
    def test_hoh_standard_deduction_is_not_single(self):
        assert deductions(40, 40, filing_status="HoH") == 24_150.0
        assert deductions(40, 40, filing_status="HoH") != STD_DEDUCTION_SINGLE

    def test_hoh_senior_extra_is_unmarried_amount(self):
        assert deductions(66, 0, filing_status="HoH") == 24_150.0 + 2_050.0

    def test_taxable_ss_hoh_uses_single_thresholds(self):
        hoh = taxable_ss(20_000.0, 20_000.0, filing_status="HoH")
        assert hoh == pytest.approx(taxable_ss(20_000.0, 20_000.0, filing_status="Single"))
        assert hoh == pytest.approx(2_500.0)

    def test_taxable_ss_mfs_living_together_has_zero_base(self):
        # provisional = 10_000 + 10_000 = 20_000; base $0 -> 85% of provisional,
        # capped at 85% of benefits (= 17_000).
        assert taxable_ss(20_000.0, 10_000.0, filing_status="MFS") == pytest.approx(17_000.0)
        assert taxable_ss(20_000.0, 0.0, filing_status="MFS") == pytest.approx(8_500.0)


# ---------------------------------------------------------------------------
# F-41 / F-56: Form 8962 line 5 truncates the FPL percentage
# ---------------------------------------------------------------------------

_FPL_MFJ_2026 = aca.fpl_for("MFJ", 2026, 0.025)


class TestAcaFplTruncation:
    def test_cliff_is_at_401_percent_not_400_point_0(self):
        magi = 4.005 * _FPL_MFJ_2026  # 400.5% -> line 5 = 400 -> still eligible
        assert aca.aca_premium_cap_rate(magi, False, "MFJ", year=2026) == pytest.approx(0.0996)
        assert aca.aca_subsidy(magi, 20_000.0, False, "MFJ", year=2026) > 0.0

    def test_401_percent_is_ineligible(self):
        magi = 4.0101 * _FPL_MFJ_2026
        assert aca.aca_premium_cap_rate(magi, False, "MFJ", year=2026) == 0.0
        assert aca.aca_subsidy(magi, 20_000.0, False, "MFJ", year=2026) == 0.0

    def test_ramp_interpolates_on_truncated_percent(self):
        hi = aca.aca_premium_cap_rate(1.9999 * _FPL_MFJ_2026, False, "MFJ", year=2026)
        exact = aca.aca_premium_cap_rate(1.99 * _FPL_MFJ_2026, False, "MFJ", year=2026)
        assert hi == pytest.approx(exact)

    def test_ramp_rate_for_truncated_199(self):
        rate = aca.aca_premium_cap_rate(1.9999 * _FPL_MFJ_2026, False, "MFJ", year=2026)
        assert rate == pytest.approx(0.0419 + 0.98 * (0.066 - 0.0419), abs=1e-6)

    def test_flat_bottom_band_edge_unchanged(self):
        # exactly 133% FPL stays in the ramp band (3.14%)
        rate = aca.aca_premium_cap_rate(1.33 * _FPL_MFJ_2026, False, "MFJ", year=2026)
        assert rate == pytest.approx(0.0314)


# ---------------------------------------------------------------------------
# F-55: QCD cap rounds to the nearest $1,000
# ---------------------------------------------------------------------------


class TestQcdRounding:
    def test_projected_limit_rounds_to_nearest_1000(self):
        # 111_000 * 1.025 = 113_775 -> 114_000
        assert tax_indexing.qcd_limit_for_year(111_000.0, 2027, 0.025) == 114_000.0
        # 111_000 * 1.0149 = 112_653.9 -> 113_000
        assert tax_indexing.qcd_limit_for_year(111_000.0, 2027, 0.0149) == 113_000.0

    def test_base_year_unchanged(self):
        assert tax_indexing.qcd_limit_for_year(111_000.0, 2026, 0.025) == 111_000.0

    @pytest.mark.parametrize("module", [scenario, sweet_spot_compute])
    def test_engines_use_rounded_limit(self, module):
        assert "qcd_limit_for_year" in inspect.getsource(module)


# ---------------------------------------------------------------------------
# F-13: Jan 1-15 maps to the prior tax year's Q4 deadline
# ---------------------------------------------------------------------------


class TestNextQuarterlyDueJanuary:
    @pytest.mark.parametrize(
        ("payment_date", "expected"),
        [
            ("2027-01-10", "2027-01-15"),
            ("2027-01-15", "2027-01-15"),
            ("2027-01-16", "2027-04-15"),
            ("2026-12-31", "2027-01-15"),
        ],
    )
    def test_calendar_anchored(self, payment_date, expected):
        assert _next_quarterly_due(payment_date) == expected

    def test_tax_year_scopes_deadlines_to_that_year(self):
        # Viewing a 2026 snapshot on 2027-01-10: the 2026 Q4 payment is due 1/15.
        assert _next_quarterly_due("2027-01-10", tax_year=2026) == "2027-01-15"
        # Viewing a 2027 snapshot the same day: next 2027 deadline is Q1.
        assert _next_quarterly_due("2027-01-10", tax_year=2027) == "2027-04-15"

    def test_safe_harbor_payment_passes_tax_year(self):
        g = safe_harbor_payment(
            10_000.0, 12_000.0, 0.0, "2027-01-10", prior_year_agi=100_000.0, tax_year=2026
        )
        assert g.next_quarterly_due == "2027-01-15"


class TestAnalysisViewUsesSnapshotTaxYear:
    def test_prior_year_and_deadline_follow_snapshot_tax_year(self):
        from models.ytd_income import YTDSnapshot
        from tests.test_audit_1008_w6_safe_harbor import TestAnalysisViewWiring
        from tests.test_views_ytd_income import _stub_hh

        wiring = TestAnalysisViewWiring()
        captured, tax_calls, _ = wiring._render_capture(
            _stub_hh(), YTDSnapshot(wages_ytd=100_000.0, tax_year=2031), agi_return=None
        )
        args, kwargs = tax_calls[0]
        assert (args[0] if args else kwargs.get("tax_year")) == 2030
        assert captured.get("tax_year") == 2031
