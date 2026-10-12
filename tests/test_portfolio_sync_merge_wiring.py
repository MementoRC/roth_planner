"""The real FinExtract sync path (views.setup.portfolio.sync_portfolio_from_finextract)
merges per account instead of replacing the whole cache; plus 403(b) key collisions.
Synthetic data only; FinExtract is mocked, the cache is the conftest tmp path."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import views.setup.portfolio as portfolio_mod
from engine.portfolio_sync import (
    AccountSummary,
    DividendsRollupSnapshot,
    OptionExercisesSnapshot,
    PortfolioSnapshot,
    load_snapshot,
    save_snapshot,
)
from engine.portfolio_sync.as_of import force_replace, merge_sync
from models.household import Household
from models.ytd_income import YTDSnapshot


class _State(dict):
    """Session-state stand-in supporting attribute writes like Streamlit's."""

    def __setattr__(self, name: str, value: object) -> None:
        self[name] = value


def _acct(
    account_type: str,
    owner: str,
    name: str,
    *,
    as_of: str,
    source: str,
    total: float = 100.0,
) -> AccountSummary:
    return AccountSummary(
        account_type=account_type,
        owner=owner,
        account_name=name,
        total_value=total,
        as_of=as_of,
        as_of_source=source,
    )


def _pdf_403b(as_of: str = "2026-10-09") -> AccountSummary:
    return _acct("403b", "spouse", "Acme Corp 403(b) Plan", as_of=as_of, source="statement")


def _run_sync(cached: list[AccountSummary], incoming: list[AccountSummary]):
    """Seed the cache, run the real sync with FinExtract returning *incoming*."""
    save_snapshot(PortfolioSnapshot(accounts=cached, server_available=True))
    fetched = PortfolioSnapshot(accounts=incoming, server_available=True)
    mock_st = MagicMock()
    mock_st.session_state = _State(account_type_overrides=None)
    with ExitStack() as stack:
        stack.enter_context(patch.object(portfolio_mod, "st", mock_st))
        stack.enter_context(patch.object(portfolio_mod, "fetch_portfolio", return_value=fetched))
        stack.enter_context(
            patch.object(
                portfolio_mod,
                "fetch_dividends_rollup",
                return_value=DividendsRollupSnapshot(server_available=False),
            )
        )
        stack.enter_context(patch.object(portfolio_mod, "fetch_magi", return_value=None))
        stack.enter_context(
            patch.object(portfolio_mod, "fetch_ytd_snapshot", return_value=YTDSnapshot())
        )
        stack.enter_context(
            patch.object(
                portfolio_mod,
                "fetch_option_exercises_with_cache",
                return_value=OptionExercisesSnapshot(server_available=False),
            )
        )
        stack.enter_context(patch.object(portfolio_mod, "save_ytd_snapshot", MagicMock()))
        outcome = portfolio_mod.sync_portfolio_from_finextract(
            Household(your_age=60, spouse_age=54)
        )
    return outcome, load_snapshot(), mock_st.session_state


class TestSyncPathMerges:
    def test_pdf_403b_survives_a_sync_that_returns_no_403b(self) -> None:
        brokerage = _acct("brokerage", "you", "Brk", as_of="2026-10-06", source="sync_time")
        _, snap, _ = _run_sync([_pdf_403b()], [brokerage])
        assert snap is not None
        types = sorted(a.account_type for a in snap.accounts)
        assert types == ["403b", "brokerage"]
        kept = next(a for a in snap.accounts if a.account_type == "403b")
        assert kept.as_of_source == "statement"

    def test_newer_pdf_403b_is_kept_over_an_older_synced_403b(self) -> None:
        pdf = _pdf_403b("2026-10-09")
        raw = _acct("403b", "spouse", "RAW LABEL", as_of="2026-10-06", source="sync_time")
        outcome, snap, state = _run_sync([pdf], [raw])
        assert snap is not None
        assert [(a.account_name, a.as_of_source) for a in snap.accounts] == [
            ("Acme Corp 403(b) Plan", "statement")
        ]
        assert [(k.account_name, r.account_name) for k, r in outcome.kept_newer] == [
            ("Acme Corp 403(b) Plan", "RAW LABEL")
        ]
        assert len(state["portfolio_kept_newer"]) == 1

    def test_finextract_account_no_longer_returned_is_dropped(self) -> None:
        closed = _acct("brokerage", "you", "Closed", as_of="2026-09-01", source="finextract")
        live = _acct("brokerage", "you", "Live", as_of="2026-10-06", source="sync_time")
        _, snap, _ = _run_sync([closed], [live])
        assert snap is not None
        assert [a.account_name for a in snap.accounts] == ["Live"]


class TestTwo403bsOneOwner:
    def test_two_finextract_403bs_both_survive_a_sync(self) -> None:
        a = _acct("403b", "you", "PLAN A", as_of="2026-10-06", source="sync_time", total=1.0)
        b = _acct("403b", "you", "PLAN B", as_of="2026-10-06", source="sync_time", total=2.0)
        res = merge_sync([], [a, b])
        assert res.accounts == [a, b]
        old_a = _acct("403b", "you", "PLAN A", as_of="2026-10-01", source="finextract")
        old_b = _acct("403b", "you", "PLAN B", as_of="2026-10-01", source="finextract")
        res = merge_sync([old_a, old_b], [a, b])
        assert res.accounts == [a, b]
        assert res.kept_newer == []

    def test_each_matches_its_own_name_not_the_first_in_the_group(self) -> None:
        old_a = _acct("403b", "you", "PLAN A", as_of="2026-10-08", source="finextract")
        old_b = _acct("403b", "you", "PLAN B", as_of="2026-10-01", source="finextract")
        new_a = _acct("403b", "you", "PLAN A", as_of="2026-10-06", source="sync_time")
        new_b = _acct("403b", "you", "PLAN B", as_of="2026-10-06", source="sync_time")
        res = merge_sync([old_a, old_b], [new_a, new_b])
        assert res.accounts == [old_a, new_b]  # A kept (newer), B updated
        assert res.kept_newer == [(old_a, new_a)]

    def test_pdf_403b_matches_one_synced_403b_the_other_is_added_or_rejected(self) -> None:
        pdf = _pdf_403b("2026-10-09")
        pdf_owner = pdf.owner
        x = _acct("403b", pdf_owner, "PLAN X", as_of="2026-10-06", source="sync_time")
        y = _acct("403b", pdf_owner, "PLAN Y", as_of="2026-10-06", source="sync_time")
        res = merge_sync([pdf], [x, y])
        # PDF is newer: it is kept, X (matched to it) is rejected, and Y is a genuinely
        # new account (the PDF is matched only once) so it is added.
        assert pdf in res.accounts
        assert y in res.accounts
        assert x not in res.accounts
        assert res.kept_newer == [(pdf, x)]

    def test_force_replace_touches_only_the_matched_403b(self) -> None:
        pdf = _pdf_403b()
        other = _acct("403b", "spouse", "PLAN Y", as_of="2026-10-01", source="finextract")
        incoming = _acct("403b", "spouse", "RAW", as_of="2026-10-06", source="sync_time")
        out = force_replace([pdf, other], incoming)
        assert other in out
        assert incoming in out
        assert pdf not in out
