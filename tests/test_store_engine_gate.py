"""Spec §1 gate: no store path parameter and no dotfile JSON path constant in engine/.

Parsed with ``ast`` (nothing is imported), so every engine module is checked.
"""

from __future__ import annotations

import ast
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent / "engine"

# Stores that stay files (spec, owner decisions). config/loader.py
# (.user_defaults.json) is outside engine/ and never scanned.
ALLOWLIST = frozenset({"dividend_forecast.py", "demo_mode.py", "data_bridge_keys.py"})
_STORE_PARAMS = frozenset({"store_path", "cache_path"})


def store_path_params(source: str) -> list[str]:
    """``function(param)`` for every parameter that names a store file."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                if arg.arg in _STORE_PARAMS or arg.arg.endswith("_path"):
                    found.append(f"{node.name}({arg.arg})")
    return found


def _is_dotfile_json(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(".")
        and node.value.endswith(".json")
    )


def dotfile_path_constants(source: str) -> list[str]:
    """Module-level ``*_PATH`` names assigned from a ``".<name>.json"`` literal."""
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        names = [t.id for t in targets if isinstance(t, ast.Name) and t.id.endswith("_PATH")]
        if names and any(_is_dotfile_json(n) for n in ast.walk(value)):
            found.extend(names)
    return found


def _engine_sources() -> dict[str, str]:
    sources = {}
    for path in sorted(ENGINE.rglob("*.py")):
        name = path.relative_to(ENGINE).as_posix()
        if name not in ALLOWLIST:
            sources[name] = path.read_text()
    return sources


def test_the_scanners_flag_store_paths_and_dotfile_constants():
    source = (
        "from pathlib import Path\n"
        '_X_PATH = Path(__file__).parent / ".x_cache.json"\n'
        'MARKER_PATH = Path(".marker")\n'
        "def load(store_path, *, cache_path=None, folder=None): ...\n"
        "def save(data, out_path: Path): ...\n"
    )
    assert dotfile_path_constants(source) == ["_X_PATH"]
    assert store_path_params(source) == ["load(store_path)", "load(cache_path)", "save(out_path)"]


def test_the_scan_sees_the_whole_engine():
    sources = _engine_sources()
    assert {"data_sources/committed.py", "store/core.py", "pdf_owner.py"} <= set(sources)
    assert not ALLOWLIST & set(sources)


def test_no_engine_function_takes_a_store_path_parameter():
    offenders = [
        f"{name}: {hit}"
        for name, src in _engine_sources().items()
        for hit in store_path_params(src)
    ]
    assert offenders == []


def test_no_engine_module_keeps_a_dotfile_json_path_constant():
    offenders = [
        f"{name}: {hit}"
        for name, src in _engine_sources().items()
        for hit in dotfile_path_constants(src)
    ]
    assert offenders == []
