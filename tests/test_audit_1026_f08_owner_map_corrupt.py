"""audit-1026 F-08: an unreadable owner-map row no longer blocks holder-named applies."""

from __future__ import annotations

from unittest.mock import patch

from engine.netbenefits_statement_pdf import parse_netbenefits_text
from engine.ssa_statement_pdf import parse_ssa_statement_text
from engine.store.core import SINGLE_KEY
from engine.store.namespaces import PDF_OWNER_MAP, PORTFOLIO, SSA_PDF
from tests import test_sync_scan_netbenefits as nb_tests
from tests.test_netbenefits_statement_pdf import PAGES as NB_PAGES
from tests.test_ssa_statement_pdf import PAGES as SSA_PAGES
from views.ytd_income._partials import _sync_scan as sync_scan_mod


def _warnings(mock_st) -> list[str]:
    return [str(c.args[0]) for c in mock_st.warning.call_args_list]


def test_an_ssa_apply_commits_though_the_owner_map_row_is_unreadable(store, corrupt_row):
    payload = corrupt_row(PDF_OWNER_MAP)
    stmt = parse_ssa_statement_text(SSA_PAGES)
    assert stmt.owner_key
    mock_st, state = nb_tests._mock_st(
        state_extra={"ssa_statements": [stmt]}, clicks={"ssa_apply_0"}, radio_choice="you"
    )
    with patch.object(sync_scan_mod, "st", mock_st):
        sync_scan_mod._render_ssa_review(owner_map={}, instance_owner="you", identity_set=True)
    assert store.keys(SSA_PDF) != []
    assert state["ssa_statements"] == []
    assert store.backend.get(PDF_OWNER_MAP, SINGLE_KEY).payload == payload
    assert any("not remembered" in w for w in _warnings(mock_st))
    mock_st.rerun.assert_not_called()


def test_a_netbenefits_apply_commits_though_the_owner_map_row_is_unreadable(store, corrupt_row):
    payload = corrupt_row(PDF_OWNER_MAP)
    stmt = parse_netbenefits_text(NB_PAGES)
    assert stmt.owner_key
    mock_st, state = nb_tests._mock_st(
        state_extra={"netbenefits_statements": [stmt]},
        clicks={"netbenefits_apply_0"},
        radio_choice="spouse",
    )
    with (
        patch.object(sync_scan_mod, "st", mock_st),
        patch.object(sync_scan_mod, "load_snapshot", return_value=nb_tests._snap()),
    ):
        sync_scan_mod._render_netbenefits_review(
            owner_map={}, instance_owner="you", identity_set=True
        )
    assert store.keys(PORTFOLIO) != []
    assert state["netbenefits_statements"] == []
    assert store.backend.get(PDF_OWNER_MAP, SINGLE_KEY).payload == payload
    assert any("not remembered" in w for w in _warnings(mock_st))


def test_a_readable_owner_map_still_learns_the_name_and_reruns(store):
    stmt = parse_ssa_statement_text(SSA_PAGES)
    mock_st, _state = nb_tests._mock_st(
        state_extra={"ssa_statements": [stmt]}, clicks={"ssa_apply_0"}, radio_choice="you"
    )
    mock_st.rerun.side_effect = None
    with patch.object(sync_scan_mod, "st", mock_st):
        sync_scan_mod._render_ssa_review(owner_map={}, instance_owner="you", identity_set=True)
    assert store.get(PDF_OWNER_MAP) == {stmt.owner_key.lower(): "you"}
    mock_st.rerun.assert_called_once()
