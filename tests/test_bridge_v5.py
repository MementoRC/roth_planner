"""Bridge v5 envelope: builder (A4), version classifier and strict parser (A5).

Plan: docs/superpowers/plans/2026-09-30-bridge-v5-envelope.md. The v5 envelope
carries per-person scalars/grants/YTD in absolute-keyed ``slots``, every record
with its own ``owner`` in ``records``, and household-level scalars in ``joint``.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.bridge_bundle import (
    BUNDLE_FORMAT_VERSION,
    BUNDLE_FORMAT_VERSION_V5,
    BundleFormatError,
    UnsupportedBundleVersionError,
    build_bundle_v5,
    classify_bundle,
    parse_v5,
)
from engine.portfolio_sync.shapes import AccountSummary, EquityGrant, Holding, PortfolioSnapshot
from models.ytd_income import OWNED_YTD_FIELDS, IncomeEvent, YTDSnapshot

NOW = "2026-09-30T14:32:11+00:00"


def _scalars() -> dict:
    """A setup_scalars payload shaped like views.setup._state._user_defaults_from_session."""
    return {
        "your_age": 60,
        "your_ira": 1_600_000,
        "spouse_age": 54,
        "spouse_ira": 1_650_000,
        "filing_status": "MFJ",
        "living_expenses": 90_000,
        "stock_price_now": 180.0,
        "grant_strikes": {"2019": 98.0, "2020": 124.0},
        "account_type_overrides": {
            "U111": "trad_ira",
            "U222": {"type": "roth_ira", "owner": "spouse"},
        },
        "prior_year_magi": {"2025": 210_000.0},
        "survivor": None,
        "inherited_iras": [],
    }


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[
            AccountSummary(account_type="trad_ira", owner="you", account_name="MyIRA"),
            AccountSummary(account_type="trad_ira", owner="spouse", account_name="TheirIRA"),
            AccountSummary(account_type="brokerage", owner="household", account_name="Joint"),
        ],
    )


def _ledger() -> dict:
    return {
        "koinly": {"you": {"stcg": 10.0}, "spouse": {"stcg": 99.0}},
        "brokerage": {"you": {"A1": {"interest": 3.0}}, "spouse": {"B2": {"interest": 7.0}}},
    }


def _grants() -> list:
    return [EquityGrant("g1", "NQO", "2019-02-01", 1000, 1000, 50_000.0)]


def _attributed_ytd() -> YTDSnapshot:
    return YTDSnapshot(
        by_owner={
            "you": {"wages_ytd": 100_000.0, "federal_withholding_ytd": 20_000.0},
            "spouse": {"wages_ytd": 77_000.0, "nqo_exercise_ytd": 5_000.0},
        },
        ira_conversions_ytd=30_000.0,
        spouse_ira_conversions_ytd=700.0,
        income_events=[
            IncomeEvent("2026-03-01", 30_000.0, "conversion", "you"),
            IncomeEvent("2026-04-01", 700.0, "conversion", "spouse"),
        ],
    )


def _build(**overrides) -> dict:
    kwargs = {
        "setup_scalars": _scalars(),
        "snapshot": _snapshot(),
        "ledger": _ledger(),
        "ytd": _attributed_ytd(),
        "grants": _grants(),
        "exporter": "you",
        "slot_captured_at": {},
        "now": NOW,
    }
    kwargs.update(overrides)
    return build_bundle_v5(**kwargs)


class TestBuildV5:
    def test_v5_has_envelope_keys(self):
        b = _build()
        assert BUNDLE_FORMAT_VERSION_V5 == 5
        assert BUNDLE_FORMAT_VERSION == 4  # v4 builder untouched
        assert b["format_version"] == 5
        assert b["exported_at"] == NOW
        assert b["exported_by"] == "you"
        assert set(b) == {
            "format_version",
            "exported_at",
            "exported_by",
            "slots",
            "records",
            "joint",
        }

    def test_exporter_slot_always_present_other_slot_opportunistic(self):
        b = _build(
            setup_scalars={"filing_status": "MFJ"},
            snapshot=None,
            ledger=None,
            ytd=None,
            grants=None,
            exporter="spouse",
        )
        assert set(b["slots"]) == {"spouse"}
        # A your_* scalar is enough for the other slot to appear.
        b = _build(setup_scalars={"your_age": 61}, ytd=None, exporter="spouse")
        assert set(b["slots"]) == {"you", "spouse"}
        # So is you-owned YTD.
        b = _build(
            setup_scalars={},
            ytd=YTDSnapshot(by_owner={"you": {"wages_ytd": 1.0}}),
            exporter="spouse",
        )
        assert set(b["slots"]) == {"you", "spouse"}

    def test_slot_scalars_use_absolute_keys(self):
        slots = _build()["slots"]
        assert slots["you"]["setup_scalars"] == {"your_age": 60, "your_ira": 1_600_000}
        assert slots["spouse"]["setup_scalars"] == {"spouse_age": 54, "spouse_ira": 1_650_000}

    def test_joint_section_holds_joint_scope_scalars(self):
        joint = _build()["joint"]
        assert joint["filing_status"] == "MFJ"  # verbatim, not normalised (R15)
        assert joint["living_expenses"] == 90_000
        assert joint["stock_price_now"] == 180.0
        assert joint["prior_year_magi"] == {"2025": 210_000.0}
        assert joint["survivor"] is None
        assert joint["inherited_iras"] == []
        for key in ("your_age", "spouse_age", "grant_strikes", "account_type_overrides"):
            assert key not in joint

    def test_records_portfolio_keeps_every_owner(self):
        accounts = _build()["records"]["portfolio"]
        assert [(a["account_name"], a["owner"]) for a in accounts] == [
            ("MyIRA", "you"),
            ("TheirIRA", "spouse"),
            ("Joint", "household"),
        ]

    def test_records_ledger_is_whole_owner_keyed_ledger(self):
        ledger = _ledger()
        b = _build(ledger=ledger)
        assert b["records"]["ledger"] == _ledger()
        b["records"]["ledger"]["koinly"]["you"]["stcg"] = -1.0
        assert ledger["koinly"]["you"]["stcg"] == 10.0  # deep-copied

    def test_account_type_overrides_normalised_to_nested_form(self):
        overrides = _build()["records"]["account_type_overrides"]
        assert overrides == {
            "U111": {"type": "trad_ira", "owner": "you"},
            "U222": {"type": "roth_ira", "owner": "spouse"},
        }

    def test_grants_and_strikes_ride_in_exporter_slot(self):
        slots = _build()["slots"]
        assert slots["you"]["grants"] == [
            {
                "grant_id": "g1",
                "grant_type": "NQO",
                "grant_date": "2019-02-01",
                "shares_granted": 1000,
                "outstanding": 1000,
                "current_value": 50_000.0,
            }
        ]
        assert slots["you"]["grant_strikes"] == {"2019": 98.0, "2020": 124.0}
        assert slots["spouse"]["grants"] == []
        assert slots["spouse"]["grant_strikes"] == {}

    def test_slot_ytd_carries_owner_values_and_owner_events(self):
        slots = _build()["slots"]
        you, spouse = slots["you"]["ytd"], slots["spouse"]["ytd"]
        assert you["values"] == {
            "wages_ytd": 100_000.0,
            "federal_withholding_ytd": 20_000.0,
        }
        assert you["ira_conversions"] == 30_000.0
        assert [e["amount"] for e in you["income_events"]] == [30_000.0]
        assert spouse["values"] == {"wages_ytd": 77_000.0, "nqo_exercise_ytd": 5_000.0}
        assert spouse["ira_conversions"] == 700.0
        assert [e["owner"] for e in spouse["income_events"]] == ["spouse"]

    def test_unattributed_ytd_goes_entirely_to_exporter_slot(self):
        legacy = YTDSnapshot(wages_ytd=100_000.0, qualified_dividends_ytd=4_000.0)
        b = _build(ytd=legacy, exporter="spouse")
        values = b["slots"]["spouse"]["ytd"]["values"]
        assert set(values) == set(OWNED_YTD_FIELDS)
        assert values["wages_ytd"] == 100_000.0
        assert values["qualified_dividends_ytd"] == 4_000.0
        assert b["slots"]["you"]["ytd"]["values"] == {}
        assert legacy.by_owner == {}  # caller's snapshot not mutated

    def test_no_ytd_means_slot_ytd_none(self):
        b = _build(ytd=None)
        assert b["slots"]["you"]["ytd"] is None

    def test_slot_captured_at(self):
        b = _build(slot_captured_at={"spouse": "2026-09-01T00:00:00+00:00"})
        assert b["slots"]["you"]["captured_at"] == NOW
        assert b["slots"]["spouse"]["captured_at"] == "2026-09-01T00:00:00+00:00"
        b = _build(slot_captured_at={})
        assert b["slots"]["spouse"]["captured_at"] is None

    def test_rejects_unknown_exporter(self):
        with pytest.raises(ValueError, match="unknown exporter slot"):
            _build(exporter="household")

    def test_v5_bundle_is_json_serialisable_and_nacl_free(self):
        b = _build()
        assert json.loads(json.dumps(b)) == b
        code = (
            "import sys\n"
            "sys.modules['nacl'] = None\n"
            "sys.modules['streamlit'] = None\n"
            "import engine.bridge_bundle\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert result.returncode == 0, result.stderr


class TestClassify:
    @pytest.mark.parametrize("version", [1, 2, 3, 4])
    def test_classify_v1_to_v4_is_legacy(self, version):
        assert classify_bundle({"format_version": version, "sections": {}}) == "v4"

    def test_classify_v5(self):
        assert classify_bundle(_build()) == "v5"

    def test_classify_unversioned_refused(self):
        with pytest.raises(UnsupportedBundleVersionError, match="no format_version"):
            classify_bundle({"your_age": 61, "filing_status": "MFJ"})

    @pytest.mark.parametrize("version", [6, 0, -1, "5", True, 5.0])
    def test_classify_future_or_bogus_version_refused(self, version):
        with pytest.raises(UnsupportedBundleVersionError, match=repr(version)):
            classify_bundle({"format_version": version})

    def test_classify_non_dict_is_format_error(self):
        with pytest.raises(BundleFormatError, match="not a JSON object"):
            classify_bundle([1, 2, 3])

    def test_both_errors_are_value_errors(self):
        # The view's pre-parse except tuple catches ValueError as "Invalid <file>".
        assert issubclass(UnsupportedBundleVersionError, ValueError)
        assert issubclass(BundleFormatError, ValueError)


class TestParseV5:
    def test_parse_v5_round_trips_a_built_bundle(self):
        raw = _build()
        before = json.dumps(raw, sort_keys=True)
        parsed = parse_v5(raw)
        assert json.dumps(raw, sort_keys=True) == before  # raw not mutated
        assert parsed.exported_by == "you"
        assert parsed.exported_at == NOW
        assert parsed.warnings == ()
        assert set(parsed.slots) == {"you", "spouse"}
        you = parsed.slots["you"]
        assert you.captured_at == NOW
        assert you.setup_scalars == {"your_age": 60, "your_ira": 1_600_000}
        assert you.grants == (EquityGrant("g1", "NQO", "2019-02-01", 1000, 1000, 50_000.0),)
        assert you.grant_strikes == {"2019": 98.0, "2020": 124.0}
        assert you.ytd is not None
        assert you.ytd.values == {"wages_ytd": 100_000.0, "federal_withholding_ytd": 20_000.0}
        assert you.ytd.ira_conversions == 30_000.0
        assert you.ytd.income_events == (IncomeEvent("2026-03-01", 30_000.0, "conversion", "you"),)
        assert parsed.slots["spouse"].captured_at is None
        assert [(a.account_name, a.owner) for a in parsed.records.accounts] == [
            ("MyIRA", "you"),
            ("TheirIRA", "spouse"),
            ("Joint", "household"),
        ]
        assert all(isinstance(a, AccountSummary) for a in parsed.records.accounts)
        assert parsed.records.ledger == _ledger()
        assert parsed.records.account_type_overrides["U111"] == {"type": "trad_ira", "owner": "you"}
        assert parsed.joint["filing_status"] == "MFJ"

    def test_parse_v5_rejects_non_v5(self):
        with pytest.raises(BundleFormatError, match="not a v5 bundle"):
            parse_v5({"format_version": 4, "sections": {}})

    def test_parse_v5_rejects_unknown_exporter(self):
        raw = _build()
        raw["exported_by"] = "household"
        with pytest.raises(BundleFormatError, match="exported_by"):
            parse_v5(raw)

    def test_parse_v5_rejects_unknown_slot_name(self):
        raw = _build()
        raw["slots"]["household"] = raw["slots"]["spouse"]
        with pytest.raises(BundleFormatError, match="unknown slot 'household'"):
            parse_v5(raw)

    def test_parse_v5_rejects_missing_exporter_slot(self):
        raw = _build()
        del raw["slots"]["you"]
        with pytest.raises(BundleFormatError, match="exporter slot 'you' is missing"):
            parse_v5(raw)

    def test_parse_v5_drops_cross_prefix_scalar_with_warning(self):
        raw = _build()
        raw["slots"]["spouse"]["setup_scalars"]["your_age"] = 99
        parsed = parse_v5(raw)
        assert "your_age" not in parsed.slots["spouse"].setup_scalars
        assert parsed.slots["spouse"].setup_scalars == {"spouse_age": 54, "spouse_ira": 1_650_000}
        assert any("your_age" in w and "spouse" in w for w in parsed.warnings)

    def test_parse_v5_rejects_account_with_unknown_owner(self):
        raw = _build()
        raw["records"]["portfolio"][0]["owner"] = "trust"
        with pytest.raises(BundleFormatError, match="owner 'trust'"):
            parse_v5(raw)

    def test_parse_v5_rejects_malformed_account(self):
        raw = _build()
        raw["records"]["portfolio"][0]["not_a_field"] = 1
        with pytest.raises(BundleFormatError, match="account"):
            parse_v5(raw)

    def test_parse_v5_reconstructs_accounts_with_holdings(self):
        raw = _build()
        raw["records"]["portfolio"][0]["holdings"] = [
            {
                "symbol": "VTI",
                "description": "Total Stock",
                "quantity": 10.0,
                "market_value": 2_500.0,
                "account_name": "MyIRA",
                "asset_class": "equity",
            }
        ]
        acct = parse_v5(raw).records.accounts[0]
        assert isinstance(acct.holdings[0], Holding)
        assert acct.holdings[0].symbol == "VTI"

    def test_parse_v5_drops_other_owners_income_event_with_warning(self):
        raw = _build()
        raw["slots"]["you"]["ytd"]["income_events"].append(
            {"date": "2026-05-01", "amount": 5.0, "kind": "conversion", "owner": "spouse"}
        )
        parsed = parse_v5(raw)
        assert [e.owner for e in parsed.slots["you"].ytd.income_events] == ["you"]
        assert any("income event" in w for w in parsed.warnings)

    def test_parse_v5_drops_non_owned_ytd_value_with_warning(self):
        raw = _build()
        raw["slots"]["you"]["ytd"]["values"]["ltcg_ytd"] = 1.0
        parsed = parse_v5(raw)
        assert "ltcg_ytd" not in parsed.slots["you"].ytd.values
        assert any("ltcg_ytd" in w for w in parsed.warnings)

    def test_parse_v5_skips_malformed_grant_with_warning(self):
        raw = _build()
        raw["slots"]["you"]["grants"].append("not-a-grant")
        parsed = parse_v5(raw)
        assert len(parsed.slots["you"].grants) == 1
        assert any("grant" in w for w in parsed.warnings)

    def test_parse_v5_null_slot_ytd_is_none(self):
        parsed = parse_v5(_build(ytd=None))
        assert parsed.slots["you"].ytd is None


class TestPortfolioSnapshotFromDict:
    def test_view_helper_delegates_to_engine(self):
        from engine.portfolio_sync.shapes import portfolio_snapshot_from_dict
        from views.setup._state import _portfolio_snapshot_from_dict

        data = {
            "accounts": [{"account_type": "trad_ira", "owner": "you", "account_name": "A"}],
            "equity_grants": [
                {
                    "grant_id": "g1",
                    "grant_type": "NQO",
                    "grant_date": "2019-02-01",
                    "shares_granted": 1,
                    "outstanding": 1,
                    "current_value": 1.0,
                }
            ],
            "stock_shares_held": 3,
        }
        assert _portfolio_snapshot_from_dict(data) == portfolio_snapshot_from_dict(data)
        assert portfolio_snapshot_from_dict(data).stock_shares_held == 3
