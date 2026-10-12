"""audit-1008 W10: statement classification (F-08, F-09, F-43).

All figures, names and account numbers are synthetic.

F-08  An IRA statement states only a total. Booking it with an all-zero
      asset-class split made weighted_return 0.0, so the owner's derived IRA
      or Roth growth dropped to 0%/yr.
F-09  The NetBenefits fund classifier called any fund with "Income" in its
      name a bond (Equity-Income, Growth & Income), and ignored the
      statement's own category labels ("Stock", "Bond", ...).
F-43  A Fidelity TAXABLE account booked its whole Income Summary Total as
      taxable dividends, including tax-exempt income.
"""

from __future__ import annotations

import pytest

from engine.brokerage_statement_pdf import BrokerageStatementRecord, parse_statement_text
from engine.ira_statement_balance import apply_ira_statement, to_ira_account_summary
from engine.netbenefits_statement_pdf import _classify_fund, parse_netbenefits_text
from engine.portfolio_sync.as_of import account_key
from engine.portfolio_sync.shapes import EXPECTED_RETURNS, AccountSummary, PortfolioSnapshot


def _ira_record(account_type: str = "traditional_ira", number: str = "XXXX0001"):
    return BrokerageStatementRecord(
        account_number=number,
        broker="fidelity",
        account_type=account_type,
        statement_period_end="2026-09-30",
        interest_taxable_ytd=0.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=0.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-10-01T00:00:00+00:00",
        ending_value=105_000.0,
    )


def _synced(account_type: str, name: str, total: float, **classes: float) -> AccountSummary:
    return AccountSummary(
        account_type=account_type,
        owner="you",
        account_name=name,
        total_value=total,
        as_of="2026-08-31",
        as_of_source="finextract",
        **classes,
    )


# --------------------------------------------------------------------- F-08


class TestF08IraStatementKeepsGrowth:
    def test_unlinked_statement_account_has_neutral_growth(self):
        acct = to_ira_account_summary(_ira_record(), "you")
        assert acct.weighted_return == pytest.approx(EXPECTED_RETURNS["target_date"])
        assert acct.target_date_value == pytest.approx(105_000.0)

    def test_linked_account_keeps_prior_class_mix_scaled_to_new_total(self):
        prior = _synced(
            "trad_ira", "ROLLOVER IRA", 100_000.0, equity_value=80_000.0, bond_value=20_000.0
        )
        snap = PortfolioSnapshot(accounts=[prior], server_available=True)
        out = apply_ira_statement(snap, _ira_record(), "you", replaces=account_key(prior))
        [acct] = [a for a in out.snapshot.accounts if a.account_type == "trad_ira"]
        assert acct.total_value == pytest.approx(105_000.0)
        assert acct.equity_value == pytest.approx(84_000.0)
        assert acct.bond_value == pytest.approx(21_000.0)
        assert acct.weighted_return == pytest.approx(prior.weighted_return)
        assert out.snapshot.pretax_weighted_return_for("you") == pytest.approx(
            prior.weighted_return
        )

    def test_linked_roth_keeps_growth(self):
        prior = _synced("roth_ira", "ROTH IRA", 50_000.0, equity_value=50_000.0)
        snap = PortfolioSnapshot(accounts=[prior], server_available=True)
        out = apply_ira_statement(
            snap, _ira_record("roth_ira", "XXXX0002"), "you", replaces=account_key(prior)
        )
        [acct] = [a for a in out.snapshot.accounts if a.account_type == "roth_ira"]
        assert acct.weighted_return == pytest.approx(EXPECTED_RETURNS["equity"])

    def test_linked_account_without_class_split_falls_back_to_neutral(self):
        prior = _synced("trad_ira", "ROLLOVER IRA", 100_000.0)  # no class values
        snap = PortfolioSnapshot(accounts=[prior], server_available=True)
        out = apply_ira_statement(snap, _ira_record(), "you", replaces=account_key(prior))
        [acct] = [a for a in out.snapshot.accounts if a.account_type == "trad_ira"]
        assert acct.weighted_return == pytest.approx(EXPECTED_RETURNS["target_date"])

    def test_separate_account_does_not_dilute_owner_growth_to_zero_return(self):
        prior = _synced("trad_ira", "ROLLOVER IRA", 100_000.0, equity_value=100_000.0)
        snap = PortfolioSnapshot(accounts=[prior], server_available=True)
        out = apply_ira_statement(snap, _ira_record(), "you")
        rate = out.snapshot.pretax_weighted_return_for("you")
        expected = (100_000 * 0.09 + 105_000 * EXPECTED_RETURNS["target_date"]) / 205_000
        assert rate == pytest.approx(expected)


# --------------------------------------------------------------------- F-09


class TestF09FundClassifier:
    @pytest.mark.parametrize(
        "name",
        ["FIDELITY EQUITY-INCOME", "FID GROWTH & INCOME", "VANGUARD EQUITY INCOME ADM"],
    )
    def test_equity_income_funds_are_equity(self, name):
        assert _classify_fund(name, in_target_date_section=False) == "equity"

    @pytest.mark.parametrize("name", ["FID US BOND INDEX", "PIMCO INCOME FUND FIXED INCOME"])
    def test_bond_funds_stay_bonds(self, name):
        assert _classify_fund(name, in_target_date_section=False) == "bond"

    @pytest.mark.parametrize("name", ["FIDELITY FREEDOM INCOME", "VANG TARGET RETIREMENT INCOME"])
    def test_retirement_income_funds_are_target_date(self, name):
        assert _classify_fund(name, in_target_date_section=False) == "target_date"

    def test_statement_category_beats_fund_name(self):
        assert _classify_fund("ACME SOMETHING", False, category="bond") == "bond"
        assert _classify_fund("ACME INCOME BUILDER", False, category="equity") == "equity"

    def test_target_date_section_still_wins(self):
        assert _classify_fund("ACME 2035", True, category="equity") == "target_date"


_NB_PAGE = """1/02/27, 8:15 AM Fidelity NetBenefits - Statement Details
Statement Details
Example Plan 403b
Retirement Savings Statement
Retirement Plan
ALEX SAMPLE Customer Service: (800) 555-
Your Account Summary
Statement Period: 07/01/2026 to 09/30/2026
Ending Balance $300,000.00
Vested Balance $300,000.00
Account Value
Statement Period: 07/01/2026 to 09/30/2026
CORE FUNDS
Stock $190,000.00 $200,000.00
FIDELITY EQUITY-INCOME 2,000.000 2,000.000 $95.00 $100.00 $190,000.00 $200,000.00
Bond $95,000.00 $100,000.00
ACME CORE PLUS INSTL 1,000.000 1,000.000 $95.00 $100.00 $95,000.00 $100,000.00
Account Totals $285,000.00 $300,000.00
"""


class TestF09StatementCategories:
    def test_category_labels_classify_holdings(self):
        stmt = parse_netbenefits_text([_NB_PAGE])
        classes = {h.description: h.asset_class for h in stmt.holdings}
        assert classes == {
            "FIDELITY EQUITY-INCOME": "equity",
            "ACME CORE PLUS INSTL": "bond",
        }
        assert stmt.warnings == []


# --------------------------------------------------------------------- F-43

_FID_TAXABLE_MIXED = """INVESTMENT REPORT
July 1, 2026 - July 31, 2026
Account # Z99-000002
SAM SAMPLE - INDIVIDUAL
Account Summary
Account Value: $250,000.00 Account Holdings
Income Summary
This Period Year-to-Date
Taxable $300.00 $2,000.00
Tax-exempt $400.00 $3,000.00
Total $700.00 $5,000.00
Contributions and Distributions
Brokerage services provided by Fidelity Brokerage Services LLC (FBS), Member NYSE, SIPC.
"""

_FID_TAXABLE_UNRECONCILED = """INVESTMENT REPORT
July 1, 2026 - July 31, 2026
Account # Z99-000003
SAM SAMPLE - INDIVIDUAL
Account Summary
Account Value: $250,000.00 Account Holdings
Income Summary
This Period Year-to-Date
Taxable $300.00 $2,000.00
Something New $400.00 $3,000.00
Total $700.00 $5,000.00
Contributions and Distributions
Brokerage services provided by Fidelity Brokerage Services LLC (FBS), Member NYSE, SIPC.
"""


class TestF43FidelityTaxableIncomeSplit:
    def test_tax_exempt_income_is_not_booked_as_taxable_dividends(self):
        [rec] = parse_statement_text([_FID_TAXABLE_MIXED])
        assert rec.account_type == "taxable"
        assert rec.dividends_taxable_ytd == pytest.approx(2_000.0)
        assert rec.dividends_tax_exempt_ytd == pytest.approx(3_000.0)

    def test_unrecognized_income_rows_mark_record_partial_not_taxable(self):
        [rec] = parse_statement_text([_FID_TAXABLE_UNRECONCILED])
        assert rec.dividends_taxable_ytd == pytest.approx(0.0)
        assert "dividends_taxable_ytd" in rec.missing_fields

    def test_ira_total_still_carried_for_display(self):
        text = _FID_TAXABLE_MIXED.replace("SAM SAMPLE - INDIVIDUAL", "SAM SAMPLE - ROLLOVER IRA")
        text = text.replace("Z99-000002", "999-000009")
        [rec] = parse_statement_text([text])
        assert rec.account_type == "traditional_ira"
        assert rec.dividends_taxable_ytd == pytest.approx(5_000.0)
