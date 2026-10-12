"""Bridge v5 export (plan task B6).

Export now seals a v5 envelope: the exporter's own slot always, the other
person's slot opportunistically, each stamped with ``captured_at``. The export
identity gate is KEPT (plan R11) -- identity is what says which slot is
"mine".

``st.download_button`` has no AppTest accessor, so these tests spy on it (the
same technique ``tests/test_shells.py`` uses for the export gate) and read the
built payload from its ``data=`` argument, with sealing replaced by identity
so the payload is plain JSON.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from models.ytd_income import YTDSnapshot
from tests.test_bridge_ytd_overwrite import _receiver_ledger, _receiver_snapshot

_SENDER_LEDGER = {
    "koinly": {"spouse": {"stcg": 7.0, "ltcg": 14.0, "income": 21.0}},
    "brokerage": {"spouse": {"S1": {"interest_taxable_ytd": 35.0}}},
}


def _sender_ytd() -> YTDSnapshot:
    """The spouse's planner: its own non-ledger YTD in the spouse slot."""
    return YTDSnapshot(
        by_owner={
            "spouse": {
                "wages_ytd": 77_000.0,
                "qualified_dividends_ytd": 7.0,
                "nqo_exercise_ytd": 0.0,
                "federal_withholding_ytd": 7_700.0,
            }
        },
        spouse_ira_conversions_ytd=700.0,
        interest_ytd=35.0,
    )


def _patch_export(monkeypatch: pytest.MonkeyPatch, *, slot_captured_at: dict | None = None):
    """Spy download_button, seal as identity, and stub every export-side read."""
    import streamlit as st_mod

    import config.loader as loader_mod
    import engine.data_bridge_crypto as data_bridge_crypto_mod
    import views.setup.data_bridge as data_bridge_mod

    download_button_spy = MagicMock(return_value=False)
    monkeypatch.setattr(st_mod, "download_button", download_button_spy)
    monkeypatch.setattr(data_bridge_crypto_mod, "seal", lambda payload, pubkey: payload)
    monkeypatch.setattr(data_bridge_mod, "_resolved_pubkey", lambda: b"\x00" * 32)
    monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
    monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", lambda: _SENDER_LEDGER)
    monkeypatch.setattr(data_bridge_mod, "load_ytd_snapshot", lambda: None)
    persisted = {"slot_captured_at": slot_captured_at} if slot_captured_at else {}
    monkeypatch.setattr(loader_mod, "load_raw_user_defaults", lambda: dict(persisted))
    return download_button_spy


def _export_app(owner: str | None, ytd: YTDSnapshot | None = None):
    from streamlit.testing.v1 import AppTest

    def _render(owner=None, ytd=None) -> None:
        import streamlit as st

        from views.setup.data_bridge import _handle_personal_exports

        if owner is not None:
            st.session_state["instance_owner"] = owner
        st.session_state.setdefault("your_age", 61)
        st.session_state.setdefault("spouse_age", 56)
        if ytd is not None:
            st.session_state.setdefault("ytd_snapshot", ytd)
        _handle_personal_exports()

    at = AppTest.from_function(_render, kwargs={"owner": owner, "ytd": ytd})
    at.run()
    return at


def _exported_payload(download_button_spy: MagicMock) -> dict:
    download_button_spy.assert_called_once()
    data = download_button_spy.call_args.kwargs["data"]
    assert data, "export produced no payload"
    return json.loads(data)


class TestExportV5:
    def test_export_emits_v5_with_exporter_slot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        spy = _patch_export(monkeypatch)
        at = _export_app("spouse", _sender_ytd())
        assert not at.exception

        payload = _exported_payload(spy)
        assert payload["format_version"] == 5
        assert payload["exported_by"] == "spouse"
        spouse_slot = payload["slots"]["spouse"]
        assert spouse_slot["setup_scalars"] == {"spouse_age": 56}
        assert spouse_slot["captured_at"] == payload["exported_at"]
        assert spouse_slot["ytd"]["values"]["wages_ytd"] == 77_000.0
        assert spouse_slot["ytd"]["ira_conversions"] == 700.0
        # This planner also holds a copy of the other person, so it rides along
        # (opportunistic other slot), with capture time unknown here.
        assert payload["slots"]["you"]["setup_scalars"] == {"your_age": 61}
        assert payload["slots"]["you"]["captured_at"] is None
        assert payload["records"]["ledger"] == _SENDER_LEDGER

    def test_export_carries_persisted_capture_time_for_other_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spy = _patch_export(monkeypatch, slot_captured_at={"you": "2026-09-01T09:00:00"})
        at = _export_app("spouse", _sender_ytd())
        assert not at.exception

        payload = _exported_payload(spy)
        assert payload["slots"]["you"]["captured_at"] == "2026-09-01T09:00:00"

    def test_export_still_disabled_when_identity_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plan R11: identity still says which slot is 'mine', so export keeps its gate."""
        import engine.bridge_bundle as bridge_bundle_mod

        spy = _patch_export(monkeypatch)
        build_spy = MagicMock()
        monkeypatch.setattr(bridge_bundle_mod, "build_bundle_v5", build_spy)

        at = _export_app(None)
        assert not at.exception

        spy.assert_called_once()
        assert spy.call_args.kwargs["disabled"] is True
        build_spy.assert_not_called()

    def test_export_click_does_not_move_local_captured_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A download click must not stamp this planner's capture time.

        ``on_click`` cannot know whether the browser saved the file -- on stlite
        a click whose fetch fails still runs it -- and the bytes come from an
        earlier render. Stamping there made a user's own fresh export look
        stale on re-import. Local capture times move only on import.
        """
        from streamlit.testing.v1 import AppTest

        import views.setup.data_bridge as data_bridge_mod

        saved: list[dict] = []
        spy = _patch_export(monkeypatch, slot_captured_at={"you": "2026-09-01T09:00:00"})
        monkeypatch.setattr(data_bridge_mod, "save_user_defaults", saved.append)
        at = _export_app("spouse", _sender_ytd())
        assert not at.exception
        kwargs = spy.call_args.kwargs

        def _click(on_click=None, click_args=()) -> None:
            # Run whatever the button would run on a click, as Streamlit would.
            if callable(on_click):
                on_click(*click_args)

        clicked = AppTest.from_function(
            _click,
            kwargs={"on_click": kwargs.get("on_click"), "click_args": kwargs.get("args") or ()},
        )
        clicked.run()
        assert not clicked.exception
        assert saved == []
        assert "_slot_captured_at" not in clicked.session_state

    def test_reimport_own_fresh_export_is_not_flagged_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The browser sequence that produced a false "older than" warning.

        Render 1 builds the file's bytes at T1. A later rerun (T2) re-wires the
        button, and the click runs with T2's arguments while the browser saves
        T1's bytes. Re-importing that file on the same planner must never be
        called older than what the planner holds.
        """
        import streamlit as st_mod
        from streamlit.testing.v1 import AppTest

        import config.loader as loader_mod
        import views.setup.data_bridge as data_bridge_mod

        spy = _patch_export(monkeypatch)
        # One in-memory .user_defaults.json shared by every render below.
        store: dict = {}
        monkeypatch.setattr(loader_mod, "load_raw_user_defaults", lambda: dict(store))
        monkeypatch.setattr(data_bridge_mod, "save_user_defaults", store.update)

        times = iter(["2026-09-30T14:50:53", "2026-09-30T16:41:16"])
        last = ["2026-09-30T16:41:16"]

        class _Clock:
            @staticmethod
            def now():
                class _Stamp:
                    def __init__(self, value: str) -> None:
                        self._value = value

                    def isoformat(self, timespec: str = "seconds") -> str:
                        return self._value

                last[0] = next(times, last[0])
                return _Stamp(last[0])

        monkeypatch.setattr(data_bridge_mod, "datetime", _Clock)

        first = _export_app("you")  # render 1 at T1: the bytes the browser saves
        assert not first.exception
        saved_bytes = spy.call_args_list[0].kwargs["data"]
        second = _export_app("you")  # rerun at T2: the button the user clicks
        assert not second.exception
        wired = spy.call_args_list[-1].kwargs

        def _click(on_click=None, click_args=()) -> None:
            if callable(on_click):
                on_click(*click_args)

        clicked = AppTest.from_function(
            _click,
            kwargs={"on_click": wired.get("on_click"), "click_args": wired.get("args") or ()},
        )
        clicked.run()
        assert not clicked.exception

        # Re-import the saved file on the same planner.
        monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
        monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", lambda ledger: None)
        monkeypatch.setattr(data_bridge_mod, "save_ytd_snapshot", lambda snap: None)
        monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
        monkeypatch.setattr(st_mod, "rerun", lambda: None)

        def _render() -> None:
            import streamlit as st

            from views.setup.data_bridge import _handle_personal_uploads

            st.session_state["instance_owner"] = "you"
            _handle_personal_uploads()

        at = AppTest.from_function(_render)
        at.run()
        uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
        uploader.set_value(("roth_bridge.enc", saved_bytes, "application/octet-stream"))
        at.run()
        assert not at.exception
        assert not any("older than" in w.value for w in at.warning)


class TestV5RoundTrip:
    """Export from the spouse's planner, import on yours: #496 figures sum."""

    def test_round_trip_sums_both_people(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import streamlit as st_mod
        from streamlit.testing.v1 import AppTest

        import views.setup.data_bridge as data_bridge_mod

        spy = _patch_export(monkeypatch)
        exporter = _export_app("spouse", _sender_ytd())
        assert not exporter.exception
        payload_bytes = spy.call_args.kwargs["data"]

        # Receiver side: your planner, your own YTD and ledger.
        monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
        monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
        monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", _receiver_ledger)
        monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", lambda ledger: None)
        monkeypatch.setattr(data_bridge_mod, "save_ytd_snapshot", lambda snap: None)
        monkeypatch.setattr(data_bridge_mod, "save_user_defaults", lambda payload: None)
        monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
        monkeypatch.setattr(st_mod, "rerun", lambda: None)

        def _render(receiver_snapshot=None) -> None:
            import streamlit as st

            from views.setup.data_bridge import _handle_personal_uploads

            st.session_state["instance_owner"] = "you"
            if "ytd_snapshot" not in st.session_state:
                st.session_state["ytd_snapshot"] = receiver_snapshot
            _handle_personal_uploads()

        at = AppTest.from_function(_render, kwargs={"receiver_snapshot": _receiver_snapshot()})
        at.run()
        uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
        uploader.set_value(("roth_bridge.enc", payload_bytes, "application/octet-stream"))
        at.run()
        assert not at.exception
        next(b for b in at.button if b.key == "apply_uploads").set_value(True)
        at.run()
        assert not at.exception

        ytd = at.session_state["ytd_snapshot"]
        assert ytd.wages_ytd == 177_000.0
        assert ytd.federal_withholding_ytd == 27_700.0
        assert ytd.qualified_dividends_ytd == 4_007.0
        assert ytd.nqo_exercise_ytd == 50_000.0
        assert ytd.ira_conversions_ytd == 30_000.0
        assert ytd.spouse_ira_conversions_ytd == 700.0
        assert ytd.interest_ytd == 5_035.0
        assert ytd.by_owner["you"]["wages_ytd"] == 100_000.0
        assert ytd.by_owner["spouse"]["wages_ytd"] == 77_000.0
