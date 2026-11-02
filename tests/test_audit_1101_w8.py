"""audit-1101 W8: notices wiped by st.rerun(), and a render that re-dated YTD.

F-13  the warnings and success note drawn by a bundle import were followed by
      st.rerun(), which discarded them before anyone could read them.
F-23  the Review Changes "Confirmed ..." note and the 1040 "Saved ..." note
      were discarded by the same rerun.
F-16  rendering the YTD page with Manual entry on re-dated the snapshot to
      today on every run, so merely viewing it cleared the stale-data warning.

All figures are synthetic.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_status import compute_ytd_completeness
from engine.store import get_store
from engine.store.namespaces import COMMITTED
from models.household import Household
from models.sourced import Provenance, Source, SourcedValue
from models.ytd_income import IncomeEvent, YTDSnapshot

_NOW = datetime(2026, 7, 24, 12, 0, 0)
_OLD_DATE = "2020-01-02"
_KEPT_NOTE = "Kept your hand-entered Interest YTD (synthetic note)."

# --- F-13: bundle-import notices survive the rerun ---------------------------


def _import_then_render() -> None:
    from types import SimpleNamespace

    import streamlit as st

    import views.setup.data_bridge as db

    # First run: apply the (stubbed) import, which reruns. Second run: the
    # Backup & Transfer upload panel, where the carried notices must appear.
    if not st.session_state.get("_w8_applied"):
        st.session_state["_w8_applied"] = True
        ready = SimpleNamespace(
            bundle=None,
            plan=None,
            local=SimpleNamespace(account_type_overrides={}, slot_captured_at={}),
            selected=frozenset({"you"}),
            operator=None,
            data={"sections": {"setup_scalars": {}}},
            version="v5",
        )
        db._apply_ready_import(ready, "synthetic.enc")
    db._handle_personal_uploads()


@pytest.fixture
def stubbed_import(monkeypatch: pytest.MonkeyPatch) -> None:
    import views.setup.data_bridge as db

    result = SimpleNamespace(
        scalar_updates={},
        joint_conflicts=(),
        account_type_overrides={},
        magi_candidates={},
        slot_captured_at={},
        snapshot=None,
        ytd=None,
        ledger=None,
    )
    monkeypatch.setattr(db, "apply_import", lambda *a, **k: result)
    monkeypatch.setattr(db, "commit_import", lambda *a, **k: None)
    monkeypatch.setattr(db, "slots_with_replaced_accounts", lambda *a: [])
    monkeypatch.setattr(db, "discard_pdf_balance_candidates", lambda *a: None)
    monkeypatch.setattr(db, "_persist_slot_captured_at", lambda *a: None)
    monkeypatch.setattr(db, "_settle_ytd", lambda *a: [_KEPT_NOTE])


def test_f13_import_warning_and_success_survive_the_rerun(stubbed_import) -> None:
    at = AppTest.from_function(_import_then_render, default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _KEPT_NOTE in [w.value for w in at.warning]
    assert any("Applied: synthetic.enc" in s.value for s in at.success)


def test_f13_import_notices_show_exactly_once(stubbed_import) -> None:
    at = AppTest.from_function(_import_then_render, default_timeout=30)
    at.run()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert _KEPT_NOTE not in [w.value for w in at.warning]
    assert not any("Applied: synthetic.enc" in s.value for s in at.success)


# --- F-23: Review Changes confirm note survives the rerun --------------------


def _seed_pending_roth() -> None:
    committed = {"your_roth": SourcedValue(1_000.0, Provenance(Source.UNKNOWN, _NOW)).to_json()}
    get_store().put(COMMITTED, committed)
    store = CandidateStore()
    store.record_candidate("your_roth", 12_345.0, Provenance(Source.PDF, _NOW, "synthetic stmt"))
    store.save()
    ChoiceMap().save()


def _render_command_center() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup.command_center import render_command_center

    st.session_state.setdefault("_pending_review", {"your_roth"})
    render_command_center(Household())


def test_f23_confirm_note_survives_the_rerun() -> None:
    _seed_pending_roth()
    at = AppTest.from_function(_render_command_center, default_timeout=30)
    at.run()
    at.button(key="confirm_your_roth").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Confirmed Your Roth balance" in s.value for s in at.success)


def test_f23_confirm_note_shows_exactly_once() -> None:
    _seed_pending_roth()
    at = AppTest.from_function(_render_command_center, default_timeout=30)
    at.run()
    at.button(key="confirm_your_roth").click().run()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not any("Confirmed Your Roth balance" in s.value for s in at.success)


# --- F-23: 1040 save note survives the rerun ---------------------------------


def _render_1040_import() -> None:
    import streamlit as st

    from engine.tax_return_pdf import Form1040Record
    from views.setup.parameters import _render_pdf_1040_import

    if "_pdf_1040_scanned" not in st.session_state:
        st.session_state["_pdf_1040_scanned"] = {
            2025: Form1040Record(
                tax_year=2025,
                agi=120_000.0,
                tax_exempt_interest=0.0,
                taxable_ss=0.0,
                qualified_dividends=0.0,
                ordinary_dividends=0.0,
                feie=0.0,
                magi=120_000.0,
                filing_status=None,
                captured_at="2026-02-01T00:00:00",
            )
        }
    _render_pdf_1040_import()


def _rerun_after_save(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """The run st.rerun() starts after a 1040 save, on a clean element tree.

    AppTest keeps the elements an interrupted run drew when the rerun inside
    the same ``run()`` draws fewer, which the browser never shows, so the save
    is clicked with st.rerun() disabled and the rerun is a fresh AppTest
    seeded with the non-widget session state the click left behind.
    """
    import streamlit

    monkeypatch.setattr(streamlit, "rerun", lambda: None)
    clicked = AppTest.from_function(_render_1040_import, default_timeout=30)
    clicked.run()
    clicked.button(key="_pdf_1040_save_2025").click().run()
    assert not clicked.exception, [e.value for e in clicked.exception]
    monkeypatch.undo()

    rerun = AppTest.from_function(_render_1040_import, default_timeout=30)
    for key, value in clicked.session_state.filtered_state.items():
        rerun.session_state[key] = value
    rerun.run()
    assert not rerun.exception, [e.value for e in rerun.exception]
    return rerun


def test_f23_1040_save_notes_survive_the_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _rerun_after_save(monkeypatch)
    assert any("Saved 2025 1040 record" in s.value for s in at.success)
    assert any("Review Changes" in i.value for i in at.info)


def test_f23_1040_save_notes_show_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _rerun_after_save(monkeypatch)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not any("Saved 2025 1040 record" in s.value for s in at.success)
    assert not any("Review Changes" in i.value for i in at.info)


# --- F-16: viewing YTD with Manual entry on does not re-date it --------------


def _render_manual() -> None:
    import streamlit as st

    from models.household import Household
    from views.ytd_income._partials._manual_entry import render_manual_entry_partial

    st.session_state.setdefault("instance_owner", "you")
    render_manual_entry_partial(Household(filing_status="MFJ"))


def _run_manual(ytd: YTDSnapshot) -> AppTest:
    at = AppTest.from_function(_render_manual, default_timeout=30)
    at.session_state["ytd_snapshot"] = ytd
    at.session_state["ytd_manual_entry"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _base_year() -> int:
    return Household(filing_status="MFJ").base_year


def test_f16_viewing_keeps_the_snapshot_date_and_the_stale_warning() -> None:
    old = YTDSnapshot(tax_year=_base_year(), interest_ytd=1_000.0, snapshot_date=_OLD_DATE)
    assert compute_ytd_completeness(old, now=datetime.now()).issues

    at = _run_manual(old)
    at.run()  # a second view of the page
    assert not at.exception, [e.value for e in at.exception]

    ytd = at.session_state["ytd_snapshot"]
    assert ytd.snapshot_date == _OLD_DATE
    assert compute_ytd_completeness(ytd, now=datetime.now()).issues


def test_f16_save_still_dates_the_snapshot() -> None:
    at = _run_manual(YTDSnapshot(tax_year=_base_year(), snapshot_date=_OLD_DATE))
    at.number_input(key="ytd_w_household_interest_ytd").set_value(2_500)
    at.button(key="ytd_manual_save").click().run()
    assert not at.exception, [e.value for e in at.exception]
    ytd = at.session_state["ytd_snapshot"]
    assert ytd.interest_ytd == pytest.approx(2_500.0)
    assert ytd.snapshot_date == date.today().isoformat()


def test_f16_an_event_log_change_still_dates_the_snapshot() -> None:
    # The stored total lags the logged conversion, so the overlay changes data.
    conv = IncomeEvent(date=f"{_base_year()}-03-01", amount=5_000.0, kind="conversion", owner="you")
    stale = YTDSnapshot(
        tax_year=_base_year(),
        ira_conversions_ytd=0.0,
        income_events=[conv],
        snapshot_date=_OLD_DATE,
    )
    at = _run_manual(stale)
    ytd = at.session_state["ytd_snapshot"]
    assert ytd.ira_conversions_ytd == pytest.approx(5_000.0)
    assert ytd.snapshot_date == date.today().isoformat()
