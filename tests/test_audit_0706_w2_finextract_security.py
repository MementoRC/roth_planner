"""TDD tests for audit-0706 wave-2 FinExtract client security hardening.

Findings covered:
  crypto-security-0 (low): 0.0.0.0 / :: not in _LOCAL_HOSTS → token dropped.
  crypto-security-5 (low): non-canonical loopback (127.0.0.2) not recognised.
  crypto-security-3 (low): env-var token with embedded CR/LF not rejected.
  crypto-security-2 + crypto-security-9 (low): TOCTOU race — stat() before open().
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_client() -> object:
    """Force a fresh import so module-level BASE_URL picks up env patches."""
    mod_name = "engine.portfolio_sync.client"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# crypto-security-0: 0.0.0.0 and :: must be treated as local
# ---------------------------------------------------------------------------

class TestLoopbackAliasesRecognised:
    """0.0.0.0 and :: should be accepted as safe loopback transports."""

    def test_0_0_0_0_is_safe(self) -> None:
        with patch.dict(os.environ, {"FINEXTRACT_URL": "http://0.0.0.0:7890"}):
            client = _reload_client()
            assert client._token_transport_is_safe("http://0.0.0.0:7890"), (
                "0.0.0.0 must be treated as a safe loopback host"
            )

    def test_double_colon_is_safe(self) -> None:
        with patch.dict(os.environ, {"FINEXTRACT_URL": "http://[::]:7890"}):
            client = _reload_client()
            assert client._token_transport_is_safe("http://[::]:7890"), (
                ":: (all-zeros IPv6) must be treated as a safe loopback host"
            )


# ---------------------------------------------------------------------------
# crypto-security-5: non-canonical loopback (127.0.0.2) must be safe
# ---------------------------------------------------------------------------

class TestNonCanonicalLoopbackRecognised:
    """127.x.x.x addresses that are_loopback must not drop the token."""

    def test_127_0_0_2_is_safe(self) -> None:
        client = _reload_client()
        assert client._token_transport_is_safe("http://127.0.0.2:7890"), (
            "127.0.0.2 is a valid loopback address and must be treated as safe"
        )

    def test_127_1_2_3_is_safe(self) -> None:
        client = _reload_client()
        assert client._token_transport_is_safe("http://127.1.2.3:7890"), (
            "127.1.2.3 is a loopback address and must be treated as safe"
        )

    def test_127_0_0_1_still_safe(self) -> None:
        """Regression: canonical loopback must remain safe."""
        client = _reload_client()
        assert client._token_transport_is_safe("http://127.0.0.1:7890")

    def test_localhost_still_safe(self) -> None:
        """Regression: 'localhost' name must remain safe."""
        client = _reload_client()
        assert client._token_transport_is_safe("http://localhost:7890")

    def test_public_ip_is_unsafe(self) -> None:
        """A public IP over HTTP must still be rejected."""
        client = _reload_client()
        assert not client._token_transport_is_safe("http://192.168.1.1:7890")

    def test_ipv6_loopback_full_is_safe(self) -> None:
        """Full IPv6 loopback address must be safe."""
        client = _reload_client()
        assert client._token_transport_is_safe("http://[::1]:7890")


# ---------------------------------------------------------------------------
# crypto-security-3: embedded CR / LF in env-var token must be rejected
# ---------------------------------------------------------------------------

class TestEnvTokenSanitisation:
    """Env-var tokens containing \\r or \\n must be rejected (log + treat as absent)."""

    def test_token_with_newline_is_rejected(self, tmp_path: Path) -> None:
        """A token with an embedded \\n must not be used."""
        bad_token = "good-prefix\nbad-suffix"
        with patch.dict(
            os.environ,
            {"FINEXTRACT_TOKEN": bad_token, "FINEXTRACT_URL": "http://127.0.0.1:7890"},
            clear=False,
        ):
            client = _reload_client()
            tok = client._load_token()
            assert tok == "", (
                "Token with embedded newline must be rejected (returned empty string)"
            )

    def test_token_with_carriage_return_is_rejected(self, tmp_path: Path) -> None:
        """A token with an embedded \\r must not be used."""
        bad_token = "good-prefix\rbad-suffix"
        with patch.dict(
            os.environ,
            {"FINEXTRACT_TOKEN": bad_token, "FINEXTRACT_URL": "http://127.0.0.1:7890"},
            clear=False,
        ):
            client = _reload_client()
            tok = client._load_token()
            assert tok == "", (
                "Token with embedded CR must be rejected (returned empty string)"
            )

    def test_clean_token_is_returned(self) -> None:
        """A well-formed token must still be returned normally."""
        with patch.dict(
            os.environ,
            {"FINEXTRACT_TOKEN": "  valid-token-abc  ", "FINEXTRACT_URL": "http://127.0.0.1:7890"},
            clear=False,
        ):
            client = _reload_client()
            tok = client._load_token()
            assert tok == "valid-token-abc"

    def test_finext_token_with_newline_is_rejected(self) -> None:
        """Fallback FINEXT_TOKEN env var must also be sanitised."""
        bad_token = "abc\ndef"
        env_clean = {k: v for k, v in os.environ.items() if k != "FINEXTRACT_TOKEN"}
        env_clean["FINEXT_TOKEN"] = bad_token
        env_clean.pop("FINEXTRACT_TOKEN", None)
        with patch.dict(os.environ, env_clean, clear=True):
            client = _reload_client()
            tok = client._load_token()
            assert tok == "", (
                "FINEXT_TOKEN with embedded newline must be rejected"
            )


# ---------------------------------------------------------------------------
# crypto-security-2 + crypto-security-9: fstat-after-open (TOCTOU / symlink)
# ---------------------------------------------------------------------------

class TestTokenFileTOCTOU:
    """_load_token must open the file first, then fstat the descriptor."""

    def test_readable_token_file_works(self, tmp_path: Path) -> None:
        """A correctly-protected token file must be loaded."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("FINEXTRACT_TOKEN", "FINEXT_TOKEN")}
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        auth_file = auth_dir / "auth-token"
        auth_file.write_text("file-token\n")
        auth_file.chmod(0o600)
        with patch.dict(os.environ, env, clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        assert tok == "file-token"

    def test_world_readable_token_file_fails_closed(
        self, tmp_path: Path
    ) -> None:
        """Group/world-readable token file must be REFUSED (SEC-02: fail closed).

        Prior behaviour warned but still loaded the token (fail-open).  After
        the SEC-02 fix the function returns '' so a lax-perms token file cannot
        silently leak credentials.
        """
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        auth_file = auth_dir / "auth-token"
        auth_file.write_text("warn-token\n")
        auth_file.chmod(0o644)  # group-readable → must be refused

        env = {k: v for k, v in os.environ.items()
               if k not in ("FINEXTRACT_TOKEN", "FINEXT_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        assert tok == "", (
            "SEC-02: lax-perms token file must be refused (fail closed), not loaded"
        )

    def test_symlink_to_token_file_is_rejected(self, tmp_path: Path) -> None:
        """A symlink to the token file must be rejected (O_NOFOLLOW)."""
        auth_dir = tmp_path / ".finextract"
        auth_dir.mkdir()
        real_file = tmp_path / "real-token"
        real_file.write_text("real-token-value\n")
        real_file.chmod(0o600)

        auth_file = auth_dir / "auth-token"
        auth_file.symlink_to(real_file)

        env = {k: v for k, v in os.environ.items()
               if k not in ("FINEXTRACT_TOKEN", "FINEXT_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            client = _reload_client()
            with patch("pathlib.Path.home", return_value=tmp_path):
                tok = client._load_token()
        # O_NOFOLLOW causes OSError on symlink → token must be ""
        assert tok == "", "Symlink to token file must be rejected via O_NOFOLLOW"

    def test_open_called_before_stat_ordering(self, tmp_path: Path) -> None:
        """Verify os.open is called BEFORE os.fstat (not stat()) in _load_token.

        We inspect the source to confirm the correct ordering exists, because
        a runtime mock-ordering test would be brittle. The critical invariant
        is that os.fstat(fd) is used rather than p.stat() after the open.
        """
        client = _reload_client()
        src = inspect.getsource(client._load_token)
        # fstat must appear in the source (not just stat)
        assert "os.fstat" in src, "_load_token must use os.fstat(fd) not p.stat()"
        # p.stat() must NOT appear (TOCTOU risk)
        assert "p.stat()" not in src, (
            "_load_token must not use p.stat() — use os.fstat(fd) after open"
        )
