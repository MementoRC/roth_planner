"""Build a static stlite HTML bundle for the Roth Planner.

Usage:
    python deploy/build_stlite.py [--out-dir _site] [--stlite-version 0.76.0]

Scans app.py, engine/, models/, views/, config/, and pages/ for .py files
(skipping tests/, deploy/, __pycache__, and anything matching .gitignore
patterns). Embeds them as a JSON file map into deploy/template.html and
writes the resulting index.html to the output directory.

The generated index.html is fully self-contained — no runtime fetches
from GitHub. Defaults gate ensures only synthetic values are shipped.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

# Directories to scan for runtime .py files (non-existent dirs are skipped)
INCLUDE_DIRS = ["engine", "models", "views", "config", "pages"]
# Standalone files at repo root
INCLUDE_ROOT_FILES = ["app.py"]
# Pyodide-installable runtime requirements
REQUIREMENTS = [
    "streamlit",
    "plotly",
    "pandas",
    "requests",
    "pynacl",
    # pdfminer.six==20251230: the EXACT version VENDORED_PDFPLUMBER_VERSION
    # below requires. pdfplumber itself is NOT listed here -- stlite's
    # requirements list cannot skip dependencies, and pdfplumber's declared
    # pypdfium2/Pillow deps have no WASM wheels (only pdfplumber/display.py
    # imports them, via Page.to_image(), which this app never calls). It used
    # to be installed at RUNTIME instead, via
    # micropip.install("pdfplumber==0.11.9", deps=False) in
    # views/_pdf_runtime.py -- but that fire-and-forget asyncio task never
    # resolves on the public site, leaving "Setting up the PDF reader"
    # permanently stuck there; it is the leading suspect (not a proven
    # cause) for starving the Pyodide worker's message pump and wedging
    # every rerun -- the wedge does not reproduce locally, where micropip
    # reaches PyPI and the install completes. Now pdfplumber's pure-Python
    # source is VENDORED directly into the file map instead (see VENDOR_PACKAGES and
    # _collect_vendored_packages() below), so it's importable at boot with no
    # install step at all. pdfminer.six is still a genuine runtime dependency
    # of pdfplumber and DOES have a WASM wheel, so it stays listed here.
    "pdfminer.six==20251230",
]  # pynacl: needed by engine/data_bridge_crypto for V2 sealed-box upload on public site

# Packages whose pure-Python source is vendored directly into the file map
# (see _collect_vendored_packages()) instead of being listed in REQUIREMENTS.
# pdfplumber is here because stlite's requirements list cannot skip
# dependencies, and pdfplumber's declared pypdfium2 dependency has no WASM
# wheel -- there is no requirements-list spelling that installs pdfplumber
# without also trying (and failing) to install pypdfium2. Vendoring also
# guarantees the browser runs the EXACT pdfplumber version CI tested, with no
# install step (and no matching failure mode) at browser boot.
VENDOR_PACKAGES = ["pdfplumber"]

# The pdfplumber version vendored above, and the version the "pdfminer.six"
# pin in REQUIREMENTS was chosen to match. _collect_vendored_packages() raises
# if the environment's installed pdfplumber does not match this exactly --
# update BOTH this constant and the pdfminer.six pin together, never just one.
VENDORED_PDFPLUMBER_VERSION = "0.11.9"

# Default stlite version (overridable via --stlite-version)
#
# WHY 0.90.0: views/ use the Streamlit width="stretch" API (st.dataframe /
# st.plotly_chart / st.button), which requires Streamlit >=1.49. stlite 0.90.0
# is the FIRST stlite release bundling Streamlit >=1.50 (CHANGELOG: "[0.90.0]
# - 2025-11-13: Update Streamlit to 1.50.0, #1611"). stlite 0.83.0 only ships
# Streamlit 1.45.1 and crashed the deployed site at runtime with
# `TypeError: 'str' object cannot be interpreted as an integer` because
# width="stretch" isn't valid on that older Streamlit.
#
# This pin MUST stay >= the stlite version bundling the newest Streamlit API
# actually used in views/. pixi.toml separately pins streamlit>=1.50 for the
# local dev environment — if this constant falls behind, the deployed stlite
# bundle and the local dev environment diverge and the public site can crash
# again while local tests stay green.
DEFAULT_STLITE_VERSION = "1.9.1"


def _collect_files(repo_root: Path) -> dict[str, str]:
    """Walk INCLUDE_DIRS and INCLUDE_ROOT_FILES; return {relpath: source}."""
    files: dict[str, str] = {}
    for rel in INCLUDE_ROOT_FILES:
        p = repo_root / rel
        if p.exists():
            files[rel] = p.read_text(encoding="utf-8")
    for d in INCLUDE_DIRS:
        base = repo_root / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(repo_root).as_posix()
            files[rel] = p.read_text(encoding="utf-8")
    return files


def _collect_vendored_packages() -> dict[str, str]:
    """Vendor each package in VENDOR_PACKAGES' pure-Python source into the file map.

    Returns {relpath: source} with keys relative to the installed package's
    PARENT directory (e.g. "pdfplumber/__init__.py", "pdfplumber/utils/text.py"),
    matching the import layout Pyodide expects.

    Raises SystemExit if the installed pdfplumber version does not match
    VENDORED_PDFPLUMBER_VERSION -- vendoring the wrong version silently
    desyncs it from the pdfminer.six pin in REQUIREMENTS.
    """
    vendored: dict[str, str] = {}
    for name in VENDOR_PACKAGES:
        mod = importlib.import_module(name)
        if mod.__file__ is None:
            raise RuntimeError(f"Cannot vendor {name!r}: no __file__ (namespace package?)")
        pkg_dir = Path(mod.__file__).parent
        pkg_parent = pkg_dir.parent

        if name == "pdfplumber":
            installed_version = getattr(mod, "__version__", None)
            if installed_version != VENDORED_PDFPLUMBER_VERSION:
                raise SystemExit(
                    f"Installed pdfplumber version {installed_version!r} does not "
                    f"match VENDORED_PDFPLUMBER_VERSION={VENDORED_PDFPLUMBER_VERSION!r} "
                    "in deploy/build_stlite.py. This constant and the "
                    "'pdfminer.six==...' pin in REQUIREMENTS were chosen together "
                    "for one specific pdfplumber release -- update the pdfminer.six "
                    "pin and VENDORED_PDFPLUMBER_VERSION together, never just one, "
                    "then re-run the build."
                )

        for p in sorted(pkg_dir.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(pkg_parent).as_posix()
            vendored[rel] = p.read_text(encoding="utf-8")
    return vendored


def build(repo_root: Path, out_dir: Path, stlite_version: str) -> Path:
    template_path = repo_root / "deploy" / "template.html"
    template = template_path.read_text(encoding="utf-8")

    files = _collect_files(repo_root)
    vendored = _collect_vendored_packages()
    collisions = sorted(set(files) & set(vendored))
    if collisions:
        raise RuntimeError(
            "Vendored package file(s) collide with repo file(s) already in the "
            f"file map: {collisions}. Rename or remove the conflicting repo path "
            "before vendoring can proceed."
        )
    files.update(vendored)

    file_map_json = json.dumps(files, ensure_ascii=False, indent=2)
    requirements_json = json.dumps(REQUIREMENTS)

    rendered = (
        template.replace("__STLITE_VERSION__", stlite_version)
        .replace("__REQUIREMENTS_JSON__", requirements_json)
        .replace("__FILE_MAP_JSON__", file_map_json)
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_html = out_dir / "index.html"
    out_html.write_text(rendered, encoding="utf-8")

    size_kb = len(rendered) / 1024
    vendored_kb = sum(len(v) for v in vendored.values()) / 1024
    repo_file_count = len(files) - len(vendored)
    print(
        f"Wrote {out_html} ({repo_file_count} repo files + {len(vendored)} "
        f"vendored files [{vendored_kb:.1f} KB], {size_kb:.1f} KB total)"
    )
    return out_html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="_site", help="Output directory (default: _site)")
    parser.add_argument(
        "--stlite-version",
        default=DEFAULT_STLITE_VERSION,
        help=f"stlite-mountable version on jsdelivr (default: {DEFAULT_STLITE_VERSION})",
    )
    parser.add_argument("--repo-root", default=".", help="Repo root (default: cwd)")
    args = parser.parse_args()

    build(Path(args.repo_root).resolve(), Path(args.out_dir).resolve(), args.stlite_version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
