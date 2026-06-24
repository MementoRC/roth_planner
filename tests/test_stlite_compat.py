"""Tests for stlite / Streamlit version compatibility.

Cluster B fix: width="stretch" (Streamlit >=1.50 API) must not appear in any
view source file because the stlite bundle ships Streamlit ~1.40.x which does
not support that argument.  use_container_width=True is the correct replacement
for all st.dataframe / st.plotly_chart / st.button calls.

A runtime test is not practical here because the defect only manifests inside a
live Streamlit + stlite rendering loop (Pyodide-only), not in a headless pytest
session.  Instead we use static source analysis — the same approach used by
tests/test_audit_security_cluster.py and tests/test_views_setup.py.

The build-guard in deploy/build_stlite.py provides a second gate that fires at
build time (``_guard_incompatible_args``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWS_DIR = REPO_ROOT / "views"


def _collect_view_sources() -> dict[str, str]:
    """Return {relpath: source} for all .py files under views/."""
    out: dict[str, str] = {}
    for p in sorted(VIEWS_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        out[rel] = p.read_text(encoding="utf-8")
    return out


class TestNoWidthStretchInViews:
    """width="stretch" must not appear anywhere under views/.

    That argument was introduced in Streamlit >=1.50 and raises AttributeError
    on the bundled stlite version (~1.40.x).  The correct alternative is
    use_container_width=True.
    """

    def test_no_width_stretch_in_any_view_file(self):
        """Scan every views/*.py file and assert the incompatible arg is absent."""
        offenders: list[tuple[str, int]] = []
        sources = _collect_view_sources()
        for relpath, source in sources.items():
            for lineno, line in enumerate(source.splitlines(), start=1):
                if 'width="stretch"' in line:
                    offenders.append((relpath, lineno))

        assert not offenders, (
            "width=\"stretch\" found in view source files "
            "(incompatible with stlite-bundled Streamlit ~1.40.x). "
            "Replace with use_container_width=True:\n"
            + "\n".join(f"  {path}:{ln}" for path, ln in offenders)
        )

    def test_use_container_width_used_instead(self):
        """At least one view must use use_container_width=True, confirming the
        replacement was applied and not just deleted."""
        sources = _collect_view_sources()
        found = any(
            "use_container_width=True" in source for source in sources.values()
        )
        assert found, (
            "use_container_width=True not found in any view — "
            "width=\"stretch\" may have been deleted rather than replaced"
        )


class TestBuildGuardFunction:
    """Unit tests for deploy/build_stlite._guard_incompatible_args."""

    def _import_guard(self):
        deploy_dir = str(REPO_ROOT / "deploy")
        if deploy_dir not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        # Import via importlib to avoid namespace clash
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_stlite", REPO_ROOT / "deploy" / "build_stlite.py"
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod._guard_incompatible_args

    def test_clean_files_pass(self):
        """Files without width="stretch" must pass without error."""
        guard = self._import_guard()
        guard({"views/foo.py": 'st.dataframe(df, use_container_width=True)'})

    def test_width_stretch_raises(self):
        """A file containing width="stretch" must cause SystemExit."""
        guard = self._import_guard()
        with pytest.raises(SystemExit, match='width="stretch"'):
            guard({"views/bar.py": 'st.dataframe(df, width="stretch")'})

    def test_error_message_names_file(self):
        """SystemExit message must include the offending file path."""
        guard = self._import_guard()
        with pytest.raises(SystemExit) as exc_info:
            guard({"views/problem_file.py": 'st.plotly_chart(fig, width="stretch")'})
        assert "views/problem_file.py" in str(exc_info.value)

    def test_error_message_names_replacement(self):
        """SystemExit message must mention the correct replacement."""
        guard = self._import_guard()
        with pytest.raises(SystemExit) as exc_info:
            guard({"views/x.py": 'st.button("Go", width="stretch")'})
        assert "use_container_width=True" in str(exc_info.value)

    def test_empty_files_dict_passes(self):
        """Empty file map must not raise."""
        guard = self._import_guard()
        guard({})  # must not raise
