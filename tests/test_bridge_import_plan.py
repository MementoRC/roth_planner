"""Bridge v5 import planner (plan task A6): a pure function producing a previewable plan.

Plan: docs/superpowers/plans/2026-09-30-bridge-v5-envelope.md. ``plan_import``
describes what an import WOULD write -- per slot, per record family -- and must
never mutate any of its inputs.
"""

import copy
import json

import pytest

from engine.bridge_bundle import build_bundle, build_bundle_v5, parse_v5
from engine.bridge_import import ImportPlan, LocalState, SlotPlan, plan_import
from engine.portfolio_sync.shapes import AccountSummary, EquityGrant, PortfolioSnapshot
from models.ytd_income import IncomeEvent, YTDSnapshot

NOW = "2026-09-30T14:32:11+00:00"
EARLIER = "2026-09-01T00:00:00+00:00"
LATER = "2026-09-29T00:00:00+00:00"


def _local(**overrides) -> LocalState:
    """The receiver ('you' operator) holding both people's data."""
    kwargs = {
        "scalars": {
            "your_age": 60,
            "your_ira": 1_600_000,
            "spouse_age": 50,
            "spouse_ira": 1_000_000,
            "filing_status": "MFJ",
            "living_expenses": 90_000,
        },
        "persisted_scalars": {"filing_status": "MFJ"},
        "snapshot": PortfolioSnapshot(
            accounts=[
                AccountSummary("trad_ira", "you", "MyIRA", total_value=1_600_000.0),
                AccountSummary("trad_ira", "spouse", "OldSpouseIRA", total_value=1.0),
                AccountSummary("brokerage", "household", "Joint", total_value=10.0),
            ],
            equity_grants=[EquityGrant("g1", "NQO", "2019-02-01", 1000, 1000, 50_000.0)],
        ),
        "ledger": {
            "koinly": {"you": {"stcg": 1_000.0}},
            "brokerage": {"you": {"R1": {"interest_taxable_ytd": 5_000.0}}},
        },
        "ytd": YTDSnapshot(wages_ytd=100_000.0, ira_conversions_ytd=30_000.0),
        "account_type_overrides": {"U111": "trad_ira"},
        "slot_captured_at": {},
    }
    kwargs.update(overrides)
    return LocalState(**kwargs)


def _spouse_bundle(**overrides) -> dict:
    """What the spouse's instance exports: their own slot plus an opportunistic 'you' slot."""
    kwargs = {
        "setup_scalars": {
            "your_age": 60,
            "your_ira": 1_500_000,  # their possibly-stale copy of the receiver
            "spouse_age": 54,
            "spouse_ira": 1_650_000,
            "filing_status": "MFJ",
            "living_expenses": 95_000,
        },
        "snapshot": PortfolioSnapshot(
            accounts=[
                AccountSummary("trad_ira", "spouse", "SpouseIRA", total_value=1_650_000.0),
                AccountSummary("roth_ira", "spouse", "SpouseRoth", total_value=50_000.0),
                AccountSummary("brokerage", "household", "Joint", total_value=20.0),
            ],
        ),
        "ledger": {
            "koinly": {"spouse": {"stcg": 7.0}},
            "brokerage": {"spouse": {"S1": {"interest_taxable_ytd": 35.0}}},
        },
        "ytd": YTDSnapshot(
            by_owner={"spouse": {"wages_ytd": 77_000.0}},
            spouse_ira_conversions_ytd=700.0,
            income_events=[IncomeEvent("2026-04-01", 700.0, "conversion", "spouse")],
        ),
        "grants": [],
        "exporter": "spouse",
        "slot_captured_at": {},
        "now": NOW,
    }
    kwargs.update(overrides)
    return build_bundle_v5(**kwargs)


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=lambda o: getattr(o, "__dict__", str(o)))


class TestPlanV5:
    def test_plan_two_slot_bundle_lists_both_slots_with_counts(self):
        plan = plan_import(_spouse_bundle(), _local())
        assert isinstance(plan, ImportPlan)
        assert plan.version == "v5"
        assert plan.declared_by == "spouse"
        assert set(plan.slots) == {"you", "spouse"}
        spouse = plan.slots["spouse"]
        assert isinstance(spouse, SlotPlan)
        assert spouse.scalar_writes == {
            "spouse_age": (50, 54),
            "spouse_ira": (1_000_000, 1_650_000),
        }
        assert spouse.accounts_incoming == 2
        assert spouse.accounts_replaced == 0  # no local spouse account shares a name
        assert spouse.accounts_removed == 1  # OldSpouseIRA is not in the bundle
        assert spouse.ledger_rows == 2  # one koinly slot + one brokerage account
        assert spouse.ytd_writes == {
            "wages_ytd": (0.0, 77_000.0),
            "spouse_ira_conversions_ytd": (0.0, 700.0),
        }
        assert spouse.grants_replaced is False  # empty slot grants are a no-op (R7)

    def test_plan_reports_overwrites_only_where_values_differ(self):
        plan = plan_import(_spouse_bundle(), _local())
        you = plan.slots["you"]
        # your_age matches locally (60); only your_ira differs.
        assert you.scalar_writes == {"your_ira": (1_600_000, 1_500_000)}

    def test_plan_flags_stale_slot(self):
        bundle = _spouse_bundle(slot_captured_at={"you": EARLIER})
        plan = plan_import(bundle, _local(slot_captured_at={"you": LATER}))
        assert plan.slots["you"].stale is True
        assert plan.slots["you"].incoming_captured_at == EARLIER
        assert plan.slots["you"].local_captured_at == LATER

    def test_plan_fresh_slot_is_not_stale(self):
        plan = plan_import(_spouse_bundle(), _local(slot_captured_at={"spouse": EARLIER}))
        assert plan.slots["spouse"].stale is False

    @pytest.mark.parametrize(
        ("incoming", "local"),
        [(None, {"you": LATER}), (EARLIER, {}), ("not-a-date", {"you": LATER})],
    )
    def test_plan_missing_or_bad_timestamp_is_unknown_not_flagged(self, incoming, local):
        bundle = _spouse_bundle(slot_captured_at={"you": incoming} if incoming else {})
        plan = plan_import(bundle, _local(slot_captured_at=local))
        assert plan.slots["you"].stale is None

    def test_plan_reports_joint_conflicts(self):
        plan = plan_import(
            _spouse_bundle(),
            _local(persisted_scalars={"filing_status": "MFJ", "living_expenses": 90_000}),
        )
        assert plan.joint_conflicts == (
            {"field": "living_expenses", "mine": 90_000, "theirs": 95_000},
        )
        assert plan.joint_writes == {}

    def test_plan_takes_joint_value_never_persisted_locally(self):
        plan = plan_import(_spouse_bundle(), _local())
        assert plan.joint_conflicts == ()
        assert plan.joint_writes == {"living_expenses": (90_000, 95_000)}

    def test_plan_counts_household_records_separately(self):
        plan = plan_import(_spouse_bundle(), _local())
        assert plan.household.accounts_upserted == 1  # Joint matches by account_name
        assert plan.household.accounts_added == 0
        assert plan.slots["spouse"].accounts_incoming == 2  # household not counted here

    def test_plan_mutates_nothing(self):
        bundle = _spouse_bundle()
        local = _local()
        before_bundle = _dump(bundle)
        before_local = _dump(copy.deepcopy(local).__dict__)
        plan_import(bundle, local)
        assert _dump(bundle) == before_bundle
        assert _dump(local.__dict__) == before_local

    def test_plan_accepts_a_parsed_bundle(self):
        assert plan_import(parse_v5(_spouse_bundle()), _local()).declared_by == "spouse"

    def test_plan_warns_when_other_slot_has_no_records(self):
        """A bundle carrying an opportunistic 'you' slot but no 'you' records keeps local ones."""
        plan = plan_import(_spouse_bundle(), _local())
        you = plan.slots["you"]
        assert you.accounts_incoming == 0
        assert you.accounts_removed == 0  # non-exporter slot without records: kept
        assert you.ledger_rows == 0
        # The spouse's instance has no YTD for the receiver; its zero-filled slot
        # ytd must not read as "wipe your wages and conversions".
        assert you.ytd_writes == {}
        assert any("you" in w and "kept" in w for w in plan.warnings)


def _v4_bundle() -> dict:
    return build_bundle(
        {"your_age": 55, "your_ira": 1_650_000, "filing_status": "MFJ"},
        PortfolioSnapshot(accounts=[AccountSummary("trad_ira", "you", "TheirIRA")]),
        {"koinly": {"you": {"stcg": 7.0}}, "brokerage": {}},
        owner="you",
        ytd=YTDSnapshot(wages_ytd=77_000.0),
    )


class TestPlanV4:
    def test_plan_v4_defaults_to_spouse_slot(self):
        plan = plan_import(_v4_bundle(), _local())
        assert plan.version == "v4"
        assert plan.declared_by is None
        assert set(plan.slots) == {"spouse"}
        assert plan.slots["spouse"].scalar_writes == {
            "spouse_age": (50, 55),
            "spouse_ira": (1_000_000, 1_650_000),
        }
        assert plan.slots["spouse"].accounts_incoming == 1

    def test_plan_v4_redirect_to_you(self):
        plan = plan_import(_v4_bundle(), _local(), v4_target="you")
        assert set(plan.slots) == {"you"}
        assert plan.slots["you"].scalar_writes == {
            "your_age": (60, 55),
            "your_ira": (1_600_000, 1_650_000),
        }

    def test_plan_v4_has_no_ytd_writes(self):
        """PR #497: a v4 import never adopts the sender's YTD -- the plan says so too."""
        for target in ("spouse", "you"):
            plan = plan_import(_v4_bundle(), _local(), v4_target=target)
            assert plan.slots[target].ytd_writes == {}

    def test_plan_v4_rejects_unknown_target(self):
        with pytest.raises(ValueError, match="v4_target"):
            plan_import(_v4_bundle(), _local(), v4_target="household")
