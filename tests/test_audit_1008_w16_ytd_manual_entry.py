"""audit-1008 W16: YTD manual entry / event log.

F-36  a stale session ``income_events`` list overrode an imported snapshot's
      events, so the manual-entry overlay wiped the spouse's conversions.
F-76  the ``magi_ytd`` docstring described IRMAA MAGI wrongly.
F-87  the manual-entry overlay dropped the per-grant NQO breakdown.
W9 follow-up: the manual-entry form had no crypto lending-interest field.

All figures are synthetic.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from models.ytd_income import (
    MANUAL_HOUSEHOLD_FIELDS,
    TRANSIENT_ATTRS,
    IncomeEvent,
    YTDSnapshot,
)
from views.ytd_drafts import FIELD_LABELS, FLOAT_FIELDS

_YOU_CONV = IncomeEvent(date="2026-03-01", amount=5_000.0, kind="conversion", owner="you")
_SPOUSE_CONV = IncomeEvent(date="2026-04-01", amount=10_000.0, kind="conversion", owner="spouse")


def _render_manual() -> None:
    import streamlit as st

    from models.household import Household
    from views.ytd_income._partials._manual_entry import render_manual_entry_partial

    st.session_state.setdefault("instance_owner", "you")
    render_manual_entry_partial(Household(filing_status="MFJ"))


def _run(ytd: YTDSnapshot, **session) -> AppTest:
    at = AppTest.from_function(_render_manual, default_timeout=30)
    at.session_state["ytd_snapshot"] = ytd
    at.session_state["ytd_manual_entry"] = True
    for key, value in session.items():
        at.session_state[key] = value
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _imported() -> YTDSnapshot:
    """What a v5 spouse import leaves in session: both people's events."""
    return YTDSnapshot(
        ira_conversions_ytd=5_000.0,
        spouse_ira_conversions_ytd=10_000.0,
        income_events=[_YOU_CONV, _SPOUSE_CONV],
    )


# --- F-36 --------------------------------------------------------------------


class TestStaleIncomeEvents:
    def test_import_wins_over_a_stale_session_event_list(self):
        # The user logged their own $5k conversion earlier in the session (the
        # event log wrote session_state["income_events"]), then imported the
        # spouse's bundle. The page must show the imported events.
        at = _run(_imported(), income_events=[_YOU_CONV])
        ytd = at.session_state["ytd_snapshot"]
        assert ytd.spouse_ira_conversions_ytd == pytest.approx(10_000.0)
        assert ytd.ira_conversions_ytd == pytest.approx(5_000.0)
        assert [(e.owner, e.amount) for e in ytd.income_events] == [
            ("you", 5_000.0),
            ("spouse", 10_000.0),
        ]

    def test_session_list_still_used_when_snapshot_unchanged(self):
        # A removal is written to session and the page reruns BEFORE the
        # overlay saves it: the session list must win while the snapshot is
        # the one it was seeded from.
        at = _run(_imported())
        at.session_state["income_events"] = [_SPOUSE_CONV]
        at.run()
        assert not at.exception
        ytd = at.session_state["ytd_snapshot"]
        assert [(e.owner, e.amount) for e in ytd.income_events] == [("spouse", 10_000.0)]
        assert ytd.ira_conversions_ytd == 0.0
        assert ytd.spouse_ira_conversions_ytd == pytest.approx(10_000.0)

    def test_second_import_after_render_reseeds(self):
        at = _run(YTDSnapshot(ira_conversions_ytd=5_000.0, income_events=[_YOU_CONV]))
        at.session_state["ytd_snapshot"] = _imported()
        at.run()
        assert not at.exception
        ytd = at.session_state["ytd_snapshot"]
        assert ytd.spouse_ira_conversions_ytd == pytest.approx(10_000.0)
        assert len(ytd.income_events) == 2


# --- F-87 --------------------------------------------------------------------


def _with_breakdown(ytd: YTDSnapshot) -> YTDSnapshot:
    ytd._option_exercises_by_grant = {"GA-1": 10_000.0}  # type: ignore[attr-defined]  # noqa: SLF001
    ytd._option_exercises_sale_info = {"ZZ": {"strike": 90.0}}  # type: ignore[attr-defined]  # noqa: SLF001
    return ytd


class TestPerGrantBreakdownSurvives:
    @pytest.mark.parametrize(
        "copy",
        [
            lambda y: y.overlay(manually_entered=True),
            lambda y: y.attribute_unowned("you"),
            lambda y: y.attribute_unowned("you").replace_owner_values("you", {}),
            lambda y: y.attribute_unowned("you").with_manual_values(
                owner="you", owned={"wages_ytd": 1.0}, household={}, as_of="2026-01-01"
            ),
        ],
        ids=["overlay", "attribute_unowned", "replace_owner_values", "with_manual_values"],
    )
    def test_copy_helpers_carry_the_breakdown(self, copy):
        out = copy(_with_breakdown(YTDSnapshot(nqo_exercise_ytd=30_000.0)))
        assert out._option_exercises_by_grant == {"GA-1": 10_000.0}  # noqa: SLF001
        assert out._option_exercises_sale_info == {"ZZ": {"strike": 90.0}}  # noqa: SLF001

    def test_copies_do_not_share_the_dict(self):
        src = _with_breakdown(YTDSnapshot())
        out = src.overlay()
        out._option_exercises_by_grant["GA-1"] = 0.0  # noqa: SLF001
        assert src._option_exercises_by_grant == {"GA-1": 10_000.0}  # noqa: SLF001

    def test_snapshot_without_breakdown_gains_no_attribute(self):
        out = YTDSnapshot().overlay(wages_ytd=1.0)
        for name in TRANSIENT_ATTRS:
            assert not hasattr(out, name)

    def test_manual_entry_render_keeps_breakdown_in_session(self):
        at = _run(_with_breakdown(YTDSnapshot(nqo_exercise_ytd=30_000.0)))
        ytd = at.session_state["ytd_snapshot"]
        assert ytd._option_exercises_by_grant == {"GA-1": 10_000.0}  # noqa: SLF001


# --- F-76 --------------------------------------------------------------------


def test_magi_ytd_docstring_matches_irmaa_magi():
    doc = YTDSnapshot.magi_ytd.__doc__ or ""
    # §1839(i)(4): AGI + tax-exempt interest (+ excluded foreign income). Untaxed
    # Social Security belongs to ACA MAGI (§36B), not IRMAA MAGI.
    assert "non-taxable SS + foreign" not in doc
    assert "1839(i)(4)" in doc
    assert "Social Security" in doc


# --- W9 follow-up: crypto lending interest ----------------------------------


class TestCryptoInterestField:
    def test_is_a_manual_household_field(self):
        assert "crypto_interest_ytd" in MANUAL_HOUSEHOLD_FIELDS
        assert "crypto_interest_ytd" in FIELD_LABELS
        assert "crypto_interest_ytd" in FLOAT_FIELDS

    def test_form_shows_the_imported_interest(self):
        ytd = YTDSnapshot(crypto_income_ytd=3_000.0, crypto_interest_ytd=1_200.0)
        at = _run(ytd)
        widget = at.number_input(key="ytd_w_household_crypto_interest_ytd")
        assert widget.value == pytest.approx(1_200.0)

    def test_hand_entered_interest_is_saved_and_counts_as_nii(self):
        ytd = YTDSnapshot(crypto_income_ytd=3_000.0, crypto_interest_ytd=1_200.0)
        at = _run(ytd)
        at.number_input(key="ytd_w_household_crypto_interest_ytd").set_value(400.0).run()
        next(b for b in at.button if b.label == "Save entries").click().run()
        assert not at.exception
        saved = at.session_state["ytd_snapshot"]
        assert saved.crypto_interest_ytd == pytest.approx(400.0)
        assert saved.total_investment_income == pytest.approx(400.0)
        assert "household.crypto_interest_ytd" in saved.manual_marks
