"""A malformed ROTH_PLANNER_DATA_BRIDGE_PUBKEY must not crash the export panel.

``load_pubkey`` decodes that variable unguarded (``engine/data_bridge_keys.py``
``_try_load``), and the export panel reached it from two places -- the key
resolution itself and the session-vs-disk key mismatch check -- so a bad value
escaped as a raw traceback. Mirrors the B2 private-key fix: a visible error
naming the variable, never its value, and a disabled export.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine.data_bridge_keys import PUBKEY_ENV


class TestMalformedPubkeyEnv:
    def _run(self, monkeypatch: pytest.MonkeyPatch, *, session_privkey: str | None = None):
        import streamlit as st_mod
        from streamlit.testing.v1 import AppTest

        import config.loader as loader_mod
        import views.setup.data_bridge as data_bridge_mod

        spy = MagicMock(return_value=False)
        monkeypatch.setattr(st_mod, "download_button", spy)
        monkeypatch.setenv(PUBKEY_ENV, "garbage-not-a-key")
        monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
        monkeypatch.setattr(
            data_bridge_mod, "_load_pdf_ledger", lambda: {"koinly": {}, "brokerage": {}}
        )
        monkeypatch.setattr(data_bridge_mod, "load_ytd_snapshot", lambda: None)
        monkeypatch.setattr(loader_mod, "load_raw_user_defaults", lambda: {})

        def _render(session_privkey=None) -> None:
            import streamlit as st

            from views.setup.data_bridge import _handle_personal_exports

            st.session_state["instance_owner"] = "you"
            if session_privkey:
                st.session_state["data_bridge_privkey_b64"] = session_privkey
            _handle_personal_exports()

        at = AppTest.from_function(_render, kwargs={"session_privkey": session_privkey})
        at.run()
        return at, spy

    def test_malformed_pubkey_env_var_shows_error_not_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        at, spy = self._run(monkeypatch)
        assert not at.exception, at.exception
        errors = [e.value for e in at.error]
        assert any(PUBKEY_ENV in e for e in errors), errors
        assert not any("garbage-not-a-key" in e for e in errors), "env value must not be echoed"
        spy.assert_called_once()
        assert spy.call_args.kwargs["disabled"] is True
        assert not spy.call_args.kwargs["data"]

    def test_session_key_still_exports_despite_malformed_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pasted session key wins over the env var, so a bad env var must not block it."""
        import base64

        from engine.data_bridge_crypto import generate_keypair

        _pub, priv = generate_keypair()
        at, spy = self._run(monkeypatch, session_privkey=base64.b64encode(priv).decode("ascii"))
        assert not at.exception, at.exception
        spy.assert_called_once()
        assert spy.call_args.kwargs["disabled"] is False
        assert spy.call_args.kwargs["data"]
