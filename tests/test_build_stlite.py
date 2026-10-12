"""Tests for deploy/build_stlite.py's pdfplumber vendoring.

pdfplumber cannot be listed in REQUIREMENTS (stlite's requirements list
cannot skip dependencies, and pdfplumber's declared pypdfium2 dependency has
no WASM wheel) and cannot be installed at runtime via a fire-and-forget
micropip task either -- that exact pattern never resolves on the public
site, leaving "Setting up the PDF reader" permanently stuck there, and is
the leading suspect (not a proven cause) for the public site's rerun wedge.
The wedge does not reproduce locally, where micropip reaches PyPI and the
install completes. Instead deploy/build_stlite.py vendors pdfplumber's
pure-Python source directly into the file map at build time. These tests
guard that vendoring, never building into the repo tree itself.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_build_stlite_mod():
    spec = importlib.util.spec_from_file_location(
        "build_stlite", REPO_ROOT / "deploy" / "build_stlite.py"
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestPdfplumberIsVendoredNotRequired:
    """pdfplumber must never appear in REQUIREMENTS -- only vendored."""

    def test_requirements_excludes_pypdfium2(self) -> None:
        mod = _load_build_stlite_mod()
        combined = " ".join(mod.REQUIREMENTS)
        assert "pypdfium2" not in combined, (
            "REQUIREMENTS must not list pypdfium2 -- it has no WASM wheel and "
            "cannot be installed in the stlite/Pyodide runtime"
        )

    def test_requirements_excludes_pillow(self) -> None:
        mod = _load_build_stlite_mod()
        combined = " ".join(mod.REQUIREMENTS).lower()
        assert "pillow" not in combined, (
            "REQUIREMENTS must not list Pillow -- pdfplumber only needs it for "
            "Page.to_image(), which this app never calls, and it has no WASM wheel"
        )

    def test_requirements_excludes_pdfplumber_itself(self) -> None:
        mod = _load_build_stlite_mod()
        combined = " ".join(mod.REQUIREMENTS)
        assert "pdfplumber" not in combined, (
            "pdfplumber must be VENDORED via VENDOR_PACKAGES, not listed in "
            "REQUIREMENTS -- a requirements-list entry would try to install "
            "its pypdfium2 dependency and fail"
        )


class TestVendoredFileMap:
    """The built file map must embed pdfplumber's actual source tree."""

    def test_file_map_contains_pdfplumber_init(self) -> None:
        mod = _load_build_stlite_mod()
        vendored = mod._collect_vendored_packages()
        assert "pdfplumber/__init__.py" in vendored

    def test_file_map_contains_nested_pdfplumber_module(self) -> None:
        mod = _load_build_stlite_mod()
        vendored = mod._collect_vendored_packages()
        nested = [k for k in vendored if "/" in k[len("pdfplumber/") :]]
        assert nested, (
            f"expected at least one nested pdfplumber module (e.g. "
            f"pdfplumber/utils/text.py), got top-level keys only: {sorted(vendored)}"
        )

    def test_build_output_embeds_vendored_files(self, tmp_path: Path) -> None:
        mod = _load_build_stlite_mod()
        out_dir = tmp_path / "site"
        out_html = mod.build(REPO_ROOT, out_dir, mod.DEFAULT_STLITE_VERSION)
        assert out_html.exists()
        assert out_html.parent == out_dir  # never written into the repo tree
        rendered = out_html.read_text(encoding="utf-8")
        assert '"pdfplumber/__init__.py"' in rendered


class TestVendorRepoCollisionGuard:
    """A vendored key colliding with a repo file must raise, not overwrite."""

    def test_collision_between_vendored_and_repo_files_raises(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        mod = _load_build_stlite_mod()
        monkeypatch.setattr(
            mod,
            "_collect_vendored_packages",
            lambda: {"app.py": "# vendored collision"},
        )
        with pytest.raises(RuntimeError, match="collide"):
            mod.build(REPO_ROOT, tmp_path / "site", mod.DEFAULT_STLITE_VERSION)


class TestFileWatcherDisabledInBuiltBundle:
    """The built file map must ship a .streamlit/config.toml disabling the polling watcher.

    Guards against Streamlit's polling file watcher exhausting WASM memory and
    killing the Pyodide kernel after ~135s of idle: watchdog is unavailable in
    WASM, so fileWatcherType="auto" silently degrades to polling, and the
    polling task self-reschedules forever -- a static single-file bundle's
    source can never change, so the watcher has no purpose here and must stay
    disabled.
    """

    def test_config_toml_constant_disables_watcher(self) -> None:
        mod = _load_build_stlite_mod()
        assert 'fileWatcherType = "none"' in mod.STLITE_CONFIG_TOML

    def test_build_output_embeds_config_toml_disabling_watcher(self, tmp_path: Path) -> None:
        mod = _load_build_stlite_mod()
        out_dir = tmp_path / "site"
        out_html = mod.build(REPO_ROOT, out_dir, mod.DEFAULT_STLITE_VERSION)
        rendered = out_html.read_text(encoding="utf-8")
        start = rendered.index("const FILES = ") + len("const FILES = ")
        end = rendered.index(";\n", start)
        files = json.loads(rendered[start:end])
        assert ".streamlit/config.toml" in files
        assert 'fileWatcherType = "none"' in files[".streamlit/config.toml"]

    def test_build_output_config_carries_theme_and_watcher(self, tmp_path: Path) -> None:
        mod = _load_build_stlite_mod()
        out_html = mod.build(REPO_ROOT, tmp_path / "site", mod.DEFAULT_STLITE_VERSION)
        rendered = out_html.read_text(encoding="utf-8")
        start = rendered.index("const FILES = ") + len("const FILES = ")
        end = rendered.index(";\n", start)
        config = json.loads(rendered[start:end])[".streamlit/config.toml"]
        assert "[theme.light]" in config
        assert "[theme.dark]" in config
        assert 'fileWatcherType = "none"' in config
        assert 'base = "' not in config
        repo_theme = (REPO_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        assert repo_theme.rstrip("\n") in config
        assert "[server]" not in repo_theme  # local behaviour unchanged


class TestVendoredVersionGuard:
    """A pdfplumber version mismatch must raise, never silently vendor the wrong source."""

    def test_version_mismatch_raises(self, monkeypatch) -> None:
        mod = _load_build_stlite_mod()
        monkeypatch.setattr(mod, "VENDORED_PDFPLUMBER_VERSION", "999.0.0")
        with pytest.raises(SystemExit, match="does not"):
            mod._collect_vendored_packages()
