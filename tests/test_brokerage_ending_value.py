"""Statement ending value + holder name per broker (synthetic fixtures only).

The ending value feeds IRA balances (engine/ira_statement_balance.py); it must
never change YTD behaviour, so a missing value is NOT a missing field.
"""

from __future__ import annotations

from engine.brokerage_statement_pdf import BrokerageStatementRecord, parse_statement_text
from tests.test_brokerage_statement_pdf import (
    FIDELITY_HSA_TEXT,
    FIDELITY_ROLLOVER_IRA_TEXT,
    FIDELITY_TAXABLE_TOD_TEXT,
    IBKR_ACCOUNT_1_FULL_TEXT,
    IBKR_ACCOUNT_2_FULL_TEXT,
    IBKR_ACCOUNT_3_FULL_TEXT,
    VANGUARD_ROTH_INCOME_SUMMARY_TEXT,
    VANGUARD_ROTH_OVERVIEW_TEXT,
    VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT,
    VANGUARD_TAXABLE_OVERVIEW_TEXT,
)

# Synthetic "Net Asset Value" tables, cell-flattened the way pdfplumber emits them.
_NAV_TAXABLE = """Net Asset Value
Asset Class Prior Total Current Long Current Short Current Total Change
Cash 100.00 100.00 0.00 100.00 0.00
Stocks 40,000.00 41,000.00 0.00 41,000.00 1,000.00
Total 40,100.00 41,100.00 0.00 41,100.00 1,000.00
Ending Value 41,100.00
"""
_NAV_TRAD = """Net Asset Value
Asset Class Prior Total Current Long Current Short Current Total Change
Funds 70,000.00 75,000.00 0.00 75,000.00 5,000.00 Ending Value 75,000.00
Time Weighted Rate of Return 1.00%
Ending Value 1.00
"""
_NAV_ROTH = """Net Asset Value
Funds 20,000.00 22,000.00 0.00 22,000.00 2,000.00
Ending Value 22,000.00
"""


def _ibkr_pages() -> list[str]:
    return [
        IBKR_ACCOUNT_1_FULL_TEXT + _NAV_TAXABLE,
        IBKR_ACCOUNT_2_FULL_TEXT + _NAV_TRAD,
        IBKR_ACCOUNT_3_FULL_TEXT + _NAV_ROTH,
    ]


class TestEndingValueRecordRoundTrip:
    def _rec(self, **kw: object) -> BrokerageStatementRecord:
        return BrokerageStatementRecord(
            account_number="X1234",
            broker="vanguard",
            account_type="roth_ira",
            statement_period_end="2026-06-30",
            interest_taxable_ytd=0.0,
            interest_tax_exempt_ytd=0.0,
            dividends_taxable_ytd=0.0,
            dividends_tax_exempt_ytd=0.0,
            stcg_net_ytd=0.0,
            ltcg_net_ytd=0.0,
            captured_at="2026-07-01T00:00:00",
            **kw,  # type: ignore[arg-type]
        )

    def test_defaults_to_none(self) -> None:
        assert self._rec().ending_value is None

    def test_round_trips(self) -> None:
        rec = self._rec(ending_value=12_345.67)
        assert BrokerageStatementRecord.from_dict(rec.to_dict()).ending_value == 12_345.67

    def test_old_cache_without_the_key_loads_as_none(self) -> None:
        data = self._rec().to_dict()
        del data["ending_value"]
        assert BrokerageStatementRecord.from_dict(data).ending_value is None


class TestVanguardEndingValue:
    def test_taxable(self) -> None:
        [rec] = parse_statement_text(
            [VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT]
        )
        assert rec.ending_value == 115_350.00
        assert rec.missing_fields == ()

    def test_roth(self) -> None:
        [rec] = parse_statement_text(
            [VANGUARD_ROTH_OVERVIEW_TEXT, VANGUARD_ROTH_INCOME_SUMMARY_TEXT]
        )
        assert rec.account_type == "roth_ira"
        assert rec.ending_value == 44_900.00

    def test_absent_label_is_none_and_not_a_missing_field(self) -> None:
        text = VANGUARD_ROTH_OVERVIEW_TEXT.replace("Account overview $44,900.00\n", "")
        assert text != VANGUARD_ROTH_OVERVIEW_TEXT
        [rec] = parse_statement_text([text, VANGUARD_ROTH_INCOME_SUMMARY_TEXT])
        assert rec.ending_value is None
        assert rec.missing_fields == ()


class TestFidelityEndingValue:
    def test_each_section_gets_its_own_value_and_holder(self) -> None:
        # The holdings page repeats the header with a different figure; the
        # FIRST "Account Value:" of each section is the one wanted.
        repeat = (
            "Account # 999-000002\nHoldings\nJOHN Q SAMPLE - ROLLOVER IRA\n"
            "Account Value: $1.00 Account Holdings\n"
        )
        recs = parse_statement_text([FIDELITY_ROLLOVER_IRA_TEXT + repeat, FIDELITY_HSA_TEXT])
        by_type = {r.account_type: r for r in recs}
        assert by_type["traditional_ira"].ending_value == 1_153_250.00
        assert by_type["hsa"].ending_value == 60_420.00
        assert by_type["traditional_ira"].owner_key == "JOHN Q SAMPLE"
        assert by_type["hsa"].owner_key == "JOHN Q SAMPLE"

    def test_holder_line_between_account_number_and_summary(self) -> None:
        [rec] = parse_statement_text([FIDELITY_TAXABLE_TOD_TEXT])
        assert rec.owner_key == "JANE Q SAMPLE"
        assert rec.ending_value == 123_456.78

    def test_missing_value_is_none_and_not_a_missing_field(self) -> None:
        text = FIDELITY_ROLLOVER_IRA_TEXT.replace("Account Value: $1,153,250.00 ", "")
        assert text != FIDELITY_ROLLOVER_IRA_TEXT
        [rec] = parse_statement_text([text])
        assert rec.ending_value is None
        assert rec.missing_fields == ()


class TestIbkrEndingValue:
    def test_each_account_gets_its_own_nav_ending_value(self) -> None:
        recs = {r.account_number: r for r in parse_statement_text(_ibkr_pages())}
        assert recs["U00000001"].ending_value == 41_100.00
        # First "Ending Value" after "Net Asset Value" wins, not a later one.
        assert recs["U00000002"].ending_value == 75_000.00
        assert recs["U00000003"].ending_value == 22_000.00

    def test_owner_key_from_name_line(self) -> None:
        recs = {r.account_number: r for r in parse_statement_text(_ibkr_pages())}
        assert {r.owner_key for r in recs.values()} == {"John Q SAMPLE"}

    def test_ending_cash_is_never_mistaken_for_the_value(self) -> None:
        [rec] = parse_statement_text([IBKR_ACCOUNT_2_FULL_TEXT])
        assert rec.ending_value is None
        assert rec.missing_fields == ()
