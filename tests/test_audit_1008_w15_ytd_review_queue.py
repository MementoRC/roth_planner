"""audit-1008 W15: YTD scan / review-queue defects.

- F-37: review-queue widgets (owner radios, IRA link selectbox) were keyed by
  queue position, so after an Apply/Dismiss the next statement inherited the
  previous one's choice.
- F-69: each PDF scan replaced session ``statement_by_account`` with only that
  scan's accounts, so a pending Schwab confirmation vanished.
- F-70: a corrupt UBS order store was overwritten with only the new upload.
- F-86: stale navigation text around the 1040 import.
- W9 follow-up: ``pick_latest_per_account`` keyed one scan's records by
  account number alone, so two brokers' same-number accounts collapsed.

Mocked-``st`` harness, same style as tests/test_sync_scan_ira_statements.py.
Synthetic names and round amounts only.
"""

from __future__ import annotations

import contextlib
import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from engine import brokerage_statement_pdf as bsp
from engine import ubs_activity_store
from engine.brokerage_statement_pdf import BrokerageStatementRecord, pick_latest_per_account
from engine.netbenefits_statement_pdf import NetBenefitsStatement
from engine.pdf_import import PdfImportResult
from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from engine.ssa_statement_pdf import SsaStatement
from engine.tax_return_pdf import Form1040Record
from engine.ubs_activity_csv import UbsActivityOrder, UbsExerciseLot
from engine.ubs_activity_store import UbsActivityStoreError
from models.ytd_income import YTDSnapshot
from views.ytd_income._partials import _sync_scan as sync_scan_mod


class _RerunError(Exception):
    pass


def _mock_st(*, state_extra=None, clicks=()):
    mock_st = MagicMock()
    state: dict = {"ytd_snapshot": YTDSnapshot(), **(state_extra or {})}
    session_state = MagicMock()
    session_state.get.side_effect = lambda key, default=None: state.get(key, default)
    session_state.__setitem__.side_effect = state.__setitem__
    session_state.__getitem__.side_effect = state.__getitem__
    session_state.__contains__.side_effect = state.__contains__
    session_state.pop.side_effect = lambda key, default=None: state.pop(key, default)
    mock_st.session_state = session_state
    mock_st.button.side_effect = lambda label, key=None, **kw: key in clicks
    mock_st.columns.side_effect = lambda n: [
        MagicMock() for _ in range(n if isinstance(n, int) else len(n))
    ]
    mock_st.radio.side_effect = lambda label, options, index=0, key=None, **kw: options[index]
    mock_st.selectbox.side_effect = lambda label, options, index=0, key=None, **kw: options[index]
    mock_st.rerun.side_effect = _RerunError
    return mock_st, state


def _rec(**kw: object) -> BrokerageStatementRecord:
    base: dict[str, object] = {
        "account_number": "1111-2222",
        "broker": "schwab",
        "account_type": "unknown",
        "statement_period_end": "2026-09-30",
        "interest_taxable_ytd": 1_000.0,
        "interest_tax_exempt_ytd": 0.0,
        "dividends_taxable_ytd": 2_000.0,
        "dividends_tax_exempt_ytd": 0.0,
        "stcg_net_ytd": 0.0,
        "ltcg_net_ytd": 3_000.0,
        "captured_at": "2026-10-01T00:00:00",
        "owner_key": None,
    }
    base.update(kw)
    return BrokerageStatementRecord(**base)  # type: ignore[arg-type]


def _ssa(date_: str, fra_monthly: float) -> SsaStatement:
    return SsaStatement(
        owner_key=None, statement_date=date_, fra_age=67, estimates={67: fra_monthly}
    )


def _keys(mock_widget: MagicMock) -> list[str]:
    return [c.kwargs["key"] for c in mock_widget.call_args_list]


# ---------------------------------------------------------------------------
# W9 follow-up: same account number at two brokers within one scan
# ---------------------------------------------------------------------------


class TestPickLatestPerAccountBrokerIdentity:
    def test_same_number_at_two_brokers_stays_two_accounts(self) -> None:
        schwab = _rec(broker="schwab")
        fidelity = _rec(broker="fidelity", interest_taxable_ytd=7.0)
        by_account = pick_latest_per_account([schwab, fidelity])
        assert sorted(by_account.values(), key=lambda r: r.broker) == [fidelity, schwab]

    def test_first_account_keeps_bare_number_second_is_broker_qualified(self) -> None:
        by_account = pick_latest_per_account([_rec(broker="schwab"), _rec(broker="fidelity")])
        assert set(by_account) == {"1111-2222", "fidelity:1111-2222"}

    def test_same_broker_same_number_keeps_newest(self) -> None:
        older = _rec(statement_period_end="2026-06-30")
        newer = _rec(statement_period_end="2026-09-30")
        assert pick_latest_per_account([newer, older]) == {"1111-2222": newer}
        assert pick_latest_per_account([older, newer]) == {"1111-2222": newer}

    def test_disk_cache_keeps_both_brokers(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(bsp, "_STATEMENT_CACHE_PATH", tmp_path / "stmts.json")
        bsp.save_statement_records({"1111-2222": _rec(broker="schwab")})
        bsp.save_statement_records({"1111-2222": _rec(broker="fidelity")})
        brokers = sorted(r.broker for r in bsp.load_statement_records().values())
        assert brokers == ["fidelity", "schwab"]

    def test_scan_resolves_owner_by_real_account_number(self) -> None:
        """A broker-qualified dict key must never reach the owner override
        lookup -- account overrides are keyed (broker, account_number)."""
        mock_st, _state = _mock_st()
        recs = [
            _rec(broker="schwab", account_type="taxable"),
            _rec(broker="fidelity", account_type="taxable"),
        ]
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "save_ytd_snapshot"),
            patch.object(sync_scan_mod, "save_ledger"),
            patch.object(sync_scan_mod, "_apply_ubs_correction", return_value=[]),
            patch.object(sync_scan_mod, "resolve_account_owner", return_value="you") as resolve,
            patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
            patch("engine.brokerage_statement_pdf.load_statement_records", return_value={}),
            patch("engine.brokerage_statement_pdf.save_statement_records"),
        ):
            sync_scan_mod._apply_scan_result(
                PdfImportResult(brokerage_records=recs),
                ledger={"koinly": {}, "brokerage": {}},
                account_overrides={},
                owner_map={},
                instance_owner="you",
            )
        looked_up = sorted((c.args[0], c.args[1]) for c in resolve.call_args_list)
        assert looked_up == [("fidelity", "1111-2222"), ("schwab", "1111-2222")]


# ---------------------------------------------------------------------------
# F-69: a later scan must not drop a pending account from the session
# ---------------------------------------------------------------------------


def _scan(mock_st, result: PdfImportResult, *, disk: dict | None = None) -> None:
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "save_ytd_snapshot"),
        patch("engine.brokerage_statement_pdf.load_account_type_overrides", return_value={}),
        patch("engine.brokerage_statement_pdf.load_statement_records", return_value=disk or {}),
        patch("engine.brokerage_statement_pdf.save_statement_records"),
    ):
        sync_scan_mod._apply_scan_result(
            result,
            ledger={"koinly": {}, "brokerage": {}},
            account_overrides={},
            owner_map={},
            instance_owner="you",
        )


class TestScanMergesStatementByAccount:
    def test_ssa_only_scan_keeps_pending_schwab_account(self) -> None:
        mock_st, state = _mock_st()
        _scan(mock_st, PdfImportResult(brokerage_records=[_rec()]))
        assert list(state["statement_by_account"]) == ["1111-2222"]

        _scan(mock_st, PdfImportResult(ssa_statements=[_ssa("2026-02-01", 1_500.0)]))

        assert list(state["statement_by_account"]) == ["1111-2222"]

    def test_second_account_is_added_not_replacing(self) -> None:
        mock_st, state = _mock_st()
        _scan(mock_st, PdfImportResult(brokerage_records=[_rec()]))
        _scan(mock_st, PdfImportResult(brokerage_records=[_rec(account_number="3333-4444")]))
        assert sorted(state["statement_by_account"]) == ["1111-2222", "3333-4444"]

    def test_first_scan_of_a_session_starts_from_the_disk_cache(self) -> None:
        mock_st, state = _mock_st()
        cached = _rec(account_number="5555-6666")
        _scan(
            mock_st,
            PdfImportResult(brokerage_records=[_rec()]),
            disk={"5555-6666": cached},
        )
        assert sorted(state["statement_by_account"]) == ["1111-2222", "5555-6666"]

    def test_newer_statement_replaces_older_for_same_account(self) -> None:
        mock_st, state = _mock_st()
        older = _rec(statement_period_end="2026-06-30")
        newer = _rec(statement_period_end="2026-09-30", interest_taxable_ytd=9.0)
        _scan(mock_st, PdfImportResult(brokerage_records=[older]))
        _scan(mock_st, PdfImportResult(brokerage_records=[newer]))
        assert state["statement_by_account"] == {"1111-2222": newer}

    def test_older_statement_does_not_replace_newer(self) -> None:
        mock_st, state = _mock_st()
        older = _rec(statement_period_end="2026-06-30")
        newer = _rec(statement_period_end="2026-09-30")
        _scan(mock_st, PdfImportResult(brokerage_records=[newer]))
        _scan(mock_st, PdfImportResult(brokerage_records=[older]))
        assert state["statement_by_account"] == {"1111-2222": newer}


# ---------------------------------------------------------------------------
# F-37: queue widgets keyed by statement identity, not list position
# ---------------------------------------------------------------------------


class TestSsaQueueWidgetKeys:
    def _render(self, mock_st) -> None:
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "ssa_comparison_table", return_value=None),
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_ssa_review(owner_map={}, instance_owner="you", identity_set=True)

    def test_next_statement_does_not_inherit_the_applied_ones_radio(self) -> None:
        a, b = _ssa("2026-01-01", 2_000.0), _ssa("2026-02-01", 1_500.0)
        mock_st, _ = _mock_st(state_extra={"ssa_statements": [a, b]})
        self._render(mock_st)
        key_a, key_b = _keys(mock_st.radio)
        assert key_a != key_b

        mock_st2, _ = _mock_st(state_extra={"ssa_statements": [b]})
        self._render(mock_st2)

        assert _keys(mock_st2.radio) == [key_b]


class TestNetBenefitsQueueWidgetKeys:
    def test_owner_radio_key_follows_the_statement(self) -> None:
        a = NetBenefitsStatement(
            plan_name="Acme 403(b)",
            owner_key=None,
            period_start="2026-07-01",
            period_end="2026-09-30",
            ending_balance=1_000.0,
            vested_balance=1_000.0,
        )
        b = NetBenefitsStatement(
            plan_name="Globex 403(b)",
            owner_key=None,
            period_start="2026-07-01",
            period_end="2026-09-30",
            ending_balance=2_000.0,
            vested_balance=2_000.0,
        )

        def render(queue):
            mock_st, _ = _mock_st(state_extra={"netbenefits_statements": queue})
            with (
                patch.object(sync_scan_mod, "st", mock_st),
                patch.object(sync_scan_mod, "load_snapshot", return_value=PortfolioSnapshot()),
                contextlib.suppress(_RerunError),
            ):
                sync_scan_mod._render_netbenefits_review(
                    owner_map={}, instance_owner="you", identity_set=True
                )
            return _keys(mock_st.radio)

        key_a, key_b = render([a, b])
        assert key_a != key_b
        assert render([b]) == [key_b]


class TestIraQueueWidgetKeys:
    def _render(self, queue, snap):
        mock_st, _ = _mock_st(state_extra={"ira_statements": queue})
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "load_snapshot", return_value=snap),
            patch.object(sync_scan_mod, "load_link_map", return_value={}),
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_ira_statement_review(
                owner_map={}, instance_owner="you", identity_set=True
            )
        return _keys(mock_st.radio), _keys(mock_st.selectbox)

    def test_owner_and_link_keys_follow_the_statement(self) -> None:
        a = _rec(broker="fidelity", account_type="traditional_ira", account_number="X11112222")
        a.ending_value = 111_111.0
        b = _rec(broker="fidelity", account_type="traditional_ira", account_number="Y33334444")
        b.ending_value = 55_555.0
        alpha = AccountSummary(
            account_type="trad_ira", owner="you", account_name="Synced IRA Alpha"
        )
        beta = AccountSummary(account_type="trad_ira", owner="you", account_name="Synced IRA Beta")

        radios_ab, links_ab = self._render([a, b], PortfolioSnapshot(accounts=[alpha, beta]))
        assert len(set(radios_ab)) == 2
        assert len(set(links_ab)) == 2

        # After A was applied (linked to Alpha), B's candidates shrink to Beta.
        radios_b, links_b = self._render([b], PortfolioSnapshot(accounts=[beta]))

        assert radios_b == [radios_ab[1]]
        # The candidate list changed, so B's link widget must be a fresh one:
        # an index stored against the old list would point at the wrong account.
        assert links_b[0] not in links_ab


# ---------------------------------------------------------------------------
# F-70: a corrupt UBS order store is never overwritten
# ---------------------------------------------------------------------------


def _order(ref: str) -> UbsActivityOrder:
    lot = UbsExerciseLot(
        grant_number="S0000000001",
        grant_date=date(2020, 2, 1),
        grant_price=90.0,
        grant_quantity=40,
        execution_quantity=40,
        open_quantity=0,
        cancel_quantity=0,
        total_fees=8.0,
        total_taxes=200.0,
        gross_proceeds=7_200.0,
        net_proceeds=3_392.0,
        funds_available_date=date(2026, 3, 19),
    )
    return UbsActivityOrder(
        reference_number=ref,
        source="Options",
        entry_date=date(2026, 3, 15),
        execution_date=date(2026, 3, 15),
        quantity=40,
        order_type="MKT",
        transaction_type="SDS",
        limit_price=None,
        execution_price=180.0,
        entered_through="WEB",
        proceeds_method="",
        funds_available_date=date(2026, 3, 19),
        total_shares_delivered=0,
        lots=(lot,),
    )


class TestCorruptUbsStoreIsNotOverwritten:
    def test_store_refuses_to_overwrite_an_unreadable_file(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "ubs.json"
        monkeypatch.setattr(ubs_activity_store, "_UBS_ACTIVITY_CACHE_PATH", path)
        ubs_activity_store.save_ubs_orders([_order("R1"), _order("R2"), _order("R3")])
        truncated = path.read_bytes()[:200]
        path.write_bytes(truncated)

        with pytest.raises(UbsActivityStoreError):
            ubs_activity_store.save_ubs_orders([_order("R4")])

        assert path.read_bytes() == truncated

    def test_wrong_shape_file_is_reported_as_corrupt(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "ubs.json"
        monkeypatch.setattr(ubs_activity_store, "_UBS_ACTIVITY_CACHE_PATH", path)
        path.write_text(json.dumps([{"reference_number": "R1"}]))

        with pytest.raises(UbsActivityStoreError):
            ubs_activity_store.load_ubs_orders()

    def test_readable_store_is_still_replaced_on_save(self, tmp_path, monkeypatch) -> None:
        path = tmp_path / "ubs.json"
        monkeypatch.setattr(ubs_activity_store, "_UBS_ACTIVITY_CACHE_PATH", path)
        ubs_activity_store.save_ubs_orders([_order("R1")])
        ubs_activity_store.save_ubs_orders([_order("R1"), _order("R2")])
        refs = [o.reference_number for o in ubs_activity_store.load_ubs_orders()]
        assert refs == ["R1", "R2"]

    def test_uploader_does_not_save_when_the_store_is_corrupt(self) -> None:
        mock_st, _ = _mock_st(clicks={"import_ubs_csv_btn"})
        upload = MagicMock()
        upload.name = "order.csv"
        mock_st.file_uploader.return_value = [upload]
        with (
            patch.object(sync_scan_mod, "st", mock_st),
            patch.object(sync_scan_mod, "parse_ubs_activity_text", return_value=_order("R4")),
            patch.object(
                sync_scan_mod,
                "load_ubs_orders",
                side_effect=UbsActivityStoreError("cache is corrupt"),
            ),
            patch.object(sync_scan_mod, "save_ubs_orders") as save,
            patch.object(sync_scan_mod, "save_ytd_snapshot") as save_ytd,
            contextlib.suppress(_RerunError),
        ):
            sync_scan_mod._render_ubs_csv_uploader(
                ledger={"koinly": {}, "brokerage": {}}, identity_set=True
            )

        save.assert_not_called()
        save_ytd.assert_not_called()
        errors = " ".join(str(c.args[0]) for c in mock_st.error.call_args_list)
        assert "not imported" in errors
        mock_st.success.assert_not_called()


# ---------------------------------------------------------------------------
# F-86: navigation text around the 1040 import
# ---------------------------------------------------------------------------


def _f1040() -> Form1040Record:
    return Form1040Record(
        tax_year=2025,
        agi=1.0,
        magi=1.0,
        tax_exempt_interest=0.0,
        taxable_ss=0.0,
        qualified_dividends=0.0,
        ordinary_dividends=0.0,
        feie=0.0,
        filing_status=None,
        captured_at="2026-10-01T00:00:00",
    )


class TestForm1040Messages:
    def test_scan_message_points_to_the_1040_import_section(self) -> None:
        mock_st, _ = _mock_st()
        rec = _f1040()
        _scan(mock_st, PdfImportResult(form_1040_records={2025: rec}))
        infos = " ".join(str(c.args[0]) for c in mock_st.info.call_args_list)
        assert "**Household** tab" not in infos
        assert "1040 Import" in infos

    def test_save_message_names_review_changes(self) -> None:
        from views.setup import parameters as params_mod

        rec = _f1040()
        mock_st, _ = _mock_st(
            state_extra={"_pdf_1040_scanned": {2025: rec}}, clicks={"_pdf_1040_save_2025"}
        )
        mock_st.expander.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_st.expander.return_value.__exit__ = MagicMock(return_value=False)
        mock_st.columns.side_effect = lambda n: [MagicMock() for _ in range(n)]
        with (
            patch.object(params_mod, "st", mock_st),
            patch.object(params_mod, "load_pdf_tax_records", return_value={}),
            patch.object(params_mod, "save_pdf_tax_records"),
            patch.object(params_mod, "is_pyodide", return_value=False),
            contextlib.suppress(_RerunError),
        ):
            params_mod._render_pdf_1040_import()
        infos = " ".join(str(c.args[0]) for c in mock_st.info.call_args_list)
        assert "Command Center" not in infos
        assert "Review Changes" in infos
