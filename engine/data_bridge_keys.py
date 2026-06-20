"""Load V2 data-bridge keypair from env vars or ``~/.finextract/data-bridge.{pub,priv}``.

Mirrors the precedence pattern from ``engine.portfolio_sync._load_token``:
env wins, dotfile falls back. Re-read per call so a key rotated mid-session
is picked up without restarting Streamlit.

Key material on disk and in env vars is stored as base64 (preferred) or hex.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

PUBKEY_PATH = Path.home() / ".finextract" / "data-bridge.pub"
PRIVKEY_PATH = Path.home() / ".finextract" / "data-bridge.priv"

PUBKEY_ENV = "ROTH_PLANNER_DATA_BRIDGE_PUBKEY"
PRIVKEY_ENV = "ROTH_PLANNER_DATA_BRIDGE_PRIVKEY"

_KEY_LEN = 32


def _decode_keymaterial(s: str) -> bytes:
    """Decode 32-byte key material from base64 (preferred) or hex.

    Whitespace is stripped. Raises ``ValueError`` if the input does not
    decode to exactly 32 bytes via either encoding.
    """
    s = s.strip()
    try:
        decoded = base64.b64decode(s, validate=True)
        if len(decoded) == _KEY_LEN:
            return decoded
    except ValueError:
        pass
    try:
        decoded = bytes.fromhex(s)
        if len(decoded) == _KEY_LEN:
            return decoded
    except ValueError:
        pass
    raise ValueError(f"Expected {_KEY_LEN}-byte key in base64 or hex; got {len(s)} chars")


def _try_load(env_name: str, path: Path) -> bytes | None:
    env = os.environ.get(env_name)
    if env:
        return _decode_keymaterial(env)
    if path.is_file():
        try:
            return _decode_keymaterial(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def load_pubkey() -> bytes | None:
    """Resolve the V2 data-bridge public key.

    Order: ``ROTH_PLANNER_DATA_BRIDGE_PUBKEY`` env, then
    ``~/.finextract/data-bridge.pub`` file. Returns 32 raw bytes,
    or ``None`` if no key is configured.
    """
    return _try_load(PUBKEY_ENV, PUBKEY_PATH)


def load_privkey() -> bytes | None:
    """Resolve the V2 data-bridge private key.

    Order: ``ROTH_PLANNER_DATA_BRIDGE_PRIVKEY`` env, then
    ``~/.finextract/data-bridge.priv`` file. Returns 32 raw bytes,
    or ``None`` if no key is configured.
    """
    return _try_load(PRIVKEY_ENV, PRIVKEY_PATH)


def _write_keyfile(path: Path, text: str, mode: int) -> None:
    """Create *path* with *mode* atomically, closing the create-then-chmod race.

    ``os.open`` with the mode argument means the private key is never momentarily
    world-readable (the old ``write_text`` + ``chmod`` pattern left a 0644 window).
    ``os.fchmod`` re-asserts the exact mode so a force=True overwrite of a
    pre-existing looser-permissioned file is corrected, independent of umask.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def write_keypair(pubkey: bytes, privkey: bytes, *, force: bool = False) -> None:
    """Write keypair to ``~/.finextract/data-bridge.{pub,priv}`` as base64.

    Public key gets mode 0644 (safe to share); private key gets 0600.
    Refuses to overwrite existing files unless ``force=True``.
    """
    if not force:
        if PUBKEY_PATH.exists():
            raise FileExistsError(f"{PUBKEY_PATH} already exists; pass force=True to overwrite")
        if PRIVKEY_PATH.exists():
            raise FileExistsError(f"{PRIVKEY_PATH} already exists; pass force=True to overwrite")
    PUBKEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_keyfile(PUBKEY_PATH, base64.b64encode(pubkey).decode("ascii") + "\n", 0o644)
    _write_keyfile(PRIVKEY_PATH, base64.b64encode(privkey).decode("ascii") + "\n", 0o600)


def decode_keymaterial(s: str) -> bytes:
    """Public wrapper for :func:`_decode_keymaterial`.

    Decodes 32-byte key material from base64 (preferred) or hex.
    Used by callers outside this module that need to validate or parse
    a pasted key string (e.g., the Streamlit private-key widget).
    """
    return _decode_keymaterial(s)
