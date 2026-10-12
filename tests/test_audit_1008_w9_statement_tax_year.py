"""Audit-1008 wave W9: statement tax year + account identity.

F-10  Koinly reports and brokerage statements from another tax year must not
      feed the YTD snapshot's year (they stay in the ledger, are skipped from
      the totals, and the scan says so).
F-27  UBS ACTIVITY orders executed in another year must not count as this
      year's NQO income / withholding, nor be subtracted from this year's STCG.
F-55  The ledger's account identity is (broker, account_number): the same
      number string at two brokers is two accounts, even for one owner.
F-54  Koinly "Lending interest" is interest: ordinary income AND net
      investment income under IRC §1411(c)(1)(A)(i).

All figures are synthetic round numbers.
"""

from __future__ import annotations

import contextlib
from datetime import date
from unittest.mock import MagicMock, patch

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport, parse_koinly_text
from engine.niit import niit
from engine.pdf_import import PdfImportResult
from engine.pdf_ledger import (
    PdfLedger,
    apply_koinly_totals,
    derive_brokerage_totals,
    derive_koinly_interest,
    derive_koinly_totals,
    latest_brokerage_period_end,
    latest_koinly_capture,
    off_year_contributions,
    write_brokerage_contribution,
    write_koinly_contribution,
)
from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict
from engine.ubs_activity_csv import (
    FEDERAL_SUPPLEMENTAL_RATE,
    MEDICARE_RATE,
    SOCIAL_SECURITY_RATE,
    UbsActivityOrder,
    UbsExerciseLot,
)
from engine.ubs_exercise_ytd import apply_ubs_exercise_income, correct_ubs_option_basis
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials import _sync_scan as sync_scan_mod

_EMPTY: PdfLedger = {"koinly": {}, "brokerage": {}}


def _koinly(
    *, tax_year: int, stcg: float = 0.0, ltcg: float = 0.0, income: float = 0.0
) -> KoinlyReport:
    return KoinlyReport(
        tax_year=tax_year,
        crypto_stcg=stcg,
        crypto_ltcg=ltcg,
        crypto_income=income,
        captured_at=f"{tax_year + 1}-02-10T00:00:00+00:00",
        provenance={"pages": tax_year},
    )


def _stmt(
    account_number: str,
    *,
    period_end: str,
    broker: str = "schwab",
    interest: float = 0.0,
    dividends: float = 0.0,
    stcg: float = 0.0,
    ltcg: float = 0.0,
) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number=account_number,
        broker=broker,
        account_type="taxable",
        statement_period_end=period_end,
        interest_taxable_ytd=interest,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=dividends,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=stcg,
        ltcg_net_ytd=ltcg,
        captured_at="2026-10-01T00:00:00+00:00",
    )


_BLEND = FEDERAL_SUPPLEMENTAL_RATE + SOCIAL_SECURITY_RATE + MEDICARE_RATE


def _order(ref: str, executed: date, bargain: float, fees: float = 0.0) -> UbsActivityOrder:
    lot = UbsExerciseLot(
        grant_number="G1",
        grant_date=date(2020, 1, 1),
        grant_price=0.0,
        grant_quantity=1,
        execution_quantity=1,
        open_quantity=0,
        cancel_quantity=0,
        total_fees=fees,
        total_taxes=bargain * _BLEND,
        gross_proceeds=bargain,
        net_proceeds=bargain - fees - bargain * _BLEND,
        funds_available_date=None,
    )
    return UbsActivityOrder(
        reference_number=ref,
        source="Options",
        entry_date=executed,
        execution_date=executed,
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


def _ubs_ledger(period_end: str, stcg: float) -> PdfLedger:
    rec = _stmt("U-1", period_end=period_end, broker="ubs", stcg=stcg)
    return write_brokerage_contribution(_EMPTY, "you", rec)


# ---------------------------------------------------------------------------
# F-10: ledger totals are gated on the snapshot's tax year
# ---------------------------------------------------------------------------


class TestF10LedgerTaxYearGate:
    def test_prior_year_brokerage_statement_is_not_summed(self) -> None:
        ledger = write_brokerage_contribution(
            _EMPTY, "you", _stmt("X-1", period_end="2025-12-31", dividends=8000.0, ltcg=12000.0)
        )
        ledger = write_brokerage_contribution(
            ledger, "you", _stmt("X-2", period_end="2026-09-30", dividends=500.0, ltcg=700.0)
        )
        totals = derive_brokerage_totals(ledger, tax_year=2026)
        assert totals["ordinary_dividends_ytd"] == 500.0
        assert totals["ltcg_ytd"] == 700.0

    def test_default_without_tax_year_still_sums_every_slot(self) -> None:
        ledger = write_brokerage_contribution(
            _EMPTY, "you", _stmt("X-1", period_end="2025-12-31", ltcg=10.0)
        )
        assert derive_brokerage_totals(ledger)["ltcg_ytd"] == 10.0

    def test_prior_year_koinly_report_is_not_summed(self) -> None:
        ledger = write_koinly_contribution(_EMPTY, "you", _koinly(tax_year=2026, stcg=1000.0))
        ledger = write_koinly_contribution(ledger, "spouse", _koinly(tax_year=2025, ltcg=9000.0))
        totals = derive_koinly_totals(ledger, tax_year=2026)
        assert totals == {"stcg": 1000.0, "ltcg": 0.0, "income": 0.0}

    def test_apply_koinly_totals_uses_the_snapshot_year(self) -> None:
        ledger = write_koinly_contribution(
            _EMPTY, "you", _koinly(tax_year=2025, stcg=4000.0, ltcg=6000.0, income=2000.0)
        )
        ytd = YTDSnapshot(tax_year=2026)
        apply_koinly_totals(ytd, ledger)
        assert (ytd.crypto_stcg_ytd, ytd.crypto_ltcg_ytd, ytd.crypto_income_ytd) == (0.0, 0.0, 0.0)

    def test_as_of_dates_ignore_other_years(self) -> None:
        ledger = write_brokerage_contribution(_EMPTY, "you", _stmt("X-1", period_end="2025-12-31"))
        ledger = write_koinly_contribution(ledger, "you", _koinly(tax_year=2025))
        assert latest_brokerage_period_end(ledger, tax_year=2026) is None
        assert latest_koinly_capture(ledger, tax_year=2026) is None

    def test_off_year_contributions_names_what_was_skipped(self) -> None:
        ledger = write_brokerage_contribution(_EMPTY, "you", _stmt("X-1", period_end="2025-12-31"))
        ledger = write_brokerage_contribution(ledger, "you", _stmt("X-2", period_end="2026-09-30"))
        ledger = write_koinly_contribution(ledger, "spouse", _koinly(tax_year=2025))
        notes = off_year_contributions(ledger, 2026)
        assert len(notes) == 2
        assert any("X-1" in n and "2025" in n for n in notes)
        assert any("Koinly" in n and "2025" in n for n in notes)
        assert not any("X-2" in n for n in notes)


def _mock_st() -> tuple[MagicMock, dict]:
    mock_st = MagicMock()
    state: dict = {"ytd_snapshot": YTDSnapshot(tax_year=2026)}
    session_state = MagicMock()
    session_state.get.side_effect = lambda key, default=None: state.get(key, default)
    session_state.__setitem__.side_effect = state.__setitem__
    session_state.__getitem__.side_effect = state.__getitem__
    session_state.__contains__.side_effect = state.__contains__
    session_state.pop.side_effect = lambda key, default=None: state.pop(key, default)
    mock_st.session_state = session_state
    mock_st.button.return_value = False
    mock_st.columns.side_effect = lambda n: [
        MagicMock() for _ in range(n if isinstance(n, int) else len(n))
    ]
    return mock_st, state


class TestF10ScanPath:
    def test_scanning_prior_year_documents_leaves_this_years_ytd_alone(self) -> None:
        mock_st, state = _mock_st()
        result = PdfImportResult(
            brokerage_records=[
                _stmt("X-1", period_end="2025-12-31", interest=1500.0, dividends=8000.0)
            ],
            koinly_reports=[_koinly(tax_year=2025, stcg=4000.0, ltcg=6000.0, income=2000.0)],
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sync_scan_mod, "st", mock_st))
            stack.enter_context(patch.object(sync_scan_mod, "save_ytd_snapshot"))
            stack.enter_context(patch.object(sync_scan_mod, "save_ledger"))
            stack.enter_context(
                patch.object(sync_scan_mod, "_load_ubs_orders_safe", return_value=[])
            )
            stack.enter_context(
                patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={})
            )
            stack.enter_context(patch("engine.brokerage_statement_pdf.save_statement_records"))
            stack.enter_context(patch("engine.koinly_report_pdf.save_koinly_report"))
            ledger = sync_scan_mod._apply_scan_result(
                result,
                ledger={"koinly": {}, "brokerage": {}},
                account_overrides={},
                owner_map={},
                instance_owner="you",
            )
        snap = state["ytd_snapshot"]
        assert snap.interest_ytd == 0.0
        assert snap.ordinary_dividends_ytd == 0.0
        assert snap.crypto_stcg_ytd == 0.0
        assert snap.magi_ytd == 0.0
        # The documents stay in the ledger (they are still valid for 2025) ...
        assert ledger["brokerage"]["you"]
        assert ledger["koinly"]["you"]
        # ... and the scan says why they were not counted.
        shown = " ".join(str(c.args[0]) for c in mock_st.warning.call_args_list)
        shown += " ".join(str(c.args[0]) for c in mock_st.info.call_args_list)
        assert "2025" in shown


# ---------------------------------------------------------------------------
# F-27: UBS orders are gated on the snapshot's tax year
# ---------------------------------------------------------------------------


class TestF27UbsTaxYear:
    def test_prior_year_order_is_not_this_years_income_or_withholding(self) -> None:
        orders = [
            _order("REF-2025", date(2025, 12, 15), 10000.0),
            _order("REF-2026", date(2026, 3, 10), 5000.0),
        ]
        ytd = YTDSnapshot(tax_year=2026)
        warnings = apply_ubs_exercise_income(ytd, orders)
        assert ytd.nqo_exercise_ytd == 5000.0
        assert ytd.ubs_withholding_by_owner["you"] == 5000.0 * FEDERAL_SUPPLEMENTAL_RATE
        assert any("REF-2025" in w for w in warnings)

    def test_only_prior_year_orders_leave_a_fresh_snapshot_untouched(self) -> None:
        ytd = YTDSnapshot(tax_year=2026)
        apply_ubs_exercise_income(ytd, [_order("REF-2025", date(2025, 12, 15), 10000.0)])
        assert ytd.nqo_exercise_ytd == 0.0
        assert ytd.federal_withholding_ytd == 0.0

    def test_stale_ubs_figures_from_prior_year_orders_are_cleared(self) -> None:
        # A pre-fix apply landed the 2025 order on the 2026 snapshot; once
        # every stored order is out of year, the UBS-owned values reset.
        old = _order("REF-2025", date(2025, 12, 15), 10000.0)
        ytd = YTDSnapshot(tax_year=2026)
        ytd.set_ubs_withholding("you", 10000.0 * FEDERAL_SUPPLEMENTAL_RATE)
        ytd.set_owned("you", nqo_exercise_ytd=10000.0)
        apply_ubs_exercise_income(ytd, [old])
        assert ytd.nqo_exercise_ytd == 0.0
        assert ytd.ubs_withholding_by_owner.get("you", 0.0) == 0.0

    def test_prior_year_order_is_not_subtracted_from_this_years_stcg(self) -> None:
        orders = [
            _order("REF-2025", date(2025, 12, 15), 10000.0),
            _order("REF-2026", date(2026, 3, 10), 5000.0, fees=10.0),
        ]
        ledger = _ubs_ledger("2026-03-31", 4990.0)
        corrected, _ = correct_ubs_option_basis({"stcg_ytd": 4990.0}, ledger, orders, tax_year=2026)
        assert corrected["stcg_ytd"] == -10.0

    def test_prior_year_ubs_statement_does_not_cover_this_years_order(self) -> None:
        ledger = _ubs_ledger("2025-12-31", 0.0)
        corrected, warnings = correct_ubs_option_basis(
            {"stcg_ytd": 0.0},
            ledger,
            [_order("REF-2026", date(2026, 3, 10), 5000.0)],
            tax_year=2026,
        )
        assert corrected["stcg_ytd"] == 0.0
        assert warnings


# ---------------------------------------------------------------------------
# F-55: same number at two brokers is two accounts
# ---------------------------------------------------------------------------


class TestF55AccountIdentity:
    def test_same_number_at_two_brokers_both_survive(self) -> None:
        ledger = write_brokerage_contribution(
            _EMPTY,
            "you",
            _stmt("XXXX1234", period_end="2026-09-30", broker="schwab", interest=100.0),
        )
        ledger = write_brokerage_contribution(
            ledger,
            "you",
            _stmt("XXXX1234", period_end="2026-09-30", broker="fidelity", interest=200.0),
        )
        assert derive_brokerage_totals(ledger)["interest_ytd"] == 300.0

    def test_older_other_broker_statement_does_not_replace_newer_one(self) -> None:
        ledger = write_brokerage_contribution(
            _EMPTY,
            "you",
            _stmt("XXXX1234", period_end="2026-09-30", broker="schwab", interest=100.0),
        )
        ledger = write_brokerage_contribution(
            ledger,
            "you",
            _stmt("XXXX1234", period_end="2026-01-31", broker="fidelity", interest=5.0),
        )
        assert derive_brokerage_totals(ledger)["interest_ytd"] == 105.0

    def test_rescan_of_either_account_replaces_only_that_account(self) -> None:
        ledger = write_brokerage_contribution(
            _EMPTY,
            "you",
            _stmt("XXXX1234", period_end="2026-06-30", broker="schwab", interest=100.0),
        )
        ledger = write_brokerage_contribution(
            ledger,
            "you",
            _stmt("XXXX1234", period_end="2026-06-30", broker="fidelity", interest=200.0),
        )
        ledger = write_brokerage_contribution(
            ledger,
            "you",
            _stmt("XXXX1234", period_end="2026-09-30", broker="fidelity", interest=250.0),
        )
        ledger = write_brokerage_contribution(
            ledger,
            "you",
            _stmt("XXXX1234", period_end="2026-09-30", broker="schwab", interest=150.0),
        )
        assert derive_brokerage_totals(ledger)["interest_ytd"] == 400.0
        assert sum(len(v) for v in ledger["brokerage"].values()) == 2

    def test_owner_move_still_moves_the_matching_broker_only(self) -> None:
        ledger = write_brokerage_contribution(
            _EMPTY,
            "you",
            _stmt("XXXX1234", period_end="2026-06-30", broker="schwab", interest=100.0),
        )
        ledger = write_brokerage_contribution(
            ledger,
            "you",
            _stmt("XXXX1234", period_end="2026-06-30", broker="fidelity", interest=200.0),
        )
        ledger = write_brokerage_contribution(
            ledger,
            "spouse",
            _stmt("XXXX1234", period_end="2026-06-30", broker="fidelity", interest=200.0),
        )
        assert derive_brokerage_totals(ledger)["interest_ytd"] == 300.0
        assert len(ledger["brokerage"]["you"]) == 1
        assert len(ledger["brokerage"]["spouse"]) == 1


# ---------------------------------------------------------------------------
# F-54: Koinly lending interest is net investment income
# ---------------------------------------------------------------------------

_CG_PAGE = """TAX YEAR 2026
Capital gains summary
Net gains $0.00
Short term $0.00
Long term $0.00
"""


def _income_page(*, reward: float = 0.0, lending: float = 0.0, year: int = 2026) -> str:
    return (
        f"TAX YEAR {year}\nIncome summary\n"
        f"Airdrop $0.00\nFork $0.00\nMining $0.00\nReward ${reward:.2f}\n"
        f"Salary $0.00\nLending interest ${lending:.2f}\nOther income $0.00\n"
        f"Total ${reward + lending:.2f}\n"
    )


class TestF54KoinlyLendingInterestIsNii:
    def test_lending_interest_counts_as_nii_and_ordinary(self) -> None:
        report = parse_koinly_text([_CG_PAGE, _income_page(lending=20000.0)])
        ledger = write_koinly_contribution(_EMPTY, "you", report)
        ytd = YTDSnapshot(tax_year=2026, wages_ytd=270000.0, ordinary_dividends_ytd=10000.0)
        apply_koinly_totals(ytd, ledger)
        assert ytd.crypto_income_ytd == 20000.0
        assert ytd.crypto_interest_ytd == 20000.0
        assert ytd.total_investment_income == 30000.0
        assert ytd.total_ordinary_income == 300000.0
        # Same NIIT as entering the $20k as ordinary interest.
        assert niit(ytd.niit_magi_ytd, ytd.total_investment_income, "MFJ") == 1140.0

    def test_staking_rewards_stay_out_of_nii(self) -> None:
        report = parse_koinly_text([_CG_PAGE, _income_page(reward=500.0, lending=100.0)])
        ledger = write_koinly_contribution(_EMPTY, "you", report)
        assert derive_koinly_interest(ledger) == 100.0
        ytd = YTDSnapshot(tax_year=2026)
        apply_koinly_totals(ytd, ledger)
        assert ytd.crypto_income_ytd == 600.0
        assert ytd.total_investment_income == 100.0

    def test_interest_is_year_gated_and_summed_across_owners(self) -> None:
        r26 = parse_koinly_text([_CG_PAGE, _income_page(lending=100.0)])
        r25 = parse_koinly_text(
            [_CG_PAGE.replace("2026", "2025"), _income_page(lending=900.0, year=2025)]
        )
        ledger = write_koinly_contribution(_EMPTY, "you", r26)
        ledger = write_koinly_contribution(ledger, "spouse", r25)
        assert derive_koinly_interest(ledger, tax_year=2026) == 100.0
        assert derive_koinly_interest(ledger) == 1000.0

    def test_nii_part_never_exceeds_a_hand_lowered_crypto_income(self) -> None:
        ytd = YTDSnapshot(tax_year=2026, crypto_income_ytd=50.0, crypto_interest_ytd=200.0)
        assert ytd.total_investment_income == 50.0

    def test_crypto_interest_survives_the_cache_round_trip(self) -> None:
        ytd = YTDSnapshot(tax_year=2026, crypto_income_ytd=300.0, crypto_interest_ytd=120.0)
        assert ytd_from_dict(ytd_to_dict(ytd)).crypto_interest_ytd == 120.0
        legacy = ytd_to_dict(ytd)
        legacy.pop("crypto_interest_ytd")
        assert ytd_from_dict(legacy).crypto_interest_ytd == 0.0
