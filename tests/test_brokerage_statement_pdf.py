"""Tests for engine.brokerage_statement_pdf -- anchored to real Schwab and
Vanguard statement dumps captured 2026-07 (pdfplumber extract_text() output,
trimmed to the pages containing the anchors this parser reads)."""

from __future__ import annotations

import pathlib

import pytest

from engine.brokerage_statement_pdf import (
    BrokerageStatementRecord,
    StatementParseError,
    parse_statement_text,
)

SCHWAB_SAMPLE = pathlib.Path("/home/memento/Downloads/Brokerage Statement_2026-06-30_847.PDF")
VANGUARD_TAXABLE_SAMPLE = pathlib.Path("/home/memento/Downloads/2026-06 VG Statement x9320.pdf")
VANGUARD_ROTH_SAMPLE = pathlib.Path("/home/memento/Downloads/2026-06 VG Statement Roth IRA x7368.pdf")

# --- Real Schwab dump excerpt (page 1 of 12: account identity, Income
# Summary, Gain or (Loss) Summary). Schwab's extract_text() strips spaces
# from labels -- this is copied verbatim, not reformatted. ---
SCHWAB_PAGE_TEXT = """Schwab One® Account of
AccountNumber StatementPeriod
CLAUDECIRBA
DESIGNATEDBENEPLAN/TOD ****-*847 June1-30,2026
i
Asset Allocation Income Summary
Current
3
ThisPeriod Allocation
2.4
CashandCashInvestments 476,291.32 49% 2.04
ExchangeTradedFunds 490,412.58 51% 1.8
$966.70K
1.2
Total $966,703.90 100%
0.6
InvestmentObjective:
Growth 0
Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec
i
Top Account Holdings This Period
SYMBOL Market %of
CUSIP Description Value Accounts
TDBANKUSANA 249,000.00 26%
TDBANKNA 227,291.32 24%
VWO VANGUARDFTSEEMERGING 60,065.92 6%
EFA ISHARESMSCIEAFEETF 54,278.93 6%
SCHI SCHWAB5-10YEAR 40,978.40 4%
.
Gain or (Loss) Summary
Short-Term(ST) Long-Term(LT)
Gain (Loss) Net Gain (Loss) Net
This 0.00 0.00 0.00 0.00 0.00 0.00
Period
YTD 19.11 283,895.77
Unrealized $143,294.97
Valuesmaynotreflectallofyourgains/lossesandmayberoundeduptothenearestdollar;Schwabhas
providedaccurategainandlossinformationwhereverpossibleformostinvestments.Costbasismaybe
incompleteorunavailableforsomeofyourholdingsandmaychangeorbeadjustedincertaincases.
PleaselogintoyouraccountatSchwab.comforreal-timegain/lossinformation.Statementinformation
shouldnotbeusedfortaxpreparation,insteadrefertoofficialtaxdocuments.Foradditionalinformation
refertoTermsandConditions.
2of12
)$(sdnasuohT
ThisPeriod YTD
FederalTaxStatus Tax-Exempt1 Taxable Tax-Exempt1 Taxable
BankSweepInterest 0.00 4.38 0.00 18.56
CashDividends 16.05 2,023.39 78.74 4,846.82
TotalIncome $16.05 $2,027.77 $78.74 $4,865.38
1Certainincomeinthiscategorymayqualifyforstatetaxexemption;consultyourtaxadvisor
i
Margin Loan Information
"""

# --- Real Vanguard taxable-account dump excerpt (page 2: account overview
# header; page 4: Income summary table). ---
VANGUARD_TAXABLE_OVERVIEW_TEXT = """CDDLRREG
Individual brokerage account—XXXX9320 Vanguard Personal Investor
Claude R Cirba 877-662-7447
Account overview $126,201.74
Total account value as of June 30, 2026
June 30, 2026, quarter-to-date statement
20260703
194758STMT21202120000000683724598
C
Activity summary for statement period Cost basis summary (all investments)
Value on March 31, 2026 $114,726.10 Realized gain/loss
Deposits and withdrawals* -46.90 June short-term -
June long-term -
Change in value** 10,889.78
Unrealized gain/loss 38,299.29
Dividends, interest, and capital gains 632.76
Value on June 30, 2026 $126,201.74
Year-to-date income
Taxable income $1,028.55
Nontaxable income 0.00
Total $1,028.55
Balances and holdings for Vanguard Brokerage Account—XXXX9320
"""

VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT = """Individual brokerage account—XXXX9320 Vanguard Personal Investor
Claude R Cirba 877-662-7447
June 30, 2026, quarter-to-date statement Page 5 of10
Account activity for Vanguard Brokerage Account—XXXX9320
This section shows transactions that have settled by June 30, 2026.
Income summary
Dividends Interest Tax-exempt interest Short-term capital gains Long-term capital gains Other income
June $415.10 $0.00 $0.00 $0.00 $0.00 $0.00
Year-to-date 1,028.55 0.00 0.00 0.00 0.00 0.00
"""

# --- Real Vanguard Roth-account dump excerpt (same structure). ---
VANGUARD_ROTH_OVERVIEW_TEXT = """CDDLRREG
Roth IRA brokerage account—XXXX7368 Vanguard Personal Investor
Claude R. Cirba 877-662-7447
Account overview $48,498.19
Total account value as of June 30, 2026
June 30, 2026, quarter-to-date statement
20260704
081448STMT21462146000000422602549
C
Activity summary for statement period Retirement summary
Value on March 31, 2026 $43,160.11 2026 contributions $0.00
Deposits and withdrawals* 0.00 2026 distributions $0.00
Change in value** 5,192.10
Dividends, interest, and capital gains 145.98
Value on June 30, 2026 $48,498.19
Year-to-date income
Taxable income $0.00
Nontaxable income 283.86
Total $283.86
Balances and holdings for Vanguard Brokerage Account—XXXX7368
"""

VANGUARD_ROTH_INCOME_SUMMARY_TEXT = """Roth IRA brokerage account—XXXX7368 Vanguard Personal Investor
Claude R. Cirba 877-662-7447
June 30, 2026, quarter-to-date statement Page 5 of8
Account activity for Vanguard Brokerage Account—XXXX7368
This section shows transactions that have settled by June 30, 2026.
Income summary
Dividends Interest Tax-exempt interest Short-term capital gains Long-term capital gains Other income
June $104.18 $0.00 $0.00 $0.00 $0.00 $0.00
Year-to-date 283.86 0.00 0.00 0.00 0.00 0.00
"""


class TestParseSchwab:
    def test_account_number(self):
        rec = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert rec.account_number == "****-*847"
        assert rec.broker == "schwab"

    def test_account_type_is_unknown(self):
        # Schwab statements never state account type -- must default to
        # unknown, NOT be inferred as taxable just because no IRA label was found.
        rec = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert rec.account_type == "unknown"

    def test_interest_ytd_split(self):
        rec = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert rec.interest_taxable_ytd == 18.56
        assert rec.interest_tax_exempt_ytd == 0.0

    def test_dividends_ytd_split(self):
        rec = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert rec.dividends_taxable_ytd == 4846.82
        assert rec.dividends_tax_exempt_ytd == 78.74

    def test_gain_loss_ytd(self):
        rec = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert rec.stcg_net_ytd == 19.11
        assert rec.ltcg_net_ytd == 283895.77

    def test_statement_period_end(self):
        rec = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert rec.statement_period_end == "2026-06-30"


class TestParseVanguardTaxable:
    def test_account_type_taxable(self):
        rec = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert rec.account_type == "taxable"
        assert rec.account_number == "XXXX9320"
        assert rec.broker == "vanguard"

    def test_dividends_ytd(self):
        rec = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert rec.dividends_taxable_ytd == 1028.55
        assert rec.dividends_tax_exempt_ytd == 0.0

    def test_no_gains_or_interest(self):
        rec = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert rec.interest_taxable_ytd == 0.0
        assert rec.stcg_net_ytd == 0.0
        assert rec.ltcg_net_ytd == 0.0

    def test_statement_period_end(self):
        rec = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert rec.statement_period_end == "2026-06-30"


class TestParseVanguardRoth:
    def test_account_type_roth(self):
        rec = parse_statement_text([VANGUARD_ROTH_OVERVIEW_TEXT, VANGUARD_ROTH_INCOME_SUMMARY_TEXT])
        assert rec.account_type == "roth_ira"
        assert rec.account_number == "XXXX7368"

    def test_dividends_ytd(self):
        rec = parse_statement_text([VANGUARD_ROTH_OVERVIEW_TEXT, VANGUARD_ROTH_INCOME_SUMMARY_TEXT])
        # Note: "dividends_taxable_ytd" is just the dataclass field name inherited
        # from the taxable-column source; downstream aggregation must still
        # exclude this record entirely via account_type, regardless of which
        # dollar figure lives in which field.
        assert rec.dividends_taxable_ytd == 283.86


@pytest.mark.skipif(not SCHWAB_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_schwab_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    rec = parse_statement_pdf(SCHWAB_SAMPLE.read_bytes())
    assert rec.account_type == "unknown"
    assert rec.dividends_taxable_ytd > 0


@pytest.mark.skipif(not VANGUARD_TAXABLE_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_vanguard_taxable_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    rec = parse_statement_pdf(VANGUARD_TAXABLE_SAMPLE.read_bytes())
    assert rec.account_type == "taxable"
    assert rec.dividends_taxable_ytd == pytest.approx(1028.55)


@pytest.mark.skipif(not VANGUARD_ROTH_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_vanguard_roth_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    rec = parse_statement_pdf(VANGUARD_ROTH_SAMPLE.read_bytes())
    assert rec.account_type == "roth_ira"


class TestParseErrors:
    def test_unrecognized_broker_raises(self):
        with pytest.raises(StatementParseError):
            parse_statement_text(["no recognizable broker header anywhere in this text"])

    def test_invalid_account_type_rejected_by_dataclass(self):
        with pytest.raises(ValueError, match="Invalid account_type"):
            BrokerageStatementRecord(
                account_number="XXXX0000",
                broker="vanguard",
                account_type="not_a_real_type",
                statement_period_end="2026-06-30",
                interest_taxable_ytd=0.0,
                interest_tax_exempt_ytd=0.0,
                dividends_taxable_ytd=0.0,
                dividends_tax_exempt_ytd=0.0,
                stcg_net_ytd=0.0,
                ltcg_net_ytd=0.0,
                captured_at="2026-07-10T00:00:00+00:00",
            )


class TestScanStatementFolder:
    def test_skips_non_pdf_files(self, tmp_path):
        (tmp_path / "notes.txt").write_text("irrelevant")
        from engine.brokerage_statement_pdf import scan_statement_folder

        records, errors = scan_statement_folder(tmp_path)
        assert records == []
        assert errors == []

    def test_collects_parse_errors_without_raising(self, tmp_path):
        (tmp_path / "corrupt.pdf").write_bytes(b"not a real pdf")
        from engine.brokerage_statement_pdf import scan_statement_folder

        records, errors = scan_statement_folder(tmp_path)
        assert records == []
        assert len(errors) == 1
        assert "corrupt.pdf" in errors[0]

    @pytest.mark.skipif(
        not (SCHWAB_SAMPLE.exists() and VANGUARD_TAXABLE_SAMPLE.exists()),
        reason="Sample statements not present on this machine",
    )
    def test_parses_real_pdfs_in_folder(self, tmp_path):
        import shutil

        from engine.brokerage_statement_pdf import scan_statement_folder

        shutil.copy(SCHWAB_SAMPLE, tmp_path / "schwab.pdf")
        shutil.copy(VANGUARD_TAXABLE_SAMPLE, tmp_path / "vanguard_taxable.pdf")
        records, errors = scan_statement_folder(tmp_path)
        assert errors == []
        assert len(records) == 2
        assert {r.broker for r in records} == {"schwab", "vanguard"}
