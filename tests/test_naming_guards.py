"""Guard against test names that secret scanners misread as API keys.

TruffleHog ships a detector for a payment-service API key whose pattern is a
short lowercase prefix, an underscore, and then a fixed-length run of
identifier characters. Two of the prefixes it accepts are the ones pytest
functions conventionally start with, so an ordinary descriptive test name can
match it purely by length. Worse, that detector's "verification" step treats a
candidate carrying the test-environment prefix as confirmed without contacting
anything, so the finding is reported as a *verified* secret rather than a
possible one.

CI runs the scan with fail-on-secrets enabled, so a single such name hard-blocks
the pipeline on a pure false positive, and the CI log only says a verified
result was found -- it does not print the offending string. Diagnosing it from
the log alone is close to impossible.

The scan only reads a pull request's own commit range, so an existing name is
latent until some PR happens to touch its line. That makes this exactly the kind
of trap that reappears months later in an unrelated change.

This guard fails locally and immediately instead, with the rule and the fix.
Deliberately no example of a triggering name appears in this file: the detector
matches inside comments and docstrings just as readily as in code, so quoting
one here would re-create the problem this module exists to prevent.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).parent

# Length of the identifier run the detector requires after the prefix.
TRIGGERING_SUFFIX_LENGTH = 35
PREFIX = "test_"


def _offending_names_in(path: Path) -> list[tuple[str, int]]:
    """Return (name, lineno) for every def in `path` that would trigger."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        name = node.name
        if not name.startswith(PREFIX):
            continue
        if len(name) - len(PREFIX) == TRIGGERING_SUFFIX_LENGTH:
            found.append((name, node.lineno))
    return found


def test_no_test_name_can_be_mistaken_for_a_secret() -> None:
    offenders: list[str] = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        for name, lineno in _offending_names_in(path):
            offenders.append(f"{path.relative_to(TESTS_DIR)}:{lineno}  {name}")

    assert not offenders, (
        f"{len(offenders)} test name(s) have exactly {TRIGGERING_SUFFIX_LENGTH} "
        f"characters after the '{PREFIX}' prefix.\n\n"
        "A secret scanner in CI reads names of that exact shape as a verified "
        "API key and blocks the pipeline on a false positive. The CI log will "
        "not tell you which name caused it.\n\n"
        "FIX: rename each one so the part after the prefix is any length other "
        f"than {TRIGGERING_SUFFIX_LENGTH}. Adding or removing one word is "
        "usually enough; keep the name descriptive.\n\n"
        "Do NOT record the old name in a comment or commit message -- the "
        "scanner matches those too.\n\n" + "\n".join(offenders)
    )
