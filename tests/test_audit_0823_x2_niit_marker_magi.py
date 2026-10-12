"""audit-0823 differential/X2: the Sweet Spot NIIT vline is bisected on the
wrong MAGI.

views/sweet_spot.py:248 places the "NIIT $250K" marker with
magi_boundary_conversion, which bisects until ``ConversionResult.magi`` reaches
the threshold (engine/sweet_spot_compute.py:634). ``.magi`` is the
IRMAA-compatible figure (:702) -- IRC section 1839(i)(4). NIIT is not charged on
it. NIIT is charged on ``.niit_magi`` (:806), assembled at :797 as

    niit_magi = opt + conv + tss + net_inv_income + niit_magi_addl
    magi      = opt + conv + tss                  + magi_addl

so they differ in two ways: ``niit_magi`` EXCLUDES tax-exempt muni interest
(excluded from gross income under IRC section 103, so it was never in
AGI/MAGI to begin with), and it ADDS the manual "Additional net investment
income ($/yr)" input that the view collects at :65.

    niit_magi = magi - tax_exempt_interest + net_inv_income

The decisive detail is the draw guard at views/sweet_spot.py:251:

    if 0 < niit_conv < max_conv and net_inv_income > 0:

The marker is drawn ONLY when net_inv_income > 0 -- which is precisely the
condition under which niit_magi EXCEEDS magi. So in every configuration where
this line is visible at all, it sits too far RIGHT, and it under-warns: the
chart tells the household it may convert more before NIIT bites than it
actually may. The muni leg pushes the other way (over-warning) and is latent
rather than live, since with net_inv_income == 0 no line is drawn.

It also puts the chart in contradiction with itself. The shaded purple NIIT
band comes from ``all_in_at_conversion``'s ``niit_delta``, which is computed
correctly on ``niit_magi``; only the vline marking that band's onset uses the
IRMAA figure. That is the same "one page, one question, two answers" shape
audit-0809 named Class B and fixed for these very lines in PR #438 -- the
IRMAA/naive-closed-form leg was corrected there while this wrong-variable leg
survived.

Why the PR #438 guards in tests/test_audit_0809_sweet_spot_magi_vlines.py do
not catch it: they call magi_boundary_conversion with net_inv_income left at
its 0.0 default and assert on ``.magi``. With no manual NII and no muni in that
fixture, niit_magi == magi identically, so the distinction those tests would
need to see does not exist in the fixture they build.
"""

import pytest

from engine.niit import NIIT_THRESHOLD_MFJ
from engine.sweet_spot_compute import (
    all_in_at_conversion,
    base_income_for_year,
    magi_boundary_conversion,
)
from models.household import Household

# The manual "Additional net investment income ($/yr)" figure. Any value > 0
# reproduces the defect; this one is large enough that the resulting NIIT is a
# material dollar figure rather than a rounding artefact.
NII = 40_000.0


def _make_household() -> Household:
    """MFJ 66/64, both claiming SS at 62, benefits sized (~$30K/yr combined) so
    the conversion sweep passes THROUGH the section 86(b) partial-taxability
    transition rather than starting saturated.

    Same shape as TestNaiveClosedFormIsUnsafe._make_household in
    tests/test_audit_0809_sweet_spot_magi_vlines.py, restated here rather than
    imported so this gate does not break when that file's fixture is retuned.
    """
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
    )


class TestIrmaaMagiBisectionIsWrongForNiit:
    """Documents the defect using the superseded call shape, reproduced inline.

    These assertions hold BOTH before and after the fix -- the default stays
    ``magi_kind="irmaa"``, which is correct for the IRMAA tier lines that share
    this helper. What they pin is that bisecting on ``.magi`` is the wrong
    question to ask for the NIIT line.
    """

    def test_bisecting_on_irmaa_magi_overshoots_the_niit_line_by_the_manual_nii(
        self,
    ) -> None:
        hh = _make_household()
        base = base_income_for_year(hh, 2026)
        threshold = float(NIIT_THRESHOLD_MFJ)
        assert base.combined_ss > 0, "precondition: SS must be active"

        # The superseded call: bisect on the IRMAA-compatible MAGI.
        marker = magi_boundary_conversion(hh, base, threshold, NII, 0.0)
        assert marker > 0, "precondition: the NIIT line must be on-chart"

        at_marker = all_in_at_conversion(hh, base, marker, NII)

        # It does land on the threshold -- in the wrong quantity.
        assert at_marker.magi == pytest.approx(threshold, abs=1.0)

        # And NIIT MAGI is already past it, by exactly the manual NII, because
        # niit_magi = magi + net_inv_income once muni is absent.
        assert at_marker.niit_magi > threshold + 1.0
        assert at_marker.niit_magi - threshold == pytest.approx(NII, abs=1.0), (
            f"expected the overshoot to be exactly the manual NII ${NII:,.0f}; "
            f"got ${at_marker.niit_magi - threshold:,.2f}"
        )

        # The user-visible consequence: at the very conversion the chart marks
        # as the ONSET of NIIT, NIIT is already being charged.
        assert at_marker.niit_delta > 0.0, (
            "precondition drifted: NIIT should already be charged at the naive marker"
        )


class TestNiitMarkerMeasuresNiitMagi:
    """The corrected oracle: measure the quantity NIIT is actually charged on."""

    def test_marker_lands_on_the_threshold_in_niit_magi(self) -> None:
        hh = _make_household()
        base = base_income_for_year(hh, 2026)
        threshold = float(NIIT_THRESHOLD_MFJ)

        marker = magi_boundary_conversion(hh, base, threshold, NII, 0.0, magi_kind="niit")
        assert marker > 0, "precondition: the NIIT line must be on-chart"

        at_marker = all_in_at_conversion(hh, base, marker, NII)
        assert at_marker.niit_magi <= threshold + 1.0, (
            f"marker conversion ${marker:,.0f} yields NIIT MAGI "
            f"${at_marker.niit_magi:,.2f}, above the ${threshold:,.0f} threshold "
            "-- the line still sits too far right and under-warns"
        )
        assert at_marker.niit_magi >= threshold - 1.0, (
            f"marker conversion ${marker:,.0f} yields NIIT MAGI "
            f"${at_marker.niit_magi:,.2f} -- undershoots, the line would sit too "
            "far LEFT and understate the room"
        )

    def test_no_niit_is_charged_at_the_marker(self) -> None:
        """The line marks the ONSET of NIIT, so NIIT must be zero at it and
        positive just past it. This is the assertion the chart's purple band
        and its vline have to agree on."""
        hh = _make_household()
        base = base_income_for_year(hh, 2026)
        threshold = float(NIIT_THRESHOLD_MFJ)

        marker = magi_boundary_conversion(hh, base, threshold, NII, 0.0, magi_kind="niit")
        assert all_in_at_conversion(hh, base, marker, NII).niit_delta == pytest.approx(
            0.0, abs=1e-6
        ), "NIIT is already charged at the marker the chart calls its onset"
        assert all_in_at_conversion(hh, base, marker + 5_000.0, NII).niit_delta > 0.0, (
            "no NIIT past the marker -- the fixture no longer straddles the "
            "threshold and this guard has lost its meaning"
        )

    def test_niit_marker_sits_left_of_the_irmaa_magi_marker(self) -> None:
        """Direction-of-error guard: correcting the variable can only move this
        marker LEFT (less room), never right, whenever manual NII is present."""
        hh = _make_household()
        base = base_income_for_year(hh, 2026)
        threshold = float(NIIT_THRESHOLD_MFJ)

        naive = magi_boundary_conversion(hh, base, threshold, NII, 0.0)
        fixed = magi_boundary_conversion(hh, base, threshold, NII, 0.0, magi_kind="niit")
        assert fixed < naive, (
            f"corrected marker ${fixed:,.0f} must sit below the IRMAA-MAGI "
            f"marker ${naive:,.0f} when manual NII is present"
        )


class TestIrmaaLinesAreUnaffected:
    """Non-regression: the IRMAA tier lines share this helper and must be
    byte-identical before and after. ``magi_kind`` defaults to "irmaa"."""

    def test_default_still_measures_irmaa_magi(self) -> None:
        hh = _make_household()
        base = base_income_for_year(hh, 2026)
        threshold = float(NIIT_THRESHOLD_MFJ)

        explicit = magi_boundary_conversion(hh, base, threshold, NII, 0.0, magi_kind="irmaa")
        default = magi_boundary_conversion(hh, base, threshold, NII, 0.0)
        assert explicit == default

        at_marker = all_in_at_conversion(hh, base, explicit, NII)
        assert at_marker.magi == pytest.approx(threshold, abs=1.0)

    def test_both_kinds_agree_when_niit_magi_equals_magi(self) -> None:
        """With no manual NII and no muni interest, niit_magi == magi
        identically, so the two modes must return the same boundary. This is
        the partition that proves the fix bites only where the two quantities
        actually differ, and does not re-baseline the existing IRMAA markers."""
        hh = _make_household()
        base = base_income_for_year(hh, 2026)
        threshold = float(NIIT_THRESHOLD_MFJ)

        at_zero = all_in_at_conversion(hh, base, 0.0, 0.0)
        assert at_zero.niit_magi == pytest.approx(at_zero.magi, abs=0.01), (
            "precondition: fixture must have no muni interest and no modelled "
            "NII, so the two MAGI definitions coincide"
        )

        irmaa_kind = magi_boundary_conversion(hh, base, threshold, 0.0, 0.0)
        niit_kind = magi_boundary_conversion(hh, base, threshold, 0.0, 0.0, magi_kind="niit")
        assert irmaa_kind == pytest.approx(niit_kind, abs=0.01)
