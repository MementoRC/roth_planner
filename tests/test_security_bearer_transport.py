"""Security tests for bearer token transport guard and file permission warning."""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from engine.portfolio_sync import client as client_module
from engine.portfolio_sync.client import _get, _headers, _token_transport_is_safe


class TestBearerTransportGuard:
    def test_loopback_hosts_are_safe(self) -> None:
        assert _token_transport_is_safe("http://127.0.0.1:7890") is True
        assert _token_transport_is_safe("http://localhost:7890") is True
        assert _token_transport_is_safe("http://[::1]:7890") is True

    def test_https_remote_is_safe(self) -> None:
        assert _token_transport_is_safe("https://example.com") is True

    def test_remote_http_is_unsafe(self) -> None:
        assert _token_transport_is_safe("http://example.com") is False
        assert _token_transport_is_safe("http://192.168.1.50:7890") is False

    def test_headers_omit_token_on_remote_http(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("FINEXTRACT_TOKEN", "secret")
        monkeypatch.delenv("FINEXT_TOKEN", raising=False)
        monkeypatch.setattr(client_module, "BASE_URL", "http://example.com")
        with caplog.at_level(logging.WARNING, logger="engine.portfolio_sync.client"):
            headers = _headers()
        assert "Authorization" not in headers
        assert any("cleartext" in r.message for r in caplog.records)

    def test_headers_include_token_on_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FINEXTRACT_TOKEN", "secret")
        monkeypatch.delenv("FINEXT_TOKEN", raising=False)
        monkeypatch.setattr(client_module, "BASE_URL", "http://127.0.0.1:7890")
        headers = _headers()
        assert headers["Authorization"] == "Bearer secret"

    def test_headers_include_token_on_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FINEXTRACT_TOKEN", "secret")
        monkeypatch.delenv("FINEXT_TOKEN", raising=False)
        monkeypatch.setattr(client_module, "BASE_URL", "https://example.com")
        headers = _headers()
        assert headers["Authorization"] == "Bearer secret"


class TestRedirectHardening:
    def test_redirect_response_raises_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get() must raise HTTPError on any 3xx — never silently follow."""
        captured: dict[str, object] = {}

        def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(status_code=302, headers={"Location": "http://attacker/x"})

        monkeypatch.setattr(client_module.requests, "get", fake_get)
        with pytest.raises(requests.HTTPError):
            _get("/status", timeout=3)

    def test_redirect_get_called_with_allow_redirects_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """requests.get must be invoked with allow_redirects=False."""
        captured: dict[str, object] = {}

        def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(status_code=302, headers={})

        monkeypatch.setattr(client_module.requests, "get", fake_get)
        with contextlib.suppress(requests.HTTPError):
            _get("/status", timeout=3)
        assert captured.get("allow_redirects") is False

    def test_ok_response_returned_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get() returns the response object untouched on a 200."""
        fake_resp = SimpleNamespace(status_code=200, text="ok")

        monkeypatch.setattr(
            client_module.requests, "get", lambda url, **kw: fake_resp
        )
        result = _get("/status", timeout=3)
        assert result is fake_resp


class TestUserDefaultsPermissionWarning:
    def test_warns_on_world_readable(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from config import loader

        p = tmp_path / "user_defaults.json"
        p.write_text("{}")
        os.chmod(p, 0o644)
        with caplog.at_level(logging.WARNING, logger="config.loader"):
            loader._warn_if_insecure_permissions(p)
        assert any("chmod" in r.message for r in caplog.records)

    def test_no_warn_on_secure_mode(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        from config import loader

        p = tmp_path / "user_defaults.json"
        p.write_text("{}")
        os.chmod(p, 0o600)
        with caplog.at_level(logging.WARNING, logger="config.loader"):
            loader._warn_if_insecure_permissions(p)
        assert not caplog.records
