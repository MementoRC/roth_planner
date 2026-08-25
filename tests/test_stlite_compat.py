"""Tests for stlite / Streamlit version compatibility.

Option 1 approach: the stlite bundle ships Streamlit >=1.50 (DEFAULT_STLITE_VERSION
>= 0.90.0), so width="stretch" is a valid API in views/.

NOTE ON WHAT THIS FILE CAN AND CANNOT PROVE: every check below is a STATIC check
of a constant (DEFAULT_STLITE_VERSION) and/or a source-text scan. None of it
executes stlite/Pyodide, so it CANNOT detect whether the deployed bundle actually
provides the API the views call at runtime. That gap is not hypothetical: this
exact static check was green (MIN_STLITE_VERSION asserted only >= 0.80.0) while
the live deployed site crashed, because 0.80.0-0.89.x do NOT bundle Streamlit
>=1.50 despite an earlier comment here claiming otherwise. A runtime test is not
practical because the defect only manifests inside a live Streamlit + stlite
rendering loop (Pyodide-only), not in a headless pytest session — so treat these
assertions as a floor, not a guarantee.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEWS_DIR = REPO_ROOT / "views"
# First stlite release bundling Streamlit >=1.50 (CHANGELOG: "[0.90.0] -
# 2025-11-13: Update Streamlit to 1.50.0, #1611"). stlite 0.80.0 only bundles
# Streamlit 1.41.0 -- the prior comment here claiming 0.80.0 was the minimum
# was factually wrong and let a real runtime crash pass this test.
MIN_STLITE_VERSION = (0, 90, 0)


def _parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _collect_view_sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(VIEWS_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        out[rel] = p.read_text(encoding="utf-8")
    return out


def _load_build_stlite_mod():
    spec = importlib.util.spec_from_file_location(
        "build_stlite", REPO_ROOT / "deploy" / "build_stlite.py"
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class TestStliteVersionMinimum:
    """DEFAULT_STLITE_VERSION in deploy/build_stlite.py must be >=0.90.0.

    stlite 0.90.0 is the first release that bundles Streamlit >=1.50, which
    introduced the width="stretch" argument for st.dataframe / st.plotly_chart
    / st.button. (stlite 0.80.0 only bundles Streamlit 1.41.0 and does NOT
    have this API -- do not confuse the stlite version number with the
    Streamlit version it bundles.)
    """

    def test_default_stlite_version_meets_minimum(self) -> None:
        """DEFAULT_STLITE_VERSION must be >= 0.90.0 (bundles Streamlit >=1.50)."""
        mod = _load_build_stlite_mod()
        version_str = str(mod.DEFAULT_STLITE_VERSION)
        version = _parse_version(version_str)
        min_str = ".".join(str(v) for v in MIN_STLITE_VERSION)
        assert version >= MIN_STLITE_VERSION, (
            f"DEFAULT_STLITE_VERSION={version_str!r} is below minimum "
            f"{min_str} required to bundle Streamlit >=1.50 "
            '(needed for width="stretch" support)'
        )


class TestWidthStretchUsedInViews:
    """At least one view must use width="stretch", confirming the Streamlit >=1.50 API.

    This is a positive-direction guard: if views are reverted to use_container_width=True
    this test catches it, prompting a corresponding stlite version bump.
    """

    def test_width_stretch_present_in_views(self) -> None:
        """At least one view must use width="stretch" (the Streamlit >=1.50 API)."""
        sources = _collect_view_sources()
        found = any('width="stretch"' in source for source in sources.values())
        assert found, (
            'width="stretch" not found in any view — '
            "expected Streamlit >=1.50 API to be in use. "
            "If views were reverted to use_container_width=True, also update "
            "DEFAULT_STLITE_VERSION and this test."
        )


class TestStliteVersionCoversUsedStreamlitApis:
    """Derive the required stlite floor from the Streamlit APIs actually used in
    views/, rather than restating a hardcoded constant twice.

    If a future contributor adopts a newer Streamlit-only API without bumping
    DEFAULT_STLITE_VERSION to match, this test fails with an explanation of the
    coupling -- instead of silently passing like the old MIN_STLITE_VERSION did.
    """

    # {marker string found in views/ source: (min Streamlit version, min stlite
    # version that first bundles it)}. Extend this mapping when views/ starts
    # relying on a newer Streamlit-only API.
    API_STLITE_FLOORS: dict[str, tuple[str, tuple[int, int, int]]] = {
        'width="stretch"': ("1.49", (0, 90, 0)),
    }

    def test_stlite_version_covers_apis_used_in_views(self) -> None:
        sources = _collect_view_sources()
        combined = "\n".join(sources.values())
        mod = _load_build_stlite_mod()
        actual_version = _parse_version(str(mod.DEFAULT_STLITE_VERSION))

        for marker, (streamlit_min, stlite_min) in self.API_STLITE_FLOORS.items():
            if marker not in combined:
                continue
            stlite_min_str = ".".join(str(v) for v in stlite_min)
            assert actual_version >= stlite_min, (
                f"views/ use {marker!r} (requires Streamlit >={streamlit_min}), "
                f"which needs stlite >={stlite_min_str}, but "
                f"DEFAULT_STLITE_VERSION={mod.DEFAULT_STLITE_VERSION!r} in "
                "deploy/build_stlite.py is below that floor. Bump "
                "DEFAULT_STLITE_VERSION (and its API_STLITE_FLOORS entry here "
                "if you're adding a new API) so the deployed bundle actually "
                "provides the API the views call, or the public site will "
                "crash at runtime even though this file's other checks pass."
            )


class TestTemplateReferencesCurrentStlitePackage:
    """deploy/template.html must reference @stlite/browser, not the retired @stlite/mountable name.

    @stlite/mountable was renamed to @stlite/browser at v0.76.0 and never published a
    newer version under the old name — referencing @stlite/mountable at any version
    >=0.76.0 means the CDN URL 404s and the public site never boots. Caught in production
    when DEFAULT_STLITE_VERSION was bumped to 0.80.0 (PR #202) without updating the
    package name in template.html.
    """

    def test_template_does_not_reference_retired_mountable_package(self) -> None:
        template = (REPO_ROOT / "deploy" / "template.html").read_text(encoding="utf-8")
        assert "@stlite/mountable" not in template, (
            "deploy/template.html references the retired @stlite/mountable package "
            "(renamed to @stlite/browser at v0.76.0, never published past 0.75.0) — "
            "this 404s at DEFAULT_STLITE_VERSION >= 0.76.0"
        )
        assert "@stlite/browser" in template, (
            "deploy/template.html should reference @stlite/browser (the current "
            "package name post-rename)"
        )
