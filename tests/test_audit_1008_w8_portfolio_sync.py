"""Audit 2026-10-08, wave W8: portfolio sync defects.

F-11  merge_sync dropped bundle-imported accounts the operator's FinExtract
      sync does not report (a spouse's accounts at another custodian).
F-12  account classification matched "roth"/"ira" anywhere in the name, so an
      owner name ("Dorothy", "Mira") decided the account type.
F-13  FinExtract option-exercise history counted a re-scraped order once per
      scrape batch, and counted prior-year exercises into this year's YTD.
F-56  the portfolio / SSA cache loaders tolerated unknown keys at account level
      only; an unknown key on a holding, grant or SSA estimate crashed boot.
F-57  one unknown key inside a YTD gain/income event lost (or crashed) the
      whole YTD section.

All figures are synthetic.
"""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

import engine.portfolio_sync.portfolio as portfolio_mod
import engine.portfolio_sync.social_security as ssa_mod
import engine.portfolio_sync.ytd as ytd_mod
from engine.bridge_bundle import build_bundle_v5
from engine.bridge_import import LocalState, apply_import, plan_import
from engine.portfolio_sync.as_of import merge_sync
from engine.portfolio_sync.classify import _classify_account
from engine.portfolio_sync.exercises import (
    _parse_equity_sales_lots,
    _parse_option_exercises_rows,
    fetch_option_exercises,
)
from engine.portfolio_sync.shapes import (
    AccountSummary,
    EquityGrant,
    Holding,
    PortfolioSnapshot,
    SSABenefitEstimate,
    SSASnapshot,
)
from engine.portfolio_sync.ytd import ytd_from_dict, ytd_to_dict
from engine.store.core import Store
from engine.store.namespaces import PORTFOLIO, SSA, YTD
from models.ytd_income import IncomeEvent, RealizedGainEvent, YTDSnapshot

NOW = "2026-10-01T12:00:00+00:00"


# ---------------------------------------------------------------------------
# F-12: owner names must not decide the account type
# ---------------------------------------------------------------------------


class TestClassifyIgnoresOwnerName:
    @pytest.mark.parametrize(
        "name",
        [
            "Dorothy A. Sample — Brokerage Account — 11112222*",
            "Kira Rothman — Individual Account — 55556666*",
            "Alex Roth — Brokerage Account — 77778888*",
        ],
    )
    def test_roth_inside_owner_name_is_brokerage(self, name: str) -> None:
        assert _classify_account(name)[0] == "brokerage"

    @pytest.mark.parametrize(
        "name",
        [
            "Mira B. Sample — Brokerage Account — 33334444*",
            "Samira Sample — Individual Account — 12121212*",
        ],
    )
    def test_ira_inside_owner_name_is_brokerage(self, name: str) -> None:
        assert _classify_account(name)[0] == "brokerage"

    def test_owner_named_ira_still_classifies_real_roth(self) -> None:
        name = "Mira B. Sample — Roth IRA Brokerage Account — 33334444*"
        assert _classify_account(name)[0] == "roth_ira"

    def test_owner_named_roth_still_classifies_real_ira(self) -> None:
        name = "Dorothy A. Sample — Traditional IRA — 11112222*"
        assert _classify_account(name)[0] == "trad_ira"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Rollover IRA999000002", "trad_ira"),
            ("ROTH IRA 12345", "roth_ira"),
            ("Health Savings Account999000001", "hsa"),
            ("EXAMPLE 403B59208", "403b"),
            ("Individual Brokerage Account", "brokerage"),
        ],
    )
    def test_dashless_names_keep_working(self, name: str, expected: str) -> None:
        assert _classify_account(name)[0] == expected

    def test_substring_inside_a_word_does_not_match(self) -> None:
        # "Hiram" contains "ira"; "Dorothy" contains "roth".
        assert _classify_account("Hiram Dorothy Joint Account")[0] == "brokerage"


# ---------------------------------------------------------------------------
# F-13: duplicate scrape batches and prior-year exercises
# ---------------------------------------------------------------------------

_ROW = {
    "grant_number": "G-TEST-1",
    "grant_price": 50,
    "execution_quantity": 100,
    "gross_proceeds": 15000,
    "funds_available_date": "2026-03-05",
}


def _history_response(*batches: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "batches": [
            {"batch_id": f"b{i}", "captured_at": f"2026-03-0{6 + i}T00:00:00", "rows": rows}
            for i, rows in enumerate(batches)
        ]
    }
    return resp


class TestOptionExerciseDedup:
    def test_same_order_in_two_batches_counts_once(self) -> None:
        resp = _history_response([dict(_ROW)], [dict(_ROW)])
        with patch("engine.portfolio_sync.exercises._get", return_value=resp):
            snap = fetch_option_exercises()
        assert snap.total_spread == pytest.approx(10_000.0)
        assert snap.rows_count == 1
        assert snap.by_grant_id == {"G-TEST-1": pytest.approx(10_000.0)}
        assert snap.sale_info_by_grant["G-TEST-1"]["shares_ytd"] == 100

    def test_two_identical_lots_in_one_batch_both_count(self) -> None:
        # Two genuinely separate sales with identical fields in ONE scrape.
        resp = _history_response([dict(_ROW), dict(_ROW)], [dict(_ROW), dict(_ROW)])
        with patch("engine.portfolio_sync.exercises._get", return_value=resp):
            snap = fetch_option_exercises()
        assert snap.total_spread == pytest.approx(20_000.0)
        assert snap.rows_count == 2

    def test_distinct_orders_across_batches_all_count(self) -> None:
        other = dict(_ROW, gross_proceeds=16000, funds_available_date="2026-04-02")
        resp = _history_response([dict(_ROW)], [dict(_ROW), other])
        with patch("engine.portfolio_sync.exercises._get", return_value=resp):
            snap = fetch_option_exercises()
        assert snap.total_spread == pytest.approx(10_000.0 + 11_000.0)

    def test_prior_year_row_excluded_when_tax_year_given(self) -> None:
        old = dict(_ROW, funds_available_date="2025-06-01")
        snap = _parse_option_exercises_rows([dict(_ROW), old], tax_year=2026)
        assert snap.total_spread == pytest.approx(10_000.0)
        assert any("2025" in w for w in snap.warnings)

    def test_prior_year_lot_excluded_when_tax_year_given(self) -> None:
        old = dict(_ROW, funds_available_date="2025-06-01", execution_quantity="100")
        snap = _parse_equity_sales_lots([dict(_ROW), old], tax_year=2026)
        assert snap.total_spread == pytest.approx(10_000.0)

    def test_undated_row_is_kept(self) -> None:
        undated = {k: v for k, v in _ROW.items() if k != "funds_available_date"}
        snap = _parse_option_exercises_rows([undated], tax_year=2026)
        assert snap.total_spread == pytest.approx(10_000.0)

    def test_fetch_passes_tax_year_through(self) -> None:
        old = dict(_ROW, funds_available_date="2025-06-01")
        resp = _history_response([dict(_ROW), old])
        with patch("engine.portfolio_sync.exercises._get", return_value=resp):
            snap = fetch_option_exercises(tax_year=2026)
        assert snap.total_spread == pytest.approx(10_000.0)


# ---------------------------------------------------------------------------
# F-11: a FinExtract sync must not drop bundle-imported accounts
# ---------------------------------------------------------------------------


def _spouse_bundle() -> dict:
    sender = PortfolioSnapshot(
        accounts=[
            AccountSummary(
                "trad_ira",
                "spouse",
                "SPOUSE IRA X1",
                total_value=50_000.0,
                as_of="2026-10-01",
                as_of_source="finextract",
            )
        ]
    )
    return build_bundle_v5(
        setup_scalars={},
        snapshot=sender,
        ledger={},
        ytd=None,
        grants=[],
        exporter="spouse",
        slot_captured_at={"spouse": NOW},
        now=NOW,
    )


class TestSyncKeepsBundleAccounts:
    def test_spouse_bundle_account_survives_operator_sync(self) -> None:
        mine = AccountSummary(
            "trad_ira",
            "you",
            "MY IRA Y1",
            total_value=100_000.0,
            as_of="2026-09-30",
            as_of_source="finextract",
        )
        local = LocalState(snapshot=PortfolioSnapshot(accounts=[mine]))
        bundle = _spouse_bundle()
        plan = plan_import(bundle, local, operator="you")
        result = apply_import(bundle, plan, local, selected=frozenset({"spouse"}), operator="you")
        after_import = result.snapshot.accounts
        assert {a.account_name for a in after_import} == {"MY IRA Y1", "SPOUSE IRA X1"}

        incoming = AccountSummary(
            "trad_ira",
            "you",
            "MY IRA Y1",
            total_value=101_000.0,
            as_of="2026-10-05",
            as_of_source="finextract",
        )
        merged = merge_sync(after_import, [incoming])
        names = {a.account_name: a.total_value for a in merged.accounts}
        assert names == {"MY IRA Y1": 101_000.0, "SPOUSE IRA X1": 50_000.0}

    def test_bundle_account_keeps_its_date(self) -> None:
        local = LocalState(snapshot=PortfolioSnapshot())
        bundle = _spouse_bundle()
        plan = plan_import(bundle, local, operator="you")
        result = apply_import(bundle, plan, local, selected=frozenset({"spouse"}), operator="you")
        (acct,) = result.snapshot.accounts
        assert acct.as_of == "2026-10-01"
        assert acct.as_of_source == "bundle"

    def test_reimport_replaces_bundle_accounts(self) -> None:
        # A later bundle is still the authority for the spouse's accounts.
        local = LocalState(snapshot=PortfolioSnapshot())
        bundle = _spouse_bundle()
        plan = plan_import(bundle, local, operator="you")
        first = apply_import(bundle, plan, local, selected=frozenset({"spouse"}), operator="you")
        local2 = LocalState(snapshot=first.snapshot)
        plan2 = plan_import(bundle, local2, operator="you")
        second = apply_import(bundle, plan2, local2, selected=frozenset({"spouse"}), operator="you")
        assert [a.account_name for a in second.snapshot.accounts] == ["SPOUSE IRA X1"]

    def test_finextract_account_still_closes(self) -> None:
        gone = AccountSummary("brokerage", "you", "CLOSED", 1.0, as_of_source="finextract")
        assert merge_sync([gone], []).accounts == []

    def test_matching_sync_replaces_bundle_account(self) -> None:
        imported = AccountSummary(
            "trad_ira", "spouse", "S1", 1.0, as_of="2026-09-01", as_of_source="bundle"
        )
        synced = AccountSummary(
            "trad_ira", "spouse", "S1", 2.0, as_of="2026-10-01", as_of_source="finextract"
        )
        assert merge_sync([imported], [synced]).accounts == [synced]


# ---------------------------------------------------------------------------
# F-56: nested unknown keys in the portfolio / SSA caches
# ---------------------------------------------------------------------------


def _snapshot() -> PortfolioSnapshot:
    holding = Holding("VTI", "Total Stock", 10.0, 1000.0, "ACCT", "equity")
    return PortfolioSnapshot(
        accounts=[AccountSummary("brokerage", "you", "ACCT", 1000.0, holdings=[holding])],
        equity_grants=[EquityGrant("G1", "NQO", "2020-01-01", 10, 10, 100.0)],
    )


class TestPortfolioCacheForwardCompat:
    @pytest.fixture
    def cache(self, store: Store) -> Store:
        store.put(PORTFOLIO, asdict(_snapshot()))
        return store

    def test_unknown_holding_key_is_dropped(self, cache: Store) -> None:
        data = cache.get(PORTFOLIO)
        data["accounts"][0]["holdings"][0]["future_holding_field"] = 1
        cache.put(PORTFOLIO, data)
        snap = portfolio_mod.load_snapshot()
        assert snap is not None
        assert snap.accounts[0].holdings[0].symbol == "VTI"

    def test_unknown_grant_key_is_dropped(self, cache: Store) -> None:
        data = cache.get(PORTFOLIO)
        data["equity_grants"][0]["future_grant_field"] = 1
        cache.put(PORTFOLIO, data)
        snap = portfolio_mod.load_snapshot()
        assert snap is not None
        assert snap.equity_grants[0].grant_id == "G1"

    def test_grant_missing_required_key_degrades_to_none(self, cache: Store) -> None:
        data = cache.get(PORTFOLIO)
        del data["equity_grants"][0]["grant_type"]
        cache.put(PORTFOLIO, data)
        assert portfolio_mod.load_snapshot() is None


class TestSsaCacheForwardCompat:
    @pytest.fixture
    def cache(self, store: Store) -> Store:
        snap = SSASnapshot(
            estimates=[SSABenefitEstimate(67, "2032-01", "full", 2000.0)], server_available=True
        )
        ssa_mod.save_ssa_snapshot(snap, owner="you")
        return store

    def test_unknown_estimate_key_is_dropped(self, cache: Store) -> None:
        data = cache.get(SSA)
        data["you"]["estimates"][0]["future_est_field"] = 1
        cache.put(SSA, data)
        snap = ssa_mod.load_ssa_snapshot(owner="you")
        assert snap is not None
        assert snap.estimates[0].monthly_amount == 2000.0

    def test_malformed_estimate_degrades_to_none(self, cache: Store) -> None:
        data = cache.get(SSA)
        del data["you"]["estimates"][0]["claim_date"]
        cache.put(SSA, data)
        assert ssa_mod.load_ssa_snapshot(owner="you") is None


# ---------------------------------------------------------------------------
# F-57: nested unknown keys in YTD events
# ---------------------------------------------------------------------------


def _ytd() -> YTDSnapshot:
    return YTDSnapshot(
        wages_ytd=11_111.0,
        gain_events=[RealizedGainEvent("2026-02-01", "VTI", 1_500.0, 1_000.0, "long")],
        income_events=[IncomeEvent("2026-03-01", 3_000.0, "conversion", "you")],
    )


class TestYtdEventForwardCompat:
    def test_unknown_income_event_key_is_dropped(self) -> None:
        data = ytd_to_dict(_ytd())
        data["income_events"][0]["account_id"] = "X"
        ytd = ytd_from_dict(data)
        assert ytd.wages_ytd == 11_111.0
        assert ytd.income_events[0].amount == 3_000.0

    def test_unknown_gain_event_key_is_dropped(self) -> None:
        data = ytd_to_dict(_ytd())
        data["gain_events"][0]["wash_sale"] = False
        ytd = ytd_from_dict(data)
        assert len(ytd.gain_events) == 1

    def test_load_survives_unknown_event_key(self, store) -> None:
        data = ytd_to_dict(_ytd())
        data["income_events"][0]["account_id"] = "X"
        store.put(YTD, data)
        ytd = ytd_mod.load_ytd_snapshot()
        assert ytd is not None
        assert ytd.wages_ytd == 11_111.0

    def test_load_malformed_event_degrades_to_none(self, store) -> None:
        data = ytd_to_dict(_ytd())
        del data["income_events"][0]["amount"]
        store.put(YTD, data)
        assert ytd_mod.load_ytd_snapshot() is None
