"""Tests for V2 data-bridge crypto primitives and key loaders."""

from __future__ import annotations

import base64
import stat

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
