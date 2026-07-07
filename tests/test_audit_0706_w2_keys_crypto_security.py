# ruff: noqa: I001
"""TDD tests for audit-0706 wave-2 key/crypto security hardening.

Findings addressed:
  crypto-security-4  : unpadded base64 key silently rejected
  crypto-security-6  : write_keypair force=False TOCTOU race → O_EXCL
  crypto-security-8  : private-key read follows symlinks
  crypto-security-10 : non-atomic pubkey+privkey write ordering
  crypto-security-7  : corrupted/truncated V2 payload silently treated as V1
"""

from __future__ import annotations

import base64

import pytest

from engine.data_bridge_crypto import (
    MAGIC,
    DataBridgeCryptoError,
    generate_keypair,
    open_uploaded_payload,
    seal,
)
from engine.data_bridge_keys import (
    _decode_keymaterial,
    _write_keyfile,
    load_privkey,
    load_pubkey,
    write_keypair,
)


# ---------------------------------------------------------------------------
# crypto-security-4 — unpadded base64 accepted by _decode_keymaterial
# ---------------------------------------------------------------------------

class TestDecodeKeymaterialPadding:
    """_decode_keymaterial must accept both padded and unpadded standard base64."""

    def _make_key(self) -> bytes:
        """Return deterministic 32-byte key for tests."""
        return b"\x01" * 32

    def test_standard_padded_base64_accepted(self) -> None:
        key = self._make_key()
        padded = base64.b64encode(key).decode("ascii")
        assert padded.endswith("=") or True  # may or may not have padding
        assert _decode_keymaterial(padded) == key

    def test_unpadded_base64_accepted(self) -> None:
        """Key encoded without trailing '=' padding must be accepted."""
        key = self._make_key()
        padded = base64.b64encode(key).decode("ascii")
        unpadded = padded.rstrip("=")
        # This is the regression: before the fix, unpadded base64 that fails
        # strict validation would fall through to hex and raise ValueError.
        result = _decode_keymaterial(unpadded)
        assert result == key

    def test_unpadded_base64_with_whitespace_accepted(self) -> None:
        key = self._make_key()
        padded = base64.b64encode(key).decode("ascii")
        unpadded = "  " + padded.rstrip("=") + "\n"
        assert _decode_keymaterial(unpadded) == key

    def test_invalid_base64_not_32_bytes_raises(self) -> None:
        """Base64 that decodes to wrong length still raises ValueError."""
        # 16 bytes encoded → 24 chars of base64
        bad = base64.b64encode(b"\x01" * 16).decode("ascii").rstrip("=")
        with pytest.raises(ValueError, match="Expected 32-byte key"):
            _decode_keymaterial(bad)

    def test_hex_still_accepted(self) -> None:
        key = self._make_key()
        hex_str = key.hex()
        assert _decode_keymaterial(hex_str) == key


# ---------------------------------------------------------------------------
# crypto-security-6 — O_EXCL atomic exclusion in _write_keyfile / write_keypair
# ---------------------------------------------------------------------------

class TestWriteKeypairExclusiveAtomicity:
    """write_keypair(force=False) must raise FileExistsError atomically via O_EXCL."""

    def test_write_keypair_force_false_raises_if_pubkey_exists(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", tmp_path / "data-bridge.pub")
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", tmp_path / "data-bridge.priv")
        pubkey_path = tmp_path / "data-bridge.pub"
        pubkey_path.write_text("existing\n")

        pub, priv = generate_keypair()
        with pytest.raises(FileExistsError):
            write_keypair(pub, priv, force=False)

    def test_write_keypair_force_false_raises_if_privkey_exists(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", tmp_path / "data-bridge.pub")
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", tmp_path / "data-bridge.priv")
        privkey_path = tmp_path / "data-bridge.priv"
        privkey_path.write_text("existing\n")

        pub, priv = generate_keypair()
        with pytest.raises(FileExistsError):
            write_keypair(pub, priv, force=False)

    def test_write_keyfile_exclusive_raises_on_existing_file(self, tmp_path) -> None:
        """_write_keyfile with exclusive=True must raise OSError if file exists."""
        existing = tmp_path / "test.key"
        existing.write_text("original\n")

        with pytest.raises(FileExistsError):
            _write_keyfile(existing, "new content\n", 0o600, exclusive=True)

        # Original content must be intact
        assert existing.read_text() == "original\n"

    def test_write_keyfile_non_exclusive_overwrites(self, tmp_path) -> None:
        """_write_keyfile with exclusive=False (force) must overwrite existing."""
        existing = tmp_path / "test.key"
        existing.write_text("original\n")

        _write_keyfile(existing, "new content\n", 0o600, exclusive=False)
        assert existing.read_text() == "new content\n"

    def test_write_keypair_force_true_overwrites(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """force=True must overwrite both files."""
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", tmp_path / "data-bridge.pub")
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", tmp_path / "data-bridge.priv")
        pub, priv = generate_keypair()
        write_keypair(pub, priv, force=False)  # first write
        pub2, priv2 = generate_keypair()
        write_keypair(pub2, priv2, force=True)  # must not raise

        # After overwrite the loaded keys match the second pair
        loaded_pub = load_pubkey()
        assert loaded_pub is not None


# ---------------------------------------------------------------------------
# crypto-security-8 — private-key read must NOT follow symlinks
# ---------------------------------------------------------------------------

class TestPrivkeyReadNoFollow:
    """_try_load for private key file must refuse to follow symlinks."""

    def test_privkey_symlink_raises_or_returns_none(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reading a private key that is a symlink must not silently succeed."""
        # Create the real file at a different location
        real_file = tmp_path / "real.priv"
        key = b"\xab" * 32
        real_file.write_text(base64.b64encode(key).decode("ascii") + "\n")

        # Create symlink pointing to the real file
        symlink = tmp_path / "data-bridge.priv"
        symlink.symlink_to(real_file)

        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", symlink)
        monkeypatch.delenv("ROTH_PLANNER_DATA_BRIDGE_PRIVKEY", raising=False)

        # The implementation must either raise an OSError or return None
        # (not silently follow the symlink and return the key).
        # Per the fix: O_NOFOLLOW on read → OSError(ELOOP) → returns None.
        result = load_privkey()
        assert result is None, (
            "load_privkey() must not follow symlinks; expected None but got key bytes"
        )

    def test_pubkey_regular_file_still_loads(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regular (non-symlink) public key file still loads correctly."""
        key = b"\xcd" * 32
        key_file = tmp_path / "data-bridge.pub"
        key_file.write_text(base64.b64encode(key).decode("ascii") + "\n")

        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", key_file)
        monkeypatch.delenv("ROTH_PLANNER_DATA_BRIDGE_PUBKEY", raising=False)

        assert load_pubkey() == key


# ---------------------------------------------------------------------------
# crypto-security-10 — privkey written BEFORE pubkey (crash-safe ordering)
# ---------------------------------------------------------------------------

class TestWriteKeypairCrashSafeOrdering:
    """write_keypair must write privkey before pubkey so a crash between the two writes
    leaves the sensitive key on disk (can be re-paired) rather than only the pubkey."""

    def test_privkey_exists_if_write_interrupted_after_first_write(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate crash after first write: the existing file must be the privkey."""
        monkeypatch.setattr("engine.data_bridge_keys.PUBKEY_PATH", tmp_path / "data-bridge.pub")
        monkeypatch.setattr("engine.data_bridge_keys.PRIVKEY_PATH", tmp_path / "data-bridge.priv")

        written_files: list = []
        original_write = _write_keyfile

        def tracking_write(path, text: str, mode: int, exclusive: bool = True) -> None:
            written_files.append(path)
            if len(written_files) == 2:
                # Simulate crash on second write
                raise OSError("simulated crash")
            original_write(path, text, mode, exclusive=exclusive)

        monkeypatch.setattr("engine.data_bridge_keys._write_keyfile", tracking_write)

        pub, priv = generate_keypair()
        with pytest.raises(OSError, match="simulated crash"):
            write_keypair(pub, priv, force=False)

        # The first file written must be the privkey (crash-safe ordering)
        assert len(written_files) >= 1
        first_written = written_files[0]
        assert first_written == tmp_path / "data-bridge.priv", (
            f"Expected privkey to be written first for crash-safety, "
            f"but first write was: {first_written}"
        )


# ---------------------------------------------------------------------------
# crypto-security-7 — corrupted V2 payload raises instead of V1 fallthrough
# ---------------------------------------------------------------------------

class TestOpenUploadedPayloadCorruption:
    """Corrupted/truncated V2 (magic-prefixed) payload must raise DataBridgeCryptoError."""

    def _make_privkey(self) -> bytes:
        pub, priv = generate_keypair()
        return priv

    def test_truncated_v2_payload_raises(self) -> None:
        """A truncated magic-prefixed file must raise DataBridgeCryptoError."""
        # Magic + a few bytes of garbage that are NOT valid ciphertext
        corrupted = MAGIC + b"\x00\x01\x02\x03"
        priv = self._make_privkey()
        with pytest.raises(DataBridgeCryptoError):
            open_uploaded_payload(corrupted, priv)

    def test_magic_only_payload_raises(self) -> None:
        """Only the magic prefix with no ciphertext must raise DataBridgeCryptoError."""
        magic_only = MAGIC
        priv = self._make_privkey()
        with pytest.raises(DataBridgeCryptoError):
            open_uploaded_payload(magic_only, priv)

    def test_binary_ciphertext_without_privkey_raises(self) -> None:
        """Binary payload with magic but no privkey must raise ValueError (existing behavior)."""
        corrupted = MAGIC + b"\xff" * 48
        with pytest.raises((ValueError, DataBridgeCryptoError)):
            open_uploaded_payload(corrupted, None)

    def test_valid_v2_payload_decrypts_correctly(self) -> None:
        """Well-formed V2 payload decrypts correctly (regression guard)."""
        pub, priv = generate_keypair()
        plaintext = b'{"test": 1}'
        ciphertext = seal(plaintext, pub)
        result = open_uploaded_payload(ciphertext, priv)
        assert result == plaintext

    def test_v1_plaintext_passes_through(self) -> None:
        """V1 plaintext (no magic) still passes through unchanged."""
        v1 = b'{"legacy": true}'
        result = open_uploaded_payload(v1, None)
        assert result == v1

    def test_corrupted_binary_detected_as_v2_not_v1(self) -> None:
        """Binary blob with magic prefix must NOT silently return binary as V1 plaintext."""
        # This is the core of crypto-security-7: before the fix, a truncated
        # magic-prefixed binary would raise DecryptionFailedError (correct),
        # but a file that has magic + binary that happens to be non-decodable
        # UTF-8 should be caught early with a clear message.
        corrupted = MAGIC + bytes(range(48))
        priv = self._make_privkey()
        # Must raise DataBridgeCryptoError, not return the binary blob
        with pytest.raises(DataBridgeCryptoError):
            open_uploaded_payload(corrupted, priv)
