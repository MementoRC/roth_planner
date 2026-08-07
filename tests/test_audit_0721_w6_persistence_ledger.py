"""Regression tests for audit-0721 wave 6 (persistence/ledger) findings.

C8:  engine/scenario.py run_scenario() — extra_withdrawal/spouse_extra_withdrawal
     must be clamped to the IRA balance remaining after RMD/QCD, mirroring the
     scenario-core-5 conversion clamp.
C14: engine/pdf_ledger.py write_brokerage_contribution — out-of-order folder
     scans must not let an OLDER statement clobber a NEWER stored record.
C15: engine/pdf_ledger.py write_koinly_contribution — a multi-year folder scan
     must keep the LATEST tax_year's Koinly figures, not whichever PDF is
     processed last.
C16: engine/brokerage_statement_pdf.py apply_account_type_overrides — a stale/
     invalid cached override must be skipped, not crash the scan handler.
C6:  engine/dividend_forecast.py forecast_portfolio — the "no derivable rate"
     branch must merge with (not overwrite) any prior per_pos[ticker] entry.
"""

from __future__ import annotations

from pathlib import Path

from engine.brokerage_statement_pdf import (
    BrokerageStatementRecord,
    apply_account_type_overrides,
)
from engine.dividend_forecast import Position, forecast_portfolio
from engine.koinly_report_pdf import KoinlyReport
from engine.pdf_ledger import (
    derive_brokerage_totals,
    derive_koinly_totals,
    write_brokerage_contribution,
    write_koinly_contribution,
)
from engine.scenario import run_scenario
from engine.scenario_types import ConversionPlan
from models.household import Household


def _base_hh(**kwargs) -> Household:
    """Minimal Household — all keyword overrides accepted."""
    defaults: dict = {
        "your_age": 62,
        "spouse_age": 56,
        "base_year": 2026,
        "your_ira": 1_000_000.0,
        "spouse_ira": 500_000.0,
        "your_ss_fra": 0.0,
        "spouse_ss_fra": 0.0,
        "your_ss_start_age": 70,
        "spouse_ss_start_age": 70,
        "living_expenses": 60_000.0,
        "brokerage_start": 0.0,
    }
    defaults.update(kwargs)
    return Household(**defaults)


class TestC8ExtraWithdrawalClampedToIraBalance:
    """extra_withdrawal larger than the IRA balance must not produce phantom
    taxable income; it should be clamped like the conversion is."""

    def test_your_extra_withdrawal_capped_at_balance(self) -> None:
        hh = _base_hh(your_ira=10_000.0, spouse_ira=0.0)
        plan = ConversionPlan(extra_withdrawals={2026: 50_000.0})

        result = run_scenario(hh, plan, end_age=hh.your_age)
        yr = result.years[0]

        assert yr.extra_withdrawal == 10_000.0, (
            f"extra_withdrawal should be clamped to the $10,000 IRA balance, "
            f"got {yr.extra_withdrawal}"
        )
        assert yr.your_ira_end == 0.0

    def test_spouse_extra_withdrawal_capped_at_balance(self) -> None:
        hh = _base_hh(your_ira=0.0, spouse_ira=15_000.0)
        plan = ConversionPlan(spouse_extra_withdrawals={2026: 60_000.0})

        result = run_scenario(hh, plan, end_age=hh.your_age)
        yr = result.years[0]

        assert yr.spouse_extra_withdrawal == 15_000.0
        assert yr.spouse_ira_end == 0.0

    def test_extra_withdrawal_within_balance_unaffected(self) -> None:
        """Non-regression: an extra_withdrawal that fits within the balance
        passes through unchanged."""
        hh = _base_hh(your_ira=500_000.0, spouse_ira=0.0)
        plan = ConversionPlan(extra_withdrawals={2026: 20_000.0})

        result = run_scenario(hh, plan, end_age=hh.your_age)
        yr = result.years[0]

        assert yr.extra_withdrawal == 20_000.0


class TestC14BrokerageOutOfOrderScanKeepsNewer:
    def test_older_statement_processed_after_newer_does_not_clobber(self) -> None:
        newer = BrokerageStatementRecord(
            account_number="111",
            broker="schwab",
            account_type="taxable",
            statement_period_end="2026-12-31",
            interest_taxable_ytd=500.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-21T00:00:00+00:00",
        )
        older = BrokerageStatementRecord(
            account_number="111",
            broker="schwab",
            account_type="taxable",
            statement_period_end="2026-01-31",
            interest_taxable_ytd=50.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-21T00:00:01+00:00",
        )

        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", newer)
        # Simulates an out-of-order folder scan: the Dec statement (newer)
        # was already written; the Jan statement (older) arrives afterward.
        ledger = write_brokerage_contribution(ledger, "you", older)

        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == 500.0, (
            "the newer (Dec) statement must be kept, not clobbered by the "
            "older (Jan) statement processed later"
        )

    def test_newer_statement_still_replaces_older(self) -> None:
        """Non-regression: normal in-order scans still update the slot."""
        older = BrokerageStatementRecord(
            account_number="111",
            broker="schwab",
            account_type="taxable",
            statement_period_end="2026-01-31",
            interest_taxable_ytd=50.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-21T00:00:00+00:00",
        )
        newer = BrokerageStatementRecord(
            account_number="111",
            broker="schwab",
            account_type="taxable",
            statement_period_end="2026-12-31",
            interest_taxable_ytd=500.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-21T00:00:01+00:00",
        )

        ledger: dict = {}
        ledger = write_brokerage_contribution(ledger, "you", older)
        ledger = write_brokerage_contribution(ledger, "you", newer)

        totals = derive_brokerage_totals(ledger)
        assert totals["interest_ytd"] == 500.0


class TestC15KoinlyMultiYearScanKeepsLatestYear:
    def test_older_tax_year_does_not_clobber_newer(self) -> None:
        newer = KoinlyReport(
            tax_year=2026,
            crypto_stcg=100.0,
            crypto_ltcg=200.0,
            crypto_income=50.0,
            captured_at="2026-07-21T00:00:00+00:00",
        )
        older = KoinlyReport(
            tax_year=2025,
            crypto_stcg=10.0,
            crypto_ltcg=20.0,
            crypto_income=5.0,
            captured_at="2026-07-21T00:00:01+00:00",
        )

        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", newer)
        # Simulates a multi-year folder scan processing the older PDF second.
        ledger = write_koinly_contribution(ledger, "you", older)

        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 100.0, "ltcg": 200.0, "income": 50.0}, (
            "2025 report must not clobber the already-stored 2026 report"
        )

    def test_newer_tax_year_still_replaces(self) -> None:
        """Non-regression: a genuinely newer tax_year still updates the slot."""
        older = KoinlyReport(
            tax_year=2025,
            crypto_stcg=10.0,
            crypto_ltcg=20.0,
            crypto_income=5.0,
            captured_at="2026-07-21T00:00:00+00:00",
        )
        newer = KoinlyReport(
            tax_year=2026,
            crypto_stcg=100.0,
            crypto_ltcg=200.0,
            crypto_income=50.0,
            captured_at="2026-07-21T00:00:01+00:00",
        )

        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", older)
        ledger = write_koinly_contribution(ledger, "you", newer)

        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 100.0, "ltcg": 200.0, "income": 50.0}

    def test_same_tax_year_rescan_still_replaces_row(self) -> None:
        """Non-regression: same-year re-scan (idempotent correction) still replaces."""
        first = KoinlyReport(
            tax_year=2026,
            crypto_stcg=100.0,
            crypto_ltcg=200.0,
            crypto_income=50.0,
            captured_at="2026-07-21T00:00:00+00:00",
        )
        corrected = KoinlyReport(
            tax_year=2026,
            crypto_stcg=150.0,
            crypto_ltcg=200.0,
            crypto_income=50.0,
            captured_at="2026-07-21T00:00:01+00:00",
        )

        ledger: dict = {}
        ledger = write_koinly_contribution(ledger, "you", first)
        ledger = write_koinly_contribution(ledger, "you", corrected)

        totals = derive_koinly_totals(ledger)
        assert totals == {"stcg": 150.0, "ltcg": 200.0, "income": 50.0}


class TestC16InvalidOverrideSkipped:
    def test_invalid_override_skipped_valid_ones_still_apply_no_exception(self) -> None:
        rec_a = BrokerageStatementRecord(
            account_number="111",
            broker="schwab",
            account_type="unknown",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-21T00:00:00+00:00",
        )
        rec_b = BrokerageStatementRecord(
            account_number="222",
            broker="vanguard",
            account_type="unknown",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-21T00:00:00+00:00",
        )
        by_account = {"111": rec_a, "222": rec_b}
        # "111" has a stale/hand-edited invalid value; "222" has a valid one.
        overrides = {"111": "bogus_type", "222": "taxable"}

        result = apply_account_type_overrides(by_account, overrides)

        assert result["111"].account_type == "unknown", (
            "invalid cached override must be skipped, leaving the original value"
        )
        assert result["222"].account_type == "taxable"


class TestC6DividendForecastMergesNoRateBranch:
    def test_second_position_with_no_rate_keeps_first_positions_dividend(
        self, tmp_path: Path
    ) -> None:
        pos_with_dividend = Position(
            ticker="XYZ", shares=100.0, balance=10_000.0, ttm_dividends=100.0
        )
        pos_no_rate = Position(ticker="XYZ", shares=50.0, balance=5_000.0, ttm_dividends=0.0)

        result = forecast_portfolio(
            [pos_with_dividend, pos_no_rate],
            total_balance=15_000.0,
            overrides_path=tmp_path / ".dividend_rates.json",
        )

        assert result.per_position["XYZ"]["annual_div"] == 100.0, (
            "the second position's no-derivable-rate branch must not wipe out "
            "the first position's computed dividend"
        )
