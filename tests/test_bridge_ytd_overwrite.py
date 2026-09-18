"""A role-swapped bundle import must never overwrite the receiver's own YTD figures.

Measured on a full A->B->A data-bridge cycle (synthetic probe, 2026-09-18):

    wages_ytd                100,000 -> 77,000
    qualified_dividends_ytd    4,000 -> 7
    nqo_exercise_ytd          50,000 -> 0
    federal_withholding_ytd   20,000 -> 7,700
    ira_conversions_ytd       30,000 -> 700

Cause: ``views/setup/data_bridge.py`` seeded ``session_state["ytd_snapshot"]``
with the SENDER's whole snapshot before ``_rederive_ytd_from_ledger`` ran.
That re-derive only restores the 5 brokerage fields plus the 3 crypto ones, so
every other field silently kept the sender's value. The bundle's "ytd" section
is household-wide, not owner-sliced (``engine/bridge_bundle.py:99``), so it has
no business landing in the receiver's session at all.

``nqo_exercise_ytd`` is the sharpest edge: only one person in the household
holds options, so the other's correctly-zero value erased the entire option
income this planner exists to model -- with every downstream projection still
looking plausible.

The ledger-derived fields are a deliberate exception: they are recomputed from
the MERGED, owner-sliced ledger and must keep summing both people's figures.
"""

from __future__ import annotations

import json

import pytest

from engine.bridge_bundle import build_bundle
from models.ytd_income import YTDSnapshot


def _receiver_snapshot() -> YTDSnapshot:
    """The importing user's own YTD state, all multiples of 1000."""
    return YTDSnapshot(
        wages_ytd=100_000.0,
        qualified_dividends_ytd=4_000.0,
        nqo_exercise_ytd=50_000.0,
        federal_withholding_ytd=20_000.0,
        ira_conversions_ytd=30_000.0,
        interest_ytd=5_000.0,
    )


def _sender_snapshot() -> YTDSnapshot:
    """The other household member's YTD state, all multiples of 7."""
    return YTDSnapshot(
        wages_ytd=77_000.0,
        qualified_dividends_ytd=7.0,
        nqo_exercise_ytd=0.0,
        federal_withholding_ytd=7_700.0,
        ira_conversions_ytd=700.0,
        interest_ytd=35.0,
    )


def _receiver_ledger() -> dict:
    return {
        "koinly": {"you": {"stcg": 1_000.0, "ltcg": 2_000.0, "income": 3_000.0}},
        "brokerage": {"you": {"R1": {"interest_taxable_ytd": 5_000.0}}},
    }


def _sender_bundle() -> dict:
    """What the other member exports: their own slice, plus a household-wide ytd."""
    sender_ledger = {
        "koinly": {"you": {"stcg": 7.0, "ltcg": 14.0, "income": 21.0}},
        "brokerage": {"you": {"S1": {"interest_taxable_ytd": 35.0}}},
    }
    return build_bundle(
        {},
        None,
        sender_ledger,
        owner="you",
        ytd=_sender_snapshot(),
    )


def _drive_apply(at, payload_bytes: bytes) -> None:
    uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
    uploader.set_value(("roth_bridge.enc", payload_bytes, "application/octet-stream"))
    apply_button = next(b for b in at.button if b.key == "apply_uploads")
    apply_button.set_value(True)
    at.run()


def _run_import(monkeypatch: pytest.MonkeyPatch, *, receiver_has_snapshot: bool):
    """Import the sender's bundle as 'spouse'; return the resulting session snapshot."""
    import streamlit as st_mod
    from streamlit.testing.v1 import AppTest

    import engine.data_bridge_crypto as data_bridge_crypto_mod
    import views.setup.data_bridge as data_bridge_mod

    payload_bytes = json.dumps(_sender_bundle()).encode("utf-8")
    saved: dict = {}

    monkeypatch.setattr(
        data_bridge_crypto_mod, "open_uploaded_payload", lambda raw, privkey: payload_bytes
    )
    monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
    monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", _receiver_ledger)
    monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", lambda ledger: None)
    monkeypatch.setattr(data_bridge_mod, "save_ytd_snapshot", lambda snap: saved.update(snap=snap))
    monkeypatch.setattr(data_bridge_mod, "load_ytd_snapshot", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(st_mod, "rerun", lambda: None)

    def _render(receiver_snapshot=None) -> None:
        # AppTest.from_function re-executes this body in a fresh module, so it
        # captures no closure variables -- everything it needs arrives via kwargs.
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_uploads

        st.session_state["instance_owner"] = "you"
        if receiver_snapshot is not None:
            st.session_state["ytd_snapshot"] = receiver_snapshot
        _handle_personal_uploads()

    at = AppTest.from_function(
        _render,
        kwargs={"receiver_snapshot": _receiver_snapshot() if receiver_has_snapshot else None},
    )
    at.run()
    _drive_apply(at, payload_bytes)
    assert not at.exception
    # AppTest's session_state proxies attribute access to keys, so .get() is a
    # lookup for a key named "get", not the dict method.
    return at.session_state["ytd_snapshot"]


class TestReceiverYTDSurvivesRoleSwappedImport:
    """The five fields the ledger cannot re-derive must stay the receiver's."""

    @pytest.mark.parametrize(
        ("field_name", "expected"),
        [
            ("wages_ytd", 100_000.0),
            ("qualified_dividends_ytd", 4_000.0),
            ("nqo_exercise_ytd", 50_000.0),
            ("federal_withholding_ytd", 20_000.0),
            ("ira_conversions_ytd", 30_000.0),
        ],
    )
    def test_non_ledger_field_is_not_replaced_by_sender(
        self, monkeypatch: pytest.MonkeyPatch, field_name: str, expected: float
    ) -> None:
        snap = _run_import(monkeypatch, receiver_has_snapshot=True)
        assert snap is not None
        assert getattr(snap, field_name) == expected

    def test_option_income_is_not_zeroed_by_an_optionless_spouse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline defect, asserted on its own so a regression names itself."""
        snap = _run_import(monkeypatch, receiver_has_snapshot=True)
        assert snap is not None
        assert snap.nqo_exercise_ytd == 50_000.0, (
            "the receiver's option income was replaced by the sender's zero"
        )


class TestLedgerDerivedFieldsStillCombine:
    """The deliberate exception: these are recomputed from the merged ledger."""

    @pytest.mark.parametrize(
        ("field_name", "expected"),
        [
            ("crypto_stcg_ytd", 1_007.0),
            ("crypto_ltcg_ytd", 2_014.0),
            ("crypto_income_ytd", 3_021.0),
        ],
    )
    def test_crypto_totals_sum_both_owners(
        self, monkeypatch: pytest.MonkeyPatch, field_name: str, expected: float
    ) -> None:
        snap = _run_import(monkeypatch, receiver_has_snapshot=True)
        assert snap is not None
        assert getattr(snap, field_name) == expected

    def test_interest_sums_both_owners(self, monkeypatch: pytest.MonkeyPatch) -> None:
        snap = _run_import(monkeypatch, receiver_has_snapshot=True)
        assert snap is not None
        assert snap.interest_ytd == 5_035.0


class TestFreshReceiver:
    """A receiver with no snapshot must not inherit the sender's non-ledger figures."""

    def test_sender_non_ledger_values_are_not_adopted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        snap = _run_import(monkeypatch, receiver_has_snapshot=False)
        assert snap is not None
        assert snap.wages_ytd == 0.0
        assert snap.nqo_exercise_ytd == 0.0
        assert snap.federal_withholding_ytd == 0.0

    def test_ledger_derived_values_still_populate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        snap = _run_import(monkeypatch, receiver_has_snapshot=False)
        assert snap is not None
        assert snap.crypto_stcg_ytd == 1_007.0
        assert snap.interest_ytd == 5_035.0
