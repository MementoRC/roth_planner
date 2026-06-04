"""End-to-end encryption for the V2 data bridge.

Uses libsodium ``crypto_box_seal`` (X25519 + XChaCha20-Poly1305-Ietf via
PyNaCl) for anonymous-sender authenticated encryption. Sender only needs
the recipient's public key; recipient uses their private key to decrypt.

File format: 4-byte magic prefix ``FX1\\0`` followed by sealed-box ciphertext.
The magic lets the upload widget distinguish V1 plaintext ``.json`` from
V2 ``.json.enc`` deterministically.
"""

from __future__ import annotations

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox

MAGIC = b"FX1\x00"


class DataBridgeCryptoError(Exception):
    """Base exception for data bridge encryption operations."""


class InvalidMagicError(DataBridgeCryptoError):
    """Ciphertext does not begin with the expected V2 magic prefix."""


class DecryptionFailedError(DataBridgeCryptoError):
    """Sealed-box decryption failed (wrong key, tampered ciphertext, etc.)."""


def has_magic(blob: bytes) -> bool:
    """Return True if ``blob`` begins with the V2 magic prefix."""
    return blob[: len(MAGIC)] == MAGIC


def seal(plaintext: bytes, pubkey: bytes) -> bytes:
    """Encrypt ``plaintext`` to the recipient identified by ``pubkey``.

    Returns magic-prefixed sealed-box ciphertext suitable for direct
    ``.json.enc`` save.
    """
    box = SealedBox(PublicKey(pubkey))
    return MAGIC + box.encrypt(plaintext)


def unseal(ciphertext: bytes, privkey: bytes) -> bytes:
    """Decrypt magic-prefixed ``ciphertext`` using ``privkey``.

    Raises:
        InvalidMagicError: if the magic prefix is missing or wrong.
        DecryptionFailedError: if decryption fails (wrong key, tamper, etc.).
    """
    if not has_magic(ciphertext):
        raise InvalidMagicError(f"Expected magic prefix {MAGIC!r}, got {ciphertext[:4]!r}")
    box = SealedBox(PrivateKey(privkey))
    try:
        return box.decrypt(ciphertext[len(MAGIC) :])
    except CryptoError as e:
        raise DecryptionFailedError(str(e)) from e


def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a fresh X25519 keypair.

    Returns ``(pubkey, privkey)`` as raw 32-byte values.
    """
    priv = PrivateKey.generate()
    return bytes(priv.public_key), bytes(priv)
