"""The data-bridge import must fail visibly and distinctly, never as a traceback.

Plan ``docs/superpowers/plans/2026-09-30-bridge-v5-envelope.md`` tasks B1-B3,
spec ``docs/superpowers/specs/2026-09-20-import-data-ownership-design.md`` §5.1:

- B1: the uploaded bytes are stashed at upload time and decrypted from that
  copy, so a retry after a failed Apply never re-reads a handle at EOF.
- B2: a malformed ``ROTH_PLANNER_DATA_BRIDGE_PRIVKEY`` is reported, not
  raised as a raw traceback before the import's ``try`` opens.
- B3: an encrypted upload with no private key disables Apply with an inline
  reason instead of letting it fail mid-flight.

These drive the REAL decrypt path (``engine.data_bridge_crypto``) with a real
keypair. Only persistence, the on-disk key file and ``st.rerun`` are stubbed.
"""

from __future__ import annotations

import base64
import json

import pytest

from engine.bridge_bundle import build_bundle

UPLOAD_NAME = "roth_bridge.enc"


def _keypair() -> tuple[bytes, bytes]:
    from engine.data_bridge_crypto import generate_keypair

    return generate_keypair()


def _b64(key: bytes) -> str:
    return base64.b64encode(key).decode("ascii")


def _plain_bundle_bytes() -> bytes:
    return json.dumps(build_bundle({}, None, {}, owner="you")).encode("utf-8")


def _sealed_bundle(pubkey: bytes) -> bytes:
    from engine.data_bridge_crypto import seal

    return seal(_plain_bundle_bytes(), pubkey)


@pytest.fixture
def decrypt_seen(monkeypatch: pytest.MonkeyPatch, tmp_path) -> list[bytes]:
    """Isolate the import from real keys/caches; record every decrypt input."""
    import streamlit as st_mod

    import config.loader as loader_mod
    import engine.data_bridge_crypto as crypto_mod
    import engine.data_bridge_keys as keys_mod
    import views.setup._state as state_mod
    import views.setup.data_bridge as mod

    monkeypatch.delenv(keys_mod.PRIVKEY_ENV, raising=False)
    monkeypatch.setattr(keys_mod, "PRIVKEY_PATH", tmp_path / "absent.priv")
    monkeypatch.setattr(mod, "load_pubkey", lambda: None)
    monkeypatch.setattr(mod, "load_snapshot", lambda: None)
    monkeypatch.setattr(mod, "save_snapshot", lambda snap, **kwargs: None)
    monkeypatch.setattr(mod, "_load_pdf_ledger", lambda: {})
    monkeypatch.setattr(mod, "_save_pdf_ledger", lambda ledger: None)
    monkeypatch.setattr(mod, "load_ytd_snapshot", lambda: None)
    monkeypatch.setattr(mod, "save_ytd_snapshot", lambda snap: None)
    monkeypatch.setattr(loader_mod, "load_raw_user_defaults", lambda: {})
    # "Reset to demo" deletes the real .user_defaults.json in cwd otherwise.
    monkeypatch.setattr(state_mod, "clear_user_defaults", lambda: None)
    monkeypatch.setattr(st_mod, "rerun", lambda: None)

    seen: list[bytes] = []
    real_open = crypto_mod.open_uploaded_payload

    def _spy(raw: bytes, privkey: bytes | None) -> bytes:
        seen.append(bytes(raw))
        return real_open(raw, privkey)

    monkeypatch.setattr(crypto_mod, "open_uploaded_payload", _spy)
    return seen


def _render() -> None:
    import streamlit as st

    from views.setup.data_bridge import _handle_personal_uploads, _handle_v2_privkey

    st.session_state["instance_owner"] = "you"
    _handle_v2_privkey()
    _handle_personal_uploads()


def _app(session_key: str | None = None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(_render)
    if session_key is not None:
        at.session_state["data_bridge_privkey_b64"] = session_key
    at.run()
    assert not at.exception
    return at


def _upload(at, payload: bytes) -> None:
    uploader = next(w for w in at.file_uploader if w.key == "bundle_upload")
    uploader.set_value((UPLOAD_NAME, payload, "application/octet-stream"))
    at.run()


def _apply_button(at):
    return next(b for b in at.button if b.key == "apply_uploads")


def _click_apply(at) -> None:
    _apply_button(at).set_value(True)
    at.run()


def _texts(elements) -> list[str]:
    return [e.value for e in elements]


class TestUploadStash:
    """B1 -- decrypt from bytes captured at upload, never a re-read of the handle."""

    def test_retry_after_failure_decrypts_stashed_bytes(self, decrypt_seen: list[bytes]) -> None:
        right_pub, right_priv = _keypair()
        _wrong_pub, wrong_priv = _keypair()
        payload = _sealed_bundle(right_pub)

        at = _app(session_key=_b64(wrong_priv))
        _upload(at, payload)
        _click_apply(at)
        assert not at.exception
        assert _texts(at.error), "the first, wrong-key Apply must fail visibly"
        assert not _texts(at.success)

        at.session_state["data_bridge_privkey_b64"] = _b64(right_priv)
        _click_apply(at)
        assert not at.exception
        assert any("Applied" in t for t in _texts(at.success)), _texts(at.error)
        assert decrypt_seen == [payload, payload], "each attempt must see the full upload"

    def test_new_upload_replaces_stash(self, decrypt_seen: list[bytes]) -> None:
        first = _plain_bundle_bytes()
        second = first + b" " * 17  # same JSON, different bytes and size

        at = _app()
        _upload(at, first)
        assert at.session_state["_bundle_upload_bytes"] == first
        _upload(at, second)
        assert at.session_state["_bundle_upload_bytes"] == second

    def test_reset_to_demo_clears_stash(self, decrypt_seen: list[bytes]) -> None:
        at = _app()
        _upload(at, _plain_bundle_bytes())
        assert "_bundle_upload_bytes" in at.session_state

        next(b for b in at.button if b.key == "reset_demo").set_value(True)
        at.run()
        assert not at.exception
        assert "_bundle_upload_bytes" not in at.session_state
        assert "_bundle_upload_id" not in at.session_state


class TestMalformedEnvKey:
    """B2 -- a bad env-var key is a visible error, never a raw traceback."""

    def test_malformed_privkey_env_var_shows_error_not_traceback(
        self, decrypt_seen: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.data_bridge_keys as keys_mod

        monkeypatch.setenv(keys_mod.PRIVKEY_ENV, "garbage")
        pub, _priv = _keypair()

        at = _app()
        _upload(at, _sealed_bundle(pub))
        assert not at.exception
        assert any(keys_mod.PRIVKEY_ENV in t for t in _texts(at.error)), _texts(at.error)
        assert "garbage" not in " ".join(_texts(at.error)), "never echo key material"
        assert _apply_button(at).disabled is True

        _click_apply(at)
        assert not at.exception
        assert decrypt_seen == [], "a disabled Apply must not attempt decryption"

    def test_plaintext_upload_never_resolves_the_key(
        self, decrypt_seen: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unencrypted payload needs no key, so a bad key must not block it."""
        import engine.data_bridge_keys as keys_mod

        monkeypatch.setenv(keys_mod.PRIVKEY_ENV, "garbage")

        at = _app()
        _upload(at, _plain_bundle_bytes())
        assert not _texts(at.error)
        assert _apply_button(at).disabled is False
        _click_apply(at)
        assert not at.exception
        assert any("Applied" in t for t in _texts(at.success)), _texts(at.error)


_NO_KEY_REASON = "no private key is configured"


class TestApplyNeedsAKey:
    """B3 -- an encrypted upload with no key disables Apply and says why."""

    def test_apply_disabled_with_reason_when_no_key(self, decrypt_seen: list[bytes]) -> None:
        pub, _priv = _keypair()

        at = _app()
        _upload(at, _sealed_bundle(pub))
        assert not at.exception
        assert any(_NO_KEY_REASON in t for t in _texts(at.caption)), _texts(at.caption)
        assert _apply_button(at).disabled is True

        _click_apply(at)
        assert decrypt_seen == [], "a disabled Apply must not attempt decryption"

    def test_apply_enabled_with_key_and_upload(self, decrypt_seen: list[bytes]) -> None:
        pub, priv = _keypair()

        at = _app(session_key=_b64(priv))
        _upload(at, _sealed_bundle(pub))
        assert not any(_NO_KEY_REASON in t for t in _texts(at.caption))
        assert _apply_button(at).disabled is False


class TestEveryDecryptFailureIsDistinct:
    """Each decrypt outcome gets its own visible message and never a traceback."""

    def test_each_decrypt_failure_is_distinct(
        self, decrypt_seen: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import engine.data_bridge_keys as keys_mod

        pub, priv = _keypair()
        _wrong_pub, wrong_priv = _keypair()
        sealed = _sealed_bundle(pub)
        messages: dict[str, str] = {}

        # 1. No key configured.
        at = _app()
        _upload(at, sealed)
        assert not at.exception
        messages["no_key"] = next(t for t in _texts(at.caption) if _NO_KEY_REASON in t)

        # 2. Malformed pasted key -- rejected on Save.
        at = _app()
        next(w for w in at.text_input if w.key == "_v2_privkey_input").set_value("not-a-key")
        next(b for b in at.button if b.key == "save_v2_privkey").set_value(True)
        at.run()
        assert not at.exception
        messages["malformed_pasted"] = _texts(at.error)[0]

        # 3. Malformed environment variable.
        monkeypatch.setenv(keys_mod.PRIVKEY_ENV, "garbage")
        at = _app()
        _upload(at, sealed)
        assert not at.exception
        messages["malformed_env"] = _texts(at.error)[0]
        monkeypatch.delenv(keys_mod.PRIVKEY_ENV)

        # 4. Wrong key.
        at = _app(session_key=_b64(wrong_priv))
        _upload(at, sealed)
        _click_apply(at)
        assert not at.exception
        messages["wrong_key"] = _texts(at.error)[0]

        # 5. Empty payload.
        at = _app(session_key=_b64(priv))
        _upload(at, b"")
        _click_apply(at)
        assert not at.exception
        messages["empty"] = _texts(at.error)[0]

        # 6. Retry after a failure -- the right key now succeeds.
        at = _app(session_key=_b64(wrong_priv))
        _upload(at, sealed)
        _click_apply(at)
        at.session_state["data_bridge_privkey_b64"] = _b64(priv)
        _click_apply(at)
        assert not at.exception
        messages["retry_success"] = _texts(at.success)[0]

        assert len(set(messages.values())) == 6, messages
        assert "decrypt" in messages["wrong_key"].lower()
        assert "Invalid" not in messages["wrong_key"], "a wrong key is not a bad file"
        assert "empty" in messages["empty"].lower()
        assert "Applied" in messages["retry_success"]
