"""Per-account "as of" dates: newer as-of wins between a FinExtract sync and a
NetBenefits 403(b) PDF import. Synthetic data only."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

import pytest

from engine.netbenefits_statement_pdf import (
    NetBenefitsStatement,
    apply_netbenefits_statement,
    to_account_summary,
)
from engine.portfolio_sync import (
    AccountSummary,
    PortfolioSnapshot,
    load_snapshot,
    save_snapshot,
)
from engine.portfolio_sync.as_of import (
    MergeResult,
    account_key,
    as_of_label,
    as_of_text,
    extract_payload_as_of,
    force_replace,
    is_newer_or_equal,
    merge_sync,
)
from engine.portfolio_sync.shapes import account_summary_from_dict, portfolio_snapshot_from_dict


def _acct(
    account_type: str = "brokerage",
    owner: str = "you",
    name: str = "Acct A",
    *,
    as_of: str = "",
    source: str = "",
    total: float = 100.0,
) -> AccountSummary:
    return AccountSummary(
        account_type=account_type,
        owner=owner,
        account_name=name,
        total_value=total,
        as_of=as_of,
        as_of_source=source,
    )


class TestIsNewerOrEqual:
    def test_empty_existing_loses_to_anything(self) -> None:
        assert is_newer_or_equal(_acct(as_of="2020-01-01"), _acct(as_of=""))

    def test_empty_both_goes_to_incoming(self) -> None:
        assert is_newer_or_equal(_acct(as_of=""), _acct(as_of=""))

    def test_empty_incoming_never_overwrites_dated(self) -> None:
        assert not is_newer_or_equal(_acct(as_of=""), _acct(as_of="2020-01-01"))

    def test_tie_goes_to_incoming(self) -> None:
        assert is_newer_or_equal(_acct(as_of="2026-09-30"), _acct(as_of="2026-09-30"))

    def test_older_incoming_loses(self) -> None:
        assert not is_newer_or_equal(_acct(as_of="2026-09-29"), _acct(as_of="2026-09-30"))

    def test_newer_incoming_wins(self) -> None:
        assert is_newer_or_equal(_acct(as_of="2026-10-01"), _acct(as_of="2026-09-30"))


class TestAccountKey:
    def test_403b_keys_include_the_plan_name(self) -> None:
        # Issue #38: one owner's two plans must not share a key.
        a = _acct("403b", "spouse", "Acme Corp 403(b) Plan")
        b = _acct("403b", "spouse", "FIDELITY NETBENEFITS X123")
        assert account_key(a) != account_key(b)

    def test_403b_keys_differ_by_owner(self) -> None:
        assert account_key(_acct("403b", "you")) != account_key(_acct("403b", "spouse"))

    def test_other_types_keep_account_name(self) -> None:
        assert account_key(_acct("brokerage", "you", "A")) != account_key(
            _acct("brokerage", "you", "B")
        )


class TestLabels:
    @pytest.mark.parametrize(
        ("source", "label"),
        [("statement", "statement"), ("finextract", "FinExtract"), ("sync_time", "sync time")],
    )
    def test_source_labels(self, source: str, label: str) -> None:
        assert as_of_text(_acct(as_of="2026-09-30", source=source)) == f"2026-09-30 · {label}"

    def test_unknown(self) -> None:
        assert as_of_text(_acct()) == "unknown"
        assert as_of_label(_acct(as_of="2026-09-30", source="")) == "as of 2026-09-30 · unknown"


class TestMergeSync:
    def test_newer_incoming_updates(self) -> None:
        ex = _acct(as_of="2026-09-01", source="finextract", total=1.0)
        inc = _acct(as_of="2026-10-06", source="sync_time", total=2.0)
        res = merge_sync([ex], [inc])
        assert isinstance(res, MergeResult)
        assert res.accounts == [inc]
        assert res.kept_newer == []

    def test_older_incoming_is_kept_out_and_recorded(self) -> None:
        ex = _acct("403b", "spouse", "Plan", as_of="2026-10-09", source="statement")
        inc = _acct("403b", "spouse", "RAW LABEL", as_of="2026-10-06", source="sync_time")
        res = merge_sync([ex], [inc])
        assert res.accounts == [ex]
        assert res.kept_newer == [(ex, inc)]

    def test_unmatched_incoming_is_added(self) -> None:
        ex = _acct(name="A", as_of="2026-09-01", source="finextract")
        new = _acct(name="B", as_of="2026-10-06", source="sync_time")
        res = merge_sync([ex], [new])
        assert new in res.accounts
        assert ex not in res.accounts  # A was FinExtract-sourced and is gone: closed

    def test_pdf_only_account_is_kept(self) -> None:
        pdf = _acct("403b", "you", "Plan", as_of="2026-09-30", source="statement")
        res = merge_sync([pdf], [_acct(name="B", as_of="2026-10-06", source="sync_time")])
        assert pdf in res.accounts

    def test_finextract_only_account_is_dropped(self) -> None:
        old = _acct(name="Closed", as_of="2026-09-01", source="finextract")
        res = merge_sync([old], [])
        assert res.accounts == []

    def test_legacy_undated_account_is_dropped_when_unmatched_and_replaced_when_matched(
        self,
    ) -> None:
        legacy_gone = _acct(name="Gone")
        legacy_kept = _acct(name="Same", total=1.0)
        inc = _acct(name="Same", as_of="2026-10-06", source="sync_time", total=9.0)
        res = merge_sync([legacy_gone, legacy_kept], [inc])
        assert res.accounts == [inc]

    def test_undated_incoming_does_not_overwrite_dated_existing(self) -> None:
        ex = _acct(as_of="2026-09-01", source="finextract")
        inc = _acct()
        res = merge_sync([ex], [inc])
        assert res.accounts == [ex]
        assert res.kept_newer == [(ex, inc)]


class TestForceReplace:
    def test_replaces_only_the_matching_account(self) -> None:
        a = _acct("403b", "spouse", "Plan", as_of="2026-10-09", source="statement")
        other = _acct("brokerage", "you", "B")
        inc = _acct("403b", "spouse", "RAW", as_of="2026-10-06", source="sync_time", total=7.0)
        out = force_replace([a, other], inc)
        assert other in out
        assert inc in out
        assert a not in out


class TestPayloadDateExtraction:
    def test_institution_as_of(self) -> None:
        data = {"institutions": {"a": {"as_of": "2026-09-30T12:00:00Z", "rows": [{}, {}]}}}
        assert extract_payload_as_of(data) == ("", ["2026-09-30", "2026-09-30"])

    def test_institution_freshness_as_of(self) -> None:
        data = {"institutions": {"a": {"freshness": {"as_of": "2026-09-29"}, "rows": [{}]}}}
        assert extract_payload_as_of(data) == ("", ["2026-09-29"])

    def test_institution_as_of_beats_its_freshness(self) -> None:
        data = {
            "institutions": {
                "a": {"as_of": "2026-09-30", "freshness": {"as_of": "2026-01-01"}, "rows": [{}]}
            }
        }
        assert extract_payload_as_of(data)[1] == ["2026-09-30"]

    def test_top_level_as_of(self) -> None:
        assert extract_payload_as_of({"as_of": "2026-09-28", "rows": [{}]}) == ("2026-09-28", [])

    def test_top_level_freshness_as_of(self) -> None:
        data = {"freshness": {"as_of": "2026-09-27"}, "rows": [{}]}
        assert extract_payload_as_of(data) == ("2026-09-27", [])

    def test_per_institution_dates_are_per_row(self) -> None:
        data = {
            "as_of": "2026-09-01",
            "institutions": {
                "a": {"as_of": "2026-09-30", "rows": [{}]},
                "b": {"rows": [{}, {}]},
            },
        }
        assert extract_payload_as_of(data) == ("2026-09-01", ["2026-09-30", "", ""])

    def test_garbled_date_counts_as_absent(self) -> None:
        assert extract_payload_as_of({"as_of": "not a date", "rows": []}) == ("", [])


class _Resp:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class TestFetchHoldingsSnapshotDates:
    def _fetch(self, monkeypatch: pytest.MonkeyPatch, payload: dict):
        from engine.portfolio_sync import client as client_module
        from engine.portfolio_sync.holdings import fetch_holdings_snapshot

        monkeypatch.setattr(client_module.requests, "get", lambda *a, **kw: _Resp(payload))
        return fetch_holdings_snapshot(today=date(2026, 10, 6))

    def test_payload_date_is_finextract_sourced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        snap = self._fetch(monkeypatch, {"as_of": "2026-09-28", "rows": [{"symbol": "X"}]})
        assert (snap.as_of, snap.as_of_source) == ("2026-09-28", "finextract")

    def test_no_date_falls_back_to_injected_sync_day(self, monkeypatch: pytest.MonkeyPatch) -> None:
        snap = self._fetch(monkeypatch, {"rows": [{"symbol": "X"}]})
        assert (snap.as_of, snap.as_of_source) == ("2026-10-06", "sync_time")

    def test_rows_are_unchanged_and_row_dates_parallel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "institutions": {
                "a": {"as_of": "2026-09-30", "rows": [{"symbol": "X"}]},
                "b": {"rows": [{"symbol": "Y"}]},
            }
        }
        snap = self._fetch(monkeypatch, payload)
        assert snap.rows == [{"symbol": "X"}, {"symbol": "Y"}]
        assert snap.row_as_of == ["2026-09-30", ""]
        assert (snap.as_of, snap.as_of_source) == ("2026-10-06", "sync_time")


class TestFetchPortfolioStamps:
    def test_each_account_gets_its_institution_date_or_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import portfolio as portfolio_module
        from engine.portfolio_sync.shapes import HoldingsSnapshot

        class _Status:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

        rows = [
            {"account": "Brokerage One", "symbol": "AAA", "market_value": 10.0},
            {"account": "Brokerage Two", "symbol": "BBB", "market_value": 20.0},
        ]
        monkeypatch.setattr(portfolio_module, "_get", lambda *a, **kw: _Status())
        monkeypatch.setattr(
            portfolio_module,
            "fetch_holdings_snapshot",
            lambda: HoldingsSnapshot(
                server_available=True,
                rows=rows,
                as_of="2026-10-06",
                as_of_source="sync_time",
                row_as_of=["2026-09-30", ""],
            ),
        )
        monkeypatch.setattr(portfolio_module, "fetch_equity_awards", lambda: [])
        monkeypatch.setattr(portfolio_module, "fetch_shares", lambda: [])

        snap = portfolio_module.fetch_portfolio()
        by_name = {a.account_name: a for a in snap.accounts}
        assert (by_name["Brokerage One"].as_of, by_name["Brokerage One"].as_of_source) == (
            "2026-09-30",
            "finextract",
        )
        assert (by_name["Brokerage Two"].as_of, by_name["Brokerage Two"].as_of_source) == (
            "2026-10-06",
            "sync_time",
        )


class TestLoadSnapshotTolerance:
    def _write(self, accounts: list[dict]) -> None:
        from engine.store import get_store
        from engine.store.namespaces import PORTFOLIO

        get_store().put(PORTFOLIO, {"accounts": accounts})

    def test_legacy_cache_without_fields_loads_undated(self) -> None:
        self._write([{"account_type": "brokerage", "owner": "you", "total_value": 5.0}])
        snap = load_snapshot()
        assert snap is not None
        assert (snap.accounts[0].as_of, snap.accounts[0].as_of_source) == ("", "")

    def test_unknown_account_key_is_dropped_not_fatal(self) -> None:
        self._write(
            [
                {
                    "account_type": "brokerage",
                    "owner": "you",
                    "total_value": 5.0,
                    "as_of": "2026-09-30",
                    "from_the_future": 1,
                }
            ]
        )
        snap = load_snapshot()
        assert snap is not None
        assert snap.accounts[0].as_of == "2026-09-30"

    def test_save_load_round_trip_keeps_dates(self) -> None:
        save_snapshot(
            PortfolioSnapshot(
                accounts=[_acct(as_of="2026-09-30", source="statement")], server_available=True
            )
        )
        snap = load_snapshot()
        assert snap is not None
        assert (snap.accounts[0].as_of, snap.accounts[0].as_of_source) == (
            "2026-09-30",
            "statement",
        )


class TestBridgeRoundTrip:
    def test_round_trip_with_as_of(self) -> None:
        acct = _acct(as_of="2026-09-30", source="statement")
        assert account_summary_from_dict(asdict(acct)) == acct

    def test_old_bundle_without_as_of_loads(self) -> None:
        legacy = asdict(_acct())
        del legacy["as_of"], legacy["as_of_source"]
        restored = account_summary_from_dict(legacy)
        assert (restored.as_of, restored.as_of_source) == ("", "")

    def test_unknown_key_is_rejected_by_the_strict_parser(self) -> None:
        # The v5 bundle parser relies on this to reject malformed accounts.
        data = {**asdict(_acct(as_of="2026-09-30")), "future_field": "x"}
        with pytest.raises(TypeError):
            account_summary_from_dict(data)

    def test_unknown_key_is_dropped_by_the_tolerant_parser(self) -> None:
        data = {**asdict(_acct(as_of="2026-09-30")), "future_field": "x"}
        assert account_summary_from_dict(data, tolerant=True).as_of == "2026-09-30"

    def test_snapshot_from_dict_keeps_as_of(self) -> None:
        snap = portfolio_snapshot_from_dict({"accounts": [asdict(_acct(as_of="2026-09-30"))]})
        assert snap.accounts[0].as_of == "2026-09-30"

    def test_bundle_serialization_is_json_safe(self) -> None:
        from engine.bridge_bundle import _account_to_dict

        payload = _account_to_dict(_acct(as_of="2026-09-30", source="statement"))
        assert json.loads(json.dumps(payload))["as_of_source"] == "statement"


def _stmt(period_end: str = "2026-09-30") -> NetBenefitsStatement:
    return NetBenefitsStatement(
        plan_name="Acme Corp 403(b) Plan",
        owner_key=None,
        period_start="2026-07-01",
        period_end=period_end,
        ending_balance=1_000.0,
        vested_balance=1_000.0,
    )


class TestNetBenefitsApply:
    def test_account_is_stamped_with_period_end(self) -> None:
        acct = to_account_summary(_stmt(), "spouse")
        assert (acct.as_of, acct.as_of_source) == ("2026-09-30", "statement")

    def test_cache_newer_keeps_cache(self) -> None:
        cached = _acct("403b", "spouse", "RAW", as_of="2026-10-06", source="sync_time", total=5.0)
        snap = PortfolioSnapshot(accounts=[cached])
        out = apply_netbenefits_statement(snap, _stmt(), "spouse")
        assert out.kept_newer is cached
        assert out.snapshot is snap
        assert out.replaced == []
        assert out.new_account is not None
        assert out.new_account.total_value == 1_000.0

    def test_cache_older_is_replaced(self) -> None:
        cached = _acct("403b", "spouse", "RAW", as_of="2026-06-30", source="sync_time", total=5.0)
        out = apply_netbenefits_statement(PortfolioSnapshot(accounts=[cached]), _stmt(), "spouse")
        assert out.kept_newer is None
        assert out.replaced == [cached]
        assert [a.total_value for a in out.snapshot.accounts] == [1_000.0]

    def test_legacy_undated_cache_is_replaced(self) -> None:
        cached = _acct("403b", "spouse", "RAW", total=5.0)
        out = apply_netbenefits_statement(PortfolioSnapshot(accounts=[cached]), _stmt(), "spouse")
        assert out.kept_newer is None
        assert out.replaced == [cached]

    def test_force_replaces_a_newer_cache(self) -> None:
        cached = _acct("403b", "spouse", "RAW", as_of="2026-10-06", source="sync_time", total=5.0)
        out = apply_netbenefits_statement(
            PortfolioSnapshot(accounts=[cached]), _stmt(), "spouse", force=True
        )
        assert out.kept_newer is None
        assert out.replaced == [cached]
        assert out.snapshot.accounts[0].as_of_source == "statement"

    def test_other_owners_403b_does_not_block(self) -> None:
        cached = _acct("403b", "you", "RAW", as_of="2026-10-06", source="sync_time")
        out = apply_netbenefits_statement(PortfolioSnapshot(accounts=[cached]), _stmt(), "spouse")
        assert out.kept_newer is None
        assert len(out.snapshot.accounts) == 2


class TestUseAnywayButton:
    def test_use_anyway_force_replaces_and_clears_the_notice(self) -> None:
        from streamlit.testing.v1 import AppTest

        save_snapshot(
            PortfolioSnapshot(
                accounts=[
                    _acct(
                        "403b", "spouse", "Plan", as_of="2026-09-30", source="statement", total=1.0
                    )
                ],
                server_available=True,
            )
        )

        def _script() -> None:
            import streamlit as st

            from engine.portfolio_sync import AccountSummary
            from models.household import Household
            from views.setup._partials._portfolio import render_portfolio_legacy

            if "seeded" not in st.session_state:
                st.session_state["seeded"] = True
                kept = AccountSummary(
                    "403b", "spouse", "Plan", total_value=1.0, as_of="2026-09-30",
                    as_of_source="statement",
                )  # fmt: skip
                rejected = AccountSummary(
                    "403b", "spouse", "RAW", total_value=2.0, as_of="2026-09-29",
                    as_of_source="sync_time",
                )  # fmt: skip
                st.session_state["portfolio_kept_newer"] = [(kept, rejected)]
            render_portfolio_legacy(Household(), st)

        at = AppTest.from_function(_script, default_timeout=60).run()
        assert not at.exception
        assert any("kept" in w.value for w in at.warning)

        at.button(key="portfolio_use_anyway_0").click().run()
        assert not at.exception
        assert not any("kept" in w.value for w in at.warning)
        snap = load_snapshot()
        assert snap is not None
        assert [(a.account_name, a.total_value) for a in snap.accounts] == [("RAW", 2.0)]
