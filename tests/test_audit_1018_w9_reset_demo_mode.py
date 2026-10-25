"""audit-1018 W9 (F-25, F-28, F-29, F-30, F-31, F-34): "Reset to demo" parks personal data.

Owner decision D2: Reset to demo sets a DEMO-MODE marker (engine/demo_mode.py).
While it exists, personal on-disk caches are neither read nor written, export
carries demo data only, queued statements are cleared, ``terminal_ira_tax_rate``
resets, and the personal files stay on disk untouched until the user leaves demo
mode. F-34 rides along: the employer ticker becomes a registry scalar so it is
exported/imported.

All data is synthetic and every path is redirected to ``tmp_path`` by
``tests/conftest.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import engine.data_sources.paths as paths_mod
import engine.demo_mode as demo_mod
import views.setup._state as state_mod
from config import loader
from config.defaults import DEFAULTS
from config.scalar_registry import (
    SCALAR_REGISTRY,
    scalar_default,
    scalar_default_by_config_key,
    session_key_for_config_key,
)
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.resolver import magi_field_key
from engine.portfolio_sync import (
    AccountSummary,
    PortfolioSnapshot,
    save_snapshot,
    save_ssa_snapshot,
    save_ytd_snapshot,
)
from engine.portfolio_sync.shapes import SSABenefitEstimate, SSASnapshot
from engine.tax_return_pdf import Form1040Record, save_pdf_tax_records
from engine.upload_merge import JOINT_FIELDS, SCALAR_KEYS, build_user_defaults_session_updates
from models.ytd_income import YTDSnapshot

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
_PAGE_TIMEOUT = 60


@pytest.fixture
def session(monkeypatch):
    """A plain-dict ``st.session_state`` for the state helpers."""

    def make(seed: dict | None = None) -> dict:
        fake = dict(seed or {})
        monkeypatch.setattr(state_mod.st, "session_state", fake)
        return fake

    return make


# --- the marker itself -------------------------------------------------------


class TestDemoModeMarker:
    def test_enter_and_leave_round_trip(self):
        assert not demo_mod.is_demo_mode()
        demo_mod.enter_demo_mode()
        assert demo_mod.is_demo_mode()
        demo_mod.leave_demo_mode()
        assert not demo_mod.is_demo_mode()

    def test_leave_when_absent_is_a_noop(self):
        demo_mod.leave_demo_mode()
        assert not demo_mod.is_demo_mode()


# --- F-25: queued statements and digests ---------------------------------------


class TestF25QueuedStatementsCleared:
    @pytest.mark.parametrize(
        "key",
        ["ssa_statements", "netbenefits_statements", "ira_statements", "_pdf_scanned_digests"],
    )
    def test_queued_key_cleared(self, session, key):
        fake = session({key: ["SYNTH-QUEUED"]})
        state_mod._clear_personal_session_state()
        assert key not in fake, f"{key!r} survived Reset to demo"

    def test_uploader_nonce_bumped_so_a_reupload_rescans(self, session):
        fake = session({"pdf_upload_nonce": 3, "_pdf_scanned_digests": {"deadbeef"}})
        state_mod._clear_personal_session_state()
        assert fake["pdf_upload_nonce"] == 4


# --- F-29: terminal_ira_tax_rate -------------------------------------------------


class TestF29TerminalRateReset:
    def test_terminal_rate_cleared_and_reseeds_to_registry_default(self, session):
        fake = session({"terminal_ira_tax_rate": 37.0})
        state_mod._clear_personal_session_state()
        assert "terminal_ira_tax_rate" not in fake
        # app.py's registry seed loop then restores the canonical default.
        assert scalar_default("terminal_ira_tax_rate") == 24.0


# --- F-28: reset enters demo mode; personal files untouched ----------------------


class TestF28ResetEntersDemoMode:
    def test_reset_sets_marker(self, session):
        session({})
        assert not demo_mod.is_demo_mode()
        state_mod._clear_personal_session_state()
        assert demo_mod.is_demo_mode()

    def test_reset_does_not_delete_personal_user_defaults(self, session, monkeypatch):
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        personal = loader._USER_DEFAULTS_PATH
        personal.write_text(json.dumps({"your_age": 71}))
        session({})
        state_mod._clear_personal_session_state()
        assert json.loads(personal.read_text()) == {"your_age": 71}

    def test_demo_mode_hides_and_never_writes_user_defaults(self, monkeypatch):
        monkeypatch.delenv("ROTH_PLANNER_IGNORE_USER_DEFAULTS", raising=False)
        personal = loader._USER_DEFAULTS_PATH
        personal.write_text(json.dumps({"your_age": 71}))
        before = personal.read_bytes()

        demo_mod.enter_demo_mode()
        assert loader.load_defaults()["your_age"] == DEFAULTS["your_age"]
        assert loader.load_raw_user_defaults() == {}
        loader.save_user_defaults({"your_age": 99})
        assert personal.read_bytes() == before

        demo_mod.leave_demo_mode()
        assert loader.load_defaults()["your_age"] == 71

    def test_exit_demo_session_brings_personal_state_back(self, session):
        fake = session({"your_ira": 1})
        state_mod._clear_personal_session_state()
        fake["your_ira"] = 500_000  # the demo reseed
        fake["_seeded"] = True
        state_mod.exit_demo_mode()
        assert not demo_mod.is_demo_mode()
        assert "_suppress_snapshot_autoload" not in fake
        assert "_seeded" not in fake
        assert "your_ira" not in fake  # reseeds from the (now visible) personal defaults


def _app(*, suppress: bool = True) -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=_PAGE_TIMEOUT)
    if suppress:
        at.session_state["_suppress_snapshot_autoload"] = True
    return at


def _cache_state() -> dict[str, bytes | None]:
    return {
        p.name: (p.read_bytes() if p.exists() else None)
        for p in (
            paths_mod.COMMITTED_PATH,
            paths_mod.CANDIDATE_STORE_PATH,
            paths_mod.TRUST_CHOICES_PATH,
        )
    }


class TestF28RenderDoesNotTouchPersonalBaseline:
    def test_demo_render_neither_reads_nor_overwrites_committed_baseline(self):
        personal_ira = 123_456
        at = _app()
        at.session_state["your_ira"] = personal_ira
        at.run()
        assert not at.exception
        before = _cache_state()
        assert before[paths_mod.COMMITTED_PATH.name] is not None

        demo_mod.enter_demo_mode()
        at2 = _app()
        at2.run()

        assert not at2.exception
        assert _cache_state() == before, "demo render rewrote a personal cache file"
        assert at2.session_state["your_ira"] == DEFAULTS["your_ira"], (
            "the personal committed baseline was applied inside demo mode"
        )

    def test_leaving_demo_mode_finds_the_personal_baseline_intact(self):
        personal_ira = 123_456
        at = _app()
        at.session_state["your_ira"] = personal_ira
        at.run()
        before = _cache_state()

        demo_mod.enter_demo_mode()
        demo_at = _app()
        demo_at.run()
        demo_mod.leave_demo_mode()

        # The session a restored user has (personal defaults reseeded): nothing
        # from the demo detour changed the committed baseline on disk.
        back = _app()
        back.session_state["your_ira"] = personal_ira
        back.run()
        assert not back.exception
        assert _cache_state() == before
        assert back.session_state["your_ira"] == personal_ira


# --- F-30: disk autoload gated ------------------------------------------------------


def _save_personal_caches() -> None:
    save_ssa_snapshot(
        SSASnapshot(
            estimates=[SSABenefitEstimate(67, "2034-01-01", "retirement", 1_234.0)],
            server_available=True,
        ),
        owner="you",
    )
    save_ssa_snapshot(
        SSASnapshot(
            estimates=[SSABenefitEstimate(67, "2036-01-01", "retirement", 987.0)],
            server_available=True,
        ),
        owner="spouse",
    )
    save_pdf_tax_records(
        {
            2024: Form1040Record(
                tax_year=2024,
                agi=111_111.0,
                tax_exempt_interest=0.0,
                taxable_ss=0.0,
                qualified_dividends=0.0,
                ordinary_dividends=0.0,
                feie=0.0,
                magi=111_111.0,
                filing_status=None,
                captured_at="2026-01-01T00:00:00",
            )
        }
    )
    save_snapshot(
        PortfolioSnapshot(
            accounts=[
                AccountSummary(account_type="trad_ira", owner="you", total_value=2_000_000.0)
            ],
            server_available=True,
        )
    )
    save_ytd_snapshot(YTDSnapshot(wages_ytd=250_000.0))


class TestF30AutoloadGatedByDemoMode:
    def test_control_personal_caches_load_outside_demo_mode(self):
        _save_personal_caches()
        at = _app(suppress=False)
        at.run()
        assert not at.exception
        assert "ssa_snapshot_you" in at.session_state
        assert "ssa_snapshot_spouse" in at.session_state
        assert "portfolio_snapshot" in at.session_state
        assert CandidateStore.load(paths_mod.CANDIDATE_STORE_PATH).candidates_for(
            magi_field_key(2024)
        )

    def test_demo_mode_skips_every_personal_autoload(self):
        _save_personal_caches()
        demo_mod.enter_demo_mode()
        at = _app(suppress=False)
        at.run()
        assert not at.exception
        for key in (
            "ssa_snapshot_you",
            "ssa_snapshot_spouse",
            "portfolio_snapshot",
            "ytd_snapshot",
        ):
            assert key not in at.session_state, f"{key} autoloaded inside demo mode"
        assert not CandidateStore.load(paths_mod.CANDIDATE_STORE_PATH).candidates_for(
            magi_field_key(2024)
        ), "1040 MAGI from the disk cache was recorded inside demo mode"


# --- F-31: export carries demo data only ---------------------------------------------


class TestF31ExportInputsAreDemoOnly:
    def test_demo_mode_ignores_on_disk_portfolio_ledger_and_ytd(self, session, monkeypatch):
        import views.setup.data_bridge as bridge

        save_snapshot(
            PortfolioSnapshot(
                accounts=[
                    AccountSummary(account_type="trad_ira", owner="you", total_value=2_000_000.0)
                ],
                server_available=True,
            )
        )
        save_ytd_snapshot(YTDSnapshot(wages_ytd=250_000.0))
        monkeypatch.setattr(bridge, "_load_pdf_ledger", lambda: {"brokerage": {"you": {"A": 1}}})
        monkeypatch.setattr(bridge.st, "session_state", {})

        snapshot, ledger, ytd = bridge._held_portfolio_state()
        # control: personal state is visible outside demo mode
        assert snapshot is not None
        assert ledger
        assert ytd is not None

        demo_mod.enter_demo_mode()
        snapshot, ledger, ytd = bridge._held_portfolio_state()
        assert snapshot is None
        assert not ledger
        assert ytd is None

    def test_demo_mode_still_exports_in_session_state(self, monkeypatch):
        import views.setup.data_bridge as bridge

        in_session = PortfolioSnapshot(
            accounts=[AccountSummary(account_type="roth_ira", owner="you", total_value=10.0)]
        )
        monkeypatch.setattr(
            bridge.st,
            "session_state",
            {"portfolio_snapshot": in_session, "ytd_snapshot": YTDSnapshot(wages_ytd=5.0)},
        )
        demo_mod.enter_demo_mode()
        snapshot, _ledger, ytd = bridge._held_portfolio_state()
        assert snapshot is in_session
        assert ytd is not None
        assert ytd.wages_ytd == 5.0


# --- F-34: employer ticker is a registry scalar --------------------------------------


class TestF34StockTickerIsARegistryScalar:
    def test_registered_joint_scalar_with_demo_default(self):
        assert "stock_ticker" in SCALAR_KEYS
        assert "stock_ticker" in JOINT_FIELDS
        assert session_key_for_config_key("stock_ticker") == "_stock_ticker"
        assert session_key_for_config_key("stock_price_now") == "stock_price"
        assert session_key_for_config_key("your_age") == "your_age"
        assert scalar_default_by_config_key("stock_ticker") == DEFAULTS["stock_ticker"] == "ACME"
        assert any(s.session_key == "_stock_ticker" for s in SCALAR_REGISTRY)

    def test_exported_from_session(self, session):
        session({"_stock_ticker": "XYZ"})
        assert state_mod._user_defaults_from_session()["stock_ticker"] == "XYZ"

    def test_travels_in_the_v5_bundle_joint_section(self):
        from engine.bridge_bundle import build_bundle_v5

        bundle = build_bundle_v5(
            setup_scalars={"stock_ticker": "XYZ"},
            snapshot=None,
            ledger=None,
            ytd=None,
            grants=None,
            exporter="you",
            slot_captured_at=None,
            now="2026-01-01T00:00:00",
        )
        assert bundle["joint"]["stock_ticker"] == "XYZ"

    def test_import_lands_on_the_session_key(self):
        updates = build_user_defaults_session_updates({"stock_ticker": "XYZ"}, as_spouse=False)
        assert updates["_stock_ticker"] == "XYZ"
        assert "stock_ticker" not in updates
        spouse = build_user_defaults_session_updates(
            {"stock_ticker": "XYZ"}, as_spouse=True, receiver_persisted={}
        )
        assert spouse["_stock_ticker"] == "XYZ"
