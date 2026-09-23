"""Tests for engine/ubs_exercise_ytd.py and engine/ubs_activity_store.py.

Fixtures below are SYNTHETIC, built directly via the UbsActivityOrder /
UbsExerciseLot dataclasses (bypassing CSV parsing, which is already covered
by tests/test_ubs_activity_csv.py) except where a real parsed order is
useful for the store round-trip test.
"""

from __future__ import annotations

from datetime import date

import pytest

from engine.pdf_ledger import PdfLedger
from engine.ubs_activity_csv import (
    FEDERAL_SUPPLEMENTAL_RATE,
    MEDICARE_RATE,
    SOCIAL_SECURITY_RATE,
    UbsActivityOrder,
    UbsExerciseLot,
    parse_ubs_activity_text,
)
from engine.ubs_activity_store import (
    UbsActivityStoreError,
    load_ubs_orders,
    order_from_dict,
    order_to_dict,
    save_ubs_orders,
)
from engine.ubs_exercise_ytd import apply_ubs_exercise_income, correct_ubs_option_basis
from models.ytd_income import YTDSnapshot
from tests.test_ubs_activity_csv import BASIC_FIXTURE

_FULL_BLEND = FEDERAL_SUPPLEMENTAL_RATE + SOCIAL_SECURITY_RATE + MEDICARE_RATE


def _make_order(
    reference_number: str,
    execution_date: date,
    bargain_element: float,
    fees: float,
    taxes: float | None = None,
) -> UbsActivityOrder:
    """Build a minimal single-lot UbsActivityOrder with grant_price=0 (so
    strike_cost=0 and bargain_element == gross_proceeds), for arithmetic that
    is easy to hand-verify in each test."""
    if taxes is None:
        taxes = bargain_element * _FULL_BLEND
    lot = UbsExerciseLot(
        grant_number="G1",
        grant_date=date(2020, 1, 1),
        grant_price=0.0,
        grant_quantity=1,
        execution_quantity=1,
        open_quantity=0,
        cancel_quantity=0,
        total_fees=fees,
        total_taxes=taxes,
        gross_proceeds=bargain_element,
        net_proceeds=bargain_element - fees - taxes,
        funds_available_date=None,
    )
    return UbsActivityOrder(
        reference_number=reference_number,
        source="Options",
        entry_date=execution_date,
        execution_date=execution_date,
        quantity=1,
        order_type="MKT",
        transaction_type="SDS",
        limit_price=None,
        execution_price=100.0,
        entered_through="WEB",
        proceeds_method="",
        funds_available_date=None,
        total_shares_delivered=1,
        lots=(lot,),
    )


def _ledger_with_ubs_record(
    period_end: str,
    stcg_net_ytd: float,
    account_type: str = "taxable",
) -> PdfLedger:
    return {
        "koinly": {},
        "brokerage": {
            "you": {
                "ACCT123": {
                    "account_number": "ACCT123",
                    "broker": "ubs",
                    "account_type": account_type,
                    "statement_period_end": period_end,
                    "interest_taxable_ytd": 0.0,
                    "interest_tax_exempt_ytd": 0.0,
                    "dividends_taxable_ytd": 0.0,
                    "dividends_tax_exempt_ytd": 0.0,
                    "stcg_net_ytd": stcg_net_ytd,
                    "ltcg_net_ytd": 0.0,
                    "captured_at": "2026-09-01T00:00:00",
                    "source": "pdf",
                    "parser_version": "1.0.0",
                    "provenance": {},
                    "owner_key": None,
                    "missing_fields": [],
                }
            }
        },
    }


_EMPTY_LEDGER: PdfLedger = {"koinly": {}, "brokerage": {}}


# ---------------------------------------------------------------------------
# apply_ubs_exercise_income
# ---------------------------------------------------------------------------


class TestApplyUbsExerciseIncome:
    def test_income_and_withholding_applied(self) -> None:
        order = _make_order("FA1", date(2026, 3, 15), bargain_element=24396.92, fees=31.27)
        ytd = YTDSnapshot()

        warnings = apply_ubs_exercise_income(ytd, [order])

        assert warnings == []
        assert ytd.nqo_exercise_ytd == pytest.approx(24396.92)
        expected_federal_withholding = 24396.92 * FEDERAL_SUPPLEMENTAL_RATE
        assert ytd.federal_withholding_ytd == pytest.approx(expected_federal_withholding)
        # Guard against the ~35% overstatement trap: total_taxes (the 29.65%
        # blend) must NOT be what landed in federal_withholding_ytd.
        blended_total_taxes = sum(lot.total_taxes for lot in order.lots)
        assert ytd.federal_withholding_ytd != pytest.approx(blended_total_taxes)
        assert ytd.federal_withholding_ytd < blended_total_taxes

    def test_empty_orders_leaves_ytd_untouched(self) -> None:
        ytd = YTDSnapshot(nqo_exercise_ytd=999.0, federal_withholding_ytd=111.0)

        warnings = apply_ubs_exercise_income(ytd, [])

        assert warnings == []
        assert ytd.nqo_exercise_ytd == 999.0
        assert ytd.federal_withholding_ytd == 111.0

    def test_wages_warning_fires_when_wages_nonzero(self) -> None:
        order = _make_order("FA1", date(2026, 3, 15), bargain_element=1000.0, fees=5.0)
        ytd = YTDSnapshot(wages_ytd=50000.0)

        warnings = apply_ubs_exercise_income(ytd, [order])

        assert any("wages_ytd" in w for w in warnings)

    def test_wages_warning_absent_when_wages_zero(self) -> None:
        order = _make_order("FA1", date(2026, 3, 15), bargain_element=1000.0, fees=5.0)
        ytd = YTDSnapshot(wages_ytd=0.0)

        warnings = apply_ubs_exercise_income(ytd, [order])

        assert not any("wages_ytd" in w for w in warnings)

    def test_finextract_disagreement_warning_and_csv_wins(self) -> None:
        order = _make_order("FA1", date(2026, 3, 15), bargain_element=1000.0, fees=5.0)
        ytd = YTDSnapshot(nqo_exercise_ytd=250.0)

        warnings = apply_ubs_exercise_income(ytd, [order])

        assert any("nqo_exercise_ytd" in w and "250.00" in w and "1000.00" in w for w in warnings)
        assert ytd.nqo_exercise_ytd == pytest.approx(1000.0)

    def test_no_disagreement_warning_when_values_match(self) -> None:
        order = _make_order("FA1", date(2026, 3, 15), bargain_element=1000.0, fees=5.0)
        ytd = YTDSnapshot(nqo_exercise_ytd=1000.0)

        warnings = apply_ubs_exercise_income(ytd, [order])

        assert not any("disagree" in w for w in warnings)

    def test_blend_mismatch_note_is_informational_not_a_defect(self) -> None:
        # SS-capped order: only 22% + 1.45% withheld, no 6.2% SS component.
        order = _make_order(
            "FA1",
            date(2026, 11, 1),
            bargain_element=1000.0,
            fees=5.0,
            taxes=1000.0 * (FEDERAL_SUPPLEMENTAL_RATE + MEDICARE_RATE),
        )
        ytd = YTDSnapshot()

        warnings = apply_ubs_exercise_income(ytd, [order])

        assert any("FA1" in w for w in warnings)


# ---------------------------------------------------------------------------
# correct_ubs_option_basis
# ---------------------------------------------------------------------------


class TestCorrectUbsOptionBasis:
    def test_headline_reproduces_real_arithmetic(self) -> None:
        # Real reconciled figures: bargain 24,396.92; fees 31.27;
        # statement STCG 24,365.65 = bargain - fees.
        order = _make_order("FA1", date(2026, 6, 10), bargain_element=24396.92, fees=31.27)
        ledger = _ledger_with_ubs_record(period_end="2026-06-30", stcg_net_ytd=24365.65)
        totals = {"stcg_ytd": 24365.65}

        corrected, warnings = correct_ubs_option_basis(totals, ledger, [order])

        assert corrected["stcg_ytd"] == pytest.approx(-31.27)
        assert warnings == []
        # Input not mutated.
        assert totals["stcg_ytd"] == 24365.65

    def test_date_gate_excludes_order_after_latest_statement(self) -> None:
        order = _make_order("FA1", date(2026, 9, 1), bargain_element=24396.92, fees=31.27)
        ledger = _ledger_with_ubs_record(period_end="2026-07-31", stcg_net_ytd=5000.0)
        totals = {"stcg_ytd": 5000.0}

        corrected, warnings = correct_ubs_option_basis(totals, ledger, [order])

        assert corrected["stcg_ytd"] == pytest.approx(5000.0)
        assert any("FA1" in w for w in warnings)

    def test_no_ubs_record_in_ledger_leaves_totals_unchanged(self) -> None:
        order = _make_order("FA1", date(2026, 6, 10), bargain_element=24396.92, fees=31.27)
        totals = {"stcg_ytd": 5000.0}

        corrected, warnings = correct_ubs_option_basis(totals, _EMPTY_LEDGER, [order])

        assert corrected["stcg_ytd"] == pytest.approx(5000.0)
        assert any("FA1" in w for w in warnings)

    def test_unrelated_genuine_stcg_survives_correction(self) -> None:
        # Statement STCG = 5,000 genuine gain + (bargain - fees) phantom.
        bargain, fees = 1000.0, 50.0
        statement_stcg = 5000.0 + (bargain - fees)
        order = _make_order("FA1", date(2026, 6, 10), bargain_element=bargain, fees=fees)
        ledger = _ledger_with_ubs_record(period_end="2026-06-30", stcg_net_ytd=statement_stcg)
        totals = {"stcg_ytd": statement_stcg}

        corrected, warnings = correct_ubs_option_basis(totals, ledger, [order])

        assert corrected["stcg_ytd"] == pytest.approx(5000.0 - fees)
        assert warnings == []

    def test_non_taxable_ubs_record_is_not_considered(self) -> None:
        order = _make_order("FA1", date(2026, 6, 10), bargain_element=24396.92, fees=31.27)
        ledger = _ledger_with_ubs_record(
            period_end="2026-06-30", stcg_net_ytd=24365.65, account_type="unknown"
        )
        totals = {"stcg_ytd": 24365.65}

        corrected, warnings = correct_ubs_option_basis(totals, ledger, [order])

        assert corrected["stcg_ytd"] == pytest.approx(24365.65)
        assert any("FA1" in w for w in warnings)

    def test_broker_matched_case_insensitively(self) -> None:
        order = _make_order("FA1", date(2026, 6, 10), bargain_element=24396.92, fees=31.27)
        ledger = _ledger_with_ubs_record(period_end="2026-06-30", stcg_net_ytd=24365.65)
        ledger["brokerage"]["you"]["ACCT123"]["broker"] = "UBS"
        totals = {"stcg_ytd": 24365.65}

        corrected, _warnings = correct_ubs_option_basis(totals, ledger, [order])

        assert corrected["stcg_ytd"] == pytest.approx(-31.27)


# ---------------------------------------------------------------------------
# Store round-trip
# ---------------------------------------------------------------------------


class TestUbsActivityStore:
    def test_order_to_dict_from_dict_round_trip(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)

        round_tripped = order_from_dict(order_to_dict(order))

        assert round_tripped == order

    def test_save_and_load_round_trip(self) -> None:
        order = parse_ubs_activity_text(BASIC_FIXTURE)

        save_ubs_orders([order])
        loaded = load_ubs_orders()

        assert loaded == [order]

    def test_load_missing_file_returns_empty_list(self) -> None:
        assert load_ubs_orders() == []

    def test_load_corrupt_file_raises(self) -> None:
        import engine.ubs_activity_store as ubs_store_mod

        ubs_store_mod._UBS_ACTIVITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ubs_store_mod._UBS_ACTIVITY_CACHE_PATH.write_text("{not valid json")

        with pytest.raises(UbsActivityStoreError):
            load_ubs_orders()
