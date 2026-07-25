"""Tests for ``views/shells/`` — Tasks 8-9 of the ui-shell-theme-toggle plan.

Two guarantees per shell:
  1. Smoke: each of the 4 implemented shells (Classic/Domains/Hub/Contextual)
     renders without exception for a demo household.
  2. Key-set parity: editing the same field (``your_ira``) through
     Domains/Hub/Contextual updates the exact same ``session_state`` key
     Classic does — proving no silent fork of the data model (Owner
     decisions 4/5 in
     ``docs/superpowers/plans/2026-07-24-ui-shell-theme-toggle.md``).

Task 9 adds Contextual's own status-bar tests further down (a household
with a missing/stale/conflict field shows the matching chip; an all-good
household shows the "All set" affirmation with no chips).

Each shell is exercised directly via ``AppTest.from_function`` with a small
self-contained session-state seed (NOT via the real ``app.py`` — Task 10
hasn't wired the theme selector into ``app.py`` yet, and app.py's script-level
sidebar/page-dispatch would always render Classic regardless of which shell
we want to test). Only ONE shell's ``render(hh)`` is ever called per AppTest
run — the widget-key-uniqueness concern the plan flags for Task 8 doesn't
apply here since each test function calls exactly one shell.

``AppTest.from_function`` extracts and execs only the target function's OWN
source text in a fresh namespace — it does NOT carry along this module's
other top-level names (a sibling helper function is invisible inside the
executed function, confirmed empirically: an earlier draft that called a
module-level ``_seed_demo_session_state()`` helper from 3 separate
``_render_*`` functions raised ``NameError`` at AppTest run time). So the
seed logic below lives entirely INSIDE the one function passed to
``AppTest.from_function``, parametrized by ``theme`` via ``kwargs=`` (mirrors
``tests/test_setup_shell_characterization.py``'s
``_render_portfolio_with_snapshot(snap)`` pattern of a single self-contained
target function taking parameters through ``kwargs``).
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from views.shells import THEMES, render_setup

_SHELL_NAME_TO_THEME = {
    "classic": "Classic",
    "domains": "Domains",
    "hub": "Hub",
    "contextual": "Contextual",
}


def _render_shell(theme: str, seed_1040_scanned: bool = False) -> None:
    """AppTest.from_function target: seed a minimal demo session_state, then
    render the shell named by *theme* ("Classic"/"Domains"/"Hub").

    Seeds the fields every partial reads without a ``.get()`` fallback
    (``your_ira``/``spouse_ira``/``your_ss_fra``/``spouse_ss_fra``/
    ``txn_price``/``growth_rate``/``living_expenses``), plus a few more for
    determinism — a trimmed, shell-test-scoped mirror of
    ``app.py:_seed_session_state`` (values sourced from
    ``config.defaults.DEFAULTS`` to stay in sync with the real app's demo
    numbers).

    ``seed_1040_scanned=True`` additionally seeds
    ``st.session_state["_pdf_1040_scanned"]`` with a fake scanned
    Form1040Record, exercising the "Import 1040 PDF" section's confirmation
    UI (added to Domains/Hub post-Task-8 for parity with Classic).
    """
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from engine.tax_return_pdf import Form1040Record
    from models.household import Household
    from views.shells import render_setup

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    if seed_1040_scanned:
        st.session_state["_pdf_1040_scanned"] = {
            2024: Form1040Record(
                tax_year=2024,
                agi=280_000.0,
                tax_exempt_interest=1_000.0,
                taxable_ss=0.0,
                qualified_dividends=0.0,
                ordinary_dividends=0.0,
                feie=0.0,
                magi=281_000.0,
                filing_status=None,
                captured_at="2026-07-17T00:00:00+00:00",
            )
        }

    render_setup(Household(), theme)


def _run_shell(shell_name: str, monkeypatch, seed_1040_scanned: bool = False) -> AppTest:
    """Run the shell named by *shell_name* ("classic"/"domains"/"hub") under
    ``AppTest``, neutralizing local-disk sources of non-determinism the same
    way ``tests/test_setup_shell_characterization.py``'s ``setup_app_test``
    fixture does (a developer's real V2 pubkey / PDF-tax cache must not leak
    into these tests).
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(
        _render_shell,
        kwargs={"theme": _SHELL_NAME_TO_THEME[shell_name], "seed_1040_scanned": seed_1040_scanned},
    )
    at.run()
    return at


def _number_input_by_label(at: AppTest, label: str):
    return next(w for w in at.number_input if w.label == label)


# --- THEMES / render_setup dispatcher --------------------------------------


def test_themes_list_matches_plan_scope() -> None:
    assert THEMES == ["Classic", "Domains", "Hub", "Contextual"]


def test_render_setup_unknown_theme_raises_value_error() -> None:
    from models.household import Household

    with pytest.raises(ValueError, match="Unknown UI theme"):
        render_setup(Household(), "Nonexistent")


# --- Smoke tests: each shell renders without exception ---------------------


@pytest.mark.parametrize("shell_name", ["classic", "domains", "hub", "contextual"])
def test_shell_renders_without_exception(shell_name, clean_command_center_caches, monkeypatch) -> None:
    at = _run_shell(shell_name, monkeypatch)
    assert not at.exception


# --- Key-set parity: Domains/Hub/Contextual touch the same session_state key
# as Classic


@pytest.mark.parametrize("shell_name", ["classic", "domains", "hub", "contextual"])
def test_your_ira_edit_updates_same_session_state_key(
    shell_name, clean_command_center_caches, monkeypatch
) -> None:
    """Setting ``your_ira`` through each shell must update the identical
    ``session_state["your_ira"]`` key — proves Domains/Hub/Contextual don't
    fork the data model onto a differently-named key (Task 8's key-set parity
    check).
    """
    at = _run_shell(shell_name, monkeypatch)
    assert not at.exception

    _number_input_by_label(at, "Your Trad IRA").set_value(999_000).run()

    assert not at.exception
    assert at.session_state["your_ira"] == 999_000


# --- 1040 PDF import section: parity fix (Domains/Hub, post-Task-8) --------


@pytest.mark.parametrize("shell_name", ["domains", "hub"])
def test_1040_import_section_renders_without_exception(
    shell_name, clean_command_center_caches, monkeypatch
) -> None:
    """The "Import 1040 PDF" workflow (``_render_pdf_1040_import``) is now
    reachable from Domains/Hub, not just Classic — closes the parity gap a
    spec-compliance review of Task 8 found. With no scanned record pending,
    it should render its "scan on YTD Income" caption without exception.
    """
    at = _run_shell(shell_name, monkeypatch)
    assert not at.exception


@pytest.mark.parametrize("shell_name", ["classic", "domains", "hub"])
def test_1040_import_section_reuses_classic_widget_key(
    shell_name, clean_command_center_caches, monkeypatch
) -> None:
    """With a scanned 1040 record pending, Domains/Hub's confirmation
    selectbox must carry the EXACT SAME key Classic's copy of this widget
    uses (``_pdf_1040_filing_status_2024``) — proving the new section
    reuses Classic's existing widget key rather than minting a new one
    (plan Owner decision 4: no session_state key renames/forks).
    """
    at = _run_shell(shell_name, monkeypatch, seed_1040_scanned=True)
    assert not at.exception

    matches = [w for w in at.selectbox if w.key == "_pdf_1040_filing_status_2024"]
    assert len(matches) == 1, (
        f"expected exactly one selectbox with key '_pdf_1040_filing_status_2024' in "
        f"{shell_name}, found {len(matches)}"
    )


# --- Contextual status bar (Task 9) -----------------------------------------
#
# Each target function below is self-contained (see the module docstring's
# note on ``AppTest.from_function`` only carrying its OWN source) and shares
# the same base session_state seed ``_render_shell`` uses, so Classic's
# wrapped body renders without crashing regardless of which chip case is
# under test.


def _render_contextual_missing_fields() -> None:
    """AppTest target: a brand-new, never-sourced household -> "missing"
    chips (``your_ira``, ``grants``, ...), no "All set" affirmation.
    """
    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from views.shells import render_setup

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    render_setup(Household(), "Contextual")


def _render_contextual_conflict_field() -> None:
    """AppTest target: ``your_ira`` confirmed but ALSO in ``_pending_review``
    -> a "conflict" chip for ``your_ira`` specifically.
    """
    from datetime import datetime

    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.household import Household
    from models.sourced import Provenance, Source, SourcedValue
    from views.shells import render_setup

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state["_pending_review"] = {"your_ira"}
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    hh = Household()
    hh.your_ira = SourcedValue(
        float(DEFAULTS["your_ira"]),
        Provenance(source=Source.MANUAL, recorded_at=datetime.now(), detail="test fixture"),
    )
    render_setup(hh, "Contextual")


def _render_contextual_all_good() -> None:
    """AppTest target: every governed field confirmed recently, nothing
    pending -> the "All set" affirmation and zero chips.
    """
    from datetime import datetime, timedelta

    import streamlit as st

    from config.defaults import DEFAULTS
    from engine.irmaa import BASE_PART_B
    from models.grants import StockGrant
    from models.household import Household
    from models.sourced import Provenance, Source, SourcedList, SourcedValue
    from views.shells import render_setup

    st.session_state["_suppress_snapshot_autoload"] = True
    st.session_state.setdefault("filing_status", "MFJ")
    st.session_state.setdefault("your_ira", DEFAULTS["your_ira"])
    st.session_state.setdefault("spouse_ira", DEFAULTS["spouse_ira"])
    st.session_state.setdefault("your_roth", DEFAULTS["your_roth"])
    st.session_state.setdefault("spouse_roth", DEFAULTS["spouse_roth"])
    st.session_state.setdefault("your_ss_fra", DEFAULTS["your_ss_fra"])
    st.session_state.setdefault("spouse_ss_fra", DEFAULTS["spouse_ss_fra"])
    st.session_state.setdefault("txn_price", DEFAULTS["stock_price_now"])
    st.session_state.setdefault("growth_rate", 7.0)
    st.session_state.setdefault("living_expenses", DEFAULTS["living_expenses"])
    st.session_state.setdefault("aca_benchmark_premium_annual", 21_600.0)
    st.session_state.setdefault("advance_aptc_annual", 0)
    st.session_state.setdefault("medicare_part_b_base_monthly", BASE_PART_B / 12)
    st.session_state.setdefault("cpi_assumption", 0.025)
    st.session_state.setdefault("_pending_review", set())
    st.session_state.setdefault("_stock_ticker", DEFAULTS["stock_ticker"])

    recent = datetime.now() - timedelta(hours=1)
    prov = Provenance(source=Source.MANUAL, recorded_at=recent, detail="test fixture")
    hh = Household()
    hh.your_ira = SourcedValue(float(DEFAULTS["your_ira"]), prov)
    hh.spouse_ira = SourcedValue(float(DEFAULTS["spouse_ira"]), prov)
    hh.your_roth = SourcedValue(float(DEFAULTS["your_roth"]), prov)
    hh.spouse_roth = SourcedValue(float(DEFAULTS["spouse_roth"]), prov)
    hh.txn_price_now = SourcedValue(float(DEFAULTS["stock_price_now"]), prov)
    hh.your_ss_fra = SourcedValue(float(DEFAULTS["your_ss_fra"]), prov)
    hh.spouse_ss_fra = SourcedValue(float(DEFAULTS["spouse_ss_fra"]), prov)
    hh.grants = SourcedList([StockGrant(year=2019, strike=104.0, shares=100, expiry_year=2029)], [prov])

    render_setup(hh, "Contextual")


def _run_contextual(target, monkeypatch) -> AppTest:
    """Same disk-source neutralization as ``_run_shell``, for a bespoke
    Contextual target function that doesn't go through ``_render_shell``.
    """
    import engine.portfolio_sync as portfolio_sync_mod
    import engine.tax_return_pdf as tax_return_pdf_mod
    import views.setup.data_bridge as data_bridge_mod

    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(tax_return_pdf_mod, "load_pdf_tax_records", lambda: {})
    monkeypatch.setattr(portfolio_sync_mod, "load_ssa_snapshot", lambda *, owner: None)

    at = AppTest.from_function(target)
    at.run()
    return at


def test_contextual_missing_field_shows_missing_chip(clean_command_center_caches, monkeypatch) -> None:
    at = _run_contextual(_render_contextual_missing_fields, monkeypatch)
    assert not at.exception

    # No "All set" affirmation (Command Center's own unrelated "reconciled"
    # success — driven purely by an empty _pending_review, not by missing
    # fields — is allowed to coexist and is deliberately not asserted here).
    assert not any("All set" in s.value for s in at.success)
    warnings = [w.value for w in at.warning]
    assert any("Your IRA balance" in w and "missing" in w for w in warnings)


def test_contextual_conflict_field_shows_conflict_chip(clean_command_center_caches, monkeypatch) -> None:
    at = _run_contextual(_render_contextual_conflict_field, monkeypatch)
    assert not at.exception

    assert not any("All set" in s.value for s in at.success)
    warnings = [w.value for w in at.warning]
    assert any("Your IRA balance" in w and "conflict" in w for w in warnings)


def test_contextual_all_good_household_shows_affirmation_no_chips(
    clean_command_center_caches, monkeypatch
) -> None:
    at = _run_contextual(_render_contextual_all_good, monkeypatch)
    assert not at.exception

    assert any("All set" in s.value for s in at.success)
    warnings = [w.value for w in at.warning if "rejected" not in w.value]
    assert warnings == []
