"""Tests for engine.brokerage_statement_pdf -- anchored to real Schwab and
Vanguard statement dumps captured 2026-07 (pdfplumber extract_text() output,
trimmed to the pages containing the anchors this parser reads)."""

from __future__ import annotations

import pathlib

import pytest

from engine.brokerage_statement_pdf import (
    BrokerageStatementRecord,
    StatementParseError,
    extract_owner_key,
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
        recs = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.account_number == "****-*847"
        assert rec.broker == "schwab"

    def test_account_type_is_unknown(self):
        # Schwab statements never state account type -- must default to
        # unknown, NOT be inferred as taxable just because no IRA label was found.
        recs = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.account_type == "unknown"

    def test_interest_ytd_split(self):
        recs = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.interest_taxable_ytd == 18.56
        assert rec.interest_tax_exempt_ytd == 0.0

    def test_dividends_ytd_split(self):
        recs = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.dividends_taxable_ytd == 4846.82
        assert rec.dividends_tax_exempt_ytd == 78.74

    def test_gain_loss_ytd(self):
        recs = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.stcg_net_ytd == 19.11
        assert rec.ltcg_net_ytd == 283895.77

    def test_statement_period_end(self):
        recs = parse_statement_text([SCHWAB_PAGE_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.statement_period_end == "2026-06-30"


class TestParseVanguardTaxable:
    def test_account_type_taxable(self):
        recs = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.account_type == "taxable"
        assert rec.account_number == "XXXX9320"
        assert rec.broker == "vanguard"

    def test_dividends_ytd(self):
        recs = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.dividends_taxable_ytd == 1028.55
        assert rec.dividends_tax_exempt_ytd == 0.0

    def test_no_gains_or_interest(self):
        recs = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.interest_taxable_ytd == 0.0
        assert rec.stcg_net_ytd == 0.0
        assert rec.ltcg_net_ytd == 0.0

    def test_statement_period_end(self):
        recs = parse_statement_text([VANGUARD_TAXABLE_OVERVIEW_TEXT, VANGUARD_TAXABLE_INCOME_SUMMARY_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.statement_period_end == "2026-06-30"


class TestParseVanguardRoth:
    def test_account_type_roth(self):
        recs = parse_statement_text([VANGUARD_ROTH_OVERVIEW_TEXT, VANGUARD_ROTH_INCOME_SUMMARY_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        assert rec.account_type == "roth_ira"
        assert rec.account_number == "XXXX7368"

    def test_dividends_ytd(self):
        recs = parse_statement_text([VANGUARD_ROTH_OVERVIEW_TEXT, VANGUARD_ROTH_INCOME_SUMMARY_TEXT])
        assert len(recs) == 1
        rec = recs[0]
        # Note: "dividends_taxable_ytd" is just the dataclass field name inherited
        # from the taxable-column source; downstream aggregation must still
        # exclude this record entirely via account_type, regardless of which
        # dollar figure lives in which field.
        assert rec.dividends_taxable_ytd == 283.86


@pytest.mark.skipif(not SCHWAB_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_schwab_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    recs = parse_statement_pdf(SCHWAB_SAMPLE.read_bytes())
    assert len(recs) == 1
    rec = recs[0]
    assert rec.account_type == "unknown"
    assert rec.dividends_taxable_ytd > 0


@pytest.mark.skipif(not VANGUARD_TAXABLE_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_vanguard_taxable_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    recs = parse_statement_pdf(VANGUARD_TAXABLE_SAMPLE.read_bytes())
    assert len(recs) == 1
    rec = recs[0]
    assert rec.account_type == "taxable"
    assert rec.dividends_taxable_ytd == pytest.approx(1028.55)


@pytest.mark.skipif(not VANGUARD_ROTH_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_vanguard_roth_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    recs = parse_statement_pdf(VANGUARD_ROTH_SAMPLE.read_bytes())
    assert len(recs) == 1
    rec = recs[0]
    assert rec.account_type == "roth_ira"


# --- Real IBKR "Account Information" table excerpts (verbatim from a real
# 16-page pdfplumber dump of a 3-account consolidated Activity Statement).
# Note the deliberate absence of a colon after "Customer Type" -- pdfplumber
# flattened it to plain space-separated text, same lesson as Schwab. Also
# note the unrelated "Account Type Individual" line present on ALL THREE
# accounts (IBKR account-structure field, not tax treatment) -- this is a
# real trap the Customer Type detector must not fall into. ---
IBKR_ACCOUNT_1_TEXT = """Interactive Brokers LLC, Two Pickwick Plaza, Greenwich, CT 06830
Account Information
Name Claude R CIRBA
Account Alias Broker
Account U24711481
Account Type Individual
Customer Type Individual
Account Capabilities Margin
"""

IBKR_ACCOUNT_2_TEXT = """Interactive Brokers LLC, Two Pickwick Plaza, Greenwich, CT 06830
Account Information
Name Claude R CIRBA Rollover IRA, Interactive Brokers LLC Custodian
Account Alias IRA
Account U24721230
Account Type Individual
Customer Type IRA-Traditional Rollover
Account Capabilities Margin
"""

IBKR_ACCOUNT_3_TEXT = """Interactive Brokers LLC, Two Pickwick Plaza, Greenwich, CT 06830
Account Information
Name Claude R CIRBA Roth IRA, Interactive Brokers LLC Custodian
Account Alias Roth
Account U24727897
Account Type Individual
Customer Type IRA-Roth New
Account Capabilities Margin
"""


class TestDetectIbkrAccountType:
    def test_individual_is_taxable(self):
        from engine.brokerage_statement_pdf import _detect_ibkr_account

        account_number, account_type = _detect_ibkr_account(IBKR_ACCOUNT_1_TEXT)
        assert account_number == "U24711481"
        assert account_type == "taxable"

    def test_traditional_ira_rollover(self):
        from engine.brokerage_statement_pdf import _detect_ibkr_account

        account_number, account_type = _detect_ibkr_account(IBKR_ACCOUNT_2_TEXT)
        assert account_number == "U24721230"
        assert account_type == "traditional_ira"

    def test_roth_ira_new(self):
        from engine.brokerage_statement_pdf import _detect_ibkr_account

        account_number, account_type = _detect_ibkr_account(IBKR_ACCOUNT_3_TEXT)
        assert account_number == "U24727897"
        assert account_type == "roth_ira"

    def test_unrecognized_customer_type_falls_back_to_unknown(self):
        from engine.brokerage_statement_pdf import _detect_ibkr_account

        text = "Account Information\nAccount U99999999\nCustomer Type SEP-IRA\n"
        account_number, account_type = _detect_ibkr_account(text)
        assert account_number == "U99999999"
        assert account_type == "unknown"

    def test_account_type_individual_line_does_not_false_positive(self):
        # Regression test: every IBKR account has an unrelated "Account Type
        # Individual" line (account structure, not tax treatment) -- the
        # Customer Type detector must not confuse the two fields.
        from engine.brokerage_statement_pdf import _detect_ibkr_account

        _account_number, account_type = _detect_ibkr_account(IBKR_ACCOUNT_2_TEXT)
        assert account_type == "traditional_ira"  # not "taxable" despite "Account Type Individual" also present


class TestSplitIbkrSections:
    def test_splits_into_one_section_per_account(self):
        from engine.brokerage_statement_pdf import _split_ibkr_sections

        full_text = IBKR_ACCOUNT_1_TEXT + IBKR_ACCOUNT_2_TEXT + IBKR_ACCOUNT_3_TEXT
        sections = _split_ibkr_sections(full_text)
        assert len(sections) == 3

    def test_each_section_contains_its_own_account_number(self):
        from engine.brokerage_statement_pdf import _split_ibkr_sections

        full_text = IBKR_ACCOUNT_1_TEXT + IBKR_ACCOUNT_2_TEXT + IBKR_ACCOUNT_3_TEXT
        sections = _split_ibkr_sections(full_text)
        assert "U24711481" in sections[0]
        assert "U24721230" in sections[1]
        assert "U24727897" in sections[2]

    def test_roster_page_precedes_first_section_and_is_excluded(self):
        # The page-0 roster table (listing all accounts by NAV) has no
        # "Account Information" table of its own -- confirmed against a real
        # 3-account sample -- so text before the first "Account Information"
        # match must not become a bogus extra section.
        from engine.brokerage_statement_pdf import _split_ibkr_sections

        roster = "Account Summary\nU24711481 Broker Claude R CIRBA 6,976.29 6,979.02 0.04%\n"
        full_text = roster + IBKR_ACCOUNT_1_TEXT + IBKR_ACCOUNT_2_TEXT
        sections = _split_ibkr_sections(full_text)
        assert len(sections) == 2

    def test_no_account_information_raises(self):
        from engine.brokerage_statement_pdf import _split_ibkr_sections

        with pytest.raises(StatementParseError):
            _split_ibkr_sections("no account sections here")


class TestDetectBrokerIbkrOrdering:
    def test_ibkr_detected_even_with_vanguard_branded_holding(self):
        # Regression test for a confirmed real scenario: the sampled Roth
        # IBKR account holds a Vanguard-branded fund (VIMAX) -- IBKR must be
        # checked before Vanguard in _detect_broker or this would misdetect.
        from engine.brokerage_statement_pdf import _detect_broker

        text = IBKR_ACCOUNT_3_TEXT + "\nVWO Vanguard Vanguard Mid-Cap Index Fund Admiral\n"
        assert _detect_broker(text) == "ibkr"


# --- Real IBKR per-account full-text fixtures (Account Information + Month &
# Year to Date Performance Summary + Cash Report), verbatim from the same
# real 16-page dump. Confirmed real document order per account section:
# Account Information, then Performance Summary, then Cash Report. ---
IBKR_ACCOUNT_1_FULL_TEXT = """Activity Statement - July 9, 2026 Page: 1 of 5
Interactive Brokers LLC, Two Pickwick Plaza, Greenwich, CT 06830
Account Information
Name Claude R CIRBA
Account Alias Broker
Account U24711481
Account Type Individual
Customer Type Individual
Account Capabilities Margin
Month & Year to Date Performance Summary
Mark-to-Market Realized S/T Realized L/T
Symbol Description MTD YTD MTD YTD MTD YTD
Stocks
VHT VANGUARD HEALTH CARE ETF 158.13 620.77 0.00 0.00 0.00 0.00
Total Stocks 158.13 620.77 0.00 0.00 0.00 0.00
Cash Report
Total Securities Futures Month to Date Year to Date
Base Currency Summary
Cash Detail
Starting Cash 541.68 541.68 0.00
Deposits 0.00 0.00 0.00 0.00 500.14
Withdrawals 0.00 0.00 0.00 0.00 -0.14
Dividends 0.00 0.00 0.00 0.00 41.59
Broker Interest Paid and Received 0.00 0.00 0.00 0.04 0.09
Ending Cash 541.68 541.68 0.00
Ending Settled Cash 541.68 541.68 0.00
"""

IBKR_ACCOUNT_2_FULL_TEXT = """Activity Statement - July 9, 2026 Page: 2 of 5
Interactive Brokers LLC, Two Pickwick Plaza, Greenwich, CT 06830
Account Information
Name Claude R CIRBA Rollover IRA, Interactive Brokers LLC Custodian
Account Alias IRA
Account U24721230
Account Type Individual
Customer Type IRA-Traditional Rollover
Account Capabilities Margin
Month & Year to Date Performance Summary
Mark-to-Market Realized S/T Realized L/T
Symbol Description MTD YTD MTD YTD MTD YTD
Stocks
XYZ SOME STOCK 18,370.00 -27,110.00 0.00 0.00 0.00 0.00
Total Stocks 18,370.00 -27,110.00 0.00 0.00 0.00 0.00
Cash Report
Total Securities Futures Month to Date Year to Date
Base Currency Summary
Starting Cash 0.00 0.00 0.00
Ending Cash 0.00 0.00 0.00
Ending Settled Cash 0.00 0.00 0.00
"""

IBKR_ACCOUNT_3_FULL_TEXT = """Activity Statement - July 9, 2026 Page: 3 of 5
Interactive Brokers LLC, Two Pickwick Plaza, Greenwich, CT 06830
Account Information
Name Claude R CIRBA Roth IRA, Interactive Brokers LLC Custodian
Account Alias Roth
Account U24727897
Account Type Individual
Customer Type IRA-Roth New
Account Capabilities Margin
Month & Year to Date Performance Summary
Mark-to-Market Realized S/T Realized L/T
Symbol Description MTD YTD MTD YTD MTD YTD
Mutual Funds
ABC SOME MUTUAL FUND 56.37 2,254.48 0.00 0.00 0.00 0.00
Total Mutual Funds 56.37 2,254.48 0.00 0.00 0.00 0.00
Cash Report
Total Securities Futures Month to Date Year to Date
Base Currency Summary
Starting Cash 5.89 5.89 0.00
Dividends 0.00 0.00 0.00 0.00 128.42
Trades (Purchase) 0.00 0.00 0.00 0.00 -122.53
Ending Cash 5.89 5.89 0.00
Ending Settled Cash 5.89 5.89 0.00
"""


class TestExtractIbkrCashReport:
    def test_dividends_and_interest_ytd_take_last_column_not_first(self):
        # Regression test for the exact bug the grounding step caught: a
        # naive "first number after the label" match would capture the Total
        # column (0.00), not the real YTD figure (last column).
        from engine.brokerage_statement_pdf import _extract_ibkr_cash_report

        dividends, interest = _extract_ibkr_cash_report(IBKR_ACCOUNT_1_FULL_TEXT)
        assert dividends == 41.59
        assert interest == 0.09

    def test_missing_rows_fall_back_to_zero(self):
        # The Traditional IRA account has zero cash flow of either type this
        # period, so neither row appears at all -- confirmed real scenario.
        from engine.brokerage_statement_pdf import _extract_ibkr_cash_report

        dividends, interest = _extract_ibkr_cash_report(IBKR_ACCOUNT_2_FULL_TEXT)
        assert dividends == 0.0
        assert interest == 0.0

    def test_dividends_present_without_interest_row(self):
        from engine.brokerage_statement_pdf import _extract_ibkr_cash_report

        dividends, interest = _extract_ibkr_cash_report(IBKR_ACCOUNT_3_FULL_TEXT)
        assert dividends == 128.42
        assert interest == 0.0


class TestExtractIbkrGains:
    def test_zero_realized_gains_real_sample(self):
        from engine.brokerage_statement_pdf import _extract_ibkr_gains

        stcg, ltcg = _extract_ibkr_gains(IBKR_ACCOUNT_1_FULL_TEXT)
        assert stcg == 0.0
        assert ltcg == 0.0

    def test_handles_comma_formatted_and_negative_numbers(self):
        # Account 2's Performance Summary row uses comma-grouped and negative
        # figures (18,370.00 / -27,110.00 in the MTM columns) -- confirms the
        # extraction doesn't choke on either, even though the realized S/T/L/T
        # columns themselves are zero in this sample.
        from engine.brokerage_statement_pdf import _extract_ibkr_gains

        stcg, ltcg = _extract_ibkr_gains(IBKR_ACCOUNT_2_FULL_TEXT)
        assert stcg == 0.0
        assert ltcg == 0.0

    def test_sums_across_multiple_asset_classes(self):
        # Synthetic test: no sampled account happens to hold >1 asset class
        # with nonzero realized gains, so this exercises the summing logic
        # itself (Bug #2 from grounding: there is no single "Total (All
        # Assets)" row in this table -- every asset-class row must be summed).
        from engine.brokerage_statement_pdf import _extract_ibkr_gains

        section = """Month & Year to Date Performance Summary
Mark-to-Market Realized S/T Realized L/T
Symbol Description MTD YTD MTD YTD MTD YTD
Stocks
ABC SOME STOCK 10.00 20.00 5.00 15.00 0.00 0.00
Total Stocks 10.00 20.00 5.00 15.00 0.00 0.00
Mutual Funds
XYZ SOME FUND 1.00 2.00 0.00 0.00 3.00 40.00
Total Mutual Funds 1.00 2.00 0.00 0.00 3.00 40.00
Cash Report
Total Securities Futures Month to Date Year to Date
Starting Cash 0.00 0.00 0.00
"""
        stcg, ltcg = _extract_ibkr_gains(section)
        assert stcg == 15.00
        assert ltcg == 40.00


class TestExtractIbkrPeriodEnd:
    def test_extracts_statement_date(self):
        from engine.brokerage_statement_pdf import _extract_ibkr_period_end

        assert _extract_ibkr_period_end(IBKR_ACCOUNT_1_FULL_TEXT) == "2026-07-09"

    def test_missing_date_raises(self):
        from engine.brokerage_statement_pdf import _extract_ibkr_period_end

        with pytest.raises(StatementParseError):
            _extract_ibkr_period_end("no date here")


class TestParseIbkr:
    def test_returns_one_record_per_account(self):
        recs = parse_statement_text([IBKR_ACCOUNT_1_FULL_TEXT, IBKR_ACCOUNT_2_FULL_TEXT, IBKR_ACCOUNT_3_FULL_TEXT])
        assert len(recs) == 3
        assert {r.account_number for r in recs} == {"U24711481", "U24721230", "U24727897"}
        assert all(r.broker == "ibkr" for r in recs)
        assert all(r.statement_period_end == "2026-07-09" for r in recs)

    def test_individual_account_dividends_and_interest(self):
        from engine.brokerage_statement_pdf import _parse_ibkr

        recs = _parse_ibkr(IBKR_ACCOUNT_1_FULL_TEXT)
        assert len(recs) == 1
        rec = recs[0]
        assert rec.broker == "ibkr"
        assert rec.account_type == "taxable"
        assert rec.dividends_taxable_ytd == 41.59
        assert rec.interest_taxable_ytd == 0.09
        assert rec.dividends_tax_exempt_ytd == 0.0  # IBKR gives no split -- see module docstring
        assert rec.interest_tax_exempt_ytd == 0.0
        assert rec.stcg_net_ytd == 0.0
        assert rec.ltcg_net_ytd == 0.0

    def test_traditional_ira_account_zero_dividends(self):
        from engine.brokerage_statement_pdf import _parse_ibkr

        recs = _parse_ibkr(IBKR_ACCOUNT_2_FULL_TEXT)
        rec = recs[0]
        assert rec.account_type == "traditional_ira"
        assert rec.dividends_taxable_ytd == 0.0
        assert rec.interest_taxable_ytd == 0.0

    def test_roth_account_dividends(self):
        from engine.brokerage_statement_pdf import _parse_ibkr

        recs = _parse_ibkr(IBKR_ACCOUNT_3_FULL_TEXT)
        rec = recs[0]
        assert rec.account_type == "roth_ira"
        assert rec.dividends_taxable_ytd == 128.42


IBKR_SAMPLE = pathlib.Path("/home/memento/Downloads/MULTI_20260709.pdf")


@pytest.mark.skipif(not IBKR_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_ibkr_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    recs = parse_statement_pdf(IBKR_SAMPLE.read_bytes())
    assert len(recs) == 3
    account_types = {r.account_type for r in recs}
    assert account_types == {"taxable", "traditional_ira", "roth_ira"}
    # Every account number must be unique -- if the splitter double-counted a
    # section or missed a boundary, this catches it.
    assert len({r.account_number for r in recs}) == 3


# --- Real Fidelity per-account excerpts (verbatim from a real 18-page
# pdfplumber dump of a 2-account consolidated statement). Each begins with
# its "Accounts Included in This Report" roster-line (informational only,
# not used by the splitter) followed by the real per-account detail header,
# Account Summary, and Income Summary. ---
FIDELITY_ROLLOVER_IRA_TEXT = """4 FIDELITY ROLLOVER IRA CLAUDE R CIRBA - ROLLOVER IRA - FIDELITY 233-813501 $1,261,639.23 $1,265,478.63
INVESTMENT REPORT
June 1, 2026 - June 30, 2026
Account # 233-813501
Account Summary
CLAUDE R CIRBA - ROLLOVER IRA
Account Value: $1,265,478.63 Account Holdings
Change in Account Value $3,839.40
Income Summary
This Period Year-to-Date
Tax-deferred $3,919.90 $17,540.05
Total $3,919.90 $17,540.05
Contributions and Distributions
This Period Year-to-Date
2026 Contributions $800.00 $5,600.00
"""

FIDELITY_HSA_TEXT = """9 FIDELITY HEALTH SAVINGS ACCOUNT CLAUDE R CIRBA HEALTH SAVINGS ACCOUNT FIDELITY PERSONAL TRUST CO - CUSTODIAN 178-734462 $64,988.63 $65,435.87
INVESTMENT REPORT
June 1, 2026 - June 30, 2026
Account # 178-734462
Account Summary
CLAUDE R CIRBA - HEALTH SAVINGS ACCOUNT
Account Value: $65,435.87 Account Holdings
Change in Account Value $447.24
Income Summary
This Period Year-to-Date
Tax-free $119.36 $464.05
Total $119.36 $464.05
Contributions and Distributions
This Period Year-to-Date
2026 Partic. $515.00 $3,090.00
"""


class TestDetectFidelityAccountType:
    def test_rollover_ira_is_traditional_ira(self):
        from engine.brokerage_statement_pdf import _detect_fidelity_account

        account_number, account_type = _detect_fidelity_account(FIDELITY_ROLLOVER_IRA_TEXT)
        assert account_number == "233-813501"
        assert account_type == "traditional_ira"

    def test_hsa_is_hsa(self):
        from engine.brokerage_statement_pdf import _detect_fidelity_account

        account_number, account_type = _detect_fidelity_account(FIDELITY_HSA_TEXT)
        assert account_number == "178-734462"
        assert account_type == "hsa"

    def test_unrecognized_fidelity_account_falls_back_to_unknown(self):
        from engine.brokerage_statement_pdf import _detect_fidelity_account

        text = "Account # 999-999999\nAccount Summary\nSOME OTHER ACCOUNT TYPE\n"
        account_number, account_type = _detect_fidelity_account(text)
        assert account_number == "999-999999"
        assert account_type == "unknown"  # never guess -- same safety rule as Vanguard/IBKR fallbacks


class TestSplitFidelitySections:
    def test_splits_into_one_section_per_account(self):
        from engine.brokerage_statement_pdf import _split_fidelity_sections

        full_text = FIDELITY_ROLLOVER_IRA_TEXT + FIDELITY_HSA_TEXT
        sections = _split_fidelity_sections(full_text)
        assert len(sections) == 2

    def test_bare_account_hash_repeat_does_not_oversplit(self):
        # Regression test: a bare "Account # <number>" repeats on every page
        # of that account's section (Holdings, Activity, Cash Flow all
        # restate it) -- only the "Account # <number>\nAccount Summary" combo
        # should count as a section start, or this would over-split.
        from engine.brokerage_statement_pdf import _split_fidelity_sections

        text = (
            FIDELITY_ROLLOVER_IRA_TEXT
            + "Account # 233-813501\nHoldings\nCLAUDE R CIRBA - ROLLOVER IRA\n"
            + FIDELITY_HSA_TEXT
        )
        sections = _split_fidelity_sections(text)
        assert len(sections) == 2

    def test_no_account_summary_marker_raises(self):
        from engine.brokerage_statement_pdf import _split_fidelity_sections

        with pytest.raises(StatementParseError):
            _split_fidelity_sections("no account sections here")


class TestExtractFidelityIncomeYtd:
    def test_rollover_ira_tax_deferred_total(self):
        from engine.brokerage_statement_pdf import _extract_fidelity_income_ytd

        assert _extract_fidelity_income_ytd(FIDELITY_ROLLOVER_IRA_TEXT) == 17540.05

    def test_hsa_tax_free_total(self):
        from engine.brokerage_statement_pdf import _extract_fidelity_income_ytd

        assert _extract_fidelity_income_ytd(FIDELITY_HSA_TEXT) == 464.05


class TestParseFidelity:
    def test_returns_one_record_per_account(self):
        recs = parse_statement_text([FIDELITY_ROLLOVER_IRA_TEXT, FIDELITY_HSA_TEXT])
        assert len(recs) == 2
        assert {r.broker for r in recs} == {"fidelity"}
        assert {r.account_type for r in recs} == {"traditional_ira", "hsa"}
        assert all(r.statement_period_end == "2026-06-30" for r in recs)

    def test_rollover_ira_income(self):
        from engine.brokerage_statement_pdf import _parse_fidelity

        recs = _parse_fidelity(FIDELITY_ROLLOVER_IRA_TEXT)
        rec = recs[0]
        assert rec.account_type == "traditional_ira"
        assert rec.dividends_taxable_ytd == 17540.05
        assert rec.dividends_tax_exempt_ytd == 0.0

    def test_hsa_income(self):
        from engine.brokerage_statement_pdf import _parse_fidelity

        recs = _parse_fidelity(FIDELITY_HSA_TEXT)
        rec = recs[0]
        assert rec.account_type == "hsa"
        assert rec.dividends_taxable_ytd == 464.05


FIDELITY_SAMPLE = pathlib.Path("/home/memento/Downloads/FidelityStatement06302026.pdf")


@pytest.mark.skipif(not FIDELITY_SAMPLE.exists(), reason="Sample statement not present on this machine")
def test_parse_real_fidelity_sample():
    from engine.brokerage_statement_pdf import parse_statement_pdf

    recs = parse_statement_pdf(FIDELITY_SAMPLE.read_bytes())
    assert len(recs) == 2
    account_types = {r.account_type for r in recs}
    assert account_types == {"traditional_ira", "hsa"}
    assert len({r.account_number for r in recs}) == 2


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


def _rec(account: str, period_end: str, account_type: str = "taxable", **overrides) -> BrokerageStatementRecord:
    base = {
        "account_number": account,
        "broker": "schwab",
        "account_type": account_type,
        "statement_period_end": period_end,
        "interest_taxable_ytd": 0.0,
        "interest_tax_exempt_ytd": 0.0,
        "dividends_taxable_ytd": 0.0,
        "dividends_tax_exempt_ytd": 0.0,
        "stcg_net_ytd": 0.0,
        "ltcg_net_ytd": 0.0,
        "captured_at": "2026-07-10T00:00:00+00:00",
    }
    base.update(overrides)
    return BrokerageStatementRecord(**base)


class TestPickLatestPerAccount:
    def test_keeps_latest_period_end_per_account(self):
        from engine.brokerage_statement_pdf import pick_latest_per_account

        older = _rec("111-1111", "2026-05-31", dividends_taxable_ytd=100.0)
        newer = _rec("111-1111", "2026-06-30", dividends_taxable_ytd=200.0)
        result = pick_latest_per_account([older, newer])
        assert result["111-1111"].dividends_taxable_ytd == 200.0

    def test_keeps_separate_accounts_independent(self):
        from engine.brokerage_statement_pdf import pick_latest_per_account

        acct_a = _rec("111-1111", "2026-06-30")
        acct_b = _rec("222-2222", "2026-06-30")
        result = pick_latest_per_account([acct_a, acct_b])
        assert set(result.keys()) == {"111-1111", "222-2222"}


class TestPartitionByAccountType:
    def test_roth_ira_excluded_from_taxable(self):
        # Regression test for the exact bug that motivated this whole feature:
        # Roth/IRA income must never land in taxable YTD sums.
        from engine.brokerage_statement_pdf import partition_by_account_type

        taxable_acct = _rec("XXXX9320", "2026-06-30", account_type="taxable", dividends_taxable_ytd=1028.55)
        roth_acct = _rec("XXXX7368", "2026-06-30", account_type="roth_ira", dividends_taxable_ytd=283.86)
        by_account = {"XXXX9320": taxable_acct, "XXXX7368": roth_acct}

        taxable, excluded, unknown = partition_by_account_type(by_account)
        assert set(taxable.keys()) == {"XXXX9320"}
        assert set(excluded.keys()) == {"XXXX7368"}
        assert unknown == {}

    def test_unknown_is_excluded_by_default_not_summed(self):
        from engine.brokerage_statement_pdf import partition_by_account_type

        unknown_acct = _rec("3413-3847", "2026-06-30", account_type="unknown", dividends_taxable_ytd=4846.82)
        taxable, excluded, unknown = partition_by_account_type({"3413-3847": unknown_acct})
        assert taxable == {}
        assert excluded == {}
        assert set(unknown.keys()) == {"3413-3847"}

    def test_hsa_is_valid_account_type(self):
        rec = _rec("178-734462", "2026-06-30", account_type="hsa")
        assert rec.account_type == "hsa"

    def test_hsa_excluded_from_taxable_partition(self):
        from engine.brokerage_statement_pdf import partition_by_account_type

        hsa_acct = _rec("178-734462", "2026-06-30", account_type="hsa", dividends_taxable_ytd=100.0)
        taxable, excluded, unknown = partition_by_account_type({"178-734462": hsa_acct})
        assert taxable == {}
        assert set(excluded.keys()) == {"178-734462"}
        assert unknown == {}


class TestAggregateToYtdFields:
    def test_sums_across_taxable_accounts_only(self):
        from engine.brokerage_statement_pdf import aggregate_to_ytd_fields

        taxable = {
            "111-1111": _rec("111-1111", "2026-06-30", dividends_taxable_ytd=100.0, interest_taxable_ytd=10.0),
            "222-2222": _rec("222-2222", "2026-06-30", dividends_taxable_ytd=50.0, dividends_tax_exempt_ytd=5.0),
        }
        totals = aggregate_to_ytd_fields(taxable)
        assert totals["ordinary_dividends_ytd"] == 150.0
        assert totals["interest_ytd"] == 10.0
        assert totals["tax_exempt_interest_ytd"] == 5.0


class TestCacheRoundTrip:
    def test_save_and_load(self, tmp_path, monkeypatch):
        import engine.brokerage_statement_pdf as mod

        monkeypatch.setattr(mod, "_STATEMENT_CACHE_PATH", tmp_path / "cache.json")
        rec = _rec("111-1111", "2026-06-30", dividends_taxable_ytd=100.0)
        mod.save_statement_records({"111-1111": rec})
        loaded = mod.load_statement_records()
        assert loaded["111-1111"].dividends_taxable_ytd == 100.0

    def test_load_missing_file_returns_empty(self, tmp_path, monkeypatch):
        import engine.brokerage_statement_pdf as mod

        monkeypatch.setattr(mod, "_STATEMENT_CACHE_PATH", tmp_path / "missing.json")
        assert mod.load_statement_records() == {}

    def test_empty_scan_does_not_wipe_existing_cache(self, tmp_path, monkeypatch):
        """C101 (audit-0805, LOW-but-real): save_statement_records is a
        full-file overwrite, so scanning a folder with no brokerage PDFs
        (by_account={}) silently wipes every previously confirmed account."""
        import engine.brokerage_statement_pdf as mod

        monkeypatch.setattr(mod, "_STATEMENT_CACHE_PATH", tmp_path / "cache.json")
        rec = _rec("111-1111", "2026-06-30", dividends_taxable_ytd=100.0)
        mod.save_statement_records({"111-1111": rec})

        # A later scan of a folder with no brokerage PDFs produces {}.
        mod.save_statement_records({})

        loaded = mod.load_statement_records()
        assert "111-1111" in loaded, (
            "An empty-dict save_statement_records() call wiped the confirmed "
            f"account cache; loaded={loaded}"
        )


class TestFolderPathConfig:
    def test_save_and_load_folder_path(self, tmp_path, monkeypatch):
        import engine.brokerage_statement_pdf as mod

        monkeypatch.setattr(mod, "_FOLDER_CONFIG_PATH", tmp_path / "folder.json")
        mod.save_statement_folder_path("/home/memento/Statements")
        assert mod.load_statement_folder_path() == "/home/memento/Statements"

    def test_load_folder_path_missing_returns_none(self, tmp_path, monkeypatch):
        import engine.brokerage_statement_pdf as mod

        monkeypatch.setattr(mod, "_FOLDER_CONFIG_PATH", tmp_path / "missing.json")
        assert mod.load_statement_folder_path() is None

    def test_save_and_load_empty_folder_path_roundtrips_as_empty_string(self, tmp_path, monkeypatch):
        import engine.brokerage_statement_pdf as mod

        monkeypatch.setattr(mod, "_FOLDER_CONFIG_PATH", tmp_path / "folder.json")
        mod.save_statement_folder_path("")
        assert mod.load_statement_folder_path() == ""


class TestExtractOwnerKeySchwab:
    def test_extracts_account_holder_name(self) -> None:
        # Schwab's extract_text() strips spaces from labels but NOT from the
        # holder's own name line ("CLAUDECIRBA" in the real dump has no space
        # because Schwab renders it as one run -- confirmed in SCHWAB_PAGE_TEXT).
        assert extract_owner_key(SCHWAB_PAGE_TEXT) == "CLAUDECIRBA"


class TestExtractOwnerKeyVanguard:
    def test_extracts_account_holder_name(self) -> None:
        assert extract_owner_key(VANGUARD_TAXABLE_OVERVIEW_TEXT) == "Claude R Cirba"


class TestExtractOwnerKeyAbsent:
    def test_returns_none_when_no_name_found(self) -> None:
        assert extract_owner_key("Some Broker Statement\nNo holder name here\n") is None


class TestAccountTypeOverrides:
    def test_save_and_apply_override(self, tmp_path, monkeypatch):
        import engine.brokerage_statement_pdf as mod

        monkeypatch.setattr(mod, "_ACCOUNT_TYPE_OVERRIDES_PATH", tmp_path / "overrides.json")
        mod.save_account_type_override("3413-3847", "taxable")
        overrides = mod.load_account_type_overrides()
        assert overrides["3413-3847"] == "taxable"

    def test_apply_overrides_reclassifies_unknown(self, tmp_path):
        from engine.brokerage_statement_pdf import apply_account_type_overrides

        unknown_acct = _rec("3413-3847", "2026-06-30", account_type="unknown")
        result = apply_account_type_overrides({"3413-3847": unknown_acct}, {"3413-3847": "taxable"})
        assert result["3413-3847"].account_type == "taxable"
