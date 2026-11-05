"""Checksum gate for the human-owned acceptance specs under tests/acceptance/.

`record` writes tests/acceptance.sha256 (sorted "<sha256>  <relative path>" lines).
`verify` recomputes and exits 1, listing added/removed/changed files, on any difference.
The manifest lives outside tests/acceptance so it is not part of the hash.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "tests" / "acceptance"
MANIFEST = ROOT / "tests" / "acceptance.sha256"


def compute(spec_dir: Path) -> dict[str, str]:
    """Map relative posix path -> sha256 for every spec file (no caches)."""
    result: dict[str, str] = {}
    for path in sorted(spec_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        result[path.relative_to(spec_dir).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return result


def record(spec_dir: Path = SPEC_DIR, manifest: Path = MANIFEST) -> int:
    digests = compute(spec_dir)
    manifest.write_text("".join(f"{d}  {p}\n" for p, d in sorted(digests.items())))
    print(f"recorded {len(digests)} files to {manifest}")
    return 0


def _load(manifest: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in manifest.read_text().splitlines():
        if line.strip():
            digest, _, rel = line.partition("  ")
            entries[rel] = digest
    return entries


def verify(spec_dir: Path = SPEC_DIR, manifest: Path = MANIFEST) -> int:
    if not manifest.is_file():
        print(f"manifest missing: {manifest}; run the acceptance-record task", file=sys.stderr)
        return 1
    expected, actual = _load(manifest), compute(spec_dir)
    added = sorted(actual.keys() - expected.keys())
    removed = sorted(expected.keys() - actual.keys())
    changed = sorted(p for p in actual.keys() & expected.keys() if actual[p] != expected[p])
    if not (added or removed or changed):
        print("acceptance specs match manifest")
        return 0
    for label, files in (("added", added), ("removed", removed), ("changed", changed)):
        for rel in files:
            print(f"{label}: {rel}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("record", "verify"):
        print("usage: acceptance_checksum.py record|verify", file=sys.stderr)
        return 2
    return record() if argv[1] == "record" else verify()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
