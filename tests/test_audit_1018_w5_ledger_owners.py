"""audit-1018 W5: F-07 (Koinly holder name ignored by owner resolver) and
F-08 (bundle import keeps the same account / Koinly report under two owners).

All households, names and figures are synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace

from engine.account_attribution import resolve_account_owner
from engine.bridge_import import _apply_ledger
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.koinly_report_pdf import KoinlyReport
from engine.pdf_ledger import (
    derive_brokerage_totals,
    derive_koinly_totals,
    write_brokerage_contribution,
    write_koinly_contribution,
)

OWNER_MAP = {"alice example": "you", "bob example": "spouse"}


def _report(stcg: float, ltcg: float, income: float, owner_key: str | None) -> KoinlyReport:
    return KoinlyReport(
        tax_year=2026,
        crypto_stcg=stcg,
        crypto_ltcg=ltcg,
        crypto_income=income,
        captured_at="2026-07-13T00:00:00+00:00",
        provenance={"pages": stcg},
        owner_key=owner_key,
    )


def _record(number: str = "X111") -> BrokerageStatementRecord:
    return BrokerageStatementRecord(
        account_number=number,
        broker="fidelity",
        account_type="taxable",
        statement_period_end="2026-06-30",
        interest_taxable_ytd=1000.0,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=2000.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=3000.0,
        captured_at="2026-07-13T00:00:00+00:00",
    )


class TestF07KoinlyOwnerFromHolderName:
    def test_resolver_uses_owner_map_before_instance_owner(self):
        owner = resolve_account_owner(
            "koinly", "BOB EXAMPLE", {}, "you", holder_key="BOB EXAMPLE", owner_map=OWNER_MAP
        )
        assert owner == "spouse"

    def test_override_still_beats_owner_map(self):
        owner = resolve_account_owner(
            "koinly",
            "BOB EXAMPLE",
            {("koinly", "BOB EXAMPLE"): "household"},
            "you",
            holder_key="BOB EXAMPLE",
            owner_map=OWNER_MAP,
        )
        assert owner == "household"

    def test_unknown_holder_falls_back_to_instance_owner(self):
        owner = resolve_account_owner(
            "koinly", "CAROL", {}, "you", holder_key="CAROL", owner_map=OWNER_MAP
        )
        assert owner == "you"

    def test_two_reports_two_slots_summed(self):
        ledger: dict = {"koinly": {}, "brokerage": {}}
        for report in (
            _report(1000, 2000, 300, "ALICE EXAMPLE"),
            _report(500, 700, 50, "BOB EXAMPLE"),
        ):
            owner = resolve_account_owner(
                "koinly",
                report.owner_key or "unknown",
                {},
                "you",
                holder_key=report.owner_key,
                owner_map=OWNER_MAP,
            )
            ledger = write_koinly_contribution(ledger, owner, report)
        assert set(ledger["koinly"]) == {"you", "spouse"}
        totals = derive_koinly_totals(ledger, 2026)
        assert (totals["stcg"], totals["ltcg"], totals["income"]) == (1500, 2700, 350)


class TestF08BundleImportCrossOwnerPrune:
    @staticmethod
    def _bundle(ledger: dict) -> SimpleNamespace:
        return SimpleNamespace(records=SimpleNamespace(ledger=ledger))

    def _receiver_and_bundle(self):
        local = write_brokerage_contribution({"koinly": {}, "brokerage": {}}, "you", _record())
        local = write_koinly_contribution(local, "household", _report(500, 700, 100, None))
        incoming = write_brokerage_contribution(
            {"koinly": {}, "brokerage": {}}, "spouse", _record()
        )
        incoming = write_koinly_contribution(incoming, "spouse", _report(500, 700, 100, None))
        return local, self._bundle(incoming)

    def test_same_account_not_double_counted(self):
        local, bundle = self._receiver_and_bundle()
        out = _apply_ledger(local, bundle, frozenset({"spouse"}), True)
        assert list(out["brokerage"].get("you", {})) == []
        assert list(out["brokerage"]["spouse"]) == ["X111"]
        totals = derive_brokerage_totals(out, 2026)
        assert totals["interest_ytd"] == 1000
        assert totals["ordinary_dividends_ytd"] == 2000
        assert totals["ltcg_ytd"] == 3000

    def test_same_koinly_report_not_double_counted(self):
        local, bundle = self._receiver_and_bundle()
        out = _apply_ledger(local, bundle, frozenset({"spouse"}), True)
        assert list(out["koinly"]) == ["spouse"]
        totals = derive_koinly_totals(out, 2026)
        assert (totals["stcg"], totals["ltcg"], totals["income"]) == (500, 700, 100)

    def test_distinct_local_account_survives(self):
        local, bundle = self._receiver_and_bundle()
        local = write_brokerage_contribution(local, "you", _record("Y222"))
        out = _apply_ledger(local, bundle, frozenset({"spouse"}), True)
        assert list(out["brokerage"]["you"]) == ["Y222"]
