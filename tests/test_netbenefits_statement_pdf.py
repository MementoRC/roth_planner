"""Tests for the Fidelity NetBenefits workplace-plan (403(b)) statement parser.

The fixture below is SYNTHETIC: the layout copies a NetBenefits "Statement
Details" page printed from Chrome (page header/footer lines, wrapped fund
names, wrapped contribution-source labels), but every name, address, plan and
amount is invented. Amounts are internally consistent: fund values and
per-source balances both sum to the ending balance.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from engine.brokerage_statement_pdf import detect_broker
from engine.data_sources.record import record_ira_balance_candidate
from engine.netbenefits_statement_pdf import (
    NetBenefitsParseError,
    apply_netbenefits_statement,
    default_netbenefits_owner,
    is_netbenefits_statement,
    parse_netbenefits_text,
    pick_latest_netbenefits,
    replace_owner_403b,
    to_account_summary,
)
from engine.pdf_import import DocKind, PdfImportResult, _dispatch_pdf, classify_pdf_text
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from engine.upload_merge import derive_ira_balances

_HEADER = "1/02/27, 8:15 AM Fidelity NetBenefits - Statement Details"
_FOOTER = "https://workplaceservices.fidelity.com/mybenefits/savings2/sod/soddetail {n}/3"
_TABLE_HEADER = (
    "Shares/Units Shares/Units Account Account\n"
    "Price as of Price as of\n"
    "Investment as of as of Value as of Value as of\n"
    "12/31/2025 06/30/2026\n"
    "12/31/2025 06/30/2026 12/31/2025 06/30/2026"
)

PAGE_1 = f"""{_HEADER}
Statement Details
Example State University 403b
Retirement Savings Statement
Retirement Plan
JANE Q SAMPLE Customer Service: (800) 555-
100 PRETEND LANE 0100
SOMEWHERE, ZZ 00000- Fidelity Brokerage Services LLC
1 Example Street, Nowhere, ZZ
00000
Your Account Summary
Statement Period: 01/01/2026 to 06/30/2026
Beginning Balance $29,500.00
Fees -$5.00
Change in Account Value $5,405.00
Ending Balance $34,900.00
Additional Information
Vested Balance $34,900.00
Dividends & Interest $812.34
Your Personal Rate of Return
This Period 18.3%
Account Value
Statement Period: 01/01/2026 to 06/30/2026
CORE FUNDS
{_TABLE_HEADER}
Stock $3,700.00 $4,000.00
International
{_FOOTER.format(n=1)}"""

PAGE_2 = f"""{_HEADER}
{_TABLE_HEADER}
Acme Intl 10.000 12.000 $20.00 $25.00 $200.00 $300.00
Equity IS
Small Cap
Bigco US 50.000 50.000 $10.00 $12.00 $500.00 $600.00
Small Value
Large Cap
Vang Div
Growth INV 100.000 100.000 $30.00 $31.00 $3,000.00 $3,100.00
TARGET DATE FUNDS
{_TABLE_HEADER}
Blended Investments $25,800.00 $30,900.00
Vang Target 20.000 20.000 $40.00 $45.00 $800.00 $900.00
Ret 2030
Vang Target
Ret 2035 1,000.000 1,000.000 $25.00 $30.00 $25,000.00 $30,000.00
Account Totals $29,500.00 $34,900.00
Source Group 2
Includes: VOLUNTARY ROTH DEFERRAL, ROLLOVER
ROTH, ROTH IN-PLAN CONVERSION, ROTH IPC-
{_FOOTER.format(n=2)}"""

PAGE_3 = f"""{_HEADER}
Your Contribution Summary
Statement Period: 01/01/2026 to 06/30/2026
Total Account Total Vested
Contributions Period to date Vested Percent
Balance Balance
Employee
$0.00 100% $10,000.00 $10,000.00
Mandatory
Voluntary Pretax
$0.00 100% $14,900.00 $14,900.00
Deferral
Employer Match $0.00 100% $6,000.00 $6,000.00
Prior Supplemental $0.00 100% $0.00 $0.00
Voluntary Match $0.00 100% $4,000.00 $4,000.00
Your Account Activity
Statement Period: 01/01/2026 to 06/30/2026
Activity Total
Ending Balance $34,900.00
Dividends & Interest $812.34
{_FOOTER.format(n=3)}"""

PAGES = [PAGE_1, PAGE_2, PAGE_3]

# A Fidelity brokerage statement page: says "Fidelity" but carries none of the
# NetBenefits markers, so it must keep routing to the brokerage parser.
FIDELITY_BROKERAGE_PAGE = (
    "Fidelity Brokerage Services LLC\n"
    "INVESTMENT REPORT January 1, 2026 - June 30, 2026\n"
    "Account Summary"
)


class TestDetection:
    def test_netbenefits_statement_is_detected(self) -> None:
        assert is_netbenefits_statement("\n".join(PAGES))

    def test_plain_fidelity_brokerage_is_not_netbenefits(self) -> None:
        assert not is_netbenefits_statement(FIDELITY_BROKERAGE_PAGE)

    def test_classifier_routes_netbenefits_before_the_bare_fidelity_match(self) -> None:
        # The bare "Fidelity" broker match would claim this document too.
        assert detect_broker("\n".join(PAGES)) == "fidelity"
        assert classify_pdf_text(PAGES) == DocKind.NETBENEFITS

    def test_netbenefits_naming_vanguard_still_routes_to_netbenefits(self) -> None:
        pages = [*PAGES[:-1], PAGES[-1] + "\nVanguard Target Retirement 2035 Trust"]
        assert classify_pdf_text(pages) == DocKind.NETBENEFITS

    def test_plain_fidelity_brokerage_still_classifies_as_brokerage(self) -> None:
        assert classify_pdf_text([FIDELITY_BROKERAGE_PAGE]) == DocKind.BROKERAGE


class TestParse:
    def test_summary_fields(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        assert stmt.plan_name == "Example State University 403b"
        assert stmt.owner_key == "JANE Q SAMPLE"
        assert stmt.period_start == "2026-01-01"
        assert stmt.period_end == "2026-06-30"
        assert stmt.ending_balance == pytest.approx(34_900.00)
        assert stmt.vested_balance == pytest.approx(34_900.00)
        assert stmt.warnings == []

    def test_source_balances_rejoin_wrapped_labels(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        assert stmt.source_balances == {
            "Employee Mandatory": pytest.approx(10_000.00),
            "Voluntary Pretax Deferral": pytest.approx(14_900.00),
            "Employer Match": pytest.approx(6_000.00),
            "Prior Supplemental": pytest.approx(0.0),
            "Voluntary Match": pytest.approx(4_000.00),
        }

    def test_holdings_use_period_end_columns_and_rejoin_wrapped_names(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        got = [(h.description, h.shares, h.price, h.value, h.asset_class) for h in stmt.holdings]
        assert got == [
            ("Acme Intl Equity IS", 12.0, 25.0, 300.0, "equity"),
            ("Bigco US Small Value", 50.0, 12.0, 600.0, "equity"),
            ("Vang Div Growth INV", 100.0, 31.0, 3_100.0, "equity"),
            ("Vang Target Ret 2030", 20.0, 45.0, 900.0, "target_date"),
            ("Vang Target Ret 2035", 1_000.0, 30.0, 30_000.0, "target_date"),
        ]

    def test_missing_ending_balance_raises(self) -> None:
        pages = [PAGE_1.replace("Ending Balance $34,900.00", ""), PAGE_2, ""]
        with pytest.raises(NetBenefitsParseError, match="Ending Balance"):
            parse_netbenefits_text(pages)

    def test_missing_statement_period_raises(self) -> None:
        pages = [p.replace("Statement Period:", "Period") for p in PAGES]
        with pytest.raises(NetBenefitsParseError, match="Statement Period"):
            parse_netbenefits_text(pages)

    def test_holdings_that_do_not_reconcile_are_dropped_with_a_warning(self) -> None:
        pages = [PAGE_1, PAGE_2.replace("$30,000.00\n", "$29,000.00\n"), PAGE_3]
        stmt = parse_netbenefits_text(pages)
        assert stmt.holdings == []
        assert any("do not add up" in w for w in stmt.warnings)
        assert stmt.ending_balance == pytest.approx(34_900.00)

    def test_missing_holder_name_is_none_not_a_guess(self) -> None:
        pages = [PAGE_1.replace("JANE Q SAMPLE Customer Service:", "Customer Service:"), *PAGES[1:]]
        assert parse_netbenefits_text(pages).owner_key is None


class TestAccountSummary:
    def test_account_summary_is_a_403b_with_asset_class_split(self) -> None:
        acct = to_account_summary(parse_netbenefits_text(PAGES), "spouse")
        assert acct.account_type == "403b"
        assert acct.owner == "spouse"
        assert acct.account_name == "Example State University 403b"
        assert acct.total_value == pytest.approx(34_900.00)
        assert acct.equity_value == pytest.approx(4_000.00)
        assert acct.target_date_value == pytest.approx(30_900.00)
        assert len(acct.holdings) == 5
        assert all(h.account_name == acct.account_name for h in acct.holdings)

    def test_unreconciled_statement_puts_whole_balance_in_target_date(self) -> None:
        pages = [PAGE_1, PAGE_2.replace("$30,000.00\n", "$29,000.00\n"), PAGE_3]
        acct = to_account_summary(parse_netbenefits_text(pages), "you")
        assert acct.holdings == []
        assert acct.target_date_value == pytest.approx(34_900.00)
        assert acct.total_value == pytest.approx(34_900.00)

    def test_rejects_household_owner(self) -> None:
        with pytest.raises(ValueError, match="you.*spouse"):
            to_account_summary(parse_netbenefits_text(PAGES), "household")


class TestReplaceOwner403b:
    def _snap(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            accounts=[
                AccountSummary(account_type="trad_ira", owner="you", total_value=100_000.0),
                AccountSummary(
                    account_type="403b", owner="you", account_name="FX 403b", total_value=50_000.0
                ),
                AccountSummary(account_type="403b", owner="spouse", total_value=20_000.0),
                AccountSummary(account_type="brokerage", owner="you", total_value=7_000.0),
            ],
            server_available=True,
        )

    def test_replaces_only_that_owners_403b(self) -> None:
        snap = self._snap()
        new_acct = to_account_summary(parse_netbenefits_text(PAGES), "you")

        new_snap, replaced = replace_owner_403b(snap, new_acct)

        assert [a.account_name for a in replaced] == ["FX 403b"]
        assert derive_ira_balances(new_snap) == (
            pytest.approx(100_000.0 + 34_900.0),
            pytest.approx(20_000.0),
        )
        assert sum(1 for a in new_snap.accounts if a.account_type == "brokerage") == 1

    def test_does_not_mutate_the_input_snapshot(self) -> None:
        snap = self._snap()
        new_acct = to_account_summary(parse_netbenefits_text(PAGES), "you")
        replace_owner_403b(snap, new_acct)
        assert derive_ira_balances(snap) == (150_000.0, 20_000.0)

    def test_owner_without_a_403b_gets_one_added(self) -> None:
        snap = PortfolioSnapshot(
            accounts=[AccountSummary(account_type="trad_ira", owner="spouse", total_value=5.0)]
        )
        new_acct = to_account_summary(parse_netbenefits_text(PAGES), "spouse")
        new_snap, replaced = replace_owner_403b(snap, new_acct)
        assert replaced == []
        assert derive_ira_balances(new_snap) == (0.0, pytest.approx(34_905.0))


class TestPickLatest:
    def test_latest_period_end_wins_per_plan_and_holder(self) -> None:
        older_pages = [p.replace("06/30/2026", "03/31/2026") for p in PAGES]
        older = parse_netbenefits_text(older_pages)
        newer = parse_netbenefits_text(PAGES)
        assert pick_latest_netbenefits([newer, older]) == [newer]
        assert pick_latest_netbenefits([older, newer]) == [newer]

    def test_different_holders_are_both_kept(self) -> None:
        a = parse_netbenefits_text(PAGES)
        b_pages = [PAGE_1.replace("JANE Q SAMPLE", "JOHN R SAMPLE"), PAGE_2, PAGE_3]
        b = parse_netbenefits_text(b_pages)
        assert len(pick_latest_netbenefits([a, b])) == 2


class TestApplyStatement:
    def test_apply_replaces_403b_and_reports_the_new_ira_total(self) -> None:
        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(account_type="trad_ira", owner="spouse", total_value=1_000.0),
                AccountSummary(account_type="403b", owner="spouse", total_value=9.0),
            ]
        )
        outcome = apply_netbenefits_statement(snap, parse_netbenefits_text(PAGES), "spouse")
        assert outcome.field_key == "spouse_ira"
        assert outcome.ira_total == pytest.approx(35_900.0)
        assert [a.total_value for a in outcome.replaced] == [9.0]
        assert derive_ira_balances(outcome.snapshot)[1] == pytest.approx(35_900.0)
        assert "Example State University 403b" in outcome.detail
        assert "2026-06-30" in outcome.detail

    def test_you_owner_maps_to_your_ira_and_records(self, tmp_path: Path) -> None:
        # Issue #552: f"{owner}_ira" produced "you_ira", which the candidate
        # recorder rejects, so Apply crashed for the "you" owner.
        snap = PortfolioSnapshot(
            accounts=[AccountSummary(account_type="403b", owner="you", total_value=9.0)]
        )
        outcome = apply_netbenefits_statement(snap, parse_netbenefits_text(PAGES), "you")
        assert outcome.field_key == "your_ira"
        assert record_ira_balance_candidate(
            outcome.field_key,
            outcome.ira_total,
            outcome.detail,
            datetime(2026, 7, 1),
            store_path=tmp_path / "candidates.json",
        )


class TestDefaultOwner:
    def test_learned_holder_name_wins(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        assert default_netbenefits_owner(stmt, {"jane q sample": "spouse"}, "you") == "spouse"

    def test_unknown_holder_falls_back_to_the_instance_owner(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        assert default_netbenefits_owner(stmt, {}, "spouse") == "spouse"

    def test_household_mapping_or_owner_falls_back_to_you(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        assert default_netbenefits_owner(stmt, {"jane q sample": "household"}, "household") == "you"


class TestRecordIraCandidate:
    def test_records_a_pdf_candidate(self, tmp_path) -> None:
        from datetime import datetime

        from engine.data_sources.candidate_store import CandidateStore
        from engine.data_sources.record import record_ira_balance_candidate
        from models.sourced import Source

        store_path = tmp_path / "store.json"
        when = datetime(2026, 10, 1, 12, 0)
        assert record_ira_balance_candidate(
            "spouse_ira", 35_900.4, "NetBenefits 403(b) PDF", when, store_path=store_path
        )
        [cand] = CandidateStore.load(store_path).candidates_for("spouse_ira")
        assert cand.value == 35_900.0
        assert cand.prov.source == Source.PDF
        assert cand.prov.detail == "NetBenefits 403(b) PDF"

    def test_rejects_a_non_ira_field(self, tmp_path) -> None:
        from datetime import datetime

        from engine.data_sources.record import record_ira_balance_candidate

        with pytest.raises(ValueError, match="your_ira"):
            record_ira_balance_candidate(
                "your_roth", 1.0, "x", datetime(2026, 1, 1), store_path=tmp_path / "s.json"
            )


class TestDispatch:
    def test_dispatch_collects_netbenefits_statements_and_no_ytd_records(self) -> None:
        result = PdfImportResult()
        _dispatch_pdf(DocKind.NETBENEFITS, "nb.pdf", PAGES, None, result)
        assert [s.plan_name for s in result.netbenefits_statements] == [
            "Example State University 403b"
        ]
        assert result.brokerage_records == []
        assert result.errors == []
