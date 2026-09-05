"""The data-bridge import must not disguise its own bugs as a bad upload.

audit-0823, finding surfaced while shipping PS-2b (PR #456).

`views/setup/data_bridge.py::_handle_personal_uploads` wraps ~85 lines in one
try, and its handler

    except (JSONDecodeError, ValueError, TypeError, KeyError,
            AttributeError, DataBridgeCryptoError) as e:
        st.error(f"Invalid {bundle_file.name}: {e}")

labels everything inside it as a malformed upload. Only the first ~15 lines
actually parse the bundle; the rest is application logic (save_snapshot,
_save_pdf_ledger, _apply_user_defaults_to_session, record_magi_candidates,
_rederive_ytd_from_ledger). A TypeError raised there is OUR defect being
reported to the user as THEIR file being invalid.

This is not hypothetical: during PS-2b a stale `lambda snap: None` test stub
raised TypeError on a new keyword argument, was swallowed here, and surfaced
~90 lines away as a missing success message.

These tests are the first coverage of this failure branch in the repo.
"""

import json

import pytest


def _minimal_bundle() -> dict:
    return {
        "format_version": 4,
        "sections": {"setup_scalars": {}, "portfolio": {"accounts": []}},
    }


def _drive_apply(at, payload_bytes: bytes) -> None:
    """Populate the uploader, click Apply, and rerun."""
    uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
    uploader.set_value(("roth_bridge.enc", payload_bytes, "application/octet-stream"))
    apply_button = next(b for b in at.button if b.key == "apply_uploads")
    apply_button.set_value(True)
    at.run()


class TestApplyPhaseBugsAreNotDisguised:
    """A defect in our own application logic must surface, not wear a bad-file label."""

    def test_apply_phase_typeerror_is_not_reported_as_an_invalid_bundle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st_mod
        from streamlit.testing.v1 import AppTest

        import engine.data_bridge_crypto as data_bridge_crypto_mod
        import views.setup.data_bridge as data_bridge_mod

        payload_bytes = json.dumps(_minimal_bundle()).encode("utf-8")

        def _fake_apply_bundle(target_owner, bundle, *, existing_snapshot, existing_ledger):
            return existing_snapshot, existing_ledger

        def _exploding_save_pdf_ledger(ledger):
            # Stands in for any defect in the apply phase. TypeError is in the
            # handler's tuple, so today it is swallowed and mislabelled.
            raise TypeError("simulated defect in application logic")

        monkeypatch.setattr(
            data_bridge_crypto_mod, "open_uploaded_payload", lambda raw, privkey: payload_bytes
        )
        monkeypatch.setattr(data_bridge_mod, "apply_bundle", _fake_apply_bundle)
        monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
        monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
        monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", lambda: {})
        monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", _exploding_save_pdf_ledger)
        monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
        monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
        monkeypatch.setattr(st_mod, "rerun", lambda: None)

        def _render() -> None:
            import streamlit as st

            from views.setup.data_bridge import _handle_personal_uploads

            st.session_state["instance_owner"] = "you"
            _handle_personal_uploads()

        at = AppTest.from_function(_render)
        at.run()
        assert not at.exception

        _drive_apply(at, payload_bytes)

        error_texts = [e.value for e in at.error]
        assert not any("Invalid" in t for t in error_texts), (
            "an apply-phase defect was reported to the user as a malformed "
            f"upload: {error_texts}"
        )
        assert at.exception, "the defect must surface rather than be swallowed"


class TestMalformedBundlesStillReportAsInvalid:
    """The honest 'Invalid <file>' message must survive for genuine bad input."""

    def test_bundle_missing_sections_reports_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import streamlit as st_mod
        from streamlit.testing.v1 import AppTest

        import engine.data_bridge_crypto as data_bridge_crypto_mod
        import views.setup.data_bridge as data_bridge_mod

        payload_bytes = json.dumps({"format_version": 4}).encode("utf-8")

        monkeypatch.setattr(
            data_bridge_crypto_mod, "open_uploaded_payload", lambda raw, privkey: payload_bytes
        )
        monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
        monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
        monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", lambda: {})
        monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", lambda ledger: None)
        monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
        monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
        monkeypatch.setattr(st_mod, "rerun", lambda: None)

        def _render() -> None:
            import streamlit as st

            from views.setup.data_bridge import _handle_personal_uploads

            st.session_state["instance_owner"] = "you"
            _handle_personal_uploads()

        at = AppTest.from_function(_render)
        at.run()
        _drive_apply(at, payload_bytes)

        error_texts = [e.value for e in at.error]
        assert any("Invalid" in t and "roth_bridge.enc" in t for t in error_texts), error_texts
        assert not at.exception

    def test_bundle_missing_setup_scalars_reports_invalid_not_a_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pins the up-front structural validation.

        `data["sections"]["setup_scalars"]` is not read until well inside the
        apply phase (data_bridge.py lines 341/349/385). Without validating the
        bundle's structural contract BEFORE that phase begins, re-raising
        apply-phase errors would turn this genuinely malformed bundle into a
        traceback instead of the correct 'Invalid' message.
        """
        import streamlit as st_mod
        from streamlit.testing.v1 import AppTest

        import engine.data_bridge_crypto as data_bridge_crypto_mod
        import views.setup.data_bridge as data_bridge_mod

        bundle = {"format_version": 4, "sections": {"portfolio": {"accounts": []}}}
        payload_bytes = json.dumps(bundle).encode("utf-8")

        def _fake_apply_bundle(target_owner, b, *, existing_snapshot, existing_ledger):
            return existing_snapshot, existing_ledger

        monkeypatch.setattr(
            data_bridge_crypto_mod, "open_uploaded_payload", lambda raw, privkey: payload_bytes
        )
        monkeypatch.setattr(data_bridge_mod, "apply_bundle", _fake_apply_bundle)
        monkeypatch.setattr(data_bridge_mod, "load_snapshot", lambda: None)
        monkeypatch.setattr(data_bridge_mod, "save_snapshot", lambda snap, **kwargs: None)
        monkeypatch.setattr(data_bridge_mod, "_load_pdf_ledger", lambda: {})
        monkeypatch.setattr(data_bridge_mod, "_save_pdf_ledger", lambda ledger: None)
        monkeypatch.setattr(data_bridge_mod, "_resolve_privkey_bytes", lambda: None)
        monkeypatch.setattr(data_bridge_mod, "load_pubkey", lambda: None)
        monkeypatch.setattr(st_mod, "rerun", lambda: None)

        def _render() -> None:
            import streamlit as st

            from views.setup.data_bridge import _handle_personal_uploads

            st.session_state["instance_owner"] = "you"
            _handle_personal_uploads()

        at = AppTest.from_function(_render)
        at.run()
        _drive_apply(at, payload_bytes)

        error_texts = [e.value for e in at.error]
        assert any("Invalid" in t and "roth_bridge.enc" in t for t in error_texts), error_texts
        assert not at.exception
