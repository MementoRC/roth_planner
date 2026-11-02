"""audit-1101 W9: display fixes (F-15, F-19, F-24, F-25) + the Reset-to-demo note.

* F-15 — ``"$,.0s"`` keeps ONE significant digit, so neighbouring axis ticks
  repeat ("$1M", "$1M", "$2M"). Every dollar axis uses the shared
  ``views._format.DOLLAR_TICKFORMAT`` instead.
* F-19 — the ACA+IRMAA legend said "2 people" for every MFJ household; it now
  names the Medicare head count the surcharge curve was computed with.
* F-24 — "You's" / "you dies": the "you" owner takes "your"/"yours" and the
  bare verb.
* F-25 — the default short-dollar format rendered small balances as "$0.0M".
* Reset to demo — its success note was drawn just before st.rerun() and
  discarded; it now rides the import-notice carry-over (as W8's F-13).

All data is synthetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from engine.aca_irmaa_compute import compute_cost_curves
from models.household import Household
from models.ytd_income import YTDSnapshot

_VIEWS = Path(__file__).resolve().parent.parent / "views"

# --- F-15: one shared tick format, no one-digit "$,.0s" ----------------------


def test_f15_no_view_uses_a_one_digit_si_tick_format() -> None:
    offenders = [
        f"{p.relative_to(_VIEWS)}:{n}"
        for p in sorted(_VIEWS.rglob("*.py"))
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if ',.0s"' in line
    ]
    assert offenders == [], offenders


def test_f15_shared_tick_format_keeps_three_significant_digits() -> None:
    from views import _format

    assert _format.DOLLAR_TICKFORMAT == "$,.3~s"


# --- F-19: legend names the real Medicare head count -------------------------


def test_f19_mfj_with_one_spouse_on_medicare_counts_one_person() -> None:
    # Payment year = base_year + 2: you 66 -> 68 (Medicare), spouse 55 -> 57 (not).
    hh = Household(filing_status="MFJ", your_age=66, spouse_age=55)
    curves = compute_cost_curves(
        [300_000.0], 300_000.0, 0.0, hh, year=hh.base_year, cpi=hh.cpi_assumption
    )
    assert curves.irmaa_people == 1


def test_f19_legend_label_follows_the_head_count() -> None:
    from views.aca_irmaa import irmaa_legend_name

    assert irmaa_legend_name(0) == "IRMAA Surcharge (nobody on Medicare)"
    assert irmaa_legend_name(1) == "IRMAA Surcharge (1 person)"
    assert irmaa_legend_name(2) == "IRMAA Surcharge (2 people)"


# --- F-24: grammar for the "you" owner ---------------------------------------


def test_f24_possessive_and_verb_helpers() -> None:
    from views._format import owner_possessive, verb_for

    assert owner_possessive("You") == "Your"
    assert owner_possessive("You", standalone=True) == "yours"
    assert owner_possessive("Me") == "My"
    assert owner_possessive("Spouse") == "Spouse's"
    assert owner_possessive("Spouse", standalone=True) == "Spouse's"
    assert verb_for("you", "die") == "die"
    assert verb_for("spouse", "die") == "dies"


def _render_manual() -> None:
    import streamlit as st

    from models.household import Household
    from views.ytd_income._partials._manual_entry import render_manual_entry_partial

    st.session_state.setdefault("instance_owner", "you")
    render_manual_entry_partial(Household(filing_status="MFJ"))


def test_f24_manual_entry_caption_reads_yours() -> None:
    at = AppTest.from_function(_render_manual, default_timeout=30)
    at.session_state["ytd_snapshot"] = YTDSnapshot(tax_year=Household().base_year)
    at.session_state["ytd_manual_entry"] = True
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    captions = [c.value for c in at.caption if "Wages" in c.value]
    assert captions, [c.value for c in at.caption]
    assert "You's" not in captions[0]
    assert "below are yours." in captions[0]


# --- F-25: small values never read "$0.0M" -----------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(42_000, "$42.0K"), (-42_000, "$-42.0K"), (400, "$400.0"), (1_500_000, "$1.5M")],
)
def test_f25_default_short_format_scales_down_when_m_would_round_to_zero(
    value: float, expected: str
) -> None:
    from views._format import fmt_dollars_short

    assert fmt_dollars_short(value) == expected


def test_f25_m_suffix_kept_when_it_does_not_round_to_zero() -> None:
    from views._format import fmt_dollars_short

    assert fmt_dollars_short(60_000) == "$0.1M"
    assert fmt_dollars_short(0) == "$0.0M"


# --- Reset to demo: the success note survives the rerun ----------------------


def _render_uploads() -> None:
    import views.setup.data_bridge as db

    db._handle_personal_uploads()


def _rerun_after_reset(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """The run st.rerun() starts after Reset to demo, on a clean element tree
    (same harness as W8's 1040 test: AppTest keeps an interrupted run's
    elements, which the browser never shows)."""
    import streamlit

    monkeypatch.setattr(streamlit, "rerun", lambda: None)
    clicked = AppTest.from_function(_render_uploads, default_timeout=30)
    clicked.run()
    clicked.button(key="reset_demo").click().run()
    assert not clicked.exception, [e.value for e in clicked.exception]
    monkeypatch.undo()

    rerun = AppTest.from_function(_render_uploads, default_timeout=30)
    for key, value in clicked.session_state.filtered_state.items():
        rerun.session_state[key] = value
    rerun.run()
    assert not rerun.exception, [e.value for e in rerun.exception]
    return rerun


def test_reset_to_demo_note_survives_the_rerun(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _rerun_after_reset(monkeypatch)
    assert "Reset to demo defaults." in [s.value for s in at.success]


def test_reset_to_demo_note_shows_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _rerun_after_reset(monkeypatch)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Reset to demo defaults." not in [s.value for s in at.success]
