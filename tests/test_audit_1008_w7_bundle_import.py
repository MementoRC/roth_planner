"""audit-1008 wave W7: bundle-import defects (F-01, F-04, F-05, F-06).

Synthetic figures only. F-33 (slot checkboxes surviving a new upload) lives in
tests/test_data_bridge_preview.py next to the other preview-widget tests.
"""

from __future__ import annotations

from datetime import datetime

from config.scalar_registry import SCALAR_REGISTRY
from engine.bridge_bundle import build_bundle_v5
from engine.bridge_import import LocalState, apply_import, plan_import
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.orchestrator import resolve_for_app
from engine.data_sources.resolver import GRANTS_KEY, confirm
from engine.data_sources.snapshot_ingest import (
    derive_snapshot_growth,
    snapshot_carries_portfolio_data,
)
from engine.portfolio_sync.shapes import AccountSummary, EquityGrant, Holding, PortfolioSnapshot
from engine.upload_merge import (
    JOINT_FIELDS,
    build_user_defaults_session_updates,
    receiver_joint_value_is_set,
)
from models.grants import StockGrant
from models.household import Household
from models.sourced import Source

NOW = "2026-10-01T09:00:00"


def _demo_persisted() -> dict:
    """What one visit to Backup & Transfer autosaves: every joint field at its demo seed."""
    out: dict = {
        (spec.config_key or spec.session_key): spec.default
        for spec in SCALAR_REGISTRY
        if spec.in_scalar_keys and spec.scope == "joint"
    }
    out["survivor"] = None
    out["inherited_iras"] = []
    return out


def _local(**overrides) -> LocalState:
    kwargs: dict = {
        "scalars": {"your_age": 55, "filing_status": "MFJ", "living_expenses": 60_000},
        "persisted_scalars": _demo_persisted(),
        "snapshot": None,
        "ledger": {},
        "ytd": None,
        "account_type_overrides": {},
        "slot_captured_at": {},
    }
    kwargs.update(overrides)
    return LocalState(**kwargs)


def _own_backup(**scalar_overrides) -> dict:
    scalars = {
        "your_age": 60,
        "your_ira": 111_111,
        "filing_status": "Single",
        "living_expenses": 91_000,
        "cpi_assumption": 0.019,
        "survivor": {"who_dies": "spouse", "death_year": 2030},
        "inherited_iras": [{"balance": 50_000.0, "owner": "you"}],
    }
    scalars.update(scalar_overrides)
    return build_bundle_v5(
        setup_scalars=scalars,
        snapshot=None,
        ledger={"koinly": {}, "brokerage": {}},
        ytd=None,
        grants=None,
        exporter="you",
        slot_captured_at={},
        now=NOW,
    )


class TestF04DemoSeedsAreNotDeliberateChoices:
    def test_demo_default_counts_as_unset(self) -> None:
        persisted = _demo_persisted()
        for key in JOINT_FIELDS | {"survivor", "inherited_iras"}:
            assert receiver_joint_value_is_set(key, persisted) is False, key

    def test_non_default_value_counts_as_set(self) -> None:
        assert receiver_joint_value_is_set("filing_status", {"filing_status": "Single"})
        assert receiver_joint_value_is_set("living_expenses", {"living_expenses": 75_000})
        assert receiver_joint_value_is_set("survivor", {"survivor": {"who_dies": "you"}})
        assert receiver_joint_value_is_set("inherited_iras", {"inherited_iras": [{"x": 1}]})

    def test_absent_counts_as_unset(self) -> None:
        assert receiver_joint_value_is_set("filing_status", {}) is False

    def test_v5_restore_after_backup_page_visit_takes_bundle_joint_values(self) -> None:
        local = _local()
        bundle = _own_backup()
        plan = plan_import(bundle, local)
        assert plan.joint_conflicts == ()
        result = apply_import(
            bundle,
            plan,
            local,
            selected=frozenset({"you"}),
            operator="you",
            apply_v4_bundle=None,  # type: ignore[arg-type]
        )
        updates = result.scalar_updates
        assert updates["filing_status"] == "Single"
        assert updates["living_expenses"] == 91_000
        assert updates["cpi_assumption"] == 0.019
        assert updates["survivor"] == {"who_dies": "spouse", "death_year": 2030}
        assert updates["_survivor_enabled"] is True
        assert updates["inherited_iras"] == [{"balance": 50_000.0, "owner": "you"}]

    def test_deliberate_receiver_value_still_wins_and_is_reported(self) -> None:
        persisted = {**_demo_persisted(), "living_expenses": 75_000}
        plan = plan_import(_own_backup(), _local(persisted_scalars=persisted))
        assert {"field": "living_expenses", "mine": 75_000, "theirs": 91_000} in (
            plan.joint_conflicts
        )
        assert "living_expenses" not in plan.joint_writes

    def test_v4_spouse_cross_map_uses_the_same_rule(self) -> None:
        updates = build_user_defaults_session_updates(
            {"filing_status": "Single", "living_expenses": 91_000},
            as_spouse=True,
            receiver_persisted=_demo_persisted(),
        )
        assert updates["filing_status"] == "Single"
        assert updates["living_expenses"] == 91_000
        assert "_bundle_joint_conflicts" not in updates


FIXED_DT = datetime(2026, 10, 1, 9, 0, 0)


def _grant(outstanding: int) -> EquityGrant:
    return EquityGrant("SYN-1", "NQO", "2020-01-01", 5_000, outstanding, 0.0)


def _exporter_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[
            AccountSummary(
                "brokerage",
                "you",
                "Brk",
                total_value=200_000.0,
                equity_value=200_000.0,
                holdings=[
                    Holding(
                        "VTI",
                        "Total market",
                        1000.0,
                        200_000.0,
                        "Brk",
                        "equity",
                        dividends_by_year={"2025": 6_000.0},
                    )
                ],
            ),
            AccountSummary("trad_ira", "you", "IRA", total_value=500_000.0, bond_value=500_000.0),
        ],
        equity_grants=[_grant(3_000)],
        server_available=True,
    )


def _bundle_from(
    snapshot: PortfolioSnapshot | None,
    *,
    exporter: str,
    strikes: dict | None = None,
    scalars: dict | None = None,
) -> dict:
    setup = dict(scalars or {})
    if strikes is not None:
        setup["grant_strikes"] = strikes
    return build_bundle_v5(
        setup_scalars=setup,
        snapshot=snapshot,
        ledger={"koinly": {}, "brokerage": {}},
        ytd=None,
        grants=getattr(snapshot, "equity_grants", None),
        grants_owner=getattr(snapshot, "equity_grants_owner", "") or None,
        exporter=exporter,
        slot_captured_at={},
        now=NOW,
    )


def _import(bundle: dict, local: LocalState, *, selected: set[str], operator: str):
    plan = plan_import(bundle, local, operator=operator)
    return apply_import(
        bundle,
        plan,
        local,
        selected=frozenset(selected),
        operator=operator,
        apply_v4_bundle=None,  # type: ignore[arg-type]
    )


class TestF01ImportedSnapshotDerivesGrowth:
    def test_imported_snapshot_is_marked_and_derives_growth(self) -> None:
        bundle = _bundle_from(_exporter_snapshot(), exporter="you", strikes={"2020": 50.0})
        result = _import(bundle, _local(persisted_scalars={}), selected={"you"}, operator="you")
        snap = result.snapshot
        assert snap.server_available is False
        assert snap.imported_from_bundle is True
        assert snapshot_carries_portfolio_data(snap)

        hh = Household()
        derive_snapshot_growth(hh, snap)
        assert hh.brokerage_growth is not None
        assert hh.brokerage_growth.yield_rate > 0
        assert hh.your_ira_growth is not None
        assert hh.your_ira_growth.default_rate == 0.04

    def test_flag_survives_save_and_load(self) -> None:
        import engine.portfolio_sync.portfolio as portfolio_mod

        snap = PortfolioSnapshot(
            accounts=_exporter_snapshot().accounts,
            equity_grants=[_grant(1)],
            imported_from_bundle=True,
            equity_grants_owner="spouse",
        )
        portfolio_mod.save_snapshot(snap)
        loaded = portfolio_mod.load_snapshot()
        assert loaded is not None
        assert loaded.imported_from_bundle is True
        assert loaded.equity_grants_owner == "spouse"

    def test_plain_unsynced_snapshot_still_does_not_derive(self) -> None:
        assert not snapshot_carries_portfolio_data(PortfolioSnapshot())


class TestF05GrantsTravelInTheirOwnersSlot:
    def test_import_tags_grants_with_the_slot_they_came_from(self) -> None:
        bundle = _bundle_from(_exporter_snapshot(), exporter="you", strikes={"2020": 50.0})
        result = _import(bundle, _local(persisted_scalars={}), selected={"you"}, operator="spouse")
        assert result.snapshot.equity_grants_owner == "you"

    def test_round_trip_does_not_bounce_stale_grants_back(self) -> None:
        # A ("you") holds the grants. B ("spouse") imports A's bundle.
        a_old = _exporter_snapshot()
        b_result = _import(
            _bundle_from(a_old, exporter="you", strikes={"2020": 50.0}),
            _local(persisted_scalars={}),
            selected={"you"},
            operator="spouse",
        )
        b_snapshot = b_result.snapshot
        b_strikes = b_result.scalar_updates["_user_grant_strikes"]

        # A then exercises (outstanding 3000 -> 2000) and edits a strike.
        a_now = _exporter_snapshot()
        a_now.equity_grants = [_grant(2_000)]
        a_local = _local(persisted_scalars={}, snapshot=a_now)

        # B exports; the grants it holds are A's, so they ride in A's slot.
        b_bundle = _bundle_from(b_snapshot, exporter="spouse", strikes=b_strikes)
        assert b_bundle["slots"]["spouse"]["grants"] == []
        assert b_bundle["slots"]["spouse"]["grant_strikes"] == {}
        assert [g["outstanding"] for g in b_bundle["slots"]["you"]["grants"]] == [3_000]

        # A imports B's bundle with the default selection (B's own slot only).
        result = _import(b_bundle, a_local, selected={"spouse"}, operator="you")
        assert [g.outstanding for g in result.snapshot.equity_grants] == [2_000]
        assert "_user_grant_strikes" not in result.scalar_updates

    def test_untagged_grants_stay_in_the_exporter_slot(self) -> None:
        bundle = _bundle_from(_exporter_snapshot(), exporter="you", strikes={"2020": 50.0})
        assert [g["outstanding"] for g in bundle["slots"]["you"]["grants"]] == [3_000]
        assert bundle["slots"]["you"]["grant_strikes"] == {"2020": 50.0}


class TestF06ImportedGrantsReachTheResolver:
    def test_imported_grants_become_a_bundle_candidate_for_review(self) -> None:
        bundle = _bundle_from(_exporter_snapshot(), exporter="you", strikes={"2020": 50.0})
        result = _import(bundle, _local(persisted_scalars={}), selected={"you"}, operator="you")
        store = CandidateStore()
        outcome = resolve_for_app(
            Household(), result.snapshot, {"2020": 50.0}, store, ChoiceMap(), None, FIXED_DT
        )
        candidates = store.candidates_for(GRANTS_KEY)
        assert len(candidates) == 1
        assert candidates[0].prov.source == Source.BUNDLE
        assert candidates[0].value == [
            StockGrant(year=2020, strike=50.0, shares=3_000, expiry_year=2030, grant_id="SYN-1")
        ]
        assert GRANTS_KEY in outcome.result.pending_review
        # IRA balances are NOT re-recorded from an imported snapshot: they
        # already travel as the slot's setup scalars.
        assert store.candidates_for("your_ira") == []

    def test_confirming_the_candidate_adopts_the_imported_grants(self) -> None:
        bundle = _bundle_from(_exporter_snapshot(), exporter="you", strikes={"2020": 50.0})
        result = _import(bundle, _local(persisted_scalars={}), selected={"you"}, operator="you")
        store = CandidateStore()
        outcome = resolve_for_app(
            Household(), result.snapshot, {"2020": 50.0}, store, ChoiceMap(), None, FIXED_DT
        )
        confirmed = confirm(
            GRANTS_KEY,
            Source.BUNDLE,
            outcome.result.household,
            store,
            ChoiceMap(),
            FIXED_DT,
        )
        assert [(g.year, g.strike, g.shares) for g in confirmed.grants] == [(2020, 50.0, 3_000)]
