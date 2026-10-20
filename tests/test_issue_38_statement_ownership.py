"""Issue #38: statement ownership/identity gaps left after audit-1018 W5-W7.

Covers portfolio_sync 403(b) keying, renamed NetBenefits plans, nameless
Koinly / NetBenefits statements, the brokerage owner fallback in sync_scan,
the SSA months-FRA estimate row, and the legacy account-type override.
Synthetic data only.
"""

from __future__ import annotations

import contextlib
import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from engine.brokerage_statement_pdf import BrokerageStatementRecord, apply_account_type_overrides
from engine.koinly_report_pdf import KoinlyReport
from engine.netbenefits_statement_pdf import (
    apply_netbenefits_statement,
    parse_netbenefits_text,
    pick_latest_netbenefits,
    renamed_plan_candidates,
    to_account_summary,
)
from engine.pdf_import import PdfImportResult
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from engine.portfolio_sync.as_of import force_replace, merge_sync
from engine.ssa_statement_pdf import (
    load_ssa_pdf_estimates,
    parse_ssa_statement_text,
    save_ssa_pdf_estimates,
)
from tests.test_netbenefits_statement_pdf import PAGE_1, PAGES
from tests.test_sync_scan_netbenefits import _mock_st, _RerunError
from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _403b(name: str, *, owner: str = "you", as_of: str, source: str, total: float):
    return AccountSummary(
        account_type="403b",
        owner=owner,
        account_name=name,
        total_value=total,
        as_of=as_of,
        as_of_source=source,
    )


# ------------------------------------------------- 1. 403(b) keying in merge_sync


class TestPortfolioSync403bKeyedByPlan:
    def test_sync_updates_the_same_plan_not_the_owners_first_403b(self) -> None:
        acme = _403b("Acme 403(b) Plan", as_of="2026-09-30", source="statement", total=1.0)
        beta = _403b("Beta 403(b) Plan", as_of="2026-09-30", source="statement", total=2.0)
        inc = _403b("BETA 403B", as_of="2026-10-06", source="sync_time", total=9.0)

        res = merge_sync([acme, beta], [inc])

        assert acme in res.accounts
        assert beta not in res.accounts
        assert inc in res.accounts
        assert len(res.accounts) == 2

    def test_older_sync_of_one_plan_is_checked_against_that_plan(self) -> None:
        acme = _403b("Acme 403(b) Plan", as_of="2026-09-01", source="statement", total=1.0)
        beta = _403b("Beta 403(b) Plan", as_of="2026-10-09", source="statement", total=2.0)
        inc = _403b("BETA 403B", as_of="2026-10-06", source="sync_time", total=9.0)

        res = merge_sync([acme, beta], [inc])

        assert res.kept_newer == [(beta, inc)]
        assert acme in res.accounts

    def test_unmatched_label_with_several_plans_is_never_paired_by_guess(self) -> None:
        acme = _403b("Acme 403(b) Plan", as_of="2026-09-30", source="statement", total=1.0)
        beta = _403b("Beta 403(b) Plan", as_of="2026-09-30", source="statement", total=2.0)
        inc = _403b("CUSTODIAN X123", as_of="2026-10-06", source="sync_time", total=9.0)

        res = merge_sync([acme, beta], [inc])

        assert {a.account_name for a in res.accounts} == {
            "Acme 403(b) Plan",
            "Beta 403(b) Plan",
            "CUSTODIAN X123",
        }

    def test_sole_plan_and_sole_label_still_pair(self) -> None:
        plan = _403b("Acme 403(b) Plan", as_of="2026-09-30", source="statement", total=1.0)
        inc = _403b("CUSTODIAN X123", as_of="2026-10-06", source="sync_time", total=9.0)
        assert merge_sync([plan], [inc]).accounts == [inc]

    def test_force_replace_targets_the_same_plan(self) -> None:
        acme = _403b("Acme 403(b) Plan", as_of="2026-10-09", source="statement", total=1.0)
        beta = _403b("Beta 403(b) Plan", as_of="2026-10-09", source="statement", total=2.0)
        inc = _403b("BETA 403B", as_of="2026-10-06", source="sync_time", total=9.0)
        assert force_replace([acme, beta], inc) == [acme, inc]


# ------------------------------------------------- 2. renamed NetBenefits plan


def _renamed_snap() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[
            _403b("Old Name 403(b) Plan", as_of="2026-03-31", source="statement", total=30_000.0)
        ]
    )


class TestNetbenefitsRenamedPlan:
    def test_renamed_plan_is_offered_as_a_candidate(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        cands = renamed_plan_candidates(_renamed_snap(), to_account_summary(stmt, "you"))
        assert [a.account_name for a in cands] == ["Old Name 403(b) Plan"]

    def test_without_a_choice_it_is_added_beside(self) -> None:
        out = apply_netbenefits_statement(_renamed_snap(), parse_netbenefits_text(PAGES), "you")
        assert out.replaced == []
        assert len(out.snapshot.accounts) == 2

    def test_chosen_renamed_plan_is_replaced_not_duplicated(self) -> None:
        out = apply_netbenefits_statement(
            _renamed_snap(),
            parse_netbenefits_text(PAGES),
            "you",
            replaces="Old Name 403(b) Plan",
        )
        assert [a.account_name for a in out.replaced] == ["Old Name 403(b) Plan"]
        assert [a.total_value for a in out.snapshot.accounts] == [pytest.approx(34_900.0)]
        assert out.ira_total == pytest.approx(34_900.0)

    def test_a_matching_plan_offers_no_rename_choice(self) -> None:
        snap = PortfolioSnapshot(
            accounts=[
                _403b(
                    "Example State University 403(b) Plan",
                    as_of="2026-03-31",
                    source="statement",
                    total=1.0,
                )
            ]
        )
        stmt = parse_netbenefits_text(PAGES)
        assert renamed_plan_candidates(snap, to_account_summary(stmt, "you")) == []

    def test_sync_scan_review_applies_the_chosen_rename(self) -> None:
        stmt = parse_netbenefits_text(PAGES)
        mock_st, _state = _mock_st(
            state_extra={"netbenefits_statements": [stmt]},
            clicks={"netbenefits_apply_0"},
            radio_choice="you",
        )
        mock_st.selectbox.side_effect = lambda label, options, **kw: options[1]
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "load_snapshot", return_value=_renamed_snap()),
            patch.object(sync_scan_mod, "save_snapshot") as save_snap,
            patch.object(sync_scan_mod, "record_ira_balance_candidate"),
            patch.object(sync_scan_mod, "save_owner_map"),
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_netbenefits_review(
                owner_map={}, instance_owner="you", identity_set=True
            )
        [saved] = save_snap.call_args.args
        assert [a.account_name for a in saved.accounts] == ["Example State University 403b"]


# ------------------------------------------------- 3. nameless Koinly reports


def _koinly(stcg: float, owner_key: str | None = None) -> KoinlyReport:
    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=stcg,
        crypto_ltcg=10.0,
        crypto_income=5.0,
        captured_at="2026-07-13T00:00:00+00:00",
        provenance={"pdf_pages_total": 4},
        owner_key=owner_key,
    )


def _scan_patches(mock_st):
    return (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "save_ledger"),
        patch.object(sync_scan_mod, "save_ytd_snapshot"),
        patch.object(sync_scan_mod, "auto_deselect_manual_entry", return_value=True),
        patch("engine.koinly_report_pdf.save_koinly_report"),
        patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
        patch("engine.brokerage_statement_pdf.load_statement_records", return_value={}),
        patch("engine.brokerage_statement_pdf.save_statement_records"),
    )


class TestKoinlyNamelessReports:
    def test_two_nameless_reports_get_distinct_keys(self) -> None:
        assert _koinly(100.0).override_key != _koinly(200.0).override_key

    def test_rescanned_report_keeps_its_key(self) -> None:
        a, b = _koinly(100.0), _koinly(100.0)
        b.captured_at = "2026-08-01T00:00:00+00:00"
        assert a.override_key == b.override_key

    def test_named_report_is_keyed_by_holder(self) -> None:
        assert _koinly(1.0, "PAT SAMPLE").override_key == "PAT SAMPLE"

    def test_override_splits_two_nameless_reports(self) -> None:
        a, b = _koinly(100.0), _koinly(200.0)
        mock_st, _state = _mock_st()
        with contextlib.ExitStack() as stack:
            for p in _scan_patches(mock_st):
                stack.enter_context(p)
            ledger = sync_scan_mod._apply_scan_result(
                PdfImportResult(koinly_reports=[a, b]),
                ledger={"koinly": {}, "brokerage": {}},
                account_overrides={("koinly", b.override_key): "spouse"},
                owner_map={},
                instance_owner="you",
            )
        assert ledger["koinly"]["you"]["stcg"] == 100.0
        assert ledger["koinly"]["spouse"]["stcg"] == 200.0


# ------------------------------------------------- 4. brokerage owner fallback


def _taxable(owner_key: str | None) -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number="9999-0001",
        broker="schwab",
        account_type="taxable",
        statement_period_end="2026-06-30",
        interest_taxable_ytd=0.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=100.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at="2026-07-10T00:00:00+00:00",
        owner_key=owner_key,
    )


class TestSyncScanBrokerageOwnerFallback:
    def _scan(self, rec, account_overrides):
        mock_st, _state = _mock_st()
        writer = MagicMock(side_effect=lambda ledger, owner, rec: ledger)
        with contextlib.ExitStack() as stack:
            for p in _scan_patches(mock_st):
                stack.enter_context(p)
            stack.enter_context(patch.object(sync_scan_mod, "write_brokerage_contribution", writer))
            stack.enter_context(
                patch.object(sync_scan_mod, "_apply_ubs_correction", return_value=[])
            )
            sync_scan_mod._apply_scan_result(
                PdfImportResult(brokerage_records=[rec]),
                ledger={"koinly": {}, "brokerage": {}},
                account_overrides=account_overrides,
                owner_map={"pat sample": "spouse"},
                instance_owner="you",
            )
        return writer, _state

    def test_named_holder_wins_over_instance_owner(self) -> None:
        writer, state = self._scan(_taxable("PAT SAMPLE"), {})
        assert writer.call_args.args[1] == "spouse"
        assert not state.get("holder_name_mismatches")

    def test_override_still_beats_the_holder_name(self) -> None:
        writer, _ = self._scan(_taxable("PAT SAMPLE"), {("schwab", "9999-0001"): "household"})
        assert writer.call_args.args[1] == "household"

    def test_nameless_statement_falls_back_to_instance_owner(self) -> None:
        writer, _ = self._scan(_taxable(None), {})
        assert writer.call_args.args[1] == "you"


# ------------------------------------------------- 5. nameless NetBenefits participants


class TestNetbenefitsNamelessParticipants:
    @staticmethod
    def _nameless(balance: str = "$34,900.00"):
        page_1 = PAGE_1.replace("JANE Q SAMPLE Customer Service:", "Customer Service:")
        return parse_netbenefits_text([page_1.replace("$34,900.00", balance), *PAGES[1:]])

    def test_two_nameless_participants_both_stay_queued(self) -> None:
        a = self._nameless()
        b = self._nameless("$12,000.00")
        assert a.owner_key is None
        assert b.owner_key is None
        assert pick_latest_netbenefits([a, b]) == [a, b]

    def test_identical_nameless_statement_is_deduplicated(self) -> None:
        a = self._nameless()
        assert pick_latest_netbenefits([a, self._nameless()]) == [a]


# ------------------------------------------------- 6. SSA months-FRA estimate row

_SSA_PAGE = """Your Social Security Statement
March 1, 2026
Your full retirement age is 66 and 10 months.
62 $1,400
66 $1,889
66 and 10 months $2,005
67 $2,027
70 $2,507
Form SSA-7005
"""


class TestSsaMonthsEstimateRow:
    def test_months_row_is_the_fra_amount(self) -> None:
        stmt = parse_ssa_statement_text([_SSA_PAGE])
        assert stmt.fra_estimate == pytest.approx(2005.0)
        assert stmt.fra_monthly == pytest.approx(2005.0)
        assert sorted(stmt.estimates) == [62, 66, 67, 70]

    def test_months_row_alone_is_enough(self) -> None:
        stmt = parse_ssa_statement_text([_SSA_PAGE.replace("66 $1,889\n", "")])
        assert stmt.fra_monthly == pytest.approx(2005.0)

    def test_fra_estimate_survives_persistence(self, tmp_path) -> None:
        stmt = parse_ssa_statement_text([_SSA_PAGE])
        path = tmp_path / "ssa.json"
        save_ssa_pdf_estimates("you", stmt, path=path)
        loaded = load_ssa_pdf_estimates("you", path=path)
        assert loaded is not None
        assert loaded.fra_monthly == pytest.approx(2005.0)


# ------------------------------------------------- 7. legacy bare-number override


def _unknown(broker: str) -> BrokerageStatementRecord:
    return dataclasses.replace(_taxable(None), broker=broker, account_type="unknown")


class TestLegacyOverrideNeedsAResolvableBroker:
    def test_ambiguous_number_in_the_batch_is_not_typed(self) -> None:
        by_account = {"9999-0001": _unknown("schwab"), "ubs:9999-0001": _unknown("ubs")}
        result = apply_account_type_overrides(by_account, {"9999-0001": "taxable"})
        assert [r.account_type for r in result.values()] == ["unknown", "unknown"]

    def test_sole_holder_still_gets_the_legacy_type(self) -> None:
        result = apply_account_type_overrides(
            {"9999-0001": _unknown("schwab")}, {"9999-0001": "taxable"}
        )
        assert result["9999-0001"].account_type == "taxable"

    def test_broker_keyed_override_is_unaffected(self) -> None:
        by_account = {"9999-0001": _unknown("schwab"), "ubs:9999-0001": _unknown("ubs")}
        result = apply_account_type_overrides(by_account, {"ubs:9999-0001": "taxable"})
        assert result["9999-0001"].account_type == "unknown"
        assert result["ubs:9999-0001"].account_type == "taxable"
