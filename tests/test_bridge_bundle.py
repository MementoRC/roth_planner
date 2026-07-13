import json
from dataclasses import dataclass, field

from engine.bridge_bundle import (
    BUNDLE_FORMAT_VERSION,
    apply_bundle,
    build_bundle,
    read_format_version,
)
from engine.data_bridge_crypto import generate_keypair, open_uploaded_payload, seal


@dataclass
class _Acct:
    owner: str
    account_name: str
    total_value: float = 0.0


@dataclass
class _Snap:
    accounts: list = field(default_factory=list)
    equity_grants: list = field(default_factory=list)


def _ledger():
    return {
        "koinly": {"you": {"stcg": 10.0}, "spouse": {"stcg": 99.0}},
        "brokerage": {"you": {"A1": {"interest": 3.0}}, "spouse": {"B2": {"interest": 7.0}}},
    }


class TestBuildBundle:
    def test_version_and_sections_present(self):
        snap = _Snap(accounts=[_Acct("you", "IRA")])
        b = build_bundle({"age_self": 61}, snap, _ledger(), owner="you")
        assert b["format_version"] == BUNDLE_FORMAT_VERSION
        assert set(b["sections"]) == {"setup_scalars", "portfolio", "ledger"}

    def test_accounts_are_owner_filtered(self):
        snap = _Snap(accounts=[_Acct("you", "MyIRA"), _Acct("spouse", "TheirIRA")])
        b = build_bundle({}, snap, _ledger(), owner="you")
        names = [a["account_name"] for a in b["sections"]["portfolio"]["accounts"]]
        assert names == ["MyIRA"]

    def test_ledger_is_only_exporter_slice(self):
        b = build_bundle({}, _Snap(), _ledger(), owner="you")
        assert b["sections"]["ledger"] == {"koinly": {"stcg": 10.0}, "brokerage": {"A1": {"interest": 3.0}}}

    def test_no_grants_in_bundle(self):
        snap = _Snap(accounts=[_Acct("you", "IRA")], equity_grants=[{"grant_id": "g1"}])
        b = build_bundle({}, snap, _ledger(), owner="you")
        assert "equity_grants" not in b["sections"]["portfolio"]
        assert "txn_shares" not in b["sections"]["portfolio"]

    def test_setup_scalars_passthrough(self):
        b = build_bundle({"filing_status": "mfj"}, _Snap(), _ledger(), owner="you")
        assert b["sections"]["setup_scalars"] == {"filing_status": "mfj"}


class TestFormatDetection:
    def test_current_bundle_version(self):
        assert read_format_version({"format_version": 2, "sections": {}}) == 2

    def test_legacy_payload_has_no_version(self):
        assert read_format_version({"age_self": 61, "filing_status": "mfj"}) is None

    def test_non_dict_returns_none(self):
        assert read_format_version([1, 2, 3]) is None


class _Snap2:
    def __init__(self, accounts, equity_grants=None):
        self.accounts = accounts
        self.equity_grants = equity_grants or []


class _A:
    def __init__(self, owner, name):
        self.owner = owner
        self.account_name = name


def _existing_ledger():
    return {
        "koinly": {"you": {"stcg": 10.0}, "spouse": {"stcg": 99.0}},
        "brokerage": {"you": {"A1": {"interest": 3.0}}, "spouse": {"OLD": {"interest": 500.0}}},
    }


class TestApplyBundle:
    def _incoming(self):
        return {
            "format_version": 2,
            "sections": {
                "setup_scalars": {},
                "portfolio": {"accounts": [_A("you", "SpouseIRA")]},
                "ledger": {"koinly": {"stcg": 42.0}, "brokerage": {"NEW": {"interest": 1.0}}},
            },
        }

    def test_import_as_spouse_replaces_spouse_ledger_and_clears_stale(self):
        existing_snap = _Snap2([_A("you", "MyIRA")], equity_grants=[{"grant_id": "g1"}])
        new_snap, new_led = apply_bundle(
            "spouse", self._incoming(),
            existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )
        assert new_led["koinly"]["spouse"] == {"stcg": 42.0}
        assert new_led["brokerage"]["spouse"] == {"NEW": {"interest": 1.0}}
        assert "OLD" not in new_led["brokerage"]["spouse"]
        assert new_led["koinly"]["you"] == {"stcg": 10.0}

    def test_import_as_spouse_leaves_my_grants_untouched(self):
        existing_snap = _Snap2([_A("you", "MyIRA")], equity_grants=[{"grant_id": "g1"}])
        new_snap, _ = apply_bundle(
            "spouse", self._incoming(),
            existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )
        assert [g["grant_id"] for g in new_snap.equity_grants] == ["g1"]

    def test_incoming_accounts_rewritten_to_target_owner_and_mine_kept(self):
        existing_snap = _Snap2([_A("you", "MyIRA")])
        new_snap, _ = apply_bundle(
            "spouse", self._incoming(),
            existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )
        by_name = {a.account_name: a.owner for a in new_snap.accounts}
        assert by_name == {"MyIRA": "you", "SpouseIRA": "spouse"}

    def test_empty_incoming_ledger_resets_target_owner(self):
        incoming = self._incoming()
        incoming["sections"]["ledger"] = {"koinly": {}, "brokerage": {}}
        _, new_led = apply_bundle(
            "spouse", incoming,
            existing_snapshot=_Snap2([_A("you", "MyIRA")]), existing_ledger=_existing_ledger(),
        )
        assert "spouse" not in new_led["koinly"]
        assert "spouse" not in new_led["brokerage"]


class TestExportImportRoundTrip:
    """Engine-boundary round-trip: build_bundle -> seal -> open_uploaded_payload
    -> json -> apply_bundle. No Streamlit; exercises the same pipeline the
    data-bridge view wires together."""

    def test_sealed_bundle_round_trips_into_target_owner_slot(self):
        pub, priv = generate_keypair()
        exporter_snap = _Snap(accounts=[_Acct("you", "ExportedIRA")])
        bundle = build_bundle(
            {"your_age": 61}, exporter_snap, _existing_ledger(), owner="you"
        )

        ciphertext = seal(json.dumps(bundle).encode("utf-8"), pub)

        plaintext = open_uploaded_payload(ciphertext, priv)
        received = json.loads(plaintext.decode("utf-8"))

        assert read_format_version(received) == BUNDLE_FORMAT_VERSION

        incoming_accounts = [
            _A(a["owner"], a["account_name"])
            for a in received["sections"]["portfolio"]["accounts"]
        ]
        received["sections"]["portfolio"]["accounts"] = incoming_accounts

        existing_snap = _Snap2([_A("you", "MyIRA")])
        new_snap, new_led = apply_bundle(
            "spouse", received,
            existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )

        by_name = {a.account_name: a.owner for a in new_snap.accounts}
        assert by_name == {"MyIRA": "you", "ExportedIRA": "spouse"}
        assert received["sections"]["setup_scalars"] == {"your_age": 61}
        assert new_led["koinly"]["spouse"] == {"stcg": 10.0}
