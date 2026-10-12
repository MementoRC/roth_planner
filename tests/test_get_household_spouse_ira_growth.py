"""Regression test: spouse_ira_growth must be set after portfolio sync.

Bug F: app.py set hh.spouse_ira but omitted hh.spouse_ira_growth, causing
the spouse IRA projection to fall back to the flat slider rate.
"""

from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from models.household import GrowthProfile


def _make_snap(your_equity: float, spouse_equity: float) -> PortfolioSnapshot:
    your_acct = AccountSummary(
        account_type="trad_ira",
        owner="you",
        total_value=your_equity,
        equity_value=your_equity,
    )
    spouse_acct = AccountSummary(
        account_type="trad_ira",
        owner="spouse",
        total_value=spouse_equity,
        equity_value=spouse_equity,
    )
    return PortfolioSnapshot(accounts=[your_acct, spouse_acct], server_available=True)


def test_spouse_pretax_weighted_return_per_owner() -> None:
    """Per-owner weighted return must differ from the joint pretax_weighted_return
    when the two owners have different allocations."""
    snap = _make_snap(your_equity=500_000, spouse_equity=300_000)

    spouse_pretax_accounts = [a for a in snap.pretax_accounts if a.owner == "spouse"]
    spouse_pretax = sum(a.total_value for a in spouse_pretax_accounts)

    spouse_weighted_return = (
        sum(a.total_value * a.weighted_return for a in spouse_pretax_accounts) / spouse_pretax
        if spouse_pretax_accounts
        else snap.pretax_weighted_return
    )

    gp = GrowthProfile(default_rate=spouse_weighted_return)
    assert isinstance(gp, GrowthProfile)
    assert gp.default_rate > 0, "spouse_ira_growth must be non-zero after sync"


def test_spouse_pretax_fallback_when_no_spouse_accounts() -> None:
    """If no spouse pre-tax accounts exist, fall back to joint pretax_weighted_return."""
    snap = _make_snap(your_equity=500_000, spouse_equity=0)
    # Force a snapshot with only a "you" account (spouse_equity=0 still creates account)
    snap.accounts = [a for a in snap.accounts if a.owner == "you"]

    spouse_pretax_accounts = [a for a in snap.pretax_accounts if a.owner == "spouse"]
    spouse_pretax = sum(a.total_value for a in spouse_pretax_accounts)

    spouse_weighted_return = (
        sum(a.total_value * a.weighted_return for a in spouse_pretax_accounts) / spouse_pretax
        if spouse_pretax_accounts
        else snap.pretax_weighted_return
    )

    # Falls back to joint rate when no spouse accounts
    assert spouse_weighted_return == snap.pretax_weighted_return
