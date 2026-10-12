"""Balance fields stay editable when a snapshot exists; the "(synced)" label and
help text reflect only the accounts that actually feed each field.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
from engine.upload_merge import synced_account_sums


def _snap(*accts: tuple[str, str, float]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        accounts=[AccountSummary(account_type=t, owner=o, total_value=v) for o, t, v in accts]
    )


# --- pure helper -------------------------------------------------------------


def test_sums_pretax_by_type_for_owner() -> None:
    snap = _snap(("you", "403b", 100.0), ("you", "trad_ira", 50.0), ("spouse", "trad_ira", 7.0))
    assert synced_account_sums(snap, "you", "pretax") == {"trad_ira": 50.0, "403b": 100.0}
    assert synced_account_sums(snap, "spouse", "pretax") == {"trad_ira": 7.0}


def test_sums_roth_and_empty() -> None:
    snap = _snap(("you", "roth_ira", 10.0), ("you", "roth_ira", 5.0), ("you", "brokerage", 9.0))
    assert synced_account_sums(snap, "you", "roth") == {"roth_ira": 15.0}
    assert synced_account_sums(snap, "spouse", "roth") == {}
    assert synced_account_sums(snap, "you", "pretax") == {}


# --- view --------------------------------------------------------------------


def _render(owner, snap, filing_status="MFJ"):  # noqa: ANN001, ANN201 - AppTest source-extracts this
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_accounts_partial

    st.session_state.setdefault("your_ira", 1_000)
    st.session_state.setdefault("spouse_ira", 1_000)
    st.session_state.setdefault("your_ss_fra", 2_000)
    st.session_state.setdefault("spouse_ss_fra", 2_000)
    st.session_state["filing_status"] = filing_status
    if snap is not None:
        st.session_state["portfolio_snapshot"] = snap
    render_accounts_partial(Household(), st, owner)


def _fields(owner: str, snap, filing_status: str = "MFJ") -> dict:
    at = AppTest.from_function(
        _render, kwargs={"owner": owner, "snap": snap, "filing_status": filing_status}
    )
    at.run()
    assert not at.exception
    return {w.label: w for w in at.number_input}


def test_403b_only_snapshot_unlocks_your_ira(clean_command_center_caches) -> None:
    f = _fields("your", _snap(("you", "403b", 120_000.0)))
    ira = f["Your Trad IRA (synced)"]
    assert not ira.disabled
    assert "403(b) \\$120,000" in ira.help
    assert "FinExtract" not in ira.help
    roth = f["Your Roth IRA"]
    assert not roth.disabled
    assert not roth.help


def test_spouse_only_snapshot(clean_command_center_caches) -> None:
    snap = _snap(("spouse", "trad_ira", 40_000.0))
    mine = _fields("your", snap)
    assert "Your Trad IRA" in mine
    assert "Your Roth IRA" in mine
    spouse = _fields("spouse", snap)
    ira = spouse["Spouse Trad IRA (synced)"]
    assert not ira.disabled
    assert "Trad IRA \\$40,000" in ira.help
    assert "Spouse Roth IRA" in spouse


def test_no_snapshot_all_enabled(clean_command_center_caches) -> None:
    f = _fields("your", None)
    assert not f["Your Trad IRA"].disabled
    assert not f["Your Roth IRA"].disabled


def test_single_filer_still_disables_spouse(clean_command_center_caches) -> None:
    f = _fields("spouse", _snap(("spouse", "roth_ira", 5.0)), filing_status="Single")
    assert f["Spouse Trad IRA"].disabled
    assert f["Spouse Roth IRA (synced)"].disabled


def test_trad_ira_and_403b_both_synced_escapes_dollars(clean_command_center_caches) -> None:
    """Snapshot with both trad_ira and 403b for 'you'; help must escape all $."""
    snap = _snap(("you", "trad_ira", 75_000.0), ("you", "403b", 100_000.0))
    f = _fields("your", snap)
    ira = f["Your Trad IRA (synced)"]
    assert not ira.disabled
    assert "Trad IRA" in ira.help
    assert "403(b)" in ira.help
    # Verify every $ is escaped: must have exactly 2 escaped dollar signs (trad_ira and 403b amounts)
    assert ira.help.count("\\$") == 2
