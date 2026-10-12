"""Defects from the fixture-driven browser pass (2026-10-10).

Driven by the synthetic files in tests/fixtures/synthetic/. Invented figures
only.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.bridge_import import LocalState, apply_import, plan_import
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.orchestrator import resolve_for_app
from engine.data_sources.resolver import GRANTS_KEY, confirm
from engine.portfolio_sync.shapes import EquityGrant, PortfolioSnapshot
from models.household import Household
from models.sourced import Source
from views import portfolio as portfolio_view
from views.setup._partials import _import_preview

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"
FIXED_DT = datetime(2026, 10, 10, 12, 0, 0)


def _bundle() -> dict:
    return json.loads((FIXTURES / "synthetic_backup_v5.json").read_text())


def _local() -> LocalState:
    return LocalState(scalars={"your_ira": 500_000, "your_age": 61})


class TestGridRefillGoesThroughReview:
    """The option grid seeds from hh.grants. Imported grants are a BUNDLE
    candidate (audit-1008 F-06) and reach hh.grants only once confirmed --
    the README's step 5 had skipped the confirm."""

    def _imported(self):
        bundle = _bundle()
        local = _local()
        plan = plan_import(bundle, local, operator="you")
        return apply_import(
            bundle,
            plan,
            local,
            selected=frozenset({"you", "spouse"}),
            operator="you",
            apply_v4_bundle=None,  # type: ignore[arg-type]
        )

    def test_unconfirmed_import_leaves_grants_pending(self) -> None:
        result = self._imported()
        strikes = result.scalar_updates["_user_grant_strikes"]
        store = CandidateStore()
        before = [(g.year, g.strike, g.shares) for g in Household().grants]
        outcome = resolve_for_app(
            Household(), result.snapshot, strikes, store, ChoiceMap(), None, FIXED_DT
        )
        assert GRANTS_KEY in outcome.result.pending_review
        assert [(g.year, g.strike, g.shares) for g in outcome.result.household.grants] == before

    def test_confirming_adopts_the_fixture_lots(self) -> None:
        result = self._imported()
        strikes = result.scalar_updates["_user_grant_strikes"]
        store = CandidateStore()
        outcome = resolve_for_app(
            Household(), result.snapshot, strikes, store, ChoiceMap(), None, FIXED_DT
        )
        confirmed = confirm(
            GRANTS_KEY, Source.BUNDLE, outcome.result.household, store, ChoiceMap(), FIXED_DT
        )
        assert sorted((g.year, g.strike, g.shares) for g in confirmed.grants) == [
            (2019, 50.0, 1_000),
            (2020, 60.0, 2_000),
        ]


class TestImportPreviewLayout:
    def _rendered(self, slot: str) -> str:
        plan = plan_import(_bundle(), _local(), operator="you")
        mock_st = MagicMock()
        with patch.object(_import_preview, "st", mock_st):
            _import_preview._render_slot_details(plan.slots[slot], show_freshness=True)
        return mock_st.markdown.call_args_list[0].args[0]

    def test_you_slot_lists_settings_and_ytd(self) -> None:
        text = self._rendered("you")
        for key in ("your_age", "your_ira", "wages_ytd", "ira_conversions_ytd"):
            assert f"`{key}`" in text

    def test_sections_are_separate_paragraphs(self) -> None:
        """A heading right after a list item is a lazy continuation of that
        item; it rendered as "300,000 Accounts: 1 incoming"."""
        for slot in ("you", "spouse"):
            lines = self._rendered(slot).split("\n")
            for prev, line in zip(lines, lines[1:], strict=False):
                if line.startswith("**"):
                    assert prev == "", f"{slot}: heading {line!r} follows {prev!r}"


class TestGrantComparisonSource:
    def _grant(self) -> EquityGrant:
        return EquityGrant(
            grant_id="SYN-1",
            grant_type="NQO",
            grant_date="2019-02-01",
            shares_granted=1_000,
            outstanding=1_000,
            current_value=10_000.0,
        )

    def test_bundle_grants_are_not_labelled_finextract(self) -> None:
        snap = PortfolioSnapshot(equity_grants=[self._grant()], imported_from_bundle=True)
        assert portfolio_view.grant_source_label(snap) == "Backup import"
        mock_st = MagicMock()
        with patch.object(portfolio_view, "st", mock_st):
            portfolio_view.render_grant_comparison(snap, Household())
        df = mock_st.dataframe.call_args.args[0]
        sources = set(df["Source"])
        assert "FinExtract" not in sources
        assert "Backup import" in sources

    def test_live_sync_still_says_finextract(self) -> None:
        snap = PortfolioSnapshot(equity_grants=[self._grant()], server_available=True)
        assert portfolio_view.grant_source_label(snap) == "FinExtract"
