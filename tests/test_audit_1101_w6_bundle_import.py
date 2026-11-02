"""audit-1101 W6: data-bridge import and legacy-defaults fixes.

- F-08: a v5 import merged joint (household) brokerage slots by their opaque
  storage key, so the same account and tax year could sit under two keys
  (double-counted) while another year's statement was overwritten.
- F-09: accounts from a legacy v4 bundle were not marked bundle-sourced, so the
  receiver's next FinExtract sync closed them.
- F-12: in demo mode an import's grants and MAGI never reached the household
  and could not be confirmed: every read and write went to the null store.
  Owner decision D2: in demo mode an import applies to THIS session only and
  writes nothing to disk.
- F-14: a v5 import skipped the living-expenses sanitizer the v4 path applies.
- F-20: the first autosave hid a legacy ``.user_defaults.py`` behind a new
  ``.user_defaults.json`` and dropped its grant/strike keys.

All values are synthetic.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import config.loader as loader
import engine.demo_mode as demo_mod
import engine.store as store_pkg
from config.scalar_registry import scalar_default
from engine.bridge_bundle import build_bundle, build_bundle_v5
from engine.bridge_import import LocalState, _apply_ledger, apply_import, plan_import
from engine.brokerage_statement_pdf import BrokerageStatementRecord
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed, save_committed
from engine.data_sources.confirm import confirm_field
from engine.data_sources.record import record_magi_candidates
from engine.pdf_ledger import derive_brokerage_totals, derive_koinly_totals
from engine.portfolio_sync.shapes import AccountSummary, PortfolioSnapshot
from engine.store.backend import DictBackend
from engine.store.core import Store
from models.sourced import Source

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
NOW = "2026-07-01T00:00:00+00:00"


# --------------------------------------------------------------------------- F-08


def _stmt(period_end: str, interest: float, account: str = "X100") -> dict:
    return BrokerageStatementRecord(
        account_number=account,
        broker="synthbroker",
        account_type="taxable",
        statement_period_end=period_end,
        interest_taxable_ytd=interest,
        interest_tax_exempt_ytd=0.0,
        dividends_taxable_ytd=0.0,
        dividends_tax_exempt_ytd=0.0,
        stcg_net_ytd=0.0,
        ltcg_net_ytd=0.0,
        captured_at=NOW,
    ).to_dict()


def _bundle_with_ledger(ledger: dict) -> SimpleNamespace:
    return SimpleNamespace(records=SimpleNamespace(ledger=ledger))


def _koinly(year: int, stcg: float) -> dict:
    return {
        "tax_year": year,
        "stcg": stcg,
        "ltcg": 0.0,
        "income": 0.0,
        "interest": 0.0,
        "captured_at": NOW,
        "source": "koinly",
        "parser_version": "1",
        "provenance": {"pages": year},
        "owner_key": None,
    }


class TestF08HouseholdLedgerMergesBySlotIdentity:
    def test_newer_statement_replaces_its_year_and_other_years_survive(self):
        local = {
            "koinly": {},
            "brokerage": {
                "household": {
                    "X100": _stmt("2025-12-31", 900.0),
                    "X100@2026": _stmt("2026-03-31", 100.0),
                }
            },
        }
        incoming = {"koinly": {}, "brokerage": {"household": {"X100": _stmt("2026-06-30", 300.0)}}}

        out = _apply_ledger(local, _bundle_with_ledger(incoming), frozenset({"you"}), True)

        assert derive_brokerage_totals(out, 2026)["interest_ytd"] == 300.0
        assert derive_brokerage_totals(out, 2025)["interest_ytd"] == 900.0

    def test_same_year_under_a_different_key_is_not_kept_twice(self):
        # The receiver scanned 2026 first, so its 2025 slot got the suffix; the
        # sender scanned 2025 first. Same account and years, mirrored keys.
        local = {
            "koinly": {},
            "brokerage": {
                "household": {
                    "X100": _stmt("2026-03-31", 100.0),
                    "X100@2025": _stmt("2025-12-31", 900.0),
                }
            },
        }
        incoming = {
            "koinly": {},
            "brokerage": {
                "household": {
                    "X100": _stmt("2025-12-31", 900.0),
                    "X100@2026": _stmt("2026-06-30", 300.0),
                }
            },
        }

        out = _apply_ledger(local, _bundle_with_ledger(incoming), frozenset({"you"}), True)

        assert derive_brokerage_totals(out, 2026)["interest_ytd"] == 300.0
        assert derive_brokerage_totals(out, 2025)["interest_ytd"] == 900.0

    def test_an_older_incoming_statement_does_not_replace_a_newer_one(self):
        local = {"koinly": {}, "brokerage": {"household": {"X100": _stmt("2026-06-30", 300.0)}}}
        incoming = {"koinly": {}, "brokerage": {"household": {"X100": _stmt("2026-03-31", 100.0)}}}

        out = _apply_ledger(local, _bundle_with_ledger(incoming), frozenset({"you"}), True)

        assert derive_brokerage_totals(out, 2026)["interest_ytd"] == 300.0

    def test_household_koinly_merges_per_tax_year(self):
        local = {"koinly": {"household": {"2025": _koinly(2025, 11.0)}}, "brokerage": {}}
        incoming = {"koinly": {"household": {"2026": _koinly(2026, 22.0)}}, "brokerage": {}}

        out = _apply_ledger(local, _bundle_with_ledger(incoming), frozenset({"you"}), True)

        assert derive_koinly_totals(out, 2025)["stcg"] == 11.0
        assert derive_koinly_totals(out, 2026)["stcg"] == 22.0


# --------------------------------------------------------------------------- F-09


def _apply(bundle, local, *, operator="you", **plan_kwargs):
    plan = plan_import(bundle, local, operator=operator, **plan_kwargs)
    return apply_import(bundle, plan, local, selected=frozenset(plan.slots), operator=operator)


class TestF09V4AccountsAreMarkedImported:
    @pytest.mark.parametrize("sender_source", ["finextract", ""])
    def test_v4_accounts_carry_the_bundle_mark(self, sender_source):
        theirs = AccountSummary("trad_ira", "you", "TheirIRA", total_value=10.0)
        theirs.as_of_source = sender_source
        bundle = build_bundle(
            {"your_age": 55, "filing_status": "MFJ", "grant_strikes": {}},
            PortfolioSnapshot(accounts=[theirs]),
            {"koinly": {}, "brokerage": {}},
            owner="you",
        )
        mine = AccountSummary("trad_ira", "you", "MyIRA", total_value=20.0)
        mine.as_of_source = "finextract"
        local = LocalState(snapshot=PortfolioSnapshot(accounts=[mine]))

        result = _apply(json.loads(json.dumps(bundle)), local, v4_target="spouse")

        sources = {a.account_name: (a.owner, a.as_of_source) for a in result.snapshot.accounts}
        assert sources == {"MyIRA": ("you", "finextract"), "TheirIRA": ("spouse", "bundle")}

    def test_a_statement_mark_is_kept(self):
        theirs = AccountSummary("trad_ira", "you", "TheirIRA", total_value=10.0)
        theirs.as_of_source = "statement"
        bundle = build_bundle(
            {"your_age": 55, "grant_strikes": {}},
            PortfolioSnapshot(accounts=[theirs]),
            {"koinly": {}, "brokerage": {}},
            owner="you",
        )

        result = _apply(json.loads(json.dumps(bundle)), LocalState(), v4_target="spouse")

        assert [a.as_of_source for a in result.snapshot.accounts] == ["statement"]


# --------------------------------------------------------------------------- F-14


def _spouse_bundle_with_expenses(value: float) -> dict:
    return build_bundle_v5(
        setup_scalars={"spouse_age": 55, "filing_status": "MFJ", "living_expenses": value},
        snapshot=PortfolioSnapshot(accounts=[AccountSummary("trad_ira", "spouse", "S")]),
        ledger={"koinly": {}, "brokerage": {}},
        ytd=None,
        grants=[],
        exporter="spouse",
        slot_captured_at={},
        now=NOW,
    )


class TestF14V5ImportSanitizesLivingExpenses:
    def test_negative_expenses_clamp_to_zero(self):
        result = _apply(_spouse_bundle_with_expenses(-50_000.0), LocalState())
        assert result.scalar_updates["living_expenses"] == 0

    @pytest.mark.parametrize("bad", [math.nan, math.inf])
    def test_non_finite_expenses_fall_back_to_the_default(self, bad):
        result = _apply(_spouse_bundle_with_expenses(bad), LocalState())
        assert result.scalar_updates["living_expenses"] == scalar_default("living_expenses")

    def test_a_valid_value_passes_through(self):
        result = _apply(_spouse_bundle_with_expenses(72_000.0), LocalState())
        assert result.scalar_updates["living_expenses"] == 72_000.0


# --------------------------------------------------------------------------- F-12


@pytest.fixture
def demo_session_store(store):
    """Demo mode with a per-session in-memory store installed, as app.py does."""
    session = Store(DictBackend())
    store_pkg.set_demo_store_provider(lambda: session)
    demo_mod.enter_demo_mode()
    yield session
    demo_mod.leave_demo_mode()
    store_pkg.set_demo_store_provider(None)


def _rows(store: Store) -> dict:
    from engine.store.legacy_files import registered

    return {
        (e.namespace, k): store.backend.get(e.namespace, k)
        for e in registered()
        for k in store.backend.keys(e.namespace)
    }


class TestF12DemoImportIsSessionOnly:
    def test_demo_reads_and_writes_go_to_the_session_store(self, demo_session_store, store):
        assert store_pkg.get_store() is demo_session_store
        before = _rows(store)

        assert record_magi_candidates(
            {2024: 123_456.0}, Source.BUNDLE, "bundle", datetime(2026, 3, 1)
        )

        assert CandidateStore.load().candidates_for("prior_year_magi.2024")
        assert _rows(store) == before, "demo mode must not write the personal store"

    def test_a_bundle_magi_can_be_confirmed_for_the_session(self, demo_session_store, store):
        record_magi_candidates({2024: 123_456.0}, Source.BUNDLE, "bundle", datetime(2026, 3, 1))
        choices = ChoiceMap.load()
        committed = confirm_field(
            {}, choices, "prior_year_magi.2024", 123_456.0, Source.BUNDLE, datetime(2026, 3, 2)
        )
        save_committed(committed)
        choices.save()

        assert load_committed()["prior_year_magi"]["data"] == {"2024": 123_456.0}
        assert ChoiceMap.load().get("prior_year_magi.2024") is not None
        demo_mod.leave_demo_mode()
        assert load_committed() is None, "nothing reached the personal store"

    def test_without_a_session_store_demo_mode_stays_a_null_store(self, store):
        demo_mod.enter_demo_mode()
        assert record_magi_candidates({2024: 1.0}, Source.BUNDLE, "bundle", datetime(2026, 3, 1))
        assert not CandidateStore.load().candidates_for("prior_year_magi.2024")

    def test_app_keeps_a_session_store_and_surfaces_an_imported_magi(self, store):
        demo_mod.enter_demo_mode()
        at = AppTest.from_file(str(APP_PATH), default_timeout=60)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.run()
        assert not at.exception
        from views.setup._state import DEMO_STORE_KEY

        session = at.session_state[DEMO_STORE_KEY]
        assert isinstance(session, Store)
        before = _rows(store)

        # What the import does inside the session: record the bundle MAGI.
        store_pkg.set_demo_store_provider(lambda: session)
        record_magi_candidates({2024: 123_456.0}, Source.BUNDLE, "bundle", datetime(2026, 3, 1))
        at.run()

        assert not at.exception
        assert "prior_year_magi.2024" in set(at.session_state["_pending_review"])
        assert _rows(store) == before


# --------------------------------------------------------------------------- F-20


@pytest.fixture
def legacy_py_defaults(monkeypatch):
    """A trusted ``.user_defaults.py`` as the only override file, in the cwd."""
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    monkeypatch.delenv("ROTH_PLANNER_DEFAULTS", raising=False)
    folder = loader._USER_DEFAULTS_PATH.parent
    monkeypatch.chdir(folder)
    py = folder / ".user_defaults.py"
    py.write_text(
        "OVERRIDES = {'your_age': 70, 'grant_strikes': {'2020': 11.0}, 'employer_name': 'Synth'}\n"
    )
    py.chmod(0o600)
    return py


class TestF20LegacyPyOverridesSurviveTheFirstAutosave:
    def test_py_keys_survive_the_first_autosave(self, legacy_py_defaults):
        assert loader.load_defaults()["grant_strikes"] == {"2020": 11.0}

        loader.save_user_defaults({"your_age": 71})

        after = loader.load_defaults()
        assert after["your_age"] == 71, "the autosaved value wins"
        assert after["grant_strikes"] == {"2020": 11.0}
        assert after["employer_name"] == "Synth"
        assert loader.load_raw_user_defaults()["grant_strikes"] == {"2020": 11.0}

    def test_the_json_file_wins_key_by_key(self, legacy_py_defaults):
        loader._USER_DEFAULTS_PATH.write_text(json.dumps({"grant_strikes": {"2020": 12.0}}))
        assert loader.load_defaults()["grant_strikes"] == {"2020": 12.0}
        assert loader.load_defaults()["employer_name"] == "Synth"
