"""Stores on the database: registry contents and the no-path-left gate (spec §1, §4)."""

from __future__ import annotations

import functools
import importlib
import inspect
import json
from pathlib import Path

import pytest

import engine.store.core as store_core
from engine.store import namespaces
from engine.store.legacy_files import registered

# namespace -> legacy file. Each PR 2 store task adds its entries; PR 3 adds the rest.
EXPECTED: dict[str, str] = {
    "portfolio": ".portfolio_cache.json",
    "ytd": ".ytd_cache.json",
    "ssa": ".ssa_cache.json",
    "tax_pdf": ".tax_pdf_cache.json",
    "koinly": ".koinly_cache.json",
    "ssa_pdf": ".ssa_pdf_cache.json",
    "brokerage_statements": ".brokerage_statement_cache.json",
    "statement_folder": ".statement_folder_config.json",
    "statement_account_overrides": ".statement_account_overrides.json",
    "pdf_ledger": ".pdf_import_ledger.json",
    "ubs_activity": ".ubs_activity_cache.json",
    "exercise_schedule": ".exercise_schedule_cache.json",
    "committed": ".committed_household.json",
    "candidate_store": ".candidate_store.json",
    "trust_choices": ".trust_choices.json",
    "instance_owner": ".instance_owner.json",
    "account_attribution": ".account_attribution.json",
}

# Modules whose store moved: none may keep a Path constant naming a legacy file.
MOVED_MODULES: list[str] = [
    "engine.portfolio_sync.portfolio",
    "engine.portfolio_sync.ytd",
    "engine.portfolio_sync.social_security",
    "engine.tax_return_pdf",
    "engine.koinly_report_pdf",
    "engine.ssa_statement_pdf",
    "engine.brokerage_statement_pdf",
    "engine.pdf_ledger",
    "engine.ubs_activity_store",
    "engine.exercise_schedule_store",
    "engine.data_sources.committed",
    "engine.data_sources.candidate_store",
    "engine.data_sources.record",
    "engine.data_sources.scan_ingest",
    "engine.data_sources.choices",
    "engine.instance_identity",
    "engine.account_attribution",
]

# "module:function" store functions: none may take a path parameter.
STORE_FUNCTIONS: list[str] = [
    "engine.portfolio_sync.portfolio:save_snapshot",
    "engine.portfolio_sync.portfolio:load_snapshot",
    "engine.portfolio_sync.ytd:save_ytd_snapshot",
    "engine.portfolio_sync.ytd:load_ytd_snapshot",
    "engine.portfolio_sync.social_security:save_ssa_snapshot",
    "engine.portfolio_sync.social_security:load_ssa_snapshot",
    "engine.tax_return_pdf:save_pdf_tax_records",
    "engine.tax_return_pdf:load_pdf_tax_records",
    "engine.data_sources.scan_ingest:scan_and_record",
    "engine.data_sources.scan_ingest:scan_documents_and_record",
    "engine.koinly_report_pdf:save_koinly_report",
    "engine.koinly_report_pdf:load_koinly_report",
    "engine.ssa_statement_pdf:save_ssa_pdf_estimates",
    "engine.ssa_statement_pdf:load_ssa_pdf_estimates",
    "engine.brokerage_statement_pdf:save_statement_records",
    "engine.brokerage_statement_pdf:load_statement_records",
    "engine.brokerage_statement_pdf:save_statement_folder_path",
    "engine.brokerage_statement_pdf:load_statement_folder_path",
    "engine.brokerage_statement_pdf:save_account_type_override",
    "engine.brokerage_statement_pdf:load_account_type_overrides",
    "engine.pdf_ledger:save_ledger",
    "engine.pdf_ledger:load_ledger",
    "engine.ubs_activity_store:save_ubs_orders",
    "engine.ubs_activity_store:load_ubs_orders",
    "engine.exercise_schedule_store:save_exercise_schedule",
    "engine.exercise_schedule_store:load_exercise_schedule",
    "engine.exercise_schedule_store:clear_exercise_schedule",
    "engine.data_sources.committed:load_committed",
    "engine.data_sources.committed:save_committed",
    "engine.data_sources.candidate_store:CandidateStore.load",
    "engine.data_sources.candidate_store:CandidateStore.save",
    "engine.data_sources.record:record_magi_candidates",
    "engine.data_sources.record:record_ss_fra_candidate",
    "engine.data_sources.record:record_ira_balance_candidate",
    "engine.data_sources.record:record_roth_balance_candidate",
    "engine.data_sources.record:record_stock_quote_candidate",
    "engine.data_sources.record:discard_pdf_balance_candidates",
    "engine.data_sources.choices:ChoiceMap.load",
    "engine.data_sources.choices:ChoiceMap.save",
    "engine.instance_identity:load_instance_owner",
    "engine.instance_identity:save_instance_owner",
    "engine.account_attribution:load_account_overrides",
    "engine.account_attribution:save_account_override",
    "engine.account_attribution:delete_account_override",
]

_PATH_PARAMS = {"path", "cache_path", "pdf_cache_path", "store_path"}


def test_registry_lists_exactly_the_stores_moved_so_far():
    assert {entry.namespace: entry.filename for entry in registered()} == EXPECTED


def test_ledger_reader_splits_the_file_per_owner(tmp_path):
    path = tmp_path / ".pdf_import_ledger.json"
    path.write_text(
        json.dumps(
            {
                "koinly": {"you": {"stcg": 1.0}},
                "brokerage": {"you": {"A1": {}}, "spouse": {"B1": {}}},
            }
        )
    )
    assert namespaces.ledger_reader(path) == {
        "you": {"koinly": {"stcg": 1.0}, "brokerage": {"A1": {}}},
        "spouse": {"brokerage": {"B1": {}}},
    }


def test_ledger_reader_accepts_a_missing_section(tmp_path):
    path = tmp_path / ".pdf_import_ledger.json"
    path.write_text(json.dumps({"koinly": {}}))
    assert namespaces.ledger_reader(path) == {}


@pytest.mark.parametrize("content", ["[1, 2]", '{"koinly": [1]}'])
def test_ledger_reader_rejects_a_non_object(tmp_path, content):
    path = tmp_path / ".pdf_import_ledger.json"
    path.write_text(content)
    with pytest.raises(ValueError, match="JSON object"):
        namespaces.ledger_reader(path)


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
        module_name, attr_path = ref.split(":")
        func = functools.reduce(getattr, attr_path.split("."), importlib.import_module(module_name))
        offenders += [
            f"{ref}({p})" for p in inspect.signature(func).parameters if p in _PATH_PARAMS
        ]
    assert offenders == []
