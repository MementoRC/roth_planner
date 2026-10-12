"""Display defects found by the 2026-10-11 fixture-driven browser pass.

(a) UBS import message and other ``_sync_scan`` texts lost their ``$`` to LaTeX.
(b) ACA+IRMAA chart tick formats ("$500m", repeated "$200k") and a flat
    hidden-cost chart autoranging to "-$1".
(c) The "Edit in Profile › Assumptions" button wrapped one character per line.
(d) The survivor timeline row showed "None" for the deceased person's age.
(e) After a bundle import replaced a person's accounts, Review Changes still
    offered a statement-PDF IRA balance computed from the removed accounts.
(f) The timeline's IRMAA Room column now carries an explanatory caption.

All values are synthetic.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from engine.bridge_import import ImportPlan, SlotPlan
from engine.data_sources import ingest
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.record import discard_pdf_balance_candidates
from models.sourced import Source


class _State(dict):
    """dict with attribute access, like Streamlit's session_state."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


def _text_args(mock_fn) -> list[str]:
    return [str(c.args[0]) for c in mock_fn.call_args_list if c.args]


# --- (a) ---------------------------------------------------------------------


def test_ubs_import_message_escapes_dollars():
    from views.ytd_income._partials import _sync_scan

    st = MagicMock()
    st.session_state = _State()
    st.file_uploader.return_value = [SimpleNamespace(name="order.csv", getvalue=lambda: b"x")]
    st.button.return_value = True
    order = SimpleNamespace(reference_number="R-1", bargain_element=3_600.0)
    with (
        patch.object(_sync_scan, "st", st),
        patch.object(_sync_scan, "parse_ubs_activity_text", return_value=order),
        patch.object(_sync_scan, "load_ubs_orders", return_value=[]),
        patch.object(_sync_scan, "save_ubs_orders"),
        patch.object(_sync_scan, "federal_withholding", return_value=792.0),
        patch.object(_sync_scan, "derive_brokerage_totals", return_value={}),
        patch.object(_sync_scan, "_apply_ubs_correction", return_value=[]),
        patch.object(_sync_scan, "save_ytd_snapshot"),
    ):
        _sync_scan._render_ubs_csv_uploader(ledger={}, identity_set=True)

    (msg,) = _text_args(st.success)
    assert "bargain element \\$3,600" in msg
    assert "federal withholding \\$792" in msg


def test_koinly_breakdown_caption_escapes_dollars():
    from views.ytd_income._partials import _sync_scan

    st = MagicMock()
    st.session_state = _State()
    ledger = {"koinly": {"you": {"stcg": 100.0, "ltcg": 200.0, "income": 300.0}}, "brokerage": {}}
    with (
        patch.object(_sync_scan, "st", st),
        patch("engine.koinly_report_pdf.load_koinly_report", return_value=None),
    ):
        _sync_scan._render_koinly_summary(ledger)

    captions = _text_args(st.caption)
    assert any("STCG \\$100" in c and "Income \\$300" in c for c in captions)


# --- (b) (c) (d) (f): the ACA+IRMAA page -------------------------------------


def _render_aca_page() -> None:
    from models.household import Household
    from views import aca_irmaa

    aca_irmaa.render(Household())


def test_aca_page_chart_ticks_caption_and_button():
    from views import aca_irmaa

    at = AppTest.from_function(_render_aca_page)
    at.run(timeout=120)
    assert not at.exception

    specs = [el.proto.spec for el in at.get("plotly_chart")]
    assert specs, "expected the page's plotly charts"
    for spec in specs:
        assert ",.0s" not in spec
    assert any(aca_irmaa.DOLLAR_TICKS in spec for spec in specs)

    assert aca_irmaa.IRMAA_ROOM_CAPTION in [c.value for c in at.caption]
    assert any(b.label == "Edit in Profile › Assumptions →" for b in at.button)


def test_hidden_cost_flat_detection():
    from views.aca_irmaa import hidden_cost_is_flat

    assert hidden_cost_is_flat([0.0, 0.0], [0.0], [0.0, 0.0])
    assert not hidden_cost_is_flat([0.0, 0.0], [0.0, 1_250.0], [0.0])


def test_timeline_age_cell_shows_dash_for_deceased():
    from views.aca_irmaa import _age_cell

    assert _age_cell(None) == "—"
    assert _age_cell(64) == "64"


def test_readonly_input_stacked_has_no_side_column():
    import views._shared as shared

    st = MagicMock()
    with patch.object(shared, "st", st):
        shared.render_readonly_input(
            "NII", "$20,000/yr", key="nii", page="assumptions", where="Profile", stacked=True
        )
    st.columns.assert_not_called()
    (call,) = st.button.call_args_list
    assert call.args[0] == "Edit in Profile →"


# --- (e) ---------------------------------------------------------------------


def _store_with(path: Path) -> None:
    store = CandidateStore()
    when = datetime(2026, 7, 1)
    ingest.record_candidate(store, "your_ira", 500_000.0, Source.PDF, "statement", when)
    ingest.record_candidate(
        store, "your_ira", 480_000.0, Source.FINEXTRACT_LIVE, "FinExtract", when
    )
    ingest.record_candidate(store, "your_roth", 50_000.0, Source.PDF, "statement", when)
    ingest.record_candidate(store, "spouse_ira", 100_000.0, Source.PDF, "statement", when)
    store.save(path)


def test_discard_pdf_balance_candidates_only_drops_that_persons_pdf(tmp_path):
    path = tmp_path / "store.json"
    _store_with(path)

    assert discard_pdf_balance_candidates(["you"], store_path=path) == 2

    store = CandidateStore.load(path)
    assert [c.prov.source for c in store.candidates_for("your_ira")] == [Source.FINEXTRACT_LIVE]
    assert not store.has_candidates("your_roth")
    assert store.has_candidates("spouse_ira")


def _slot(slot: str, *, incoming: int, removed: int) -> SlotPlan:
    return SlotPlan(
        slot=slot,
        incoming_captured_at=None,
        local_captured_at=None,
        stale=None,
        scalar_writes={},
        accounts_incoming=incoming,
        accounts_replaced=0,
        accounts_removed=removed,
        ledger_rows=0,
        ytd_writes={},
        grants_replaced=False,
    )


def _plan() -> ImportPlan:
    from engine.bridge_import import HouseholdPlan

    return ImportPlan(
        version="v5",
        declared_by="you",
        slots={
            "you": _slot("you", incoming=2, removed=2),
            "spouse": _slot("spouse", incoming=0, removed=0),
        },
        household=HouseholdPlan(accounts_upserted=0, accounts_added=0),
        joint_writes={},
        joint_conflicts=(),
        warnings=(),
    )


def test_slots_with_replaced_accounts():
    from views.setup.data_bridge import slots_with_replaced_accounts

    plan = _plan()
    assert slots_with_replaced_accounts(plan, frozenset({"you", "spouse"})) == ["you"]
    assert slots_with_replaced_accounts(plan, frozenset({"spouse"})) == []


def test_apply_ready_import_drops_stale_pdf_balance_candidate():
    from engine.data_sources import paths
    from views.setup import data_bridge

    _store_with(paths.CANDIDATE_STORE_PATH)
    st = MagicMock()
    st.session_state = _State()
    result = SimpleNamespace(
        scalar_updates={},
        joint_conflicts=(),
        account_type_overrides={},
        magi_candidates={},
        slot_captured_at={},
        snapshot=SimpleNamespace(equity_grants=[]),
    )
    ready = SimpleNamespace(
        bundle=None,
        plan=_plan(),
        local=SimpleNamespace(account_type_overrides={}, slot_captured_at={}),
        selected=frozenset({"you"}),
        operator="you",
        version="v5",
    )
    with (
        patch.object(data_bridge, "st", st),
        patch.object(data_bridge, "apply_import", return_value=result),
        patch.object(data_bridge, "commit_import"),
        patch.object(data_bridge, "_persist_slot_captured_at"),
        patch.object(data_bridge, "_settle_ytd", return_value=False),
    ):
        data_bridge._apply_ready_import(ready, "synthetic.enc")

    store = CandidateStore.load(paths.CANDIDATE_STORE_PATH)
    assert all(c.prov.source != Source.PDF for c in store.candidates_for("your_ira"))
    assert store.has_candidates("spouse_ira")
