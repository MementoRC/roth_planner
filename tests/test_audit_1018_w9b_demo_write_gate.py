"""audit-1018 W9b: demo mode never touches personal files (owner decision D2).

W9 made the app skip the disk autoload and the committed-baseline write in demo
mode, but the low-level persisters still wrote (and read) the personal caches.
W9b gates them at the choke points: ``engine.secure_io.write_pii_json`` /
``read_pii_json`` / ``read_pii_bytes`` plus the stores that do not go through them
(committed baseline, candidate store, trust choices, instance owner, account
attribution, the exercise-schedule delete, the dividend-rate overrides).

Every file under test lives in tmp_path (tests/conftest.py redirects the cache
constants) and all values are synthetic.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import config.loader as loader
import engine.account_attribution as attribution_mod
import engine.account_link as link_mod
import engine.brokerage_statement_pdf as brokerage_mod
import engine.data_sources.paths as paths_mod
import engine.demo_mode as demo_mod
import engine.dividend_forecast as dividend_mod
import engine.exercise_schedule_store as exercise_store_mod
import engine.instance_identity as identity_mod
import engine.pdf_ledger as ledger_mod
import engine.pdf_owner as owner_mod
import engine.portfolio_sync.social_security as ssa_mod
import engine.portfolio_sync.ytd as ytd_mod
import engine.tax_return_pdf as tax_pdf_mod
import engine.ubs_activity_store as ubs_mod
from engine.account_attribution import (
    delete_account_override,
    load_account_overrides,
    save_account_override,
)
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import load_committed, save_committed
from engine.data_sources.record import record_ira_balance_candidate, record_stock_quote_candidate
from engine.exercise_schedule_store import clear_exercise_schedule
from engine.instance_identity import load_instance_owner, save_instance_owner
from engine.pdf_ledger import load_ledger, save_ledger
from engine.portfolio_sync import (
    AccountSummary,
    PortfolioSnapshot,
    load_snapshot,
    load_ytd_snapshot,
    save_snapshot,
    save_ssa_snapshot,
    save_ytd_snapshot,
)
from engine.portfolio_sync.shapes import SSABenefitEstimate, SSASnapshot
from engine.secure_io import read_pii_bytes, read_pii_json, write_pii_json
from engine.store.core import Store
from engine.store.legacy_files import registered
from engine.tax_return_pdf import Form1040Record, save_pdf_tax_records
from models.ytd_income import YTDSnapshot

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _personal_paths() -> dict[str, Path]:
    """Every personal on-disk file the app persists (the redirected tmp copies)."""
    return {
        "committed": paths_mod.COMMITTED_PATH,
        "candidates": paths_mod.CANDIDATE_STORE_PATH,
        "choices": paths_mod.TRUST_CHOICES_PATH,
        "user_defaults": loader._USER_DEFAULTS_PATH,
        "instance_owner": identity_mod.INSTANCE_OWNER_PATH,
        "attribution": attribution_mod._ACCOUNT_ATTRIBUTION_PATH,
        "ytd": ytd_mod._YTD_CACHE_PATH,
        "ssa": ssa_mod._SSA_CACHE_PATH,
        "tax_pdf": tax_pdf_mod._PDF_TAX_CACHE_PATH,
        "ledger": ledger_mod._LEDGER_PATH,
        "owner_map": owner_mod._OWNER_MAP_PATH,
        "link_map": link_mod._ACCOUNT_LINK_PATH,
        "statements": brokerage_mod._STATEMENT_CACHE_PATH,
        "statement_folder": brokerage_mod._FOLDER_CONFIG_PATH,
        "type_overrides": brokerage_mod._ACCOUNT_TYPE_OVERRIDES_PATH,
        "ubs": ubs_mod._UBS_ACTIVITY_CACHE_PATH,
        "exercise_schedule": exercise_store_mod._EXERCISE_SCHEDULE_CACHE_PATH,
    }


# The database namespaces personal_files fills (checked once each store has moved).
_FILLED_NAMESPACES = frozenset(
    {
        "portfolio",
        "ytd",
        "ssa",
        "tax_pdf",
        "pdf_ledger",
        "brokerage_statements",
        "statement_folder",
        "statement_account_overrides",
        "ubs_activity",
        "exercise_schedule",
    }
)


def _persisted(store: Store) -> dict[str, object]:
    """Everything the app keeps: file-backed stores' bytes and every database row."""
    files = {n: (p.read_bytes() if p.exists() else None) for n, p in _personal_paths().items()}
    rows = {
        (entry.namespace, key): store.backend.get(entry.namespace, key)
        for entry in registered()
        for key in store.backend.keys(entry.namespace)
    }
    return {"files": files, "rows": rows}


def _snapshot(total: float) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[AccountSummary(account_type="trad_ira", owner="you", total_value=total)],
        server_available=True,
    )


def _form_1040(agi: float) -> dict[int, Form1040Record]:
    return {
        2024: Form1040Record(
            tax_year=2024,
            agi=agi,
            tax_exempt_interest=0.0,
            taxable_ss=0.0,
            qualified_dividends=0.0,
            ordinary_dividends=0.0,
            feie=0.0,
            magi=agi,
            filing_status=None,
            captured_at="2026-01-01T00:00:00",
        )
    }


def _ssa(amount: float) -> SSASnapshot:
    return SSASnapshot(
        estimates=[SSABenefitEstimate(67, "2034-01-01", "retirement", amount)],
        server_available=True,
    )


@pytest.fixture
def personal_files(monkeypatch, store):
    """Fill every personal file with synthetic content via the REAL savers."""
    monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
    save_committed(paths_mod.COMMITTED_PATH, {})
    CandidateStore().save(paths_mod.CANDIDATE_STORE_PATH)
    ChoiceMap().save(paths_mod.TRUST_CHOICES_PATH)
    loader._USER_DEFAULTS_PATH.write_text(json.dumps({"your_age": 71}))
    save_instance_owner("you")
    save_account_override("synthbroker", "ACCT-1", "spouse")
    save_snapshot(_snapshot(2_000_000.0))
    save_ytd_snapshot(YTDSnapshot(wages_ytd=250_000.0))
    save_ssa_snapshot(_ssa(1_234.0), owner="you")
    save_pdf_tax_records(_form_1040(111_111.0))
    save_ledger({"koinly": {}, "brokerage": {"you": {"ACCT-1": {"value": 1}}}})
    owner_mod.save_owner_map({"SYNTH NAME": "you"})
    link_mod.save_link_map({})
    brokerage_mod.save_statement_folder_path("/synthetic/folder")
    brokerage_mod.save_account_type_override("ACCT-1", "traditional_ira")
    brokerage_mod.save_statement_records({})
    ubs_mod.save_ubs_orders([])
    write_pii_json(exercise_store_mod._EXERCISE_SCHEDULE_CACHE_PATH, {"version": 1})
    before = _persisted(store)
    assert all(v is not None for v in before["files"].values()), "fixture left a file unwritten"
    on_db = {entry.namespace for entry in registered()} & _FILLED_NAMESPACES
    assert on_db <= {ns for ns, _ in before["rows"]}, "fixture left a store row unwritten"
    return before


def _run_write_paths() -> None:
    """Drive the main persisting paths with DIFFERENT values than the fixture's."""
    now = datetime(2026, 3, 1)
    save_committed(paths_mod.COMMITTED_PATH, {"your_ira": 5})
    cand = CandidateStore()
    cand.save(paths_mod.CANDIDATE_STORE_PATH)
    record_ira_balance_candidate("your_ira", 123_456.0, "synthetic", now)
    record_stock_quote_candidate(12.34)
    ChoiceMap().save(paths_mod.TRUST_CHOICES_PATH)
    loader.save_user_defaults({"your_age": 99})
    save_instance_owner("spouse")
    save_account_override("synthbroker", "ACCT-1", "you")
    save_account_override("synthbroker", "ACCT-2", "you")
    delete_account_override("synthbroker", "ACCT-1")
    save_snapshot(_snapshot(1.0))
    save_ytd_snapshot(YTDSnapshot(wages_ytd=1.0))
    save_ssa_snapshot(_ssa(1.0), owner="spouse")
    save_pdf_tax_records(_form_1040(1.0))
    save_ledger({"koinly": {}, "brokerage": {"spouse": {"ACCT-9": {"value": 9}}}})
    owner_mod.save_owner_map({"OTHER": "spouse"})
    link_mod.save_link_map({})
    brokerage_mod.save_statement_folder_path("/other/folder")
    brokerage_mod.save_account_type_override("ACCT-2", "roth_ira")
    brokerage_mod.save_statement_records({})
    ubs_mod.save_ubs_orders([])
    write_pii_json(exercise_store_mod._EXERCISE_SCHEDULE_CACHE_PATH, {"version": 2})
    clear_exercise_schedule()


class TestDemoModeWritesNothing:
    def test_every_persist_path_leaves_personal_files_byte_identical(self, personal_files, store):
        demo_mod.enter_demo_mode()
        _run_write_paths()
        assert _persisted(store) == personal_files

    def test_confirm_a_value_in_demo_mode_writes_nothing(self, personal_files, store):
        import views._shared as shared

        demo_mod.enter_demo_mode()
        shared._confirm_as_is_and_save("your_ira")
        assert _persisted(store) == personal_files

    def test_personal_files_are_not_read_in_demo_mode(self, personal_files):
        demo_mod.enter_demo_mode()
        assert load_snapshot() is None
        assert load_ytd_snapshot() is None
        assert ssa_mod.load_ssa_snapshot(owner="you") is None
        assert tax_pdf_mod.load_pdf_tax_records() == {}
        assert load_ledger() == {"koinly": {}, "brokerage": {}}
        assert owner_mod.load_owner_map() == {}
        assert link_mod.load_link_map() == {}
        assert brokerage_mod.load_statement_folder_path() is None
        assert brokerage_mod.load_account_type_overrides() == {}
        assert ubs_mod.load_ubs_orders() == []
        assert load_instance_owner() is None
        assert load_account_overrides() == {}
        assert load_committed(paths_mod.COMMITTED_PATH) is None
        assert not CandidateStore.load(paths_mod.CANDIDATE_STORE_PATH).load_corrupt
        assert exercise_store_mod.load_exercise_schedule() is None

    def test_dividend_rate_overrides_are_hidden_and_not_read(self, tmp_path):
        overrides = tmp_path / ".dividend_rates.json"
        overrides.write_text(json.dumps({"SYNTH": {"annual_rate": 1.5}}))
        assert dividend_mod._load_overrides(overrides) == {"SYNTH": {"annual_rate": 1.5}}
        demo_mod.enter_demo_mode()
        assert dividend_mod._load_overrides(overrides) == {}
        demo_mod.leave_demo_mode()
        assert dividend_mod._load_overrides(overrides)

    def test_secure_io_choke_point_is_inert_in_demo_mode(self, tmp_path):
        target = tmp_path / "x.json"
        write_pii_json(target, {"a": 1})
        before = target.read_bytes()
        demo_mod.enter_demo_mode()
        write_pii_json(target, {"a": 2})
        write_pii_json(tmp_path / "new.json", {"a": 3})
        assert target.read_bytes() == before
        assert not (tmp_path / "new.json").exists()
        assert not list(tmp_path.glob("*.tmp-*"))
        with pytest.raises(FileNotFoundError):
            read_pii_json(target)
        with pytest.raises(FileNotFoundError):
            read_pii_bytes(target)

    def test_the_marker_itself_stays_writable(self):
        demo_mod.enter_demo_mode()
        demo_mod.enter_demo_mode()
        assert demo_mod.is_demo_mode()
        demo_mod.leave_demo_mode()
        assert not demo_mod.is_demo_mode()


class TestLeavingDemoModeRestoresPersonalFiles:
    def test_files_and_readers_come_back(self, personal_files, store):
        demo_mod.enter_demo_mode()
        _run_write_paths()
        demo_mod.leave_demo_mode()

        assert _persisted(store) == personal_files
        snap = load_snapshot()
        assert snap is not None
        assert snap.accounts[0].total_value == 2_000_000.0
        ytd = load_ytd_snapshot()
        assert ytd is not None
        assert ytd.wages_ytd == 250_000.0
        assert load_instance_owner() == "you"
        assert load_account_overrides() == {("synthbroker", "ACCT-1"): "spouse"}
        assert load_committed(paths_mod.COMMITTED_PATH) == {}
        assert load_ledger()["brokerage"] == {"you": {"ACCT-1": {"value": 1}}}
        assert loader.load_defaults()["your_age"] == 71
        assert owner_mod.load_owner_map() == {"SYNTH NAME": "you"}


class TestAppRunInDemoModeLeavesPersonalFilesAlone:
    def test_edit_a_field_and_render_every_page_writes_nothing(self, personal_files, store):
        demo_mod.enter_demo_mode()
        at = AppTest.from_file(str(APP_PATH), default_timeout=60)
        at.session_state["_suppress_snapshot_autoload"] = True
        at.run()
        assert not at.exception
        at.session_state["your_ira"] = 777_777
        at.run()
        assert not at.exception
        assert _persisted(store) == personal_files
        assert at.session_state["your_ira"] == 777_777, "session edits must keep working"
