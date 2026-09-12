"""VIEW gate for audit-0823 `GRID-KEY-COLLISION`.

`StockGrant.key()` is content-based (`grant_id` else `year:strike:expiry_year`),
so two grants with an empty `grant_id` that share year+strike+expiry legitimately
collide -- e.g. one award split across two rows by an importer.
`views/option_exercise/_partials/_grid.py`'s editable exercise-schedule grid
used to loop the RAW `hh.grants` list in three places (the row-building loop,
the `raw_by_key` population loop, and the call into
`engine.exercise_grid.normalize_grid_edits`), so a colliding pair rendered as
TWO duplicate rows instead of one aggregated lot, and the second grant's
`raw_by_key` entry silently overwrote the first's.

The share-budget/remaining/out-of-range consequences of routing raw grants
into `normalize_grid_edits` are proven directly against the engine function in
`tests/test_audit_0823_grid_key_collision.py`. THAT file never renders
anything, so it cannot see the symptom a user actually hits: duplicate rows in
the grid. THIS file drives a REAL rendered Streamlit session via
`streamlit.testing.v1.AppTest` (mirroring `tests/test_option_exercise_shell.py`'s
`_render_oe`/`_run_oe` harness) and asserts on that rendered grid directly --
the VIEW leg the engine tests do not reach.

Empirically verified observable (throwaway probe script, discarded after use):
the grid produced by `render_grid_partial`'s `st.data_editor` call shows up
under `AppTest.dataframe` -- `at.dataframe[0].value` is the pandas DataFrame
actually rendered in the grid widget. There is NO separate `at.data_editor`
accessor in this streamlit version (`hasattr(at, "data_editor")` is `False`
on a built `AppTest`); `st.data_editor` elements are exposed through the same
`dataframe` list as `st.dataframe`. Classic layout renders the editable grid
as the FIRST such element (Price & Basis renders no dataframe; the two
Review-Impact mirror tables render after it), so `at.dataframe[0]` is safe to
index directly.

Per the audit's own methodology note, the headline $102K conversion-tax
exposure attributed to this collision is NOT reachable on real data: the
household's actual TXN grants (2019/$104, 2020/$130, 2021/$169) and the
`config/defaults.py` placeholder grants are pairwise distinct on
`StockGrant.key()`. This is a consistency-only fix; the gates below use a
deliberately constructed split award (year=2021, strike=$60,
expiry_year=2031, empty grant_id, shares split 400/600) to reach it.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from models.grants import StockGrant


def _render_oe(grants) -> None:
    """AppTest.from_function target. Per test_option_exercise_shell.py's
    module docstring, AppTest execs only THIS function's own source in a
    fresh namespace, so the parameter cannot be annotated with a name
    (``StockGrant``) that is only imported at this module's top level --
    doing so raises ``NameError`` at def-time inside the isolated exec."""
    import streamlit as st

    from models.household import Household
    from views.option_exercise import render

    st.session_state["_suppress_snapshot_autoload"] = True
    hh = Household(grants=grants, base_year=2026)
    render(hh, theme=None)


def _run_oe(monkeypatch, grants: list[StockGrant]) -> AppTest:
    import views.option_exercise as oe_module

    monkeypatch.setattr(oe_module, "save_exercise_schedule", lambda s: None)
    monkeypatch.setattr(oe_module, "clear_exercise_schedule", lambda: None)

    at = AppTest.from_function(_render_oe, kwargs={"grants": grants})
    at.session_state["ui_theme"] = "Classic"
    at.run()
    return at


def _grid_rows(at: AppTest) -> list[str]:
    """The editable exercise-schedule grid is `at.dataframe[0]` -- see module
    docstring for why `at.dataframe` (not a `data_editor` accessor) is the
    correct observable, and why index 0 is the grid and not a mirror table."""
    return list(at.dataframe[0].value["Grant"])


# One award split across two rows: identical year+strike+expiry, empty grant_id.
SPLIT_A = StockGrant(2021, 60.0, 400, 2031)
SPLIT_B = StockGrant(2021, 60.0, 600, 2031)

# Two genuinely distinct grants (non-regression: must NOT be merged).
DISTINCT_A = StockGrant(2019, 104.0, 1000, 2029)
DISTINCT_B = StockGrant(2020, 130.0, 500, 2030)


def test_colliding_grants_render_exactly_one_combined_row(monkeypatch) -> None:
    at = _run_oe(monkeypatch, [SPLIT_A, SPLIT_B])

    assert not at.exception
    rows = _grid_rows(at)
    assert len(rows) == 1
    assert rows == ["2021 · $60 · 1,000 sh"]


def test_distinct_grants_still_render_two_rows(monkeypatch) -> None:
    """Non-regression: aggregation must not merge grants with distinct keys."""
    at = _run_oe(monkeypatch, [DISTINCT_A, DISTINCT_B])

    assert not at.exception
    rows = _grid_rows(at)
    assert len(rows) == 2
    assert rows == ["2019 · $104 · 1,000 sh", "2020 · $130 · 500 sh"]
