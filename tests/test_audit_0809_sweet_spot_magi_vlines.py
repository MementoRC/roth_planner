"""audit-0809 Class B: the Sweet Spot chart's IRMAA and NIIT threshold vlines.

views/sweet_spot.py draws its IRMAA tier lines at `threshold - base.base_magi`
(:224) and its NIIT line the same way (:235), while the bracket guide-lines
immediately above them already bisect via bracket_boundary_conversion and the
"IRMAA-Safe Max" card binary-searches via irmaa_safe_max. One page, one
question -- "how much can I convert before this cliff?" -- and two different
answers. That is the shape audit-0809 names Class B.

The naive subtraction assumes MAGI rises exactly $1 per $1 converted. It does
not: once provisional income sits in the 50%/85% partial-taxability zone (IRC
section 86(b)), each converted dollar also drags more Social Security into
MAGI, so MAGI rises FASTER than 1-per-1 and the true boundary sits BELOW the
naive estimate. Drawing the marker at the naive value puts every cliff line
too far RIGHT -- it tells the household it may convert more than it actually
can before crossing the tier.
"""

from engine.irmaa import IRMAA_TIERS_MFJ, _index_irmaa_tiers
from engine.niit import NIIT_THRESHOLD_MFJ
from engine.sweet_spot_compute import (
    STEP,
    all_in_at_conversion,
    base_income_for_year,
    irmaa_safe_max,
    magi_boundary_conversion,
)
from models.household import Household


class TestNaiveClosedFormIsUnsafe:
    """Why views/sweet_spot.py must never go back to `threshold - base_magi`.

    Mirrors test_bracket_boundary_overshoots_without_ss_nonlinearity in
    tests/test_sweet_spot_compute.py: reproduce the superseded closed form
    INLINE so this guard keeps its meaning even after the production formula
    changes, then show the oracle the view now uses is materially safer.
    """

    def _make_household(self) -> Household:
        # Same shape as TestBracketBoundarySsTaxabilityNonlinearity in
        # tests/test_sweet_spot_compute.py: MFJ 66/64, both claiming SS at 62
        # and sized (~$30K/yr combined) so the conversion sweep passes THROUGH
        # the SS partial-taxability transition rather than starting saturated.
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

    def test_oracle_beats_the_naive_estimate_at_every_irmaa_tier(self) -> None:
        hh = self._make_household()
        base = base_income_for_year(hh, 2026)
        assert base.combined_ss > 0, "precondition: SS must be active"
        tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2028, hh.cpi_assumption)

        checked = 0
        for threshold, _, _ in tiers:
            naive = threshold - base.base_magi  # the superseded closed form
            if naive <= 0:
                continue
            checked += 1

            # The closed form really is unsafe: converting the amount it would
            # have placed the marker at breaches the very tier the line is
            # labelled with.
            naive_magi = all_in_at_conversion(hh, base, naive, 0.0).magi
            assert naive_magi > threshold + 1.0, (
                f"tier ${threshold:,.0f}: the naive estimate no longer "
                "overshoots -- this guard has lost its meaning and the SS "
                "fixture probably drifted"
            )

            # The oracle the view now uses lands ON the tier instead.
            oracle = magi_boundary_conversion(hh, base, threshold)
            assert oracle < naive, (
                f"tier ${threshold:,.0f}: oracle ${oracle:,.0f} must sit below "
                f"the naive ${naive:,.0f}"
            )
            oracle_magi = all_in_at_conversion(hh, base, oracle, 0.0).magi
            assert oracle_magi <= threshold + 1.0, (
                f"tier ${threshold:,.0f}: oracle conversion ${oracle:,.0f} "
                f"yields MAGI ${oracle_magi:,.0f}, above the tier"
            )

        assert checked, "precondition: at least one IRMAA tier must be on-chart"

    def test_oracle_beats_the_naive_estimate_at_the_niit_threshold(self) -> None:
        hh = self._make_household()
        base = base_income_for_year(hh, 2026)
        assert base.combined_ss > 0, "precondition: SS must be active"

        threshold = float(NIIT_THRESHOLD_MFJ)
        naive = threshold - base.base_magi
        assert naive > 0, "precondition: the NIIT line must be on-chart"

        naive_magi = all_in_at_conversion(hh, base, naive, 0.0).magi
        assert naive_magi > threshold + 1.0, (
            "the naive estimate no longer overshoots the NIIT threshold -- "
            "this guard has lost its meaning"
        )

        oracle = magi_boundary_conversion(hh, base, threshold)
        assert oracle < naive
        oracle_magi = all_in_at_conversion(hh, base, oracle, 0.0).magi
        assert oracle_magi <= threshold + 1.0, (
            f"oracle conversion ${oracle:,.0f} yields MAGI ${oracle_magi:,.0f}, "
            f"above the ${threshold:,.0f} NIIT threshold"
        )


class TestMagiBoundaryOracle:
    """The replacement oracle must land ON each threshold, never above it, and
    must not drift from the IRMAA-Safe Max card that answers the neighbouring
    question."""

    def _make_household(self) -> Household:
        return TestNaiveClosedFormIsUnsafe()._make_household()

    def test_oracle_lands_on_each_irmaa_tier(self) -> None:
        hh = self._make_household()
        base = base_income_for_year(hh, 2026)
        tiers = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2028, hh.cpi_assumption)
        for threshold, _, _ in tiers:
            conv = magi_boundary_conversion(hh, base, threshold)
            if conv <= 0:
                continue
            achieved = all_in_at_conversion(hh, base, conv, 0.0).magi
            assert achieved <= threshold + 1.0, (
                f"tier ${threshold:,.0f}: oracle conversion ${conv:,.0f} still "
                f"yields MAGI ${achieved:,.0f}"
            )
            assert achieved >= threshold - 1.0, (
                f"tier ${threshold:,.0f}: oracle conversion ${conv:,.0f} yields "
                f"MAGI ${achieved:,.0f} -- undershoots, the marker would sit "
                "too far LEFT and understate the room"
            )

    def test_oracle_is_below_the_naive_estimate(self) -> None:
        hh = self._make_household()
        base = base_income_for_year(hh, 2026)
        threshold = float(NIIT_THRESHOLD_MFJ)
        naive = threshold - base.base_magi
        oracle = magi_boundary_conversion(hh, base, threshold)
        assert oracle < naive - 1_000, (
            f"oracle ${oracle:,.0f} should sit materially below the naive "
            f"estimate ${naive:,.0f} once SS taxability is folded in"
        )

    def test_oracle_agrees_with_irmaa_safe_max_within_one_step(self) -> None:
        """Class B guard: the chart marker and the IRMAA-Safe Max card answer
        neighbouring questions and must not drift. irmaa_safe_max floors to the
        STEP grid, so it sits at or just below the exact boundary."""
        hh = self._make_household()
        base = base_income_for_year(hh, 2026)
        tier1 = _index_irmaa_tiers(IRMAA_TIERS_MFJ, 2028, hh.cpi_assumption)[0][0]
        exact = magi_boundary_conversion(hh, base, tier1)
        safe = irmaa_safe_max(hh, base, tier1)
        assert safe <= exact + 1.0, (
            f"irmaa_safe_max ${safe:,.0f} exceeds the exact boundary ${exact:,.0f}"
        )
        assert exact - safe < STEP, (
            f"irmaa_safe_max ${safe:,.0f} and the exact boundary ${exact:,.0f} "
            f"differ by more than one STEP (${STEP:,.0f}) -- they have drifted"
        )
