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
]  # pynacl: needed by engine/data_bridge_crypto for V2 sealed-box upload on public site
# Default stlite version (overridable via --stlite-version)
DEFAULT_STLITE_VERSION = "0.80.0"


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


def build(repo_root: Path, out_dir: Path, stlite_version: str) -> Path:
    template_path = repo_root / "deploy" / "template.html"
    template = template_path.read_text(encoding="utf-8")

    files = _collect_files(repo_root)
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
    print(f"Wrote {out_html} ({len(files)} files, {size_kb:.1f} KB)")
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
