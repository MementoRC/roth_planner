"""Regression tests for audit-0707 Batch-A fixes.

MU8-F1: sweet-spot ytd_ordinary in bracket/LTCG-stack base
PU1-M01: federal_withholding_ytd save/load round-trip
UU2-UI-07: growth_rate survives upload round-trip
"""

import pytest

# ---------------------------------------------------------------------------
# MU8-F1: ytd_ordinary in sweet-spot ordinary base
# ---------------------------------------------------------------------------


class TestSweetSpotYtdOrdinaryBase:
    """MU8-F1: all_in_at_conversion must include YTD ordinary income in the
    ordinary bracket base (gross), not just in MAGI.

    Before the fix: gross = opt + conv + tss  (ytd_ordinary missing)
    After the fix:  gross = opt + conv + tss + ytd_ordinary

    Consequence: with YTD ordinary income > 0, conversion tax and LTCG
    stacking cost are both understated pre-fix.
    """

    def _make_household(self):
        from models.exercise_schedule import ExerciseSchedule
        from models.household import Household

        hh = Household(
            your_age=66,
            spouse_age=64,
            base_year=2026,
            cpi_assumption=0.0,
            ss_cola=0.0,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
            filing_status="MFJ",
            your_aca_enrolled=False,
            spouse_aca_enrolled=False,
        )
        # Deliberately keeps default option income in base_year: these tests
        # were calibrated (conv chosen just below/above the LTCG threshold)
        # against the pre-#373 stagger default, which landed the first TXN
        # grant's full spread in base_year. The hold-to-expiration default
        # (PR #373 follow-up) now lands it in the grant's own expiry_year
        # instead, so it's pinned explicitly here to preserve the calibration.
        hh.exercise_schedule = ExerciseSchedule()
        hh.exercise_schedule.set_shares(hh.grants[0].key(), hh.base_year, hh.grants[0].shares)
        hh.exercise_schedule.set_price(hh.base_year, hh.txn_price_now)
        return hh

    def test_ytd_ordinary_shifts_taxable_income_up(self) -> None:
        """With YTD wages > 0, taxable_inc at the same conversion must be higher
        than without YTD wages (gross includes ytd_ordinary after fix)."""
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year
        from models.ytd_income import YTDSnapshot

        hh = self._make_household()
        wages = 30_000.0
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=wages)

        b_no_ytd = base_income_for_year(hh, 2026, ytd=None)
        b_with_ytd = base_income_for_year(hh, 2026, ytd=ytd)

        conv = 50_000.0
        res_no = all_in_at_conversion(hh, b_no_ytd, conv, 0.0)
        res_with = all_in_at_conversion(hh, b_with_ytd, conv, 0.0)

        # ytd_ordinary > 0 must raise taxable_inc
        assert res_with.taxable_inc > res_no.taxable_inc, (
            f"taxable_inc with ytd ({res_with.taxable_inc:.0f}) must exceed "
            f"without ytd ({res_no.taxable_inc:.0f})"
        )
        # Difference must equal ytd_ordinary (wages only, no NQO)
        assert res_with.taxable_inc - res_no.taxable_inc == pytest.approx(wages, abs=1.0), (
            "taxable_inc delta must equal ytd_ordinary (wages)"
        )

    def test_ytd_ordinary_raises_conv_tax(self) -> None:
        """With YTD wages that push income into a higher bracket, conv_tax must be
        higher than the no-YTD case at the same conversion amount."""
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year
        from models.ytd_income import YTDSnapshot

        hh = self._make_household()
        # Large wages to ensure higher marginal rate applies
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=150_000.0)

        b_no_ytd = base_income_for_year(hh, 2026, ytd=None)
        b_with_ytd = base_income_for_year(hh, 2026, ytd=ytd)

        conv = 20_000.0
        res_no = all_in_at_conversion(hh, b_no_ytd, conv, 0.0)
        res_with = all_in_at_conversion(hh, b_with_ytd, conv, 0.0)

        assert res_with.conv_tax > res_no.conv_tax, (
            f"conv_tax with ytd wages ({res_with.conv_tax:.0f}) must exceed "
            f"without ytd ({res_no.conv_tax:.0f})"
        )

    def test_ytd_ordinary_shifts_the_ltcg_stack_base(self) -> None:
        """LTCG stacking cost must be higher when ytd_ordinary pushes the ordinary
        base above the 0%/15% LTCG threshold. Confirms MU8-F1 defect scenario."""
        from engine.sweet_spot_compute import all_in_at_conversion, base_income_for_year
        from engine.tax import LTCG_THRESHOLDS_MFJ, STD_DEDUCTION_MFJ
        from models.ytd_income import YTDSnapshot

        hh = self._make_household()
        threshold_0_15 = LTCG_THRESHOLDS_MFJ[0]  # $98,900 at cpi=0

        # ages 66/64: std deduction = MFJ base (no seniors at <65 threshold).
        # taxable_inc (no ytd) = conv - deduction; choose conv just below threshold.
        # taxable_inc (with ytd) = conv + wages - deduction; wages push above threshold.
        deduction = STD_DEDUCTION_MFJ  # ~$30,000 at cpi=0
        conv = threshold_0_15 - deduction - 5_000  # below threshold without ytd
        wages = 15_000.0  # enough to push above threshold with ytd

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=wages)
        b_no_ytd = base_income_for_year(hh, 2026, ytd=None)
        b_with_ytd = base_income_for_year(hh, 2026, ytd=ytd)

        ltcg_eligible = 20_000.0
        res_no = all_in_at_conversion(hh, b_no_ytd, conv, 0.0, ltcg_eligible=ltcg_eligible)
        res_with = all_in_at_conversion(hh, b_with_ytd, conv, 0.0, ltcg_eligible=ltcg_eligible)

        # Without ytd: taxable_inc < threshold, all LTCG at 0%
        assert res_no.ltcg_delta == pytest.approx(0.0, abs=0.01), (
            f"no-ytd ltcg_delta should be ~0, got {res_no.ltcg_delta}"
        )
        # With ytd: taxable_inc > threshold, some LTCG at 15%
        assert res_with.ltcg_delta > 0.0, (
            f"with-ytd ltcg_delta should be >0 (LTCG stacks into 15% band), "
            f"got {res_with.ltcg_delta}"
        )

    def test_ytd_ordinary_excludes_ltcg_and_muni_int(self) -> None:
        """ytd_ordinary must not include LTCG or muni interest (MAGI-only items)."""
        from engine.sweet_spot_compute import base_income_for_year
        from models.ytd_income import YTDSnapshot

        hh = self._make_household()
        wages = 50_000.0
        ltcg = 30_000.0
        muni = 5_000.0
        qual_div = 8_000.0

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=wages,
            ltcg_ytd=ltcg,
            tax_exempt_interest_ytd=muni,
            qualified_dividends_ytd=qual_div,
        )
        b = base_income_for_year(hh, 2026, ytd=ytd)

        # ytd_ordinary = wages only (LTCG, muni, qual-divs excluded)
        assert b.ytd_ordinary == pytest.approx(wages, abs=0.01), (
            f"ytd_ordinary should be wages={wages}, got {b.ytd_ordinary}"
        )
        # ytd_magi must include all items
        expected_magi = wages + ltcg + muni + qual_div
        assert b.ytd_magi == pytest.approx(expected_magi, abs=0.01)

    def test_bracket_boundary_includes_ytd_ordinary(self) -> None:
        """bracket_boundary_conversion must account for ytd_ordinary narrowing room."""
        from engine.sweet_spot_compute import base_income_for_year, bracket_boundary_conversion
        from models.ytd_income import YTDSnapshot

        hh = self._make_household()
        wages = 20_000.0
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=wages)

        b_no_ytd = base_income_for_year(hh, 2026, ytd=None)
        b_with_ytd = base_income_for_year(hh, 2026, ytd=ytd)

        ceiling = 300_000.0
        conv_no = bracket_boundary_conversion(hh, b_no_ytd, ceiling)
        conv_with = bracket_boundary_conversion(hh, b_with_ytd, ceiling)

        # With ytd_ordinary, need less conversion to reach ceiling
        assert conv_with < conv_no, (
            f"conv needed WITH ytd ({conv_with:.0f}) must be less than "
            f"without ytd ({conv_no:.0f})"
        )
        assert conv_no - conv_with == pytest.approx(wages, abs=1.0), (
            "reduction in bracket boundary must equal ytd_ordinary"
        )

    def test_nqo_not_double_counted_in_ytd_ordinary(self) -> None:
        """nqo_exercise_ytd must be excluded from ytd_ordinary (already in opt)."""
        from engine.sweet_spot_compute import base_income_for_year
        from models.ytd_income import YTDSnapshot

        hh = self._make_household()
        nqo_spread = 50_000.0
        wages = 30_000.0
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=wages, nqo_exercise_ytd=nqo_spread)

        b = base_income_for_year(hh, 2026, ytd=ytd)

        # ytd_ordinary = wages only (NQO excluded to avoid double-count with opt)
        assert b.ytd_ordinary == pytest.approx(wages, abs=0.01), (
            f"ytd_ordinary should be wages={wages} (NQO excluded), got {b.ytd_ordinary}"
        )


# ---------------------------------------------------------------------------
# PU1-M01: federal_withholding_ytd save/load round-trip
# ---------------------------------------------------------------------------


class TestFederalWithholdingRoundTrip:
    """PU1-M01: federal_withholding_ytd must survive save_ytd_snapshot/load."""

    def test_federal_withholding_survives_round_trip(self, tmp_path, monkeypatch) -> None:
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd_withholding.json")

        withholding = 45_000.0
        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=180_000.0,
            federal_withholding_ytd=withholding,
        )
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()

        assert loaded is not None
        assert loaded.federal_withholding_ytd == pytest.approx(withholding), (
            f"federal_withholding_ytd lost on round-trip: "
            f"saved {withholding}, got {loaded.federal_withholding_ytd}"
        )

    def test_old_cache_missing_withholding_defaults_zero(self, tmp_path, monkeypatch) -> None:
        """Old cache without federal_withholding_ytd must load without error, default 0.0."""
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot
        from engine.secure_io import write_pii_json

        cache_path = tmp_path / "ytd_old.json"
        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", cache_path)

        old_data = {
            "tax_year": 2025,
            "snapshot_date": "",
            "wages_ytd": 100_000.0,
            "nec_income_ytd": 0.0,
            "ira_conversions_ytd": 0.0,
            "spouse_ira_conversions_ytd": 0.0,
            "ira_distributions_ytd": 0.0,
            "ltcg_ytd": 0.0,
            "stcg_ytd": 0.0,
            "qualified_dividends_ytd": 0.0,
            "ordinary_dividends_ytd": 0.0,
            "interest_ytd": 0.0,
            "tax_exempt_interest_ytd": 0.0,
            "nqo_exercise_ytd": 0.0,
            "gain_events": [],
            "manually_entered": True,
        }
        write_pii_json(cache_path, old_data)

        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.federal_withholding_ytd == pytest.approx(0.0), (
            f"Old cache must default federal_withholding_ytd to 0.0, "
            f"got {loaded.federal_withholding_ytd}"
        )

    def test_all_ytd_fields_preserved(self, tmp_path, monkeypatch) -> None:
        """All YTD fields survive a full round-trip including federal_withholding_ytd."""
        from engine import portfolio_sync
        from engine.portfolio_sync import load_ytd_snapshot, save_ytd_snapshot
        from models.ytd_income import YTDSnapshot

        monkeypatch.setattr(portfolio_sync, "_YTD_CACHE_PATH", tmp_path / "ytd_full.json")

        ytd = YTDSnapshot(
            tax_year=2026,
            wages_ytd=120_000.0,
            nqo_exercise_ytd=25_000.0,
            ltcg_ytd=80_000.0,
            federal_withholding_ytd=38_000.0,
            ordinary_dividends_ytd=3_000.0,
            interest_ytd=1_500.0,
        )
        save_ytd_snapshot(ytd)
        loaded = load_ytd_snapshot()
        assert loaded is not None
        assert loaded.wages_ytd == pytest.approx(120_000.0)
        assert loaded.nqo_exercise_ytd == pytest.approx(25_000.0)
        assert loaded.ltcg_ytd == pytest.approx(80_000.0)
        assert loaded.federal_withholding_ytd == pytest.approx(38_000.0)
        assert loaded.ordinary_dividends_ytd == pytest.approx(3_000.0)
        assert loaded.interest_ytd == pytest.approx(1_500.0)


# ---------------------------------------------------------------------------
# UU2-UI-07: growth_rate round-trip
# ---------------------------------------------------------------------------


class TestGrowthRateRoundTrip:
    """UU2-UI-07: growth_rate must survive JSON round-trip in both
    build_user_defaults_session_updates and _user_defaults_from_session."""

    def test_growth_rate_survives_upload_merge(self) -> None:
        from engine.upload_merge import build_user_defaults_session_updates

        data = {"growth_rate": 8.5, "your_age": 61, "spouse_age": 55}
        updates = build_user_defaults_session_updates(data, as_spouse=False)
        assert "growth_rate" in updates, (
            "growth_rate must appear in session updates from upload_merge"
        )
        assert updates["growth_rate"] == pytest.approx(8.5)

    def test_growth_rate_absent_not_in_updates(self) -> None:
        """When growth_rate absent from JSON, must not appear in updates."""
        from engine.upload_merge import build_user_defaults_session_updates

        data = {"your_age": 61}
        updates = build_user_defaults_session_updates(data, as_spouse=False)
        assert "growth_rate" not in updates

    def test_growth_rate_survives_session_roundtrip(self, monkeypatch) -> None:
        import streamlit as st

        from views.setup._state import _user_defaults_from_session

        monkeypatch.setattr(st, "session_state", {"growth_rate": 9.0, "your_age": 61})

        payload = _user_defaults_from_session()
        assert "growth_rate" in payload, (
            "_user_defaults_from_session must include growth_rate in payload"
        )
        assert payload["growth_rate"] == pytest.approx(9.0)

    def test_growth_rate_absent_not_in_payload(self, monkeypatch) -> None:
        """When growth_rate absent from session, not forced to 7% default."""
        import streamlit as st

        from views.setup._state import _user_defaults_from_session

        monkeypatch.setattr(st, "session_state", {"your_age": 61})

        payload = _user_defaults_from_session()
        assert "growth_rate" not in payload
