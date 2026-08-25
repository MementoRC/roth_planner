import json
from dataclasses import dataclass, field

from engine.bridge_bundle import (
    BUNDLE_FORMAT_VERSION,
    apply_bundle,
    build_bundle,
    read_bundle_grants,
    read_bundle_ytd,
    read_format_version,
)
from engine.data_bridge_crypto import generate_keypair, open_uploaded_payload, seal
from engine.portfolio_sync.shapes import (
    AccountSummary,
    EquityGrant,
    Holding,
    PortfolioSnapshot,
)
from views.setup._state import _portfolio_snapshot_from_dict


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
        assert set(b["sections"]) == {"setup_scalars", "portfolio", "ledger", "ytd", "grants"}

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

    def test_real_account_summary_with_nested_holdings_round_trip(self):
        """Hardening test: real AccountSummary/Holding nested serialization round-trip.

        Verifies that a real PortfolioSnapshot with nested Holding objects survives
        the full export-import cycle (build_bundle -> seal -> unseal -> apply_bundle)
        with all numeric fields and owner rewrite intact.
        """
        # Generate encryption keypair
        pub, priv = generate_keypair()

        # Build real Holding with all numeric fields set to distinct sentinel values
        holding1 = Holding(
            symbol="VTI",
            description="Vanguard Total Stock Market",
            quantity=123.456,
            market_value=45678.90,
            account_name="You_Roth_IRA",
            asset_class="equity",
            total_gain_loss=5432.10,
            total_gain_loss_pct=13.47,
            dividends_by_year={"2023": 456.78, "2024": 567.89},
            dividends_window={"from_date": "2023-01-01", "to_date": "2024-12-31"},
            dividends_is_stale=False,
        )

        holding2 = Holding(
            symbol="BND",
            description="Vanguard Total Bond Market",
            quantity=234.567,
            market_value=23456.78,
            account_name="You_Roth_IRA",
            asset_class="bond",
            total_gain_loss=1234.56,
            total_gain_loss_pct=5.56,
            dividends_by_year={"2023": 789.01, "2024": 890.12},
            dividends_window={"from_date": "2023-01-01", "to_date": "2024-12-31"},
            dividends_is_stale=False,
        )

        # Build real AccountSummary with nested holdings
        account = AccountSummary(
            account_type="roth_ira",
            owner="you",
            account_name="You_Roth_IRA",
            total_value=69135.68,
            equity_value=45678.90,
            bond_value=23456.78,
            cash_value=0.0,
            crypto_value=0.0,
            target_date_value=0.0,
            holdings=[holding1, holding2],
        )

        # Build real PortfolioSnapshot
        snapshot = PortfolioSnapshot(
            accounts=[account],
            equity_grants=[],
            txn_shares_held=0,
            txn_shares_value=0.0,
            server_available=True,
            error=None,
            equity_sales_lots=[],
            equity_sales_executions=[],
            order_detail_summary_captured_at="2024-12-31",
        )

        # EXPORT: build_bundle + seal
        bundle = build_bundle(
            {"filing_status": "mfj"}, snapshot, {"koinly": {}, "brokerage": {}}, owner="you"
        )
        ciphertext = seal(json.dumps(bundle).encode("utf-8"), pub)

        # IMPORT: unseal + json.loads + apply_bundle
        plaintext = open_uploaded_payload(ciphertext, priv)
        data = json.loads(plaintext.decode("utf-8"))

        # Reconstruct via _portfolio_snapshot_from_dict to exercise nested Holding parsing
        reconstructed_snap = _portfolio_snapshot_from_dict(
            {"accounts": data["sections"]["portfolio"]["accounts"]}
        )

        # Put reconstructed accounts back into the bundle for apply_bundle
        data["sections"]["portfolio"]["accounts"] = [
            {
                "account_type": a.account_type,
                "owner": a.owner,
                "account_name": a.account_name,
                "total_value": a.total_value,
                "equity_value": a.equity_value,
                "bond_value": a.bond_value,
                "cash_value": a.cash_value,
                "crypto_value": a.crypto_value,
                "target_date_value": a.target_date_value,
                "holdings": [
                    {
                        "symbol": h.symbol,
                        "description": h.description,
                        "quantity": h.quantity,
                        "market_value": h.market_value,
                        "account_name": h.account_name,
                        "asset_class": h.asset_class,
                        "total_gain_loss": h.total_gain_loss,
                        "total_gain_loss_pct": h.total_gain_loss_pct,
                        "dividends_by_year": h.dividends_by_year,
                        "dividends_window": h.dividends_window,
                        "dividends_is_stale": h.dividends_is_stale,
                    }
                    for h in a.holdings
                ],
            }
            for a in reconstructed_snap.accounts
        ]

        # Apply to target owner (spouse)
        existing_snap = PortfolioSnapshot(accounts=[], equity_grants=[])
        final_snap, _ = apply_bundle(
            "spouse", data,
            existing_snapshot=existing_snap, existing_ledger={"koinly": {}, "brokerage": {}},
        )

        # Reconstruct as dataclasses (apply_bundle may leave accounts as dicts)
        final_snap_reconstructed = _portfolio_snapshot_from_dict(
            {
                "accounts": [
                    {
                        "account_type": a["account_type"] if isinstance(a, dict) else a.account_type,
                        "owner": a["owner"] if isinstance(a, dict) else a.owner,
                        "account_name": a["account_name"] if isinstance(a, dict) else a.account_name,
                        "total_value": a["total_value"] if isinstance(a, dict) else a.total_value,
                        "equity_value": a["equity_value"] if isinstance(a, dict) else a.equity_value,
                        "bond_value": a["bond_value"] if isinstance(a, dict) else a.bond_value,
                        "cash_value": a["cash_value"] if isinstance(a, dict) else a.cash_value,
                        "crypto_value": a["crypto_value"] if isinstance(a, dict) else a.crypto_value,
                        "target_date_value": a["target_date_value"] if isinstance(a, dict) else a.target_date_value,
                        "holdings": a["holdings"] if isinstance(a, dict) else [
                            {
                                "symbol": h.symbol,
                                "description": h.description,
                                "quantity": h.quantity,
                                "market_value": h.market_value,
                                "account_name": h.account_name,
                                "asset_class": h.asset_class,
                                "total_gain_loss": h.total_gain_loss,
                                "total_gain_loss_pct": h.total_gain_loss_pct,
                                "dividends_by_year": h.dividends_by_year,
                                "dividends_window": h.dividends_window,
                                "dividends_is_stale": h.dividends_is_stale,
                            }
                            for h in a.holdings
                        ],
                    }
                    for a in final_snap.accounts
                ]
            }
        )

        # Assert: owner rewritten, account preserved, all nested holding fields survive
        assert len(final_snap_reconstructed.accounts) == 1
        final_account = final_snap_reconstructed.accounts[0]
        assert final_account.owner == "spouse"
        assert final_account.account_name == "You_Roth_IRA"
        assert final_account.account_type == "roth_ira"
        assert final_account.total_value == 69135.68
        assert final_account.equity_value == 45678.90
        assert final_account.bond_value == 23456.78
        assert final_account.cash_value == 0.0
        assert final_account.crypto_value == 0.0
        assert final_account.target_date_value == 0.0

        # Assert nested holdings
        assert len(final_account.holdings) == 2

        # Check holding 1 (VTI)
        h1 = final_account.holdings[0]
        assert h1.symbol == "VTI"
        assert h1.description == "Vanguard Total Stock Market"
        assert h1.quantity == 123.456
        assert h1.market_value == 45678.90
        assert h1.asset_class == "equity"
        assert h1.total_gain_loss == 5432.10
        assert h1.total_gain_loss_pct == 13.47
        assert h1.dividends_by_year == {"2023": 456.78, "2024": 567.89}
        assert h1.dividends_window == {"from_date": "2023-01-01", "to_date": "2024-12-31"}
        assert h1.dividends_is_stale is False

        # Check holding 2 (BND)
        h2 = final_account.holdings[1]
        assert h2.symbol == "BND"
        assert h2.description == "Vanguard Total Bond Market"
        assert h2.quantity == 234.567
        assert h2.market_value == 23456.78
        assert h2.asset_class == "bond"
        assert h2.total_gain_loss == 1234.56
        assert h2.total_gain_loss_pct == 5.56
        assert h2.dividends_by_year == {"2023": 789.01, "2024": 890.12}
        assert h2.dividends_window == {"from_date": "2023-01-01", "to_date": "2024-12-31"}
        assert h2.dividends_is_stale is False


class TestBundleYtdSection:
    """audit-0823: "YTD in the data-bridge bundle" -- build_bundle(ytd=...) /
    read_bundle_ytd. YTD is household-wide, not per-owner, so there is no
    owner-filtering step here, unlike portfolio/ledger."""

    def test_reads_v3_format_version(self):
        """Back-compat: a v3 bundle's version must still parse after the v4 bump."""
        assert read_format_version({"format_version": 3, "sections": {}}) == 3

    def test_no_ytd_kwarg_emits_none_section(self):
        b = build_bundle({}, _Snap(), _ledger(), owner="you")
        assert b["sections"]["ytd"] is None
        assert read_bundle_ytd(b) is None

    def test_ytd_kwarg_roundtrips_through_read_bundle_ytd(self):
        from models.ytd_income import YTDSnapshot

        ytd = YTDSnapshot(tax_year=2026, wages_ytd=150_000.0, nqo_exercise_ytd=96_000.0)
        b = build_bundle({}, _Snap(), _ledger(), owner="you", ytd=ytd)
        assert b["sections"]["ytd"] is not None

        recovered = read_bundle_ytd(b)
        assert recovered == ytd

    def test_v2_bundle_with_no_ytd_key_returns_none_not_raise(self):
        """Back-compat: a hand-built v2 bundle predates the "ytd" section entirely."""
        v2_bundle = {
            "format_version": 2,
            "sections": {
                "setup_scalars": {},
                "portfolio": {"accounts": []},
                "ledger": {"koinly": {}, "brokerage": {}},
            },
        }
        assert read_bundle_ytd(v2_bundle) is None

    def test_malformed_ytd_section_string_returns_none(self):
        b = {"format_version": 3, "sections": {"ytd": "not a dict"}}
        assert read_bundle_ytd(b) is None

    def test_malformed_ytd_section_empty_dict_returns_none(self):
        b = {"format_version": 3, "sections": {"ytd": {}}}
        assert read_bundle_ytd(b) is None

    def test_non_dict_bundle_returns_none(self):
        assert read_bundle_ytd([1, 2, 3]) is None


class TestBundleGrantsSection:
    """v3 -> v4: the top-level "grants" section. Guards the real-world defect
    where EquityGrant never left the exporter's browser -- the recipient's
    Household.grants silently kept the synthetic Acme demo grants forever
    while the matching strike prices sat inert (see engine/bridge_bundle.py
    module docstring / audit note). Like TestBundleYtdSection, "grants" is
    household-wide (EquityGrant has no owner field), so no owner-filtering
    step applies here either."""

    def _grant(self, suffix: str = "1") -> EquityGrant:
        return EquityGrant(
            grant_id=f"g{suffix}",
            grant_type="NQO",
            grant_date="2021-03-15",
            shares_granted=1000,
            outstanding=400,
            current_value=52_000.0,
        )

    def test_format_version_bumped_to_4(self):
        """Guards a silent version-bump regression: importers gate migrations on this constant."""
        assert BUNDLE_FORMAT_VERSION == 4

    def test_grants_kwarg_roundtrips_all_six_fields(self):
        """Guards the actual defect: real strikes travel but real grants must too, intact."""
        grants = [self._grant("1"), self._grant("2")]
        b = build_bundle({}, _Snap(), _ledger(), owner="you", grants=grants)
        recovered = read_bundle_grants(b)
        assert recovered == grants

    def test_no_grants_kwarg_emits_none_section(self):
        """Guards a caller (e.g. no PortfolioSnapshot loaded) accidentally emitting [] instead of None."""
        b = build_bundle({}, _Snap(), _ledger(), owner="you")
        assert b["sections"]["grants"] is None
        assert read_bundle_grants(b) is None

    def test_v3_bundle_with_no_grants_key_returns_none_not_raise(self):
        """Back-compat: a v3 (or older) bundle predates the "grants" section entirely."""
        v3_bundle = {
            "format_version": 3,
            "sections": {
                "setup_scalars": {},
                "portfolio": {"accounts": []},
                "ledger": {"koinly": {}, "brokerage": {}},
                "ytd": None,
            },
        }
        assert read_bundle_grants(v3_bundle) is None

    def test_malformed_grants_section_string_returns_none(self):
        b = {"format_version": 4, "sections": {"grants": "not a list"}}
        assert read_bundle_grants(b) is None

    def test_malformed_grants_section_dict_returns_none(self):
        b = {"format_version": 4, "sections": {"grants": {"grant_id": "g1"}}}
        assert read_bundle_grants(b) is None

    def test_one_bad_entry_does_not_cost_the_good_entries(self):
        """Guards a hand-edited/foreign-schema bundle losing ALL real grants over one bad entry."""
        b = {
            "format_version": 4,
            "sections": {
                "grants": [
                    {"grant_id": "good", "grant_type": "NQO", "grant_date": "2020-01-01",
                     "shares_granted": 500, "outstanding": 100, "current_value": 9000.0},
                    "not a dict",
                    {"grant_id": "bad", "shares_granted": "not-an-int"},
                ]
            },
        }
        recovered = read_bundle_grants(b)
        assert [g.grant_id for g in recovered] == ["good"]

    def test_empty_grants_list_is_distinct_from_none(self):
        """Guards the empty-list-treated-as-absent bug: a real "zero grants" export must apply, not no-op."""
        b = {"format_version": 4, "sections": {"grants": []}}
        assert read_bundle_grants(b) == []
        assert read_bundle_grants(b) is not None

    def test_apply_bundle_replaces_existing_grants_when_bundle_carries_them(self):
        """Guards the core fix: apply_bundle must actually attach the imported grants."""
        existing_snap = _Snap2([_A("you", "MyIRA")], equity_grants=[{"grant_id": "stale-demo"}])
        incoming = {
            "format_version": 4,
            "sections": {
                "setup_scalars": {},
                "portfolio": {"accounts": []},
                "ledger": {"koinly": {}, "brokerage": {}},
                "grants": [
                    {"grant_id": "real1", "grant_type": "NQO", "grant_date": "2019-06-01",
                     "shares_granted": 2000, "outstanding": 800, "current_value": 104_000.0},
                ],
            },
        }
        new_snap, _ = apply_bundle(
            "you", incoming, existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )
        assert [g.grant_id for g in new_snap.equity_grants] == ["real1"]

    def test_apply_bundle_leaves_grants_untouched_for_v3_bundle(self):
        """Regression guard for local runs: a v3 (no "grants" key) bundle must not wipe real local grants."""
        existing_snap = _Snap2([_A("you", "MyIRA")], equity_grants=[{"grant_id": "my-real-grant"}])
        v3_incoming = {
            "format_version": 3,
            "sections": {
                "setup_scalars": {},
                "portfolio": {"accounts": []},
                "ledger": {"koinly": {}, "brokerage": {}},
                "ytd": None,
            },
        }
        new_snap, _ = apply_bundle(
            "you", v3_incoming, existing_snapshot=existing_snap, existing_ledger=_existing_ledger(),
        )
        assert new_snap.equity_grants == [{"grant_id": "my-real-grant"}]
