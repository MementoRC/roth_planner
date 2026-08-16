"""audit-0809 #01 (HIGH, Class B): the Sweet Spot "Fill to 12%" / "Fill to 22%"
cards rendered ConversionResult.room_12/.room_22 -- documented on that dataclass
as GROSS-INCOME room, valid only when taxable SS is conversion-invariant -- as
the recommended CONVERSION amount.

C23 (audit-0805) already established that this is wrong and routed
compute_multi_year_summary's fill_12/fill_22 through the module's own
SS-torpedo-aware bracket_boundary_conversion oracle. It converted the multi-year
TABLE and left the CARDS, so a single page showed two different answers to one
question -- the exact shape audit-0809 names Class B ("a fix applied to one
consumer and not its sibling").

See tests/test_sweet_spot_compute.py::TestComputeMultiYearSummaryFillBoundarySsTorpedo
for the C23 gate on the table side, and that module's
TestBracketBoundarySsTaxabilityNonlinearity for the IRC 86(b) mechanics.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from engine.sweet_spot_compute import (
    all_in_at_conversion,
    base_income_for_year,
    bracket_boundary_conversion,
    compute_multi_year_summary,
    fill_conversions_for_year,
    zero_conversion_ira_draws,
)
from engine.tax import BRACKETS_MFJ
from engine.tax_indexing import index_value
from models.household import Household

# Ages/SS sized so the conversion sweep crosses the 50%/85% partial-taxability
# transition zone -- the only regime where the naive gross-income room and the
# true boundary conversion diverge. Same fixture as
# TestComputeMultiYearSummaryFillBoundarySsTorpedo.
_HH_KWARGS = {
    "your_age": 66,
    "spouse_age": 64,
    "base_year": 2026,
    "your_ss_start_age": 62,
    "spouse_ss_start_age": 62,
    "your_ss_fra": 1_500.0,
    "spouse_ss_fra": 1_000.0,
    "your_fra_age": 67,
    "spouse_fra_age": 67,
    "filing_status": "MFJ",
    "ss_cola": 0.0,
}


def _ss_torpedo_household(cpi: float = 0.0) -> Household:
    return Household(cpi_assumption=cpi, grants=[], **_HH_KWARGS)


def _render_ss_torpedo_sweet_spot() -> None:
    """AppTest entry point -- must be fully self-contained (all imports and
    object construction inside the body), mirroring _render_sweet_spot in
    tests/test_command_center_w4_nii_shared_key.py."""
    from models.household import Household
    from views.sweet_spot import render

    render(
        Household(
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
    )


class TestSweetSpotCardsMatchMultiYearTable:
    """The user-visible gate: the "Fill to 12%" card and the multi-year table's
    "Fill 12%" cell describe the same quantity for the same year, so they must
    render the same string. Pre-fix the card showed the naive gross-income room
    and the table showed the SS-torpedo-aware conversion."""

    def test_fill_cards_match_table_row_for_selected_year(self) -> None:
        at = AppTest.from_function(_render_ss_torpedo_sweet_spot, default_timeout=180)
        at.run()
        assert not at.exception, f"page raised: {at.exception}"

        conversion_metrics = [m for m in at.metric if m.label == "Conversion"]
        assert len(conversion_metrics) == 3, (
            "expected exactly 3 'Conversion' metrics (Fill 12%, Fill 22%, "
            f"IRMAA-Safe Max), got {len(conversion_metrics)}"
        )

        assert at.dataframe, "the multi-year summary table must render"
        df = at.dataframe[0].value
        # The year selectbox defaults to the first conversion year (base_year).
        row = df[df["Year"] == "2026"]
        assert len(row) == 1, f"expected exactly one 2026 row, got {len(row)}"

        assert conversion_metrics[0].value == row["Fill 12%"].iloc[0], (
            f"'Fill to 12%' card shows {conversion_metrics[0].value} but the "
            f"table's 2026 row shows {row['Fill 12%'].iloc[0]} -- one page, "
            "two answers to one question"
        )
        assert conversion_metrics[1].value == row["Fill 22%"].iloc[0], (
            f"'Fill to 22%' card shows {conversion_metrics[1].value} but the "
            f"table's 2026 row shows {row['Fill 22%'].iloc[0]}"
        )


class TestFillConversionsSharedHelper:
    """fill_conversions_for_year is the single derivation both the table and the
    cards must read, so the two cannot drift apart again."""

    def test_lands_on_both_ceilings_not_naive_gross_room(self) -> None:
        hh = _ss_torpedo_household()
        year = hh.base_year
        draws = zero_conversion_ira_draws(hh)
        base = base_income_for_year(hh, year, ira_draw=draws.get(year, 0.0))
        assert base.combined_ss > 0, "precondition: SS must be active"

        fill_12, fill_22 = fill_conversions_for_year(hh, base)

        # cpi_assumption=0.0 and year == BASE_YEAR, so the statutory literals
        # are the indexed ceilings.
        ceiling_12 = BRACKETS_MFJ[1][0]
        ceiling_22 = BRACKETS_MFJ[2][0]

        for fill, ceiling, label in (
            (fill_12, ceiling_12, "12%"),
            (fill_22, ceiling_22, "22%"),
        ):
            result = all_in_at_conversion(hh, base, fill, 0.0)
            assert result.taxable_inc == pytest.approx(ceiling, abs=1.0), (
                f"fill_{label}={fill:.0f} produced taxable_inc="
                f"{result.taxable_inc:.0f}, must land exactly on the {label} "
                f"ceiling ({ceiling:.0f})"
            )

        # The pre-fix card values, reconstructed inline so this stays meaningful
        # even after the production formula changes: the closed-form room fields
        # off a zero-conversion ConversionResult, fed back in as a conversion.
        naive = all_in_at_conversion(hh, base, 0, 0.0)
        assert fill_12 < naive.room_12 - 1_000, (
            f"fill_12 ({fill_12:.0f}) must be materially below the naive "
            f"SS-invariant room_12 ({naive.room_12:.0f}) -- the cards rendered "
            "the naive value as a conversion recommendation"
        )
        assert fill_22 < naive.room_22 - 1_000, (
            f"fill_22 ({fill_22:.0f}) must be materially below the naive "
            f"SS-invariant room_22 ({naive.room_22:.0f})"
        )

    def test_agrees_with_multi_year_table_for_every_year(self) -> None:
        """The table is the consumer C23 already fixed. The helper must reproduce
        it exactly -- this is what makes the card/table divergence impossible."""
        hh = _ss_torpedo_household()
        draws = zero_conversion_ira_draws(hh)
        rows = compute_multi_year_summary(hh, ira_draws=draws)
        assert rows, "precondition: the household must have a conversion window"

        for row in rows:
            base = base_income_for_year(hh, row.year, ira_draw=draws.get(row.year, 0.0))
            fill_12, fill_22 = fill_conversions_for_year(hh, base)
            assert fill_12 == pytest.approx(row.fill_12, abs=0.01), (
                f"{row.year}: helper fill_12 {fill_12:.2f} != table {row.fill_12:.2f}"
            )
            assert fill_22 == pytest.approx(row.fill_22, abs=0.01), (
                f"{row.year}: helper fill_22 {fill_22:.2f} != table {row.fill_22:.2f}"
            )

    def test_uses_fifty_dollar_rounded_ceilings_in_indexed_years(self) -> None:
        """Ordinary bracket ceilings round to the nearest $50 per IRC 1(f)(6);
        every bracket-indexing call in engine/tax.py passes round50=True. A
        helper that skipped it would put the cards tens of dollars off the
        engine's own brackets in any post-2026 year."""
        hh = _ss_torpedo_household(cpi=0.025)
        year = hh.base_year + 4
        base = base_income_for_year(hh, year)

        ceiling_12 = index_value(BRACKETS_MFJ[1][0], year, 0.025, round50=True)
        assert ceiling_12 % 50 == 0, "precondition: rounded ceiling is a $50 multiple"
        assert ceiling_12 != index_value(BRACKETS_MFJ[1][0], year, 0.025), (
            "precondition: rounding must actually move this ceiling, else the "
            "assertion below is vacuous"
        )

        fill_12, _ = fill_conversions_for_year(hh, base)
        assert fill_12 > 0, "precondition: the 12% ceiling must be reachable"
        result = all_in_at_conversion(hh, base, fill_12, 0.0)
        assert result.taxable_inc == pytest.approx(ceiling_12, abs=1.0), (
            f"fill_12 must land on the $50-rounded ceiling {ceiling_12:.0f}, "
            f"got taxable_inc {result.taxable_inc:.2f}"
        )

    def test_matches_direct_bracket_boundary_conversion(self) -> None:
        """The helper adds ceiling derivation, nothing else -- it must not alter
        the oracle's answer for a ceiling the caller already holds."""
        hh = _ss_torpedo_household()
        base = base_income_for_year(hh, hh.base_year)

        fill_12, fill_22 = fill_conversions_for_year(hh, base)
        assert fill_12 == pytest.approx(
            bracket_boundary_conversion(hh, base, BRACKETS_MFJ[1][0]), abs=0.01
        )
        assert fill_22 == pytest.approx(
            bracket_boundary_conversion(hh, base, BRACKETS_MFJ[2][0]), abs=0.01
        )
