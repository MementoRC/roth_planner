"""Profile / Data pages (redesign PR 3b)."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

import engine.portfolio_sync as portfolio_sync_mod
import engine.tax_return_pdf as tax_return_pdf_mod
import views.setup.data_bridge as data_bridge_mod


@pytest.fixture(autouse=True)
def _isolated_app(monkeypatch):
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)


PAGE_FUNCS = {
    "household": "views.profile_pages:render_household",
    "assumptions": "views.profile_pages:render_assumptions",
    "holdings": "views.profile_pages:render_holdings",
    "import": "views.data_pages:render_import",
    "review": "views.data_pages:render_review",
    "backup": "views.data_pages:render_backup",
}


def _page_script(target: str, with_snapshot: bool = False) -> None:
    import importlib

    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from engine.portfolio_sync import (
        AccountSummary,
        EquityGrant,
        Holding,
        PortfolioSnapshot,
    )
    from models.household import Household

    if with_snapshot and "portfolio_snapshot" not in st.session_state:
        # server_available stays False: a bundle-imported snapshot looks like this.
        st.session_state["portfolio_snapshot"] = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="trad_ira",
                    owner="you",
                    account_name="Rollover",
                    total_value=500_000.0,
                    equity_value=400_000.0,
                    bond_value=100_000.0,
                    holdings=[
                        Holding(
                            symbol="VTI",
                            description="Total Market",
                            quantity=10.0,
                            market_value=400_000.0,
                            account_name="Rollover",
                            asset_class="equity",
                        )
                    ],
                )
            ],
            equity_grants=[
                EquityGrant(
                    grant_id="g1",
                    grant_type="NQO",
                    grant_date="2020-01-01",
                    shares_granted=1000,
                    outstanding=500,
                    current_value=12345.0,
                )
            ],
            stock_shares_held=100,
            stock_shares_value=18_000.0,
        )
    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("stock_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    mod, attr = target.split(":")
    getattr(importlib.import_module(mod), attr)(Household())


def _run(target: str, with_snapshot: bool = False) -> AppTest:
    at = AppTest.from_function(_page_script, args=(target, with_snapshot), default_timeout=30)
    at.run()
    return at


@pytest.mark.parametrize("page_id", list(PAGE_FUNCS))
def test_page_renders_without_exception(page_id):
    at = _run(PAGE_FUNCS[page_id])
    assert not at.exception, [e.value for e in at.exception]


def test_assumptions_hosts_the_stock_price_widget():
    at = _run(PAGE_FUNCS["assumptions"])
    assert "Stock Price" in [s.value for s in at.subheader]


def test_household_has_filing_status_and_accounts():
    at = _run(PAGE_FUNCS["household"])
    subs = [s.value for s in at.subheader]
    assert "Filing status" in subs
    assert subs.count("Me") == 2  # Household + Accounts


def test_holdings_with_snapshot_shows_portfolio_sections():
    at = _run(PAGE_FUNCS["holdings"], with_snapshot=True)
    assert not at.exception, [e.value for e in at.exception]
    assert "Total Portfolio" in [m.label for m in at.metric]
    assert [t.label for t in at.tabs] == ["Me", "Spouse", "All"]  # rendered once
    assert len(at.get("plotly_chart")) == 1
    assert "#### vs. Planner Defaults" in [m.value for m in at.markdown]


def test_holdings_has_exactly_one_grants_table():
    at = _run(PAGE_FUNCS["holdings"], with_snapshot=True)
    grant_tables = [d for d in at.dataframe if "grant_id" in d.value.columns]
    assert len(grant_tables) == 1


def test_holdings_without_snapshot_links_to_import_and_still_renders_sync():
    at = _run(PAGE_FUNCS["holdings"])
    assert not at.exception, [e.value for e in at.exception]
    assert at.button(key="holdings_to_import") is not None
    assert any("No holdings loaded" in i.value for i in at.info)
    # the partial still renders: its FinExtract sync is how a local user loads a snapshot
    assert "Sync from FinExtract" in [b.label for b in at.button]


def test_review_is_command_center():
    at = _run(PAGE_FUNCS["review"])
    assert "🎛️ Command Center" in [h.value for h in at.header]


def test_backup_has_bundle_import_and_reset():
    at = _run(PAGE_FUNCS["backup"])
    assert at.button(key="reset_demo") is not None


def test_import_links_to_manual_entry():
    at = _run(PAGE_FUNCS["import"])
    assert at.button(key="import_to_manual_entry") is not None


# --- Task 4: "Legacy: FinExtract" expanders -------------------------------------


def _legacy(at: AppTest):
    boxes = [e for e in at.expander if e.label == "Legacy: FinExtract"]
    assert len(boxes) == 1, [e.label for e in at.expander]
    return boxes[0]


def _page_keys(block) -> set[str]:
    return {w.key for w in block.button} | {w.key for w in block.selectbox}


def test_household_ssa_sync_buttons_live_in_legacy_expander():
    at = _run(PAGE_FUNCS["household"])
    assert not at.exception, [e.value for e in at.exception]
    legacy = _legacy(at)
    assert {"_sync_ssa_you_btn", "_sync_ssa_spouse_btn"} <= _page_keys(legacy)
    assert legacy.button(key="_sync_ssa_spouse_btn").disabled is False
    # the inline copies are gone: the keys exist exactly once on the page
    assert [b.key for b in at.button].count("_sync_ssa_you_btn") == 1


def test_household_single_filer_disables_spouse_ssa_sync():
    at = _run(PAGE_FUNCS["household"])
    at.radio(key="_hh_filing_status_choice").set_value("Single").run()
    assert not at.exception, [e.value for e in at.exception]
    legacy = _legacy(at)
    assert legacy.button(key="_sync_ssa_spouse_btn").disabled is True
    assert legacy.button(key="_sync_ssa_you_btn").disabled is False


def test_holdings_overrides_live_in_legacy_and_owner_round_trips():
    at = _run(PAGE_FUNCS["holdings"], with_snapshot=True)
    assert not at.exception, [e.value for e in at.exception]
    legacy = _legacy(at)
    assert {"_override_type_Rollover", "_override_owner_Rollover"} <= _page_keys(legacy)
    legacy.selectbox(key="_override_owner_Rollover").select("spouse").run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["account_type_overrides"]["Rollover"]["owner"] == "spouse"


def test_holdings_without_snapshot_has_legacy_sync_and_no_sub_tabs():
    at = _run(PAGE_FUNCS["holdings"])
    assert not at.exception, [e.value for e in at.exception]
    legacy = _legacy(at)
    assert "Sync from FinExtract" in [b.label for b in legacy.button]
    assert len(at.tabs) == 0


def test_import_ytd_sync_lives_in_legacy_expander(monkeypatch):
    import views.ytd_income._partials._sync_scan as sync_scan_mod

    monkeypatch.setattr(sync_scan_mod, "is_pyodide", lambda: False)
    at = AppTest.from_function(_page_script, args=(PAGE_FUNCS["import"], False), default_timeout=30)
    at.session_state["instance_owner"] = "you"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "ytd_sync_btn" in _page_keys(_legacy(at))


_OWNER_GATE_TEXT = "owner on **Review Changes**"


def test_import_with_corrupt_owner_has_no_legacy_expander(corrupt_row):
    # An unset owner now auto-defaults to "you" (and unlocks Legacy); only a
    # corrupt identity keeps the Legacy expander hidden.
    from engine.store.namespaces import INSTANCE_OWNER

    corrupt_row(INSTANCE_OWNER)
    at = _run(PAGE_FUNCS["import"])
    assert not [e for e in at.expander if e.label == "Legacy: FinExtract"]


def test_import_fresh_session_has_no_owner_gate_and_scanner_is_present(store):
    from engine.store.namespaces import INSTANCE_OWNER

    assert not store.has(INSTANCE_OWNER)
    at = _run(PAGE_FUNCS["import"])
    assert not at.exception, [e.value for e in at.exception]
    assert not [i for i in at.info if _OWNER_GATE_TEXT in i.value]
    assert "pdf_upload_0" in {w.key for w in at.get("file_uploader")}
    assert store.has(INSTANCE_OWNER)


def test_import_with_corrupt_owner_keeps_gate_and_leaves_row_untouched(store, corrupt_row):
    from engine.store import SINGLE_KEY
    from engine.store.namespaces import INSTANCE_OWNER
    from tests.conftest import CORRUPT_PAYLOAD

    corrupt_row(INSTANCE_OWNER)
    at = _run(PAGE_FUNCS["import"])
    assert not at.exception, [e.value for e in at.exception]
    assert len([i for i in at.info if _OWNER_GATE_TEXT in i.value]) == 1
    assert "pdf_upload_0" not in {w.key for w in at.get("file_uploader")}
    assert store.backend.get(INSTANCE_OWNER, SINGLE_KEY).payload == CORRUPT_PAYLOAD


def test_backup_fresh_session_defaults_owner_so_export_is_enabled():
    at = _run(PAGE_FUNCS["backup"])
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["instance_owner"] == "you"


def test_holdings_sync_success_reruns_so_tables_above_legacy_refresh(monkeypatch):
    import views.setup.portfolio as portfolio_view_mod

    def _fake_sync(hh):
        import streamlit as st

        from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
        from views.setup.portfolio import PortfolioSyncOutcome

        snap = PortfolioSnapshot(
            accounts=[
                AccountSummary(
                    account_type="trad_ira",
                    owner="you",
                    account_name="Rollover",
                    total_value=500_000.0,
                    equity_value=400_000.0,
                    bond_value=100_000.0,
                    holdings=[],
                )
            ],
            equity_grants=[],
            server_available=True,
        )
        st.session_state["portfolio_snapshot"] = snap
        return PortfolioSyncOutcome(
            snap=snap,
            magi_candidates_recorded=0,
            ytd_synced=False,
            dividend_history_synced=False,
            option_exercises_synced=False,
        )

    monkeypatch.setattr(portfolio_view_mod, "sync_portfolio_from_finextract", _fake_sync)
    at = _run(PAGE_FUNCS["holdings"])
    assert len(at.tabs) == 0
    at.button[[b.label for b in at.button].index("Sync from FinExtract")].click().run()
    assert not at.exception, [e.value for e in at.exception]
    # the tables sit ABOVE the Legacy expander: they must be fresh in this same `at`
    assert [t.label for t in at.tabs] == ["Me", "Spouse", "All"]
    assert "Total Portfolio" in [m.label for m in at.metric]
    assert any("Synced: 1 accounts" in s.value for s in at.success)


def test_holdings_legacy_on_pyodide_keeps_overrides_hides_sync(monkeypatch):
    import views.setup._partials._portfolio as portfolio_mod

    monkeypatch.setattr(portfolio_mod, "is_pyodide", lambda: True)
    at = _run(PAGE_FUNCS["holdings"], with_snapshot=True)
    assert not at.exception, [e.value for e in at.exception]
    legacy = _legacy(at)
    assert "_override_owner_Rollover" in _page_keys(legacy)
    assert "Sync from FinExtract" not in [b.label for b in legacy.button]
    assert any("isn't available on the public site" in i.value for i in legacy.info)
