"""Stores on the database: registry contents and the no-path-left gate (spec §1, §4)."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import engine.store.core as store_core
from engine.store.legacy_files import registered

# namespace -> legacy file. Each PR 2 store task adds its entries; PR 3 adds the rest.
EXPECTED: dict[str, str] = {
    "portfolio": ".portfolio_cache.json",
    "ytd": ".ytd_cache.json",
}

# Modules whose store moved: none may keep a Path constant naming a legacy file.
MOVED_MODULES: list[str] = ["engine.portfolio_sync.portfolio", "engine.portfolio_sync.ytd"]

# "module:function" store functions: none may take a path parameter.
STORE_FUNCTIONS: list[str] = [
    "engine.portfolio_sync.portfolio:save_snapshot",
    "engine.portfolio_sync.portfolio:load_snapshot",
    "engine.portfolio_sync.ytd:save_ytd_snapshot",
    "engine.portfolio_sync.ytd:load_ytd_snapshot",
]

_PATH_PARAMS = {"path", "cache_path", "pdf_cache_path"}


def test_registry_lists_exactly_the_stores_moved_so_far():
    assert {entry.namespace: entry.filename for entry in registered()} == EXPECTED


def test_every_registered_store_has_a_declared_namespace():
    assert all(entry.namespace in store_core._NAMESPACES for entry in registered())


def test_moved_modules_keep_no_legacy_json_path():
    filenames = {entry.filename for entry in registered()}
    offenders = [
        f"{name}.{attr}"
        for name in MOVED_MODULES
        for attr, value in vars(importlib.import_module(name)).items()
        if isinstance(value, Path) and value.name in filenames
    ]
    assert offenders == []


def test_store_functions_take_no_path_parameter():
    offenders = []
    for ref in STORE_FUNCTIONS:
        module_name, func_name = ref.split(":")
        func = getattr(importlib.import_module(module_name), func_name)
        offenders += [
            f"{ref}({p})" for p in inspect.signature(func).parameters if p in _PATH_PARAMS
        ]
    assert offenders == []
