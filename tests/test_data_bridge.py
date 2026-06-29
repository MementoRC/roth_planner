"""Tests for V2 data-bridge crypto primitives and key loaders."""

from __future__ import annotations

import base64
import stat
import warnings

import pytest

from engine.data_bridge_crypto import (
    MAGIC,
    DecryptionFailedError,
    InvalidMagicError,
    generate_keypair,
    has_magic,
    seal,
    unseal,
)
from engine.data_bridge_keys import (
    PRIVKEY_ENV,
    PUBKEY_ENV,
    _decode_keymaterial,
    _try_load,
    load_privkey,
    load_pubkey,
    write_keypair,
)


def _fresh_keypair() -> tuple[bytes, bytes]:
    return generate_keypair()


class TestSealRoundTrip:
    def test_basic_ascii(self):
        pub, priv = _fresh_keypair()
        ct = seal(b"hello world", pub)
        assert unseal(ct, priv) == b"hello world"

    def test_non_ascii_utf8(self):
        pub, priv = _fresh_keypair()
        payload = "café résumé".encode()
        assert unseal(seal(payload, pub), priv) == payload

    def test_empty_plaintext(self):
        pub, priv = _fresh_keypair()
        assert unseal(seal(b"", pub), priv) == b""

    def test_ciphertext_starts_with_magic(self):
        pub, _ = _fresh_keypair()
        ct = seal(b"data", pub)
        assert ct[:4] == MAGIC

    def test_ciphertext_longer_than_plaintext(self):
        pub, _ = _fresh_keypair()
        ct = seal(b"x" * 100, pub)
        assert len(ct) > 104


class TestMagic:
    def test_detects_magic(self):
        pub, _ = _fresh_keypair()
        ct = seal(b"test", pub)
        assert has_magic(ct) is True

    def test_rejects_plaintext_json(self):
        assert has_magic(b'{"key": "value"}') is False

    def test_rejects_short_blob(self):
        assert has_magic(b"FX") is False

    def test_rejects_wrong_fourth_byte(self):
        assert has_magic(b"FX1\x01" + b"\x00" * 10) is False


class TestUnsealErrors:
    def test_missing_magic_raises_invalid_magic(self):
        _, priv = _fresh_keypair()
        with pytest.raises(InvalidMagicError):
            unseal(b'{"not": "encrypted"}', priv)

    def test_wrong_key_raises_decryption_failed(self):
        pub, _ = _fresh_keypair()
        _, priv2 = _fresh_keypair()
        ct = seal(b"secret", pub)
        with pytest.raises(DecryptionFailedError):
            unseal(ct, priv2)

    def test_tampered_ciphertext_raises_decryption_failed(self):
        pub, priv = _fresh_keypair()
        ct = bytearray(seal(b"secret", pub))
        ct[len(MAGIC) + 5] ^= 0xFF
        with pytest.raises(DecryptionFailedError):
            unseal(bytes(ct), priv)

    def test_truncated_ciphertext_raises_decryption_failed(self):
        pub, priv = _fresh_keypair()
        ct = seal(b"secret", pub)
        with pytest.raises(DecryptionFailedError):
            unseal(ct[: len(MAGIC) + 5], priv)


class TestKeyGen:
    def test_returns_two_32byte_values(self):
        pub, priv = generate_keypair()
        assert len(pub) == 32
        assert len(priv) == 32

    def test_pub_derived_from_priv(self):
        from nacl.public import PrivateKey

        pub, priv = generate_keypair()
        assert bytes(PrivateKey(priv).public_key) == pub

    def test_unique_per_call(self):
        pub1, _ = generate_keypair()
        pub2, _ = generate_keypair()
        assert pub1 != pub2


class TestDecodeKeymaterial:
    def test_accepts_base64(self):
        raw = b"\xab" * 32
        encoded = base64.b64encode(raw).decode("ascii")
        assert _decode_keymaterial(encoded) == raw

    def test_accepts_hex(self):
        raw = b"\xcd" * 32
        encoded = raw.hex()
        assert _decode_keymaterial(encoded) == raw

    def test_strips_whitespace(self):
        raw = b"\x01" * 32
        encoded = "  " + base64.b64encode(raw).decode("ascii") + "\n"
        assert _decode_keymaterial(encoded) == raw

    def test_rejects_wrong_length_base64(self):
        encoded = base64.b64encode(b"\x00" * 16).decode("ascii")
        with pytest.raises(ValueError, match="Expected 32-byte key"):
            _decode_keymaterial(encoded)

    def test_rejects_wrong_length_hex(self):
        with pytest.raises(ValueError, match="Expected 32-byte key"):
            _decode_keymaterial("abcd")

    def test_rejects_garbage(self):
        with pytest.raises(ValueError, match="Expected 32-byte key"):
            _decode_keymaterial("not-a-key-at-all!!!")


class TestLoadPubkey:
    def test_returns_none_when_not_configured(self, monkeypatch, tmp_path):
        monkeypatch.delenv(PUBKEY_ENV, raising=False)
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", tmp_path / "missing.pub")
        assert load_pubkey() is None

    def test_env_takes_precedence_over_file(self, monkeypatch, tmp_path):
        pub_env, _ = _fresh_keypair()
        pub_file, _ = _fresh_keypair()
        pub_path = tmp_path / "data-bridge.pub"
        pub_path.write_text(base64.b64encode(pub_file).decode("ascii"), encoding="utf-8")
        monkeypatch.setenv(PUBKEY_ENV, base64.b64encode(pub_env).decode("ascii"))
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        assert load_pubkey() == pub_env

    def test_file_fallback(self, monkeypatch, tmp_path):
        pub, _ = _fresh_keypair()
        pub_path = tmp_path / "data-bridge.pub"
        pub_path.write_text(base64.b64encode(pub).decode("ascii") + "\n", encoding="utf-8")
        monkeypatch.delenv(PUBKEY_ENV, raising=False)
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        assert load_pubkey() == pub


class TestLoadPrivkey:
    def test_returns_none_when_not_configured(self, monkeypatch, tmp_path):
        monkeypatch.delenv(PRIVKEY_ENV, raising=False)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", tmp_path / "missing.priv")
        assert load_privkey() is None

    def test_env_takes_precedence_over_file(self, monkeypatch, tmp_path):
        _, priv_env = _fresh_keypair()
        _, priv_file = _fresh_keypair()
        priv_path = tmp_path / "data-bridge.priv"
        priv_path.write_text(base64.b64encode(priv_file).decode("ascii"), encoding="utf-8")
        monkeypatch.setenv(PRIVKEY_ENV, base64.b64encode(priv_env).decode("ascii"))
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        assert load_privkey() == priv_env

    def test_file_fallback(self, monkeypatch, tmp_path):
        _, priv = _fresh_keypair()
        priv_path = tmp_path / "data-bridge.priv"
        priv_path.write_text(base64.b64encode(priv).decode("ascii") + "\n", encoding="utf-8")
        monkeypatch.delenv(PRIVKEY_ENV, raising=False)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        assert load_privkey() == priv


class TestWriteKeypair:
    def test_creates_both_files(self, monkeypatch, tmp_path):
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        pub, priv = _fresh_keypair()
        write_keypair(pub, priv)
        assert pub_path.exists()
        assert priv_path.exists()

    def test_pub_mode_644(self, monkeypatch, tmp_path):
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        pub, priv = _fresh_keypair()
        write_keypair(pub, priv)
        assert stat.S_IMODE(pub_path.stat().st_mode) == 0o644

    def test_priv_mode_600(self, monkeypatch, tmp_path):
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        pub, priv = _fresh_keypair()
        write_keypair(pub, priv)
        assert stat.S_IMODE(priv_path.stat().st_mode) == 0o600

    def test_content_roundtrips(self, monkeypatch, tmp_path):
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        monkeypatch.delenv(PUBKEY_ENV, raising=False)
        monkeypatch.delenv(PRIVKEY_ENV, raising=False)
        pub, priv = _fresh_keypair()
        write_keypair(pub, priv)
        assert load_pubkey() == pub
        assert load_privkey() == priv

    def test_refuses_overwrite_pub(self, monkeypatch, tmp_path):
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        pub_path.write_text("existing", encoding="utf-8")
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        pub, priv = _fresh_keypair()
        with pytest.raises(FileExistsError):
            write_keypair(pub, priv)

    def test_refuses_overwrite_priv(self, monkeypatch, tmp_path):
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        priv_path.write_text("existing", encoding="utf-8")
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        pub, priv = _fresh_keypair()
        with pytest.raises(FileExistsError):
            write_keypair(pub, priv)

    def test_force_overwrites(self, monkeypatch, tmp_path):
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        pub1, priv1 = _fresh_keypair()
        write_keypair(pub1, priv1)
        pub2, priv2 = _fresh_keypair()
        write_keypair(pub2, priv2, force=True)
        assert pub_path.read_text(encoding="utf-8").strip() == base64.b64encode(pub2).decode(
            "ascii"
        )


# ---------------------------------------------------------------------------
# TestDerivePubkey
# ---------------------------------------------------------------------------


class TestDerivePubkey:
    def test_matches_generate_keypair(self):
        from engine.data_bridge_crypto import derive_pubkey

        pub, priv = generate_keypair()
        assert derive_pubkey(priv) == pub

    def test_seal_with_derived_pubkey_round_trips(self):
        from engine.data_bridge_crypto import derive_pubkey

        _, priv = generate_keypair()
        pub_derived = derive_pubkey(priv)
        ct = seal(b"derived-pub-test", pub_derived)
        assert unseal(ct, priv) == b"derived-pub-test"


# ---------------------------------------------------------------------------
# TestOpenUploadedPayload
# ---------------------------------------------------------------------------


class TestOpenUploadedPayload:
    def test_plaintext_passthrough_no_key(self):
        from engine.data_bridge_crypto import open_uploaded_payload

        assert open_uploaded_payload(b'{"k": "v"}', None) == b'{"k": "v"}'

    def test_plaintext_passthrough_with_key(self):
        from engine.data_bridge_crypto import open_uploaded_payload

        _, priv = generate_keypair()
        assert open_uploaded_payload(b'{"x": 1}', priv) == b'{"x": 1}'

    def test_decrypts_when_magic_present(self):
        from engine.data_bridge_crypto import open_uploaded_payload

        pub, priv = generate_keypair()
        ct = seal(b'{"hello": "world"}', pub)
        assert open_uploaded_payload(ct, priv) == b'{"hello": "world"}'

    def test_raises_value_error_when_encrypted_without_key(self):
        from engine.data_bridge_crypto import open_uploaded_payload

        pub, _ = generate_keypair()
        ct = seal(b"secret", pub)
        with pytest.raises(ValueError, match="no private key"):
            open_uploaded_payload(ct, None)

    def test_raises_decryption_failed_with_wrong_key(self):
        from engine.data_bridge_crypto import open_uploaded_payload

        pub, _ = generate_keypair()
        _, priv2 = generate_keypair()
        ct = seal(b"secret", pub)
        with pytest.raises(DecryptionFailedError):
            open_uploaded_payload(ct, priv2)


# ---------------------------------------------------------------------------
# TestBrowserNonPyodide (CI runs outside Pyodide — verifies degradation)
# ---------------------------------------------------------------------------


class TestBrowserNonPyodide:
    def test_is_pyodide_returns_false(self):
        from engine.data_bridge_browser import is_pyodide

        assert is_pyodide() is False

    def test_local_storage_get_returns_none(self):
        from engine.data_bridge_browser import local_storage_get

        assert local_storage_get("any_key") is None

    def test_local_storage_set_is_noop(self):
        from engine.data_bridge_browser import local_storage_set

        local_storage_set("some_key", "some_value")  # must not raise

    def test_local_storage_remove_is_noop(self):
        from engine.data_bridge_browser import local_storage_remove

        local_storage_remove("some_key")  # must not raise

    def test_browser_privkey_ls_key_removed(self):
        # F38: BROWSER_PRIVKEY_LS_KEY was deleted to prevent misleading callers
        # into persisting private keys to localStorage (XSS risk).
        import engine.data_bridge_browser as _mod

        assert not hasattr(_mod, "BROWSER_PRIVKEY_LS_KEY")


# ---------------------------------------------------------------------------
# TestDecodeKeymaterialPublic (verify public wrapper matches private)
# ---------------------------------------------------------------------------


class TestDecodeKeymaterialPublic:
    def test_public_wrapper_matches_private(self):
        from engine.data_bridge_keys import _decode_keymaterial, decode_keymaterial

        raw = b"\xab" * 32
        encoded = base64.b64encode(raw).decode("ascii")
        assert decode_keymaterial(encoded) == _decode_keymaterial(encoded)


# ---------------------------------------------------------------------------
# TestWriteKeypairPermissions — security hardening regressions (atomic write)
# ---------------------------------------------------------------------------


class TestWriteKeypairPermissions:
    """Prove the atomic os.open write never exposes keys with wrong permissions."""

    def test_keypair_permissions_nested_subdir(self, monkeypatch, tmp_path):
        # write_keypair creates the parent dir; verify both file modes after mkdir
        key_dir = tmp_path / "nested" / "subdir"
        pub_path = key_dir / "data-bridge.pub"
        priv_path = key_dir / "data-bridge.priv"
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        write_keypair(b"\x01" * 32, b"\x02" * 32)
        assert stat.S_IMODE(pub_path.stat().st_mode) == 0o644
        assert stat.S_IMODE(priv_path.stat().st_mode) == 0o600

    def test_force_corrects_loose_priv_permissions(self, monkeypatch, tmp_path):
        # Pre-create priv file with mode 0o644 (too permissive); force=True must correct it
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        priv_path.write_text("old\n", encoding="utf-8")
        priv_path.chmod(0o644)
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        write_keypair(b"\x01" * 32, b"\x02" * 32, force=True)
        assert stat.S_IMODE(priv_path.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# TestKeyfileNofollow — L1/L2 security regression tests
# ---------------------------------------------------------------------------


class TestKeyfileNofollow:
    """Regression tests for L1 (O_NOFOLLOW) and L2 (permission warning)."""

    def test_write_keyfile_refuses_symlink(self, tmp_path):
        """L1: _write_keyfile must not follow a pre-planted symlink."""
        import base64

        from engine.data_bridge_keys import _write_keyfile

        target = tmp_path / "attacker-file"
        target.write_text("original", encoding="utf-8")
        symlink = tmp_path / "data-bridge.priv"
        symlink.symlink_to(target)

        priv_text = base64.b64encode(b"\xaa" * 32).decode("ascii") + "\n"
        with pytest.raises(OSError, match=".*"):
            _write_keyfile(symlink, priv_text, 0o600)

        # Confirm the symlink target was NOT overwritten
        assert target.read_text(encoding="utf-8") == "original"

    def test_write_keypair_priv_mode_600(self, monkeypatch, tmp_path):
        """L1: write_keypair sets 0o600 on the private key file (non-symlink path)."""
        pub_path = tmp_path / "data-bridge.pub"
        priv_path = tmp_path / "data-bridge.priv"
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", pub_path)
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", priv_path)
        write_keypair(b"\x01" * 32, b"\x02" * 32)
        assert stat.S_IMODE(priv_path.stat().st_mode) == 0o600

    def test_load_warns_on_world_readable_keyfile(self, monkeypatch, tmp_path):
        """L2: loading a world-readable key file emits a RuntimeWarning."""
        import base64

        raw = b"\xcc" * 32
        key_path = tmp_path / "data-bridge.priv"
        key_path.write_bytes(base64.b64encode(raw) + b"\n")
        key_path.chmod(0o644)  # group+world readable — should warn

        monkeypatch.delenv(PRIVKEY_ENV, raising=False)

        with pytest.warns(RuntimeWarning, match="group- or world-readable"):
            result = _try_load(PRIVKEY_ENV, key_path)

        assert result == raw

    def test_load_no_warning_on_600_keyfile(self, monkeypatch, tmp_path):
        """L2: loading a 0o600 key file emits no permission warning."""
        import base64

        raw = b"\xdd" * 32
        key_path = tmp_path / "data-bridge.priv"
        key_path.write_bytes(base64.b64encode(raw) + b"\n")
        key_path.chmod(0o600)

        monkeypatch.delenv(PRIVKEY_ENV, raising=False)

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = _try_load(PRIVKEY_ENV, key_path)

        assert result == raw


class TestRecipientPubkeyParse:
    def test_blank_returns_none_none(self):
        from views.setup.data_bridge import _parse_recipient_pubkey

        assert _parse_recipient_pubkey("") == (None, None)
        assert _parse_recipient_pubkey(None) == (None, None)
        assert _parse_recipient_pubkey("   ") == (None, None)

    def test_valid_base64_pubkey(self):
        from views.setup.data_bridge import _parse_recipient_pubkey

        pub, _ = generate_keypair()
        b64 = base64.b64encode(pub).decode("ascii")
        parsed, err = _parse_recipient_pubkey(b64)
        assert err is None
        assert parsed == pub

    def test_valid_hex_pubkey(self):
        from views.setup.data_bridge import _parse_recipient_pubkey

        pub, _ = generate_keypair()
        parsed, err = _parse_recipient_pubkey(pub.hex())
        assert err is None
        assert parsed == pub

    def test_invalid_returns_error(self):
        from views.setup.data_bridge import _parse_recipient_pubkey

        parsed, err = _parse_recipient_pubkey("not-a-key")
        assert parsed is None
        assert err is not None
        assert "Invalid recipient public key" in err


class TestThirdPartySeal:
    def test_sender_seals_to_recipient_only_recipient_opens(self):
        # Recipient shares only their public key; sender needs no key of their own.
        recipient_pub, recipient_priv = generate_keypair()
        ct = seal(b'{"household": "data"}', recipient_pub)
        assert unseal(ct, recipient_priv) == b'{"household": "data"}'

    def test_wrong_recipient_cannot_open(self):
        recipient_pub, _ = generate_keypair()
        _, other_priv = generate_keypair()
        ct = seal(b"secret", recipient_pub)
        with pytest.raises(DecryptionFailedError):
            unseal(ct, other_priv)


class TestGeneratedKeypairRoundTrip:
    def test_generated_keypair_b64_decodes_and_decrypts(self):
        from engine.data_bridge_crypto import derive_pubkey

        pub, priv = generate_keypair()
        pub_b64 = base64.b64encode(pub).decode("ascii")
        priv_b64 = base64.b64encode(priv).decode("ascii")
        # The privkey widget re-decodes via decode_keymaterial (== _decode_keymaterial).
        assert _decode_keymaterial(pub_b64) == pub
        recovered_priv = _decode_keymaterial(priv_b64)
        assert recovered_priv == priv
        assert derive_pubkey(recovered_priv) == pub
        # End to end: sender seals to the shared public key, recipient unseals.
        ct = seal(b"recipient payload", _decode_keymaterial(pub_b64))
        assert unseal(ct, recovered_priv) == b"recipient payload"
