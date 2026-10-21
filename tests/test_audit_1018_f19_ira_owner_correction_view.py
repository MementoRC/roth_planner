"""audit-1018 F-19 (view layer): correcting an IRA statement's owner re-records
the wrong-owner person's pre-tax candidate at their corrected total.

The engine half is covered in tests/test_audit_1018_w6_statement_apply.py; this
drives ``_render_ira_statement_review`` through a real AppTest run so the view
wiring (``outcome.other_owner_total`` -> ``record_ira_balance_candidate``) is
exercised. Synthetic data only.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _render() -> None:
    import streamlit as st

    from engine.ira_statement_balance import apply_ira_statement
    from engine.portfolio_sync import PortfolioSnapshot
    from tests.test_audit_1018_w6_statement_apply import _rec
    from views.ytd_income._partials._sync_scan import _render_ira_statement_review

    if "ira_statements" not in st.session_state:
        # The statement was first applied under the WRONG owner (spouse).
        st.session_state["ira_statements"] = [_rec()]
        st.session_state["portfolio_snapshot"] = apply_ira_statement(
            PortfolioSnapshot(), _rec(), "spouse"
        ).snapshot
    _render_ira_statement_review(owner_map={}, instance_owner="spouse", identity_set=True)


def test_owner_correction_rerecords_the_other_owners_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, float]] = []

    def _record(field_key: str, total: float, detail: str, when: object) -> None:
        recorded.append((field_key, total))

    monkeypatch.setattr(sync_scan_mod, "record_ira_balance_candidate", _record)
    monkeypatch.setattr(sync_scan_mod, "load_link_map", lambda: {})
    monkeypatch.setattr(sync_scan_mod, "save_link_map", lambda m: None)
    monkeypatch.setattr(sync_scan_mod, "save_snapshot", lambda snap, **kw: None)
    monkeypatch.setattr(sync_scan_mod, "save_owner_map", lambda m: None)

    at = AppTest.from_function(_render)
    at.run()
    assert not at.exception

    # Owner is corrected spouse -> you, then applied.
    at.radio[0].set_value("you")
    at.run()
    next(b for b in at.button if b.label == "Apply IRA balance").click()
    at.run()
    assert not at.exception

    assert recorded == [("spouse_ira", pytest.approx(0.0)), ("your_ira", pytest.approx(100_000.0))]
