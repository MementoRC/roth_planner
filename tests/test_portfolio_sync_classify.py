"""Tests for engine.portfolio_sync — account classification, symbol classification, AccountSummary, and PortfolioSnapshot."""

import pytest

from models.household import Household


def approx(expected, tol=1.0):
    return pytest.approx(expected, abs=tol)


class TestPortfolioSync:
    """Test portfolio sync parsing and classification logic."""

    def test_classify_brokerage_account(self):
        from engine.portfolio_sync import _classify_account

        acct_type, owner = _classify_account("Claude R. Cirba — Brokerage Account — 39119320*")
        assert acct_type == "brokerage"
        assert owner == "you"

    def test_classify_roth_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Claude R. Cirba — Roth IRA Brokerage Account — 61037368*")
        assert acct_type == "roth_ira"

    def test_classify_trad_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Some Person — Traditional IRA — 12345678*")
        assert acct_type == "trad_ira"

    def test_classify_rollover_ira(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Rollover IRA233813501")
        assert acct_type == "trad_ira"

    def test_classify_403b(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("VANDERBILT 403B59208")
        assert acct_type == "403b"

    def test_classify_hsa(self):
        from engine.portfolio_sync import _classify_account

        acct_type, _ = _classify_account("Health Savings Account178734462")
        assert acct_type == "hsa"

    def test_classify_symbols(self):
        from engine.portfolio_sync import _classify_symbol

        assert _classify_symbol("VTI") == "equity"
        assert _classify_symbol("VXUS") == "equity"
        assert _classify_symbol("BND") == "bond"
        assert _classify_symbol("BNDX") == "bond"
        assert _classify_symbol("ITOT") == "equity"
        assert _classify_symbol("AGG") == "bond"
        assert _classify_symbol("FBTC") == "crypto"
        assert _classify_symbol("SHV") == "cash"
        assert _classify_symbol("Cash HELD IN MONEY MARKET") == "cash"
        assert _classify_symbol("VTTHX") == "target_date"
        assert _classify_symbol("UNKNOWN") == "equity"  # default

    def test_parse_quantity(self):
        from engine.portfolio_sync import _parse_quantity

        assert _parse_quantity(100) == 100.0
        assert _parse_quantity(3.14) == 3.14
        assert _parse_quantity("2,182.861") == approx(2182.861, tol=0.001)
        assert _parse_quantity(None) == 0.0
        assert _parse_quantity("") == 0.0

    def test_account_summary_weighted_return(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(
            account_type="brokerage",
            owner="you",
            total_value=100_000,
            equity_value=60_000,
            bond_value=40_000,
        )
        # 60% * 9% + 40% * 4% = 5.4% + 1.6% = 7.0%
        assert acct.weighted_return == approx(0.07, tol=0.001)
        assert acct.equity_pct == approx(0.60, tol=0.001)

    def test_account_summary_with_crypto_and_cash(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(
            account_type="trad_ira",
            owner="you",
            total_value=200_000,
            equity_value=80_000,
            bond_value=40_000,
            cash_value=40_000,
            crypto_value=40_000,
        )
        # 80k*9% + 40k*4% + 40k*4.5% + 40k*0% = 7200+1600+1800+0 = 10600
        # 10600/200000 = 5.3%
        assert acct.weighted_return == approx(0.053, tol=0.001)

    def test_account_summary_empty(self):
        from engine.portfolio_sync import AccountSummary

        acct = AccountSummary(account_type="brokerage", owner="you")
        assert acct.weighted_return == 0.0
        assert acct.equity_pct == 0.0

    def test_pretax_accounts(self):
        from engine.portfolio_sync import AccountSummary, PortfolioSnapshot

        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="trad_ira",
                    owner="you",
                    total_value=1_500_000,
                    equity_value=500_000,
                    bond_value=500_000,
                    cash_value=500_000,
                ),
                AccountSummary(
                    account_type="403b",
                    owner="you",
                    total_value=140_000,
                    equity_value=100_000,
                    bond_value=40_000,
                ),
                AccountSummary(account_type="hsa", owner="you", total_value=60_000),
                AccountSummary(account_type="brokerage", owner="you", total_value=100_000),
            ],
            server_available=True,
        )
        assert len(snap.pretax_accounts) == 2
        assert snap.pretax_total == approx(1_640_000)
        assert snap.pretax_weighted_return > 0

    def test_pretax_weighted_return_for_owner(self):
        """pretax_weighted_return_for('you') must exclude spouse accounts."""
        from engine.portfolio_sync import AccountSummary, PortfolioSnapshot

        # "you": 100% equity → 9% return; "spouse": 100% bond → 4% return
        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="trad_ira",
                    owner="you",
                    total_value=1_000_000,
                    equity_value=1_000_000,
                ),
                AccountSummary(
                    account_type="trad_ira",
                    owner="spouse",
                    total_value=1_000_000,
                    bond_value=1_000_000,
                ),
            ],
            server_available=True,
        )

        your_return = snap.pretax_weighted_return_for("you")
        spouse_return = snap.pretax_weighted_return_for("spouse")
        joint_return = snap.pretax_weighted_return

        # Owner-filtered returns must differ from each other and from joint
        assert your_return == approx(0.09, tol=1e-6), "your return should be 100% equity (9%)"
        assert spouse_return == approx(0.04, tol=1e-6), "spouse return should be 100% bond (4%)"
        assert your_return != approx(joint_return, tol=1e-6), (
            "your-filtered return must not equal joint return when owners differ"
        )
        assert spouse_return != approx(joint_return, tol=1e-6), (
            "spouse-filtered return must not equal joint return when owners differ"
        )
        # Joint is 50/50 blend: (9% + 4%) / 2 = 6.5%
        assert joint_return == approx(0.065, tol=1e-6)

    def test_pretax_weighted_return_for_owner_fallback(self):
        """pretax_weighted_return_for falls back to joint return for unknown owner."""
        from engine.portfolio_sync import AccountSummary, PortfolioSnapshot

        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="trad_ira",
                    owner="you",
                    total_value=500_000,
                    equity_value=500_000,
                ),
            ],
            server_available=True,
        )
        # "spouse" has no pretax accounts → fallback to joint value
        assert snap.pretax_weighted_return_for("spouse") == approx(
            snap.pretax_weighted_return, tol=1e-9
        )


class TestAccountTypeOverrides:
    """Verify _classify_account honors user-supplied overrides."""

    def test_override_hit_returns_mapped_type(self):
        from engine.portfolio_sync import _classify_account

        assert _classify_account("U1234567", overrides={"U1234567": "trad_ira"}) == (
            "trad_ira",
            "you",
        )

    def test_override_miss_falls_through_to_substring_scan(self):
        from engine.portfolio_sync import _classify_account

        # Override exists for a different account; the queried name has 'ira' → substring match
        result = _classify_account("Rollover IRA233813501", overrides={"U1234567": "trad_ira"})
        assert result == ("trad_ira", "you")

    def test_empty_overrides_preserves_legacy_behavior(self):
        from engine.portfolio_sync import _classify_account

        assert _classify_account("Rollover IRA233813501") == ("trad_ira", "you")
        assert _classify_account("Individual Brokerage Account") == ("brokerage", "you")

    def test_overrides_supports_multiple_ibkr_accounts(self):
        from engine.portfolio_sync import _classify_account

        overrides = {"U1234567": "trad_ira", "U7654321": "roth_ira", "U9999999": "brokerage"}
        assert _classify_account("U1234567", overrides=overrides) == ("trad_ira", "you")
        assert _classify_account("U7654321", overrides=overrides) == ("roth_ira", "you")
        assert _classify_account("U9999999", overrides=overrides) == ("brokerage", "you")

    def test_override_can_force_brokerage_classification(self):
        from engine.portfolio_sync import _classify_account

        # Even an 'ira'-containing name can be overridden to brokerage if user knows better
        result = _classify_account(
            "Inheritance IRA Account",
            overrides={"Inheritance IRA Account": "brokerage"},
        )
        assert result == ("brokerage", "you")


class TestQueryResponseShape:
    """Verify _flatten_query_rows handles both FinExtract response shapes."""

    def test_single_institution_legacy_shape(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {
            "domain": "brokerage",
            "data_type": "holdings",
            "rows": [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
        }
        assert _flatten_query_rows(data) == [{"symbol": "AAPL"}, {"symbol": "MSFT"}]

    def test_multi_institution_current_shape(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {
            "domain": "brokerage",
            "data_type": "holdings",
            "institutions": {
                "fidelity": {"rows": [{"symbol": "AAPL"}]},
                "schwab": {"rows": [{"symbol": "MSFT"}, {"symbol": "TXN"}]},
            },
        }
        result = _flatten_query_rows(data)
        # Order across institutions is dict-iteration order — assert as a set / sorted
        assert sorted(r["symbol"] for r in result) == ["AAPL", "MSFT", "TXN"]
        assert len(result) == 3

    def test_empty_institutions(self):
        from engine.portfolio_sync import _flatten_query_rows

        data = {"institutions": {}}
        assert _flatten_query_rows(data) == []

    def test_neither_rows_nor_institutions(self):
        from engine.portfolio_sync import _flatten_query_rows

        # FinExtract returning no data at all should yield [] not raise
        data = {"domain": "brokerage", "data_type": "holdings"}
        assert _flatten_query_rows(data) == []

    def test_institutions_value_not_dict(self):
        from engine.portfolio_sync import _flatten_query_rows

        # Robustness: malformed nested batch should be skipped, not raise
        data = {
            "institutions": {"fidelity": "not-a-dict", "schwab": {"rows": [{"symbol": "MSFT"}]}}
        }
        result = _flatten_query_rows(data)
        assert result == [{"symbol": "MSFT"}]

    def test_institution_batch_missing_rows_key(self):
        from engine.portfolio_sync import _flatten_query_rows

        # If one institution's batch has no 'rows' key, skip silently rather than KeyError
        data = {
            "institutions": {
                "fidelity": {"metadata": "blah"},  # no 'rows' key
                "schwab": {"rows": [{"symbol": "MSFT"}]},
            },
        }
        assert _flatten_query_rows(data) == [{"symbol": "MSFT"}]
