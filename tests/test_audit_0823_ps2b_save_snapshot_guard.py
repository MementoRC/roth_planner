"""Guard tests for save_snapshot() — audit-0823 finding PS-2b.

PS-1 (PR #455) closed the one *documented* path by which an unanswered
/query/brokerage fetch reached save_snapshot() as accounts=[] and overwrote a
populated .portfolio_cache.json behind a green "Synced: 0 accounts" toast. The
write itself stayed unguarded, so any other caller could still clobber real
data — notably views/setup/data_bridge.py, which the filed finding did not list.

The rule is deliberately NOT "refuse to write an empty snapshot". A household
genuinely going to zero accounts is legitimate and must still persist; a naive
emptiness check would break it. A write is refused only when it is
simultaneously EMPTY, UNVERIFIED, and DESTRUCTIVE:

    no incoming accounts  AND  not verified-empty  AND  on-disk cache has accounts

"Verified-empty" defaults to snap.server_available, which is live and truthful
on the sync path. Callers where that flag is stale (the data-bridge import
inherits it from disk via apply_bundle) pass allow_empty explicitly instead.
"""

from pathlib import Path

import pytest


def _seed_populated_cache(path: Path) -> None:
    """Write a cache holding one real account, as a prior good sync would."""
    from engine.secure_io import write_pii_json

    write_pii_json(
        path,
        {
            "accounts": [
                {
                    "account_type": "trad_ira",
                    "owner": "you",
                    "account_name": "Fidelity IRA",
                    "total_value": 1_700_000.0,
                    "equity_value": 1_700_000.0,
                    "bond_value": 0.0,
                    "cash_value": 0.0,
                    "crypto_value": 0.0,
                    "target_date_value": 0.0,
                    "holdings": [],
                }
            ],
            "equity_grants": [],
            "txn_shares_held": 0,
            "txn_shares_value": 0.0,
            "server_available": True,
            "error": None,
        },
    )


def _accounts_on_disk(path: Path) -> list:
    from engine.secure_io import read_pii_json

    return read_pii_json(path).get("accounts", [])


class TestUnverifiedEmptyWriteRefused:
    """The destructive case: empty + unverified over a populated cache."""

    def test_unverified_empty_snapshot_does_not_clobber_populated_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import portfolio as portfolio_mod

        cache = tmp_path / ".portfolio_cache.json"
        _seed_populated_cache(cache)
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        with pytest.raises(portfolio_mod.EmptySnapshotWriteRefusedError):
            portfolio_mod.save_snapshot(portfolio_mod.PortfolioSnapshot(server_available=False))

        # The pre-existing good data must survive the refused write intact.
        assert len(_accounts_on_disk(cache)) == 1
        assert _accounts_on_disk(cache)[0]["total_value"] == 1_700_000.0

    def test_refusal_message_names_the_cache_and_the_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The exception must be actionable, not a bare truthiness failure."""
        from engine.portfolio_sync import portfolio as portfolio_mod

        cache = tmp_path / ".portfolio_cache.json"
        _seed_populated_cache(cache)
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        with pytest.raises(portfolio_mod.EmptySnapshotWriteRefusedError) as excinfo:
            portfolio_mod.save_snapshot(portfolio_mod.PortfolioSnapshot(server_available=False))

        message = str(excinfo.value)
        assert "1" in message  # the count that would have been destroyed
        assert cache.name in message


class TestLegitimateWritesStillPersist:
    """Everything a naive emptiness guard would have broken."""

    def test_verified_empty_snapshot_overwrites_populated_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The household genuinely closed every account — this MUST persist."""
        from engine.portfolio_sync import portfolio as portfolio_mod

        cache = tmp_path / ".portfolio_cache.json"
        _seed_populated_cache(cache)
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        portfolio_mod.save_snapshot(portfolio_mod.PortfolioSnapshot(server_available=True))

        assert _accounts_on_disk(cache) == []

    def test_empty_snapshot_writes_when_no_cache_exists_yet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing to destroy — the first-run write is never destructive."""
        from engine.portfolio_sync import portfolio as portfolio_mod

        cache = tmp_path / ".portfolio_cache.json"
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        portfolio_mod.save_snapshot(portfolio_mod.PortfolioSnapshot(server_available=False))

        assert cache.exists()
        assert _accounts_on_disk(cache) == []

    def test_empty_snapshot_writes_over_an_already_empty_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import portfolio as portfolio_mod
        from engine.secure_io import write_pii_json

        cache = tmp_path / ".portfolio_cache.json"
        write_pii_json(cache, {"accounts": [], "equity_grants": []})
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        portfolio_mod.save_snapshot(portfolio_mod.PortfolioSnapshot(server_available=False))

        assert _accounts_on_disk(cache) == []

    def test_populated_snapshot_writes_even_when_server_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-saving a snapshot loaded from disk: load_snapshot() defaults
        server_available to False, so this path must not be blocked."""
        from engine.portfolio_sync import portfolio as portfolio_mod
        from engine.portfolio_sync.shapes import AccountSummary

        cache = tmp_path / ".portfolio_cache.json"
        _seed_populated_cache(cache)
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        snap = portfolio_mod.PortfolioSnapshot(
            accounts=[AccountSummary(account_type="roth_ira", owner="spouse", total_value=42.0)],
            server_available=False,
        )
        portfolio_mod.save_snapshot(snap)

        on_disk = _accounts_on_disk(cache)
        assert len(on_disk) == 1
        assert on_disk[0]["account_type"] == "roth_ira"


class TestExplicitAllowEmptyBeatsTheInheritedFlag:
    """The data-bridge path inherits server_available from disk, so it opts out
    of the inference rather than depending on a stale value."""

    def test_allow_empty_false_refuses_despite_server_available_true(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import portfolio as portfolio_mod

        cache = tmp_path / ".portfolio_cache.json"
        _seed_populated_cache(cache)
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        with pytest.raises(portfolio_mod.EmptySnapshotWriteRefusedError):
            portfolio_mod.save_snapshot(
                portfolio_mod.PortfolioSnapshot(server_available=True),
                allow_empty=False,
            )

        assert len(_accounts_on_disk(cache)) == 1

    def test_allow_empty_true_permits_despite_server_available_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import portfolio as portfolio_mod

        cache = tmp_path / ".portfolio_cache.json"
        _seed_populated_cache(cache)
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        portfolio_mod.save_snapshot(
            portfolio_mod.PortfolioSnapshot(server_available=False),
            allow_empty=True,
        )

        assert _accounts_on_disk(cache) == []


class TestFinExtractSectionsStillPreserved:
    """The guard must not disturb save_snapshot's existing merge behaviour."""

    def test_equity_sales_and_sources_survive_a_permitted_empty_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from engine.portfolio_sync import portfolio as portfolio_mod
        from engine.secure_io import read_pii_json, write_pii_json

        cache = tmp_path / ".portfolio_cache.json"
        write_pii_json(
            cache,
            {
                "accounts": [],
                "equity_sales": {"lots": [{"grant_id": "G1"}], "executions": []},
                "sources": {"order_detail_summary": {"captured_at": "2026-01-01"}},
            },
        )
        monkeypatch.setattr(portfolio_mod, "_CACHE_PATH", cache)

        portfolio_mod.save_snapshot(portfolio_mod.PortfolioSnapshot(server_available=True))

        data = read_pii_json(cache)
        assert data["equity_sales"]["lots"] == [{"grant_id": "G1"}]
        assert data["sources"]["order_detail_summary"]["captured_at"] == "2026-01-01"
