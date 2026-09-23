"""Tests for engine/ubs_activity_csv.py -- UBS ACTIVITY CSV export parser.

All fixtures below are SYNTHETIC (invented reference numbers, grant numbers,
prices, dates) constructed to match the *shape* of 3 real UBS exports
described in the implementation plan -- no real account data appears here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from engine.ubs_activity_csv import (
    FEDERAL_SUPPLEMENTAL_RATE,
    MEDICARE_RATE,
    SOCIAL_SECURITY_RATE,
    UbsActivityParseError,
    federal_withholding,
    load_ubs_activity_folder,
    observed_withholding_rate,
    parse_ubs_activity_text,
    withholding_matches_expected_blend,
)

# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

_ORDER_HEADER = (
    '"Reference Number","Source","Entry Date (m/d/yyyy)","Execution Date (m/d/yyyy)",'
    '"Quantity","Order Type","Transaction Type","Limit Price (USD)","Execution Price (USD)",'
    '"Entered Through","Proceeds Method","Funds Available Date (m/d/yyyy)",'
    '"Total Shares Delivered"'
)
_LOT_HEADER = (
    '"Grant Number","Grant Date (m/d/yyyy)","Grant Price (USD)","Grant Quantity",'
    '"Execution Quantity","Open Quantity","Cancel Quantity","Total Fees (USD)",'
    '"Total Taxes (USD)","Gross Proceeds (USD)","Net Proceeds (USD)",'
    '"Funds Available Date (m/d/yyyy)"'
)
_EXCHANGE_HEADER = (
    '"Exchange Event Date (m/d/yyyy)","Order Status","Price (USD)","Executed Quantity",'
    '"Event Message"'
)

# One order, one lot. Numbers hand-verified to satisfy the reconciliation
# invariant exactly to the cent:
#   strike_cost = 90.00 * 40 = 3600.00
#   bargain_element = 7200.15 - 3600.00 = 3600.15
#   net = 7200.15 - 3600.00 - 8.00 - 1067.44 = 2524.71
# gross_proceeds (7200.15) deliberately differs by 15 cents from
# execution_quantity * execution_price (40 * 180.00 = 7200.00) -- mirrors the
# observed real-world quirk that reported gross_proceeds can differ from the
# quantity*price cross-check by a few cents; reported figures are the source
# of truth.
_BASIC_ORDER_ROW = (
    '"FA209912315000","Options","3/15/2026","3/15/2026","40","MKT","SDS","N/A","180.00",'
    '"WEB","","3/19/2026","0"'
)
_BASIC_LOT_ROW = (
    '"S1234567890","2/1/2020","90.00","40","40","0","0","8.00","1,067.44","7,200.15",'
    '"2,524.71","3/19/2026"'
)
_EXCHANGE_ROW = '"3/20/2026","EXE","185.00","40","FULL EXECUTION"'


def _wrap(order_header_and_rows: str, lot_header_and_rows: str, exchange_block: str = "") -> str:
    """Assemble a full synthetic CSV export text from section pieces."""
    parts = ["ACTIVITY", order_header_and_rows, "", lot_header_and_rows]
    if exchange_block:
        parts.extend(["", exchange_block])
    return "\n".join(parts) + "\n"


BASIC_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{_BASIC_LOT_ROW}',
    f'{_EXCHANGE_HEADER}\n"Exchange Events",,,,,\n{_EXCHANGE_ROW}',
)

# --- Trap 2: "Total" row placement varies -----------------------------------

TOTAL_BEFORE_LABEL_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Total",,,,,,,,,,,,\n"Order Summary",,,,,,,,,,,,\n{_BASIC_LOT_ROW}',
    f'{_EXCHANGE_HEADER}\n"Exchange Events",,,,,\n{_EXCHANGE_ROW}',
)

TOTAL_INSIDE_EXCHANGE_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{_BASIC_LOT_ROW}',
    f'{_EXCHANGE_HEADER}\n"Exchange Events",,,,,\n{_EXCHANGE_ROW}\n"Total",,,,',
)

NO_TOTAL_FIXTURE = BASIC_FIXTURE

# --- Trap 5: multiple grant lots per order ----------------------------------
# Three lots, each independently satisfying the reconciliation invariant.
# Hand-verified:
#   A: strike=60.00*20=1200.00; gross=1600.00; bargain=400.00; taxes=29.65%*400=118.60;
#      net=1600.00-1200.00-4.00-118.60=277.40
#   B: strike=75.50*15=1132.50; gross=1432.50; bargain=300.00; taxes=29.65%*300=88.95;
#      net=1432.50-1132.50-3.50-88.95=207.55
#   C: strike=110.25*10=1102.50; gross=1302.50; bargain=200.00; taxes=29.65%*200=59.30;
#      net=1302.50-1102.50-2.00-59.30=138.70
_LOT_ROW_A = (
    '"S1000000001","1/2/2018","60.00","20","20","0","0","4.00","118.60","1,600.00",'
    '"277.40","5/1/2026"'
)
_LOT_ROW_B = (
    '"S1000000002","6/15/2019","75.50","15","15","0","0","3.50","88.95","1,432.50",'
    '"207.55","5/1/2026"'
)
_LOT_ROW_C = (
    '"S1000000003","11/3/2021","110.25","10","10","0","0","2.00","59.30","1,302.50",'
    '"138.70","5/1/2026"'
)
MULTI_LOT_ORDER_ROW = (
    '"FA209912315001","Options","5/1/2026","5/1/2026","45","MKT","SDS","N/A","172.00",'
    '"WEB","","5/1/2026","0"'
)
MULTI_LOT_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{MULTI_LOT_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{_LOT_ROW_A}\n{_LOT_ROW_B}\n{_LOT_ROW_C}',
)

# --- Invariant failure -------------------------------------------------------
_MISMATCHED_LOT_ROW = (
    '"S1234567890","2/1/2020","90.00","40","40","0","0","8.00","1,067.44","7,200.15",'
    '"9,999.99","3/19/2026"'  # net_proceeds deliberately wrong
)
MISMATCHED_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{_MISMATCHED_LOT_ROW}',
)

# --- Unknown transaction type -------------------------------------------------
_UNKNOWN_TXN_ORDER_ROW = _BASIC_ORDER_ROW.replace('"SDS"', '"LMT"')
UNKNOWN_TXN_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_UNKNOWN_TXN_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{_BASIC_LOT_ROW}',
)

# --- Missing sections ---------------------------------------------------------
NO_LOT_SECTION_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
    "",
)
EMPTY_LOT_SECTION_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,',
)

# --- Below-SS-wage-base blend (used for withholding_matches_expected_blend) --
# 22.00% federal + 1.45% Medicare only (no SS component) = 23.45% blend.
# bargain=3600.15 (same gross/strike as _BASIC_LOT_ROW); taxes=23.45%*3600.15=844.24;
# net=7200.15-3600.00-8.00-844.24=2747.91
_BELOW_BLEND_LOT_ROW = (
    '"S1234567890","2/1/2020","90.00","40","40","0","0","8.00","844.24","7,200.15",'
    '"2,747.91","3/19/2026"'
)
BELOW_SS_CAP_FIXTURE = _wrap(
    f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
    f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{_BELOW_BLEND_LOT_ROW}',
)


# ---------------------------------------------------------------------------
# Happy path / shape traps
# ---------------------------------------------------------------------------


class TestParseBasicOrder:
    def test_header_row_precedes_label_row_and_parses(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        assert order.reference_number == "FA209912315000"
        assert order.entry_date == date(2026, 3, 15)
        assert order.execution_date == date(2026, 3, 15)
        assert order.quantity == 40
        assert order.transaction_type == "SDS"
        assert order.limit_price is None  # "N/A"
        assert order.execution_price == 180.00
        assert order.proceeds_method == ""  # empty field
        assert order.funds_available_date == date(2026, 3, 19)
        assert order.total_shares_delivered == 0

    def test_single_lot_fields_and_thousands_comma_numerics(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        assert len(order.lots) == 1
        lot = order.lots[0]
        assert lot.grant_number == "S1234567890"
        assert lot.grant_date == date(2020, 2, 1)
        assert lot.grant_price == 90.00
        assert lot.total_taxes == 1067.44  # parsed from quoted "1,067.44"
        assert lot.gross_proceeds == 7200.15  # parsed from quoted "7,200.15"
        assert lot.net_proceeds == 2524.71

    def test_derived_strike_cost_and_bargain_element(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        lot = order.lots[0]
        assert lot.strike_cost == pytest.approx(3600.00)
        assert lot.bargain_element == pytest.approx(3600.15)
        assert order.bargain_element == pytest.approx(3600.15)

    def test_exchange_events_section_is_tolerated_and_ignored(self) -> None:
        # Different column count (5) than order (13) or lot (12) sections --
        # must not break parsing even though nothing is modeled from it.
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        assert order.reference_number == "FA209912315000"


class TestTotalRowPlacementVaries:
    def test_total_row_before_order_summary_label_is_not_a_lot(self) -> None:
        order = parse_ubs_activity_text(TOTAL_BEFORE_LABEL_FIXTURE)
        assert len(order.lots) == 1
        assert order.lots[0].grant_number == "S1234567890"

    def test_total_row_inside_exchange_events_does_not_break_parsing(self) -> None:
        order = parse_ubs_activity_text(TOTAL_INSIDE_EXCHANGE_FIXTURE)
        assert len(order.lots) == 1

    def test_no_total_row_at_all_still_parses(self) -> None:
        order = parse_ubs_activity_text(NO_TOTAL_FIXTURE)
        assert len(order.lots) == 1


class TestMultipleGrantLots:
    def test_three_lots_all_parsed(self) -> None:
        order = parse_ubs_activity_text(MULTI_LOT_FIXTURE)
        assert len(order.lots) == 3
        assert [lot.grant_number for lot in order.lots] == [
            "S1000000001",
            "S1000000002",
            "S1000000003",
        ]

    def test_order_bargain_element_sums_across_lots(self) -> None:
        order = parse_ubs_activity_text(MULTI_LOT_FIXTURE)
        expected = sum(lot.bargain_element for lot in order.lots)
        assert order.bargain_element == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Reconciliation invariant
# ---------------------------------------------------------------------------


class TestNetProceedsInvariant:
    def test_passing_case_does_not_raise(self) -> None:
        parse_ubs_activity_text(BASIC_FIXTURE)  # no raise

    def test_mismatched_net_proceeds_raises_naming_lot_and_both_sides(self) -> None:
        with pytest.raises(UbsActivityParseError) as exc_info:
            parse_ubs_activity_text(MISMATCHED_FIXTURE)
        message = str(exc_info.value)
        assert "S1234567890" in message
        assert "9999.99" in message or "9,999.99" in message


# ---------------------------------------------------------------------------
# Transaction type handling
# ---------------------------------------------------------------------------


class TestTransactionType:
    def test_sds_is_accepted(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        assert order.transaction_type == "SDS"

    def test_unknown_transaction_type_raises(self) -> None:
        with pytest.raises(UbsActivityParseError):
            parse_ubs_activity_text(UNKNOWN_TXN_FIXTURE)


# ---------------------------------------------------------------------------
# Missing/malformed sections
# ---------------------------------------------------------------------------


class TestMissingSections:
    def test_missing_order_summary_section_raises(self) -> None:
        with pytest.raises(UbsActivityParseError):
            parse_ubs_activity_text(NO_LOT_SECTION_FIXTURE)

    def test_order_summary_with_no_lot_rows_raises(self) -> None:
        with pytest.raises(UbsActivityParseError):
            parse_ubs_activity_text(EMPTY_LOT_SECTION_FIXTURE)


# ---------------------------------------------------------------------------
# Withholding decomposition
# ---------------------------------------------------------------------------


class TestWithholdingDecomposition:
    def test_federal_withholding_uses_only_supplemental_rate(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        expected = order.bargain_element * FEDERAL_SUPPLEMENTAL_RATE
        assert federal_withholding(order) == pytest.approx(expected)

    def test_federal_withholding_rate_is_overridable(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        custom = federal_withholding(order, supplemental_rate=0.10)
        assert custom == pytest.approx(order.bargain_element * 0.10)

    def test_federal_withholding_rejects_fica_rate_arguments(self) -> None:
        # FICA cannot move a federal-safe-harbor figure, so these arguments
        # must not be silently accepted-and-ignored -- a caller modeling a
        # wage-base-capped exercise has to be told the call is meaningless.
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        with pytest.raises(TypeError):
            federal_withholding(order, ss_rate=0.0)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            federal_withholding(order, medicare_rate=0.0)  # type: ignore[call-arg]

    def test_observed_withholding_rate_matches_blend_on_basic_fixture(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        rate = observed_withholding_rate(order)
        expected_blend = FEDERAL_SUPPLEMENTAL_RATE + SOCIAL_SECURITY_RATE + MEDICARE_RATE
        assert rate == pytest.approx(expected_blend, abs=0.001)

    def test_withholding_matches_expected_blend_true_on_basic_fixture(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)
        assert withholding_matches_expected_blend(order) is True

    def test_withholding_matches_expected_blend_false_below_ss_wage_base(self) -> None:
        # 23.45% (federal + Medicare only, no SS component) must NOT be
        # silently accepted as the full 29.65% blend.
        order = parse_ubs_activity_text(BELOW_SS_CAP_FIXTURE)
        assert withholding_matches_expected_blend(order) is False


# ---------------------------------------------------------------------------
# Folder loading
# ---------------------------------------------------------------------------


class TestLoadFolder:
    def test_loads_every_csv_in_folder(self, tmp_path: Path) -> None:
        (tmp_path / "UBS_Activity_09_22_2026.csv").write_text(BASIC_FIXTURE)
        (tmp_path / "UBS_Activity_09_22_2026 (1).csv").write_text(MULTI_LOT_FIXTURE)
        result = load_ubs_activity_folder(tmp_path)
        refs = {o.reference_number for o in result.orders}
        assert refs == {"FA209912315000", "FA209912315001"}
        assert result.errors == ()

    def test_browser_download_suffixes_are_distinct_orders_not_duplicates(
        self, tmp_path: Path
    ) -> None:
        third_order_row = _BASIC_ORDER_ROW.replace("FA209912315000", "FA209912315002")
        third_fixture = _wrap(
            f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{third_order_row}',
            f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{_BASIC_LOT_ROW}',
        )
        (tmp_path / "UBS_Activity_09_22_2026.csv").write_text(BASIC_FIXTURE)
        (tmp_path / "UBS_Activity_09_22_2026 (1).csv").write_text(MULTI_LOT_FIXTURE)
        (tmp_path / "UBS_Activity_09_22_2026 (2).csv").write_text(third_fixture)
        result = load_ubs_activity_folder(tmp_path)
        assert len(result.orders) == 3
        assert {o.reference_number for o in result.orders} == {
            "FA209912315000",
            "FA209912315001",
            "FA209912315002",
        }
        assert result.errors == ()

    def test_dedupes_on_reference_number_not_filename(self, tmp_path: Path) -> None:
        (tmp_path / "a_UBS_Activity.csv").write_text(BASIC_FIXTURE)
        (tmp_path / "b_UBS_Activity.csv").write_text(BASIC_FIXTURE)  # same reference_number
        result = load_ubs_activity_folder(tmp_path)
        assert len(result.orders) == 1
        assert result.orders[0].reference_number == "FA209912315000"
        assert result.errors == ()

    def test_unparseable_file_is_reported_not_silently_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "good.csv").write_text(BASIC_FIXTURE)
        (tmp_path / "bad.csv").write_text("not,a,valid,ubs,export\n")
        result = load_ubs_activity_folder(tmp_path)
        assert [o.reference_number for o in result.orders] == ["FA209912315000"]
        # The whole point: the batch survived AND the failure is nameable.
        assert [name for name, _ in result.errors] == ["bad.csv"]
        assert "Reference Number" in result.errors[0][1]

    def test_unreadable_path_is_reported_via_oserror_branch(self, tmp_path: Path) -> None:
        (tmp_path / "good.csv").write_text(BASIC_FIXTURE)
        (tmp_path / "a_directory.csv").mkdir()
        result = load_ubs_activity_folder(tmp_path)
        assert len(result.orders) == 1
        assert [name for name, _ in result.errors] == ["a_directory.csv"]
        assert "could not read file" in result.errors[0][1]

    def test_truncated_lot_row_raises_parse_error_not_indexerror(self) -> None:
        truncated_lot_row = '"S1234567890","2/1/2020","90.00","40","40"'
        truncated_fixture = _wrap(
            f'{_ORDER_HEADER}\n"General Info",,,,,,,,,,,,,\n{_BASIC_ORDER_ROW}',
            f'{_LOT_HEADER}\n"Order Summary",,,,,,,,,,,,\n{truncated_lot_row}',
        )
        with pytest.raises(UbsActivityParseError):
            parse_ubs_activity_text(truncated_fixture)

    def test_empty_folder_returns_empty_result(self, tmp_path: Path) -> None:
        result = load_ubs_activity_folder(tmp_path)
        assert result.orders == ()
        assert result.errors == ()


def test_ubs_activity_order_is_frozen() -> None:
    order = parse_ubs_activity_text(BASIC_FIXTURE)
    with pytest.raises(AttributeError):
        order.reference_number = "changed"  # type: ignore[misc]
