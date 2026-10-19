"""audit-1018 W6: applying a statement touches only the account it describes.

F-09: a NetBenefits statement must not delete the owner's other 403(b) plans.
F-19: re-applying an IRA statement under a corrected owner must remove the
same-name copy left under the other owner. Synthetic data only.
"""

from __future__ import annotations

import pytest

from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.ira_statement_balance import apply_ira_statement
from engine.netbenefits_statement_pdf import (
    NetBenefitsStatement,
    apply_netbenefits_statement,
    pick_latest_netbenefits,
)
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot


def _stmt(plan: str, balance: float, period_end: str = "2026-09-30") -> NetBenefitsStatement:
    return NetBenefitsStatement(
        plan_name=plan,
        owner_key=None,
        period_start="2026-07-01",
        period_end=period_end,
        ending_balance=balance,
        vested_balance=balance,
    )


def _acct(
    kind: str, owner: str, name: str, total: float, source: str = "finextract"
) -> AccountSummary:
    return AccountSummary(
        account_type=kind,
        owner=owner,
        account_name=name,
        total_value=total,
        as_of="2026-09-30",
        as_of_source=source,
    )


def _names(snap: PortfolioSnapshot, kind: str, owner: str) -> dict[str, float]:
    return {
        a.account_name: a.total_value
        for a in snap.accounts
        if a.account_type == kind and a.owner == owner
    }


class TestF09TwoPlans:
    def test_statement_replaces_only_its_own_plan(self) -> None:
        snap = PortfolioSnapshot(
            accounts=[
                _acct("trad_ira", "you", "IRA", 100_000.0),
                _acct("403b", "you", "PLAN A 403B", 50_000.0),
                _acct("403b", "you", "PLAN B 403B", 30_000.0),
            ]
        )
        out = apply_netbenefits_statement(snap, _stmt("Plan A 403(b) Plan", 51_000.0), "you")
        assert [a.account_name for a in out.replaced] == ["PLAN A 403B"]
        assert sorted(_names(out.snapshot, "403b", "you").values()) == [30_000.0, 51_000.0]
        assert out.ira_total == pytest.approx(181_000.0)

    def test_two_statements_for_two_plans_both_survive(self) -> None:
        snap = PortfolioSnapshot(accounts=[_acct("trad_ira", "you", "IRA", 100_000.0)])
        stmts = pick_latest_netbenefits(
            [_stmt("Plan A 403(b) Plan", 51_000.0), _stmt("Plan B 403(b) Plan", 31_000.0)]
        )
        out = None
        for stmt in stmts:
            out = apply_netbenefits_statement(snap, stmt, "you")
            snap = out.snapshot
        assert out is not None
        assert out.ira_total == pytest.approx(182_000.0)
        assert len(_names(snap, "403b", "you")) == 2

    def test_reapplying_a_plan_replaces_its_prior_statement_copy(self) -> None:
        snap = PortfolioSnapshot(
            accounts=[_acct("403b", "you", "Plan A 403(b) Plan", 50_000.0, "statement")]
        )
        out = apply_netbenefits_statement(
            snap, _stmt("Plan A 403(b) Plan", 51_000.0, "2026-10-31"), "you"
        )
        assert _names(out.snapshot, "403b", "you") == {"Plan A 403(b) Plan": 51_000.0}

    def test_newer_guard_looks_only_at_the_matching_plan(self) -> None:
        newer_b = _acct("403b", "you", "PLAN B 403B", 30_000.0)
        newer_b.as_of = "2026-12-31"
        snap = PortfolioSnapshot(accounts=[_acct("403b", "you", "PLAN A 403B", 50_000.0), newer_b])
        out = apply_netbenefits_statement(snap, _stmt("Plan A 403(b) Plan", 51_000.0), "you")
        assert out.kept_newer is None
        assert 51_000.0 in _names(out.snapshot, "403b", "you").values()


def _rec() -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number="X00011111",
        broker="fidelity",
        account_type="traditional_ira",
        statement_period_end="2026-09-30",
        interest_taxable_ytd=0.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=0.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-10-01T00:00:00",
        owner_key="PAT SAMPLE",
        ending_value=100_000.0,
    )


class TestF19OwnerCorrection:
    def test_reapplying_under_the_other_owner_removes_the_wrong_copy(self) -> None:
        first = apply_ira_statement(PortfolioSnapshot(), _rec(), "spouse")
        out = apply_ira_statement(first.snapshot, _rec(), "you")
        assert [a.owner for a in out.snapshot.accounts] == ["you"]
        assert [a.owner for a in out.replaced] == ["spouse"]
        assert out.total == pytest.approx(100_000.0)

    def test_outcome_reports_the_other_owners_corrected_total(self) -> None:
        first = apply_ira_statement(PortfolioSnapshot(), _rec(), "spouse")
        out = apply_ira_statement(first.snapshot, _rec(), "you")
        assert out.other_owner_total == ("spouse_ira", 0.0)

    def test_no_other_owner_copy_means_no_correction(self) -> None:
        out = apply_ira_statement(PortfolioSnapshot(), _rec(), "you")
        assert out.other_owner_total is None

    def test_a_different_account_of_the_other_owner_is_kept(self) -> None:
        other = _acct("trad_ira", "spouse", "Some Other IRA", 7_000.0)
        out = apply_ira_statement(PortfolioSnapshot(accounts=[other]), _rec(), "you")
        assert other in out.snapshot.accounts
        assert out.other_owner_total is None
