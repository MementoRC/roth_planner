"""Holdings' "vs. Planner Defaults" table names where confirmed grants came from.

A grant confirmed from a backup import (or a statement) used to read
"Planner Default", the same as an untouched built-in grant. Driven by the
synthetic bundle in tests/fixtures/synthetic/. Invented figures only.
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
from models.grants import StockGrant
from models.household import Household
from models.sourced import Provenance, Source, SourcedList
from views import portfolio as portfolio_view

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"
FIXED_DT = datetime(2026, 10, 10, 12, 0, 0)


def _imported():
    bundle = json.loads((FIXTURES / "synthetic_backup_v5.json").read_text())
    local = LocalState(scalars={"your_ira": 500_000, "your_age": 61})
    plan = plan_import(bundle, local, operator="you")
    return apply_import(
        bundle,
        plan,
        local,
        selected=frozenset({"you", "spouse"}),
        operator="you",
        apply_v4_bundle=None,  # type: ignore[arg-type]
    )


def _planner_sources(snap, hh: Household) -> list[str]:
    mock_st = MagicMock()
    with patch.object(portfolio_view, "st", mock_st):
        portfolio_view.render_grant_comparison(snap, hh)
    df = mock_st.dataframe.call_args.args[0]
    return [s for s in df["Source"] if s.startswith("Planner")]


class TestConfirmedBundleGrants:
    def test_confirmed_bundle_grants_say_from_backup(self) -> None:
        result = _imported()
        strikes = result.scalar_updates["_user_grant_strikes"]
        store = CandidateStore()
        outcome = resolve_for_app(
            Household(), result.snapshot, strikes, store, ChoiceMap(), None, FIXED_DT
        )
        confirmed = confirm(
            GRANTS_KEY, Source.BUNDLE, outcome.result.household, store, ChoiceMap(), FIXED_DT
        )
        sources = _planner_sources(result.snapshot, confirmed)
        assert sources
        assert set(sources) == {"Planner (from backup)"}

    def test_unconfirmed_import_keeps_planner_default(self) -> None:
        """Before the confirm, hh.grants is still the migrated built-in list."""
        result = _imported()
        strikes = result.scalar_updates["_user_grant_strikes"]
        outcome = resolve_for_app(
            Household(), result.snapshot, strikes, CandidateStore(), ChoiceMap(), None, FIXED_DT
        )
        sources = _planner_sources(result.snapshot, outcome.result.household)
        assert sources
        assert set(sources) == {"Planner Default"}


class TestPlannerGrantLabel:
    def _grants(self, source: Source) -> SourcedList:
        grant = StockGrant(year=2019, strike=50.0, shares=1_000, expiry_year=2029)
        return SourcedList([grant], [Provenance(source, FIXED_DT)])

    def test_statement_source(self) -> None:
        grants = self._grants(Source.PDF)
        assert portfolio_view.planner_grant_label(grants, grants[0]) == "Planner (from statement)"

    def test_default_and_unknown_stay_planner_default(self) -> None:
        for source in (Source.DEFAULT, Source.UNKNOWN):
            grants = self._grants(source)
            assert portfolio_view.planner_grant_label(grants, grants[0]) == "Planner Default"

    def test_plain_list_and_missing_plan(self) -> None:
        grants = self._grants(Source.BUNDLE)
        assert portfolio_view.planner_grant_label(list(grants), grants[0]) == "Planner Default"
        assert portfolio_view.planner_grant_label(grants, None) == "Planner Default"
