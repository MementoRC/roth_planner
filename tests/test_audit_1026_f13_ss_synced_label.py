"""audit-1026 F-13: "Your SS at FRA" must not claim "(synced)" for a MANUAL value.

The label, disabled state and help text were keyed on a cached SSA snapshot
existing; they must key on the committed value's provenance
(``Source.FINEXTRACT_LIVE``) only. Synthetic figures.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from streamlit.testing.v1 import AppTest

from models.sourced import Provenance, Source, SourcedValue
from views.setup._partials._accounts import _committed_from_sync

T0 = datetime(2026, 1, 1)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Source.FINEXTRACT_LIVE, True),
        (Source.MANUAL, False),
        (Source.PDF, False),
        (Source.DEFAULT, False),
    ],
)
def test_f13_committed_from_sync_only_for_live_provenance(source: Source, expected: bool) -> None:
    assert _committed_from_sync(SourcedValue(2_500.0, Provenance(source, T0))) is expected


def test_f13_committed_from_sync_false_for_plain_number() -> None:
    assert _committed_from_sync(2_500) is False


def _render_ss(owner, source):  # noqa: ANN001, ANN201 - AppTest source-extracts this
    from datetime import datetime

    import streamlit as st

    from models.household import Household
    from models.sourced import Provenance, Source, SourcedValue
    from views.setup._partials import render_accounts_partial

    st.session_state.setdefault("your_ira", 1_000)
    st.session_state.setdefault("spouse_ira", 1_000)
    st.session_state["your_ss_fra"] = 2_500
    st.session_state["spouse_ss_fra"] = 2_500
    st.session_state["filing_status"] = "MFJ"
    # A cached SSA snapshot with a DIFFERENT estimate (2,800) exists.
    st.session_state["ssa_snapshot_you"] = object()
    st.session_state["ssa_snapshot_spouse"] = object()
    prov = Provenance(Source(source), datetime(2026, 1, 1))
    hh = Household(
        your_ss_fra=SourcedValue(2_500, prov),
        spouse_ss_fra=SourcedValue(2_500, prov),
    )
    render_accounts_partial(hh, st, owner)


def _ss_field(owner: str, source: str):  # noqa: ANN202
    at = AppTest.from_function(_render_ss, kwargs={"owner": owner, "source": source})
    at.run()
    assert not at.exception
    return next(w for w in at.number_input if " SS at FRA " in w.label)


@pytest.mark.parametrize("owner", ["your", "spouse"])
def test_f13_manual_value_with_cached_snapshot_is_editable_and_unlabelled(owner: str) -> None:
    w = _ss_field(owner, "MANUAL")
    assert "(synced)" not in w.label
    assert not w.disabled
    assert not w.help
    assert w.value == 2_500


@pytest.mark.parametrize("owner", ["your", "spouse"])
def test_f13_live_sync_value_is_locked_and_labelled(owner: str) -> None:
    w = _ss_field(owner, "FINEXTRACT_LIVE")
    assert w.label.endswith("(synced)")
    assert w.disabled
    assert "FinExtract" in w.help
