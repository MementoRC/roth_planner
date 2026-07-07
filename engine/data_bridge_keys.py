"""Load V2 data-bridge keypair from env vars or ``~/.finextract/data-bridge.{pub,priv}``.

Mirrors the precedence pattern from ``engine.portfolio_sync._load_token``:
env wins, dotfile falls back. Re-read per call so a key rotated mid-session
is picked up without restarting Streamlit.

Key material on disk and in env vars is stored as base64 (preferred) or hex.
"""

from __future__ import annotations

import base64
import os
import stat
import warnings
from pathlib import Path

PUBKEY_PATH = Path.home() / ".finextract" / "data-bridge.pub"
PRIVKEY_PATH = Path.home() / ".finextract" / "data-bridge.priv"

PUBKEY_ENV = "ROTH_PLANNER_DATA_BRIDGE_PUBKEY"
PRIVKEY_ENV = "ROTH_PLANNER_DATA_BRIDGE_PRIVKEY"

_KEY_LEN = 32


def _decode_keymaterial(s: str) -> bytes:
    """Decode 32-byte key material from base64 (preferred) or hex.

    Whitespace is stripped.  Both padded and unpadded standard base64 are
    accepted (crypto-security-4: unpadded keys were silently rejected because
    ``validate=True`` requires canonical padding).  Padding is normalised
    before decoding so either form is accepted.

    Raises ``ValueError`` if the input does not decode to exactly 32 bytes
    via either encoding.
    """
    s = s.strip()
    try:
        # Normalise padding so unpadded base64 is accepted (crypto-security-4).
        s_padded = s + "=" * (-len(s) % 4)
        decoded = base64.b64decode(s_padded, validate=False)
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


def _read_keyfile_nofollow(path: Path) -> str:
    """Read text from *path* without following symlinks (crypto-security-8).

    Uses ``os.open`` with ``os.O_NOFOLLOW`` so a pre-planted symlink at the
    expected path cannot redirect a private-key read to an attacker-readable
    location.  Raises ``OSError`` (ELOOP on Linux) if *path* is a symlink.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        file_size = os.fstat(fd).st_size
        raw = os.read(fd, max(file_size, 4096))
    finally:
        os.close(fd)
    return raw.decode("utf-8")


def _try_load(env_name: str, path: Path) -> bytes | None:
    env = os.environ.get(env_name)
    if env:
        return _decode_keymaterial(env)
    if path.is_file():
        try:
            file_mode = stat.S_IMODE(path.stat().st_mode)
            if file_mode & 0o077:
                warnings.warn(
                    f"{path}: key file is group- or world-readable (mode {oct(file_mode)}); "
                    "consider restricting to 0o600.",
                    RuntimeWarning,
                    stacklevel=3,
                )
            # crypto-security-8: use O_NOFOLLOW to prevent symlink follow on read.
            return _decode_keymaterial(_read_keyfile_nofollow(path))
        except OSError:
            return None
        except ValueError as e:
            warnings.warn(
                f"{path}: key file unreadable ({e}); ignoring.",
                RuntimeWarning,
                stacklevel=3,
            )
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


def _write_keyfile(path: Path, text: str, mode: int, *, exclusive: bool = False) -> None:
    """Create *path* with *mode* atomically, closing the create-then-chmod race.

    ``os.open`` with the mode argument means the private key is never momentarily
    world-readable (the old ``write_text`` + ``chmod`` pattern left a 0644 window).
    ``os.fchmod`` re-asserts the exact mode so a force=True overwrite of a
    pre-existing looser-permissioned file is corrected, independent of umask.
    ``os.O_NOFOLLOW`` causes ``os.open`` to raise ``OSError`` (ELOOP) if *path*
    is a symlink, preventing a pre-planted symlink from redirecting the private
    key to an attacker-readable location.

    When *exclusive* is ``True`` (non-force write), ``os.O_EXCL`` is added so
    the open fails atomically if *path* already exists, closing the TOCTOU race
    that existed between the ``path.exists()`` pre-check and the open
    (crypto-security-6).
    """
    if exclusive:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    fd = os.open(path, flags, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def write_keypair(pubkey: bytes, privkey: bytes, *, force: bool = False) -> None:
    """Write keypair to ``~/.finextract/data-bridge.{pub,priv}`` as base64.

    Public key gets mode 0644 (safe to share); private key gets 0600.
    Refuses to overwrite existing files unless ``force=True``.

    Security notes:
    - crypto-security-6: non-force writes use ``O_EXCL`` for atomic exclusion,
      eliminating the TOCTOU race between the pre-check and the open.
    - crypto-security-10: privkey is written BEFORE pubkey so that a crash
      between the two writes leaves the sensitive key on disk (recoverable)
      rather than only the public key.
    """
    PUBKEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    exclusive = not force
    # crypto-security-10: write privkey first — crash between writes preserves
    # the sensitive key rather than only the (non-secret) public key.
    _write_keyfile(PRIVKEY_PATH, base64.b64encode(privkey).decode("ascii") + "\n", 0o600, exclusive=exclusive)
    _write_keyfile(PUBKEY_PATH, base64.b64encode(pubkey).decode("ascii") + "\n", 0o644, exclusive=exclusive)


def decode_keymaterial(s: str) -> bytes:
    """Public wrapper for :func:`_decode_keymaterial`.

    Decodes 32-byte key material from base64 (preferred) or hex.
    Used by callers outside this module that need to validate or parse
    a pasted key string (e.g., the Streamlit private-key widget).
    """
    return _decode_keymaterial(s)
