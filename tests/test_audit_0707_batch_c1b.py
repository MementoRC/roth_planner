"""Audit-0707 Batch C1b — security IO fixes.

Fixes covered:
  SEC-02   engine/portfolio_sync/client.py  — lax-perms token file fail-closed (not fail-open)
  SEC-03   engine/portfolio_sync/client.py  — file token routed through _sanitise_env_token
  SU1-SEC-02  engine/data_bridge_keys.py   — open fd first, fstat for perms (TOCTOU fix)
  SU1-SEC-04  engine/data_bridge_keys.py   — drain fd in read loop (no under-read)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# SEC-02 — lax-perms token file must be refused (fail-closed)
# SEC-03 — file token routed through _sanitise_env_token
# ---------------------------------------------------------------------------


def _reload_client() -> object:
    """Load engine.portfolio_sync.client directly without triggering package __init__.

    The package __init__ re-exports from all submodules (awards, exercises, holdings…)
    which in turn need the full models package.  We only need client.py itself, so we
    load it via spec_from_file_location to stay self-contained in the sparse worktree.
    """
    import importlib.util

    mod_name = "engine.portfolio_sync.client"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Locate client.py relative to this test file's parent (worktree root)
    client_path = Path(__file__).parent.parent / "engine" / "portfolio_sync" / "client.py"
    spec = importlib.util.spec_from_file_location(mod_name, client_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _clean_env(extra_remove: tuple[str, ...] = ()) -> dict[str, str]:
    """Env without any token vars."""
    remove = {"FINEXTRACT_TOKEN", "FINEXT_TOKEN"} | set(extra_remove)
    return {k: v for k, v in os.environ.items() if k not in remove}


class TestSEC02FailClosed:
    """SEC-02: group/world-readable token file must return '' (fail closed)."""

    def test_0o644_token_file_returns_empty(self, tmp_path: Path) -> None:
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        auth_file = auth_dir / "auth-token"
        auth_file.write_text("secret-token\n")
        auth_file.chmod(0o644)

        with patch.dict(os.environ, _clean_env(), clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        assert tok == "", "0o644 token file must be refused (SEC-02 fail-closed)"

    def test_0o600_token_file_loads(self, tmp_path: Path) -> None:
        """A correctly-restricted token file must still be loaded."""
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        auth_file = auth_dir / "auth-token"
        auth_file.write_text("good-token\n")
        auth_file.chmod(0o600)

        with patch.dict(os.environ, _clean_env(), clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        assert tok == "good-token"


class TestSEC03FileSanitise:
    """SEC-03: file token with embedded CR/LF must be sanitised by _sanitise_env_token."""

    def test_crlf_in_file_token_rejected(self, tmp_path: Path) -> None:
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        auth_file = auth_dir / "auth-token"
        # Windows-style line endings embedded in the token value
        auth_file.write_bytes(b"valid\r\nbad-header-injection")
        auth_file.chmod(0o600)

        with patch.dict(os.environ, _clean_env(), clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        assert "\r" not in tok
        assert "\n" not in tok
        # The _sanitise_env_token logic rejects tokens with embedded CR/LF
        assert tok == "", "Token with embedded CR/LF must be rejected (SEC-03)"

    def test_trailing_newline_stripped(self, tmp_path: Path) -> None:
        """A plain trailing newline (no CR) must be stripped, not rejected."""
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        auth_file = auth_dir / "auth-token"
        auth_file.write_text("my-token\n")  # trailing \n only — safe
        auth_file.chmod(0o600)

        with patch.dict(os.environ, _clean_env(), clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        assert tok == "my-token"

    def test_bare_cr_in_file_token_rejected(self, tmp_path: Path) -> None:
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        auth_file = auth_dir / "auth-token"
        auth_file.write_bytes(b"tok\rbad")
        auth_file.chmod(0o600)

        with patch.dict(os.environ, _clean_env(), clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        assert tok == "", "Bare CR in file token must be rejected (SEC-03)"


# ---------------------------------------------------------------------------
# SU1-SEC-02 — open fd first, derive perms from fstat (TOCTOU fix)
# SU1-SEC-04 — read loop drains files > 4096 bytes
# SU1-SEC-01 regression — secret key with lax perms still returns None
# ---------------------------------------------------------------------------


class TestSU1SEC02TOCTOU:
    """SU1-SEC-02: permissions derived from fstat(fd), not a prior path.stat()."""

    def test_secret_0o644_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Secret key with lax perms must be refused (SU1-SEC-01 regression guard)."""
        import base64

        from engine.data_bridge_keys import _try_load

        key = b"\xab" * 32
        key_file = tmp_path / "data-bridge.priv"
        key_file.write_text(base64.b64encode(key).decode("ascii") + "\n")
        key_file.chmod(0o644)

        with pytest.warns(RuntimeWarning, match="lax permissions"):
            result = _try_load("NONEXISTENT_ENV_VAR", key_file, secret=True)
        assert result is None, "secret=True + 0o644 must return None (SU1-SEC-01)"

    def test_secret_0o600_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Secret key with correct perms must load successfully."""
        import base64

        from engine.data_bridge_keys import _try_load

        key = b"\xcd" * 32
        key_file = tmp_path / "data-bridge.priv"
        key_file.write_text(base64.b64encode(key).decode("ascii") + "\n")
        key_file.chmod(0o600)

        result = _try_load("NONEXISTENT_ENV_VAR", key_file, secret=True)
        assert result == key

    def test_non_secret_0o644_loads_without_warning(
        self, tmp_path: Path
    ) -> None:
        """Non-secret key with 0o644 (readable, not writable) loads with no warning."""
        import base64
        import warnings

        from engine.data_bridge_keys import _try_load

        key = b"\xef" * 32
        key_file = tmp_path / "data-bridge.pub"
        key_file.write_text(base64.b64encode(key).decode("ascii") + "\n")
        key_file.chmod(0o644)

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = _try_load("NONEXISTENT_ENV_VAR", key_file, secret=False)
        assert result == key

    def test_non_secret_world_writable_loads_with_warning(
        self, tmp_path: Path
    ) -> None:
        """Non-secret key with a world-writable mode must warn but still load."""
        import base64

        from engine.data_bridge_keys import _try_load

        key = b"\xef" * 32
        key_file = tmp_path / "data-bridge.pub"
        key_file.write_text(base64.b64encode(key).decode("ascii") + "\n")
        key_file.chmod(0o646)

        with pytest.warns(RuntimeWarning, match="group- or world-writable"):
            result = _try_load("NONEXISTENT_ENV_VAR", key_file, secret=False)
        assert result == key


class TestSU1SEC04ReadLoop:
    """SU1-SEC-04: files larger than 4096 bytes are read in full."""

    def test_large_key_file_read_in_full(
        self, tmp_path: Path
    ) -> None:
        """A key file whose raw bytes exceed 4096 bytes is read completely.

        _decode_keymaterial calls strip() on the entire content, so we pad with
        spaces on both sides of the key — after strip() the result is exactly the
        base64-encoded key.  Total file size > 10 000 bytes exercises the drain
        loop (SU1-SEC-04): a single os.read(fd, 4096) would return only part of
        the file; the loop must accumulate all chunks before decoding.
        """
        import base64

        from engine.data_bridge_keys import _try_load

        key = b"\x55" * 32
        key_b64 = base64.b64encode(key).decode("ascii")
        # Leading + trailing spaces: strip() reduces content to just key_b64.
        padding = " " * 5000
        content = padding + key_b64 + padding
        assert len(content.encode()) > 4096, "precondition: content must exceed 4096 bytes"

        key_file = tmp_path / "big.key"
        key_file.write_text(content)
        key_file.chmod(0o600)

        result = _try_load("NONEXISTENT_ENV_VAR", key_file, secret=True)
        assert result == key, "Key from large file (>4096 bytes) must be decoded correctly"
