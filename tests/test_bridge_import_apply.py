"""Bridge v5 import apply (plan task A7) and commit (plan task A8).

Plan: docs/superpowers/plans/2026-09-30-bridge-v5-envelope.md. ``apply_import``
is pure and transactional: it builds new objects from deep copies and never
touches the caller's state, so a failure leaves nothing half-applied.
"""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

import engine.bridge_import as bridge_import_mod
from engine.bridge_bundle import build_bundle, build_bundle_v5
from engine.bridge_import import (
    ImportResult,
    LocalState,
    apply_import,
    commit_import,
    plan_import,
)
from engine.portfolio_sync.portfolio import EmptySnapshotWriteRefusedError
from engine.portfolio_sync.shapes import AccountSummary, EquityGrant, PortfolioSnapshot
from engine.store.namespaces import PDF_LEDGER, PORTFOLIO
from models.ytd_income import OWNED_YTD_FIELDS, IncomeEvent, YTDSnapshot
from tests.test_bridge_ytd_overwrite import _receiver_snapshot, _sender_snapshot

NOW = "2026-09-30T14:32:11+00:00"
LATER = "2026-10-05T09:00:00+00:00"


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=lambda o: getattr(o, "__dict__", str(o)))


def _state_scalars() -> dict:
    return {
        "your_age": 60,
        "your_ira": 1_600_000,
        "spouse_age": 54,
        "spouse_ira": 1_650_000,
        "filing_status": "MFJ",
        "living_expenses": 90_000,
        "grant_strikes": {"2019": 98.0},
        "account_type_overrides": {
            "U111": {"type": "trad_ira", "owner": "you"},
            "U222": {"type": "roth_ira", "owner": "spouse"},
        },
    }


def _state_snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[
            AccountSummary("trad_ira", "you", "MyIRA", total_value=1_600_000.0),
            AccountSummary("trad_ira", "spouse", "SpouseIRA", total_value=1_650_000.0),
            AccountSummary("brokerage", "household", "Joint", total_value=10.0),
        ],
    )


def _state_ledger() -> dict:
    return {
        "koinly": {"you": {"stcg": 1_000.0}, "spouse": {"stcg": 7.0}},
        "brokerage": {
            "you": {"R1": {"interest_taxable_ytd": 5_000.0}},
            "spouse": {"S1": {"interest_taxable_ytd": 35.0}},
        },
    }


def _state_ytd() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={
            "you": {"wages_ytd": 100_000.0, "nqo_exercise_ytd": 50_000.0},
            "spouse": {"wages_ytd": 77_000.0},
        },
        ira_conversions_ytd=30_000.0,
        spouse_ira_conversions_ytd=700.0,
        income_events=[
            IncomeEvent("2026-03-01", 30_000.0, "conversion", "you"),
            IncomeEvent("2026-04-01", 700.0, "conversion", "spouse"),
        ],
    )


def _full_bundle(exporter: str = "you") -> dict:
    return build_bundle_v5(
        setup_scalars=_state_scalars(),
        snapshot=_state_snapshot(),
        ledger=_state_ledger(),
        ytd=_state_ytd(),
        grants=[EquityGrant("g1", "NQO", "2019-02-01", 1000, 1000, 50_000.0)],
        exporter=exporter,
        slot_captured_at={"spouse": NOW, "you": NOW},
        now=NOW,
    )


def _receiver() -> LocalState:
    """A receiver ('you' operator) with its own data and a stale spouse copy."""
    return LocalState(
        scalars={"your_age": 60, "your_ira": 1_600_000, "spouse_age": 50, "spouse_ira": 1},
        persisted_scalars={"filing_status": "MFJ"},
        snapshot=PortfolioSnapshot(
            accounts=[
                AccountSummary("trad_ira", "you", "MyIRA", total_value=1_600_000.0),
                AccountSummary("trad_ira", "spouse", "OldSpouseIRA", total_value=1.0),
                AccountSummary("brokerage", "household", "Joint", total_value=3.0),
            ],
            equity_grants=[EquityGrant("g1", "NQO", "2019-02-01", 1000, 1000, 50_000.0)],
        ),
        ledger={
            "koinly": {"you": {"stcg": 1_000.0}},
            "brokerage": {"you": {"R1": {"interest_taxable_ytd": 5_000.0}}},
        },
        ytd=_receiver_snapshot(),
        account_type_overrides={
            "U111": "trad_ira",
            "OLD_SPOUSE": {"type": "trad_ira", "owner": "spouse"},
        },
        slot_captured_at={},
    )


def _spouse_bundle(ytd: YTDSnapshot | None = None, **overrides) -> dict:
    """The spouse's own export: their slot only, their accounts and ledger."""
    kwargs = {
        "setup_scalars": {
            "spouse_age": 55,
            "spouse_ira": 1_650_000,
            "filing_status": "MFJ",
            "account_type_overrides": {"S_ROTH": {"type": "roth_ira", "owner": "spouse"}},
        },
        "snapshot": PortfolioSnapshot(
            accounts=[
                AccountSummary("trad_ira", "spouse", "SpouseIRA", total_value=1_650_000.0),
                AccountSummary("brokerage", "household", "Joint", total_value=20.0),
                AccountSummary("brokerage", "household", "JointCash", total_value=5.0),
            ],
        ),
        "ledger": {
            "koinly": {"spouse": {"stcg": 7.0}},
            "brokerage": {"spouse": {"S1": {"interest_taxable_ytd": 35.0}}},
        },
        "ytd": ytd if ytd is not None else _sender_as_spouse(),
        "grants": [],
        "exporter": "spouse",
        "slot_captured_at": {},
        "now": NOW,
    }
    kwargs.update(overrides)
    return build_bundle_v5(**kwargs)


def _sender_as_spouse() -> YTDSnapshot:
    """tests/test_bridge_ytd_overwrite.py's sender figures, owned by 'spouse' absolutely.

    The sender's legacy object is household-shaped from THEIR perspective, so
    its ira_conversions_ytd ("your side") is the spouse's conversion here.
    """
    sender = _sender_snapshot()
    return YTDSnapshot(
        by_owner={"spouse": {f: getattr(sender, f) for f in OWNED_YTD_FIELDS}},
        spouse_ira_conversions_ytd=sender.ira_conversions_ytd,
    )


def _apply(bundle, local, *, selected=None, operator="you", **plan_kwargs) -> ImportResult:
    plan = plan_import(bundle, local, operator=operator, **plan_kwargs)
    chosen = frozenset(plan.slots) if selected is None else frozenset(selected)
    return apply_import(bundle, plan, local, selected=chosen, operator=operator)


class TestApplyV5:
    def test_round_trip_two_slots_land_in_correct_fields(self):
        result = _apply(_full_bundle(), LocalState())
        for key in ("your_age", "your_ira", "spouse_age", "spouse_ira"):
            assert result.scalar_updates[key] == _state_scalars()[key]
        assert result.scalar_updates["_user_grant_strikes"] == {"2019": 98.0}
        owners = {a.account_name: a.owner for a in result.snapshot.accounts}
        assert owners == {"MyIRA": "you", "SpouseIRA": "spouse", "Joint": "household"}
        assert result.ledger == _state_ledger()
        for owner, values in _state_ytd().by_owner.items():
            # replace_owner_values stores all four fields, absent ones as 0.0.
            assert result.ytd.by_owner[owner] == {f: values.get(f, 0.0) for f in OWNED_YTD_FIELDS}
        assert result.ytd.ira_conversions_ytd == 30_000.0
        assert result.ytd.spouse_ira_conversions_ytd == 700.0

    def test_account_owner_preserved_never_reassigned(self):
        result = _apply(_spouse_bundle(), _receiver())
        spouse = [a for a in result.snapshot.accounts if a.account_name == "SpouseIRA"]
        assert [a.owner for a in spouse] == ["spouse"]
        mine = [a for a in result.snapshot.accounts if a.account_name == "MyIRA"]
        assert [a.owner for a in mine] == ["you"]

    def test_household_owned_account_survives_round_trip(self):
        result = _apply(_full_bundle(), LocalState())
        joint = [a for a in result.snapshot.accounts if a.owner == "household"]
        assert [a.account_name for a in joint] == ["Joint"]

    def test_household_records_upsert_by_name_never_deleted(self):
        local = _receiver()
        local.snapshot.accounts.append(AccountSummary("brokerage", "household", "OnlyMine"))
        result = _apply(_spouse_bundle(), local)
        joint = {
            a.account_name: a.total_value
            for a in result.snapshot.accounts
            if a.owner == "household"
        }
        assert joint == {"Joint": 20.0, "OnlyMine": 0.0, "JointCash": 5.0}

    def test_deselected_slot_untouched(self):
        local = _receiver()
        result = _apply(_full_bundle(exporter="spouse"), local, selected={"spouse"})
        assert not any(k.startswith("your_") for k in result.scalar_updates)
        mine = [a for a in result.snapshot.accounts if a.owner == "you"]
        assert [(a.account_name, a.total_value) for a in mine] == [("MyIRA", 1_600_000.0)]
        assert result.ledger["koinly"]["you"] == local.ledger["koinly"]["you"]
        assert result.ledger["brokerage"]["you"] == local.ledger["brokerage"]["you"]
        receiver = _receiver_snapshot()
        assert result.ytd.by_owner["you"] == {f: getattr(receiver, f) for f in OWNED_YTD_FIELDS}
        assert result.ytd.ira_conversions_ytd == receiver.ira_conversions_ytd

    def test_ytd_replaces_slot_never_adds(self):
        local = _receiver()
        local.ytd = local.ytd.attribute_unowned("you").replace_owner_values(
            "spouse", {"wages_ytd": 1.0}
        )
        result = _apply(_spouse_bundle(), local)
        assert result.ytd.by_owner["spouse"]["wages_ytd"] == 77_000.0  # not 77_001

    def test_reimport_same_bundle_is_idempotent(self):
        bundle = _spouse_bundle()
        first = _apply(bundle, _receiver())
        second_local = _receiver()
        second_local.snapshot = first.snapshot
        second_local.ledger = first.ledger
        second_local.ytd = first.ytd
        second = _apply(bundle, second_local)
        assert _dump(second.ytd) == _dump(first.ytd)
        assert _dump(second.snapshot) == _dump(first.snapshot)
        assert second.ledger == first.ledger

    def test_issue_496_figures_sum_across_two_slot_import(self):
        """#496: the sender's figures now SUM with the receiver's instead of being lost."""
        receiver, sender = _receiver_snapshot(), _sender_snapshot()
        result = _apply(_spouse_bundle(), _receiver(), selected={"spouse"})
        ytd = result.ytd
        assert ytd.wages_ytd == receiver.wages_ytd + sender.wages_ytd == 177_000.0
        assert (
            ytd.federal_withholding_ytd
            == receiver.federal_withholding_ytd + sender.federal_withholding_ytd
            == 27_700.0
        )
        assert (
            ytd.qualified_dividends_ytd
            == receiver.qualified_dividends_ytd + sender.qualified_dividends_ytd
            == 4_007.0
        )
        assert ytd.nqo_exercise_ytd == receiver.nqo_exercise_ytd + sender.nqo_exercise_ytd
        assert ytd.nqo_exercise_ytd == 50_000.0
        assert ytd.ira_conversions_ytd == 30_000.0
        assert ytd.spouse_ira_conversions_ytd == 700.0

    def test_legacy_ytd_migrates_to_operator_slot_on_first_v5_apply(self):
        receiver = _receiver_snapshot()
        result = _apply(_spouse_bundle(ytd=YTDSnapshot()), _receiver())
        assert result.ytd.by_owner["you"] == {f: getattr(receiver, f) for f in OWNED_YTD_FIELDS}
        for f in OWNED_YTD_FIELDS:  # spouse slot empty -> household totals unchanged
            assert getattr(result.ytd, f) == getattr(receiver, f)

    def test_distributions_move_by_owner_event_delta_only(self):
        local = _receiver()
        local.ytd.ira_distributions_ytd = 9_000.0  # a legacy total with no events
        bundle = _spouse_bundle(
            ytd=YTDSnapshot(
                by_owner={"spouse": {"wages_ytd": 1.0}},
                income_events=[IncomeEvent("2026-05-01", 2_000.0, "distribution", "spouse")],
            )
        )
        result = _apply(bundle, local)
        assert result.ytd.ira_distributions_ytd == 11_000.0
        again = _receiver()
        again.snapshot, again.ledger, again.ytd = result.snapshot, result.ledger, result.ytd
        assert _apply(bundle, again).ytd.ira_distributions_ytd == 11_000.0

    def test_failure_mid_apply_leaves_inputs_identical(self, monkeypatch):
        local = _receiver()
        bundle = _spouse_bundle()
        plan = plan_import(bundle, local)
        before = _dump(local.__dict__)
        before_bundle = _dump(bundle)

        def _boom(*args, **kwargs):
            raise RuntimeError("ledger step failed")

        monkeypatch.setattr(bridge_import_mod, "_apply_ledger", _boom)
        with pytest.raises(RuntimeError, match="ledger step failed"):
            apply_import(bundle, plan, local, selected=frozenset({"spouse"}), operator="you")
        assert _dump(local.__dict__) == before
        assert _dump(bundle) == before_bundle

    def test_successful_apply_mutates_no_input(self):
        local = _receiver()
        bundle = _spouse_bundle()
        before = _dump(local.__dict__)
        _apply(bundle, local)
        assert _dump(local.__dict__) == before

    def test_apply_refuses_result_that_empties_populated_portfolio(self):
        local = LocalState(
            snapshot=PortfolioSnapshot(accounts=[AccountSummary("trad_ira", "spouse", "Only")])
        )
        bundle = _spouse_bundle(snapshot=PortfolioSnapshot(), ledger={})
        with pytest.raises(EmptySnapshotWriteRefusedError):
            _apply(bundle, local)

    def test_account_type_overrides_replaced_per_selected_slot(self):
        result = _apply(_spouse_bundle(), _receiver())
        assert result.account_type_overrides == {
            "U111": "trad_ira",  # the receiver's own, untouched and in its original form
            "S_ROTH": {"type": "roth_ira", "owner": "spouse"},
        }

    def test_empty_slot_grants_do_not_wipe_existing_grants(self):
        result = _apply(_spouse_bundle(), _receiver())
        assert [g.grant_id for g in result.snapshot.equity_grants] == ["g1"]

    def test_non_empty_slot_grants_replace(self):
        local = LocalState(
            snapshot=PortfolioSnapshot(
                equity_grants=[EquityGrant("old", "NQO", "2018-01-01", 1, 1, 1.0)]
            )
        )
        result = _apply(_full_bundle(exporter="you"), local)
        assert [g.grant_id for g in result.snapshot.equity_grants] == ["g1"]

    def test_other_slot_without_records_keeps_local_records(self):
        """The spouse's opportunistic 'you' slot must not wipe the receiver's own data."""
        bundle = build_bundle_v5(
            setup_scalars={"your_age": 60, "spouse_age": 54},
            snapshot=PortfolioSnapshot(accounts=[AccountSummary("trad_ira", "spouse", "S")]),
            ledger={},
            ytd=YTDSnapshot(by_owner={"spouse": {"wages_ytd": 1.0}}),
            grants=[],
            exporter="spouse",
            slot_captured_at={},
            now=NOW,
        )
        local = _receiver()
        result = _apply(bundle, local)
        assert result.scalar_updates["your_age"] == 60  # the scalar itself does cross
        assert [a.account_name for a in result.snapshot.accounts if a.owner == "you"] == ["MyIRA"]
        assert result.ledger["koinly"]["you"] == local.ledger["koinly"]["you"]
        assert result.ytd.wages_ytd == 100_000.0 + 1.0
        assert result.ytd.ira_conversions_ytd == 30_000.0

    def test_joint_rule_and_magi_candidates(self):
        bundle = _spouse_bundle(
            setup_scalars={
                "spouse_age": 55,
                "filing_status": "single",
                "living_expenses": 95_000,
                "prior_year_magi": {"2025": 210_000.0},
            }
        )
        # A deliberate (non-default) local choice. The demo default "MFJ" would
        # count as never set and take the sender's value (audit-1008 F-04).
        receiver = _receiver()
        receiver.persisted_scalars = {"filing_status": "HoH"}
        result = _apply(bundle, receiver)
        assert result.scalar_updates["living_expenses"] == 95_000  # never persisted locally
        assert "filing_status" not in result.scalar_updates  # set locally: mine wins
        assert result.joint_conflicts == (
            {"field": "filing_status", "mine": "HoH", "theirs": "single"},
        )
        assert "prior_year_magi" not in result.scalar_updates
        assert result.magi_candidates == {2025: 210_000.0}

    def test_slot_captured_at_recorded_for_applied_slots(self):
        bundle = _spouse_bundle(now=LATER)
        result = _apply(bundle, _receiver())
        assert result.slot_captured_at == {"spouse": LATER}

    def test_stale_import_does_not_regress_local_captured_at(self):
        """Importing an older copy on purpose must not make the next check think it fresh."""
        local = dataclasses.replace(_receiver(), slot_captured_at={"spouse": LATER})
        result = _apply(_spouse_bundle(now=NOW), local)
        assert result.slot_captured_at == {"spouse": LATER}

    def test_fresh_import_advances_captured_at(self):
        local = dataclasses.replace(_receiver(), slot_captured_at={"spouse": NOW})
        result = _apply(_spouse_bundle(now=LATER), local)
        assert result.slot_captured_at == {"spouse": LATER}

    def test_unparseable_local_captured_at_is_replaced_by_valid_incoming(self):
        """A corrupt local time must not freeze the slot's capture time forever."""
        local = dataclasses.replace(_receiver(), slot_captured_at={"spouse": "not-a-time"})
        result = _apply(_spouse_bundle(now=LATER), local)
        assert result.slot_captured_at == {"spouse": LATER}

    def test_unparseable_incoming_keeps_local(self):
        assert bridge_import_mod._newer_captured_at(NOW, "not-a-time") == NOW

    def test_selected_must_be_planned_slots(self):
        bundle = _spouse_bundle()
        plan = plan_import(bundle, _receiver())
        with pytest.raises(ValueError, match="not in the plan"):
            apply_import(bundle, plan, _receiver(), selected=frozenset({"you"}), operator="you")


def _v4_bundle() -> dict:
    return build_bundle(
        {"your_age": 55, "your_ira": 1_650_000, "filing_status": "MFJ", "grant_strikes": {}},
        PortfolioSnapshot(accounts=[AccountSummary("trad_ira", "you", "TheirIRA")]),
        {"koinly": {"you": {"stcg": 7.0}}, "brokerage": {}},
        owner="you",
        ytd=_sender_snapshot(),
    )


class TestApplyV4:
    def test_v4_still_cross_maps_into_spouse_and_stamps_owner(self):
        local = _receiver()
        result = _apply(_v4_bundle(), local)
        assert result.scalar_updates["spouse_age"] == 55
        assert result.scalar_updates["spouse_ira"] == 1_650_000
        assert "your_age" not in result.scalar_updates
        theirs = [a for a in result.snapshot.accounts if a.account_name == "TheirIRA"]
        assert [a.owner for a in theirs] == ["spouse"]
        assert result.ledger["koinly"]["spouse"] == {"stcg": 7.0}
        assert result.ledger["koinly"]["you"] == {"stcg": 1_000.0}
        # PR #497: the receiver's own YTD, not the sender's.
        assert _dump(result.ytd) == _dump(_receiver_snapshot())

    def test_v4_redirect_to_you_uses_passthrough_mapping(self):
        result = _apply(_v4_bundle(), _receiver(), v4_target="you")
        assert result.scalar_updates["your_age"] == 55
        assert "spouse_age" not in result.scalar_updates
        mine = [a for a in result.snapshot.accounts if a.owner == "you"]
        assert [a.account_name for a in mine] == ["TheirIRA"]
        assert _dump(result.ytd) == _dump(_receiver_snapshot())

    def test_v4_apply_mutates_no_input(self):
        local = _receiver()
        bundle = _v4_bundle()
        before, before_bundle = _dump(local.__dict__), _dump(bundle)
        _apply(bundle, local)
        assert _dump(local.__dict__) == before
        assert _dump(bundle) == before_bundle

    def test_v4_apply_step_is_injectable(self):
        """The view passes its own apply_bundle so its seam stays patchable."""
        seen: dict = {}

        def _fake(target, payload, *, existing_snapshot, existing_ledger):
            seen["target"] = target
            return existing_snapshot, existing_ledger

        bundle, local = _v4_bundle(), _receiver()
        plan = plan_import(bundle, local, v4_target="you")
        result = apply_import(
            bundle, plan, local, selected=frozenset({"you"}), operator="you", apply_v4_bundle=_fake
        )
        assert seen["target"] == "you"
        assert [a.account_name for a in result.snapshot.accounts] == [
            a.account_name for a in local.snapshot.accounts
        ]

    def test_v4_apply_step_is_never_called_for_v5(self):
        def _boom(*args, **kwargs):
            raise AssertionError("the v4 apply step ran for a v5 bundle")

        bundle, local = _spouse_bundle(), _receiver()
        plan = plan_import(bundle, local)
        apply_import(
            bundle,
            plan,
            local,
            selected=frozenset(plan.slots),
            operator="you",
            apply_v4_bundle=_boom,
        )


class _Savers:
    """Fake cache writers recording every call; ``fail`` names one that raises."""

    def __init__(self, fail: str | None = None):
        self.calls: list[tuple[str, object, dict]] = []
        self.fail = fail

    def _record(self, name, obj, kwargs):
        self.calls.append((name, obj, kwargs))
        if name == self.fail:
            raise OSError(f"{name} write failed")

    def snapshot(self, obj, **kwargs):
        self._record("snapshot", obj, kwargs)

    def ledger(self, obj):
        self._record("ledger", obj, {})

    def ytd(self, obj):
        self._record("ytd", obj, {})

    def kwargs(self) -> dict:
        return {"save_snapshot": self.snapshot, "save_ledger": self.ledger, "save_ytd": self.ytd}


def _commit_in_transaction(store, result, savers: dict) -> None:
    with store.transaction():
        commit_import(result, **savers)


class TestCommitImport:
    def test_commit_writes_snapshot_ledger_ytd_in_order(self):
        result = _apply(_spouse_bundle(), _receiver())
        savers = _Savers()
        commit_import(result, **savers.kwargs())
        assert [(n, o) for n, o, _ in savers.calls] == [
            ("snapshot", result.snapshot),
            ("ledger", result.ledger),
            ("ytd", result.ytd),
        ]

    def test_commit_stops_at_the_first_failed_write_and_undoes_nothing_itself(self):
        result = _apply(_spouse_bundle(), _receiver())
        savers = _Savers(fail="ledger")
        with pytest.raises(OSError, match="ledger write failed"):
            commit_import(result, **savers.kwargs())
        # The caller's store transaction undoes the snapshot write (spec §2).
        assert [n for n, _, _ in savers.calls] == ["snapshot", "ledger"]

    def test_commit_passes_allow_empty_false_to_snapshot_saver(self):
        result = _apply(_spouse_bundle(), _receiver())
        savers = _Savers()
        commit_import(result, **savers.kwargs())
        assert savers.calls[0][2] == {"allow_empty": False}

    def test_commit_skips_ytd_when_result_has_none(self):
        local = LocalState(snapshot=PortfolioSnapshot())
        result = _apply(_v4_bundle(), local)
        assert result.ytd is None
        savers = _Savers()
        commit_import(result, **savers.kwargs())
        assert [n for n, _, _ in savers.calls] == ["snapshot", "ledger"]

    def test_in_a_store_transaction_a_failed_write_leaves_every_cache_as_it_was(self, store):
        from engine.pdf_ledger import save_ledger
        from engine.portfolio_sync.portfolio import save_snapshot

        local = _receiver()
        save_snapshot(local.snapshot, allow_empty=True)
        before = store.get(PORTFOLIO)
        result = _apply(_spouse_bundle(), local)
        savers = {
            "save_snapshot": save_snapshot,
            "save_ledger": save_ledger,
            "save_ytd": _Savers(fail="ytd").ytd,
        }
        with pytest.raises(OSError, match="ytd write failed"):
            _commit_in_transaction(store, result, savers)
        assert store.get(PORTFOLIO) == before
        assert store.keys(PDF_LEDGER) == []


class TestPurity:
    def test_bridge_import_is_streamlit_and_nacl_free(self):
        code = (
            "import sys\n"
            "sys.modules['nacl'] = None\n"
            "sys.modules['streamlit'] = None\n"
            "import engine.bridge_import\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert result.returncode == 0, result.stderr
