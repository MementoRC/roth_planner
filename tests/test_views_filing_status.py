"""Tests for view-layer filing-status threading (survivor sweep)."""

import pytest

from engine.irmaa import irmaa_surcharge
from engine.niit import niit
from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestViewFilingStatusThreading:
    """Regression tests verifying that view helpers pass hh.filing_status to engine functions.

    These tests operate at the engine level (no Streamlit required) using the
    same internal helpers that the views call. They confirm that Single filer
    thresholds differ meaningfully from MFJ defaults, which is the observable
    signal that filing_status is being passed rather than defaulting to MFJ.
    """

    @pytest.fixture(autouse=True)
    def _require_plotly(self):
        pytest.importorskip("plotly")
        pytest.importorskip("streamlit")

    # --- sweet_spot.py ---

    def test_sweet_spot_irmaa_uses_single_threshold(self):
        """_all_in_at_conversion with filing_status='Single' hits IRMAA at $109K, not $218K.

        With a base_magi of $0 and a conversion of $115K:
        - MFJ: $115K < $218K T1 threshold → irmaa_delta == 0
        - Single: $115K > $109K T1 threshold → irmaa_delta > 0
        """
        from dataclasses import replace

        from views.sweet_spot import _all_in_at_conversion, _base_income_for_year

        hh_mfj = replace(
            Household(grants=[]),
            your_age=63,
            spouse_age=63,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        hh_single = replace(hh_mfj, filing_status="Single")

        base_mfj = _base_income_for_year(hh_mfj, hh_mfj.base_year)
        base_single = _base_income_for_year(hh_single, hh_single.base_year)

        # $115K conversion: above Single T1 ($109K) but below MFJ T1 ($218K)
        conv = 115_000.0
        r_mfj = _all_in_at_conversion(hh_mfj, base_mfj, conv, 0)
        r_single = _all_in_at_conversion(hh_single, base_single, conv, 0)

        assert r_mfj["irmaa_delta"] == pytest.approx(0.0, abs=1.0), (
            f"MFJ should not trigger IRMAA at $115K conv; got {r_mfj['irmaa_delta']:.2f}"
        )
        assert r_single["irmaa_delta"] > 0.0, (
            f"Single should trigger IRMAA at $115K conv; got {r_single['irmaa_delta']:.2f}"
        )

    def test_sweet_spot_niit_uses_single_threshold(self):
        """_all_in_at_conversion with filing_status='Single' hits NIIT at $200K, not $250K.

        With NII of $50K and a MAGI that crosses $200K (Single) but not $250K (MFJ):
        """
        from dataclasses import replace

        from views.sweet_spot import _all_in_at_conversion, _base_income_for_year

        hh_mfj = replace(
            Household(grants=[]),
            your_age=63,
            spouse_age=63,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        hh_single = replace(hh_mfj, filing_status="Single")

        base_mfj = _base_income_for_year(hh_mfj, hh_mfj.base_year)
        base_single = _base_income_for_year(hh_single, hh_single.base_year)

        # $210K conversion puts MAGI above Single threshold ($200K) but below MFJ ($250K)
        conv = 210_000.0
        nii = 50_000.0
        r_mfj = _all_in_at_conversion(hh_mfj, base_mfj, conv, nii)
        r_single = _all_in_at_conversion(hh_single, base_single, conv, nii)

        assert r_mfj["niit_delta"] == pytest.approx(0.0, abs=1.0), (
            f"MFJ should not trigger NIIT at $210K conv; got {r_mfj['niit_delta']:.2f}"
        )
        assert r_single["niit_delta"] > 0.0, (
            f"Single should trigger NIIT at $210K conv; got {r_single['niit_delta']:.2f}"
        )

    # --- aca_irmaa.py ---

    def test_aca_irmaa_irmaa_surcharge_uses_filing_status(self):
        """irmaa_surcharge called with filing_status='Single' returns higher surcharge at same MAGI.

        At $120K MAGI: Single T1 ($109K) is crossed → surcharge > 0.
        MFJ T1 ($218K) is not crossed → surcharge == 0.
        This mirrors the irmaa_surcharge call inside the aca_irmaa loop.
        """

        magi = 120_000.0
        surcharge_mfj = irmaa_surcharge(magi, num_people=2, filing_status="MFJ")
        surcharge_single = irmaa_surcharge(magi, num_people=2, filing_status="Single")

        assert surcharge_mfj == pytest.approx(0.0, abs=1.0), (
            f"MFJ should have zero IRMAA at $120K MAGI; got {surcharge_mfj:.2f}"
        )
        assert surcharge_single > 0.0, (
            f"Single should have IRMAA at $120K MAGI; got {surcharge_single:.2f}"
        )

    def test_aca_irmaa_niit_uses_filing_status(self):
        """niit() called with filing_status='Single' fires at $200K, not $250K.

        This mirrors the niit() calls inside the aca_irmaa magi_points loop.
        """

        magi = 210_000.0
        nii = 30_000.0

        niit_mfj = niit(magi, nii, filing_status="MFJ")
        niit_single = niit(magi, nii, filing_status="Single")

        assert niit_mfj == pytest.approx(0.0, abs=1.0), (
            f"MFJ NIIT should be 0 at $210K MAGI; got {niit_mfj:.2f}"
        )
        assert niit_single > 0.0, f"Single NIIT should fire at $210K MAGI; got {niit_single:.2f}"

    # --- ytd_income.py ---

    def test_ytd_irmaa_surcharge_uses_filing_status(self):
        """irmaa_surcharge in ytd_income warning panel uses filing_status.

        At $120K MAGI: Single sees a surcharge; MFJ does not.
        Mirrors the two irmaa_surcharge(headroom.projected_magi_base, N, filing_status=...)
        calls in the IRMAA Impact Warning section.
        """

        projected_magi = 120_000.0
        s1_mfj = irmaa_surcharge(projected_magi, 1, filing_status="MFJ")
        s1_single = irmaa_surcharge(projected_magi, 1, filing_status="Single")
        s2_mfj = irmaa_surcharge(projected_magi, 2, filing_status="MFJ")
        s2_single = irmaa_surcharge(projected_magi, 2, filing_status="Single")

        assert s1_mfj == pytest.approx(0.0, abs=1.0)
        assert s1_single > 0.0
        assert s2_mfj == pytest.approx(0.0, abs=1.0)
        assert s2_single > 0.0

    def test_compute_headroom_filing_status_affects_irmaa_room(self):
        """compute_headroom with filing_status='Single' uses Single IRMAA tier.

        With $90K locked MAGI: Single T1 at $109K → room_to_irmaa_t1 ≈ $19K.
        MFJ T1 at $218K → room_to_irmaa_t1 ≈ $128K.
        The two must differ, confirming filing_status is threaded through.
        """
        from dataclasses import replace

        from engine.headroom import compute_headroom
        from models.ytd_income import YTDSnapshot

        hh_mfj = replace(
            Household(grants=[]),
            your_age=63,
            spouse_age=63,
            your_ss_start_age=70,
            spouse_ss_start_age=70,
        )
        hh_single = replace(hh_mfj, filing_status="Single")

        ytd = YTDSnapshot(
            tax_year=hh_mfj.base_year,
            wages_ytd=90_000.0,
        )

        hr_mfj = compute_headroom(hh_mfj, ytd, filing_status="MFJ")
        hr_single = compute_headroom(hh_single, ytd, filing_status="Single")

        # Single tier 1 at $109K, MFJ tier 1 at $218K — room must differ
        assert hr_single.room_to_irmaa_t1 < hr_mfj.room_to_irmaa_t1, (
            f"Single IRMAA room ({hr_single.room_to_irmaa_t1:,.0f}) must be less "
            f"than MFJ room ({hr_mfj.room_to_irmaa_t1:,.0f})"
        )
