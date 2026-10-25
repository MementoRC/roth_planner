"""Every store that lives in the database: its namespace and legacy JSON file.

``engine.store.get_store()`` imports this module before the one-time JSON
import. That way the registry is complete whichever store module the app
imported first. Store modules import their namespace constant from here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.secure_io import read_pii_json
from engine.store.legacy_files import declare_store, per_entry_reader

PORTFOLIO = declare_store("portfolio", ".portfolio_cache.json")
YTD = declare_store("ytd", ".ytd_cache.json")
SSA = declare_store("ssa", ".ssa_cache.json")
TAX_PDF = declare_store("tax_pdf", ".tax_pdf_cache.json")
KOINLY = declare_store("koinly", ".koinly_cache.json")
SSA_PDF = declare_store("ssa_pdf", ".ssa_pdf_cache.json")
BROKERAGE_STATEMENTS = declare_store(
    "brokerage_statements", ".brokerage_statement_cache.json", per_entry_reader
)
STATEMENT_FOLDER = declare_store("statement_folder", ".statement_folder_config.json")
STATEMENT_ACCOUNT_OVERRIDES = declare_store(
    "statement_account_overrides", ".statement_account_overrides.json"
)

UBS_ACTIVITY = declare_store("ubs_activity", ".ubs_activity_cache.json")
EXERCISE_SCHEDULE = declare_store("exercise_schedule", ".exercise_schedule_cache.json")

# Provenance and mapping stores.
COMMITTED = declare_store("committed", ".committed_household.json")
CANDIDATE_STORE = declare_store("candidate_store", ".candidate_store.json")
TRUST_CHOICES = declare_store("trust_choices", ".trust_choices.json")

_LEDGER_SECTIONS = ("koinly", "brokerage")


def ledger_reader(path: Path) -> dict[str, Any]:
    """``{"koinly": {owner: slot}, "brokerage": {owner: {...}}}`` -> one row per owner."""
    raw = read_pii_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
    rows: dict[str, dict[str, Any]] = {}
    for section in _LEDGER_SECTIONS:
        entries = raw.get(section) or {}
        if not isinstance(entries, dict):
            raise ValueError(f"{section!r} is not a JSON object")
        for owner, payload in entries.items():
            rows.setdefault(str(owner), {})[section] = payload
    return rows


PDF_LEDGER = declare_store("pdf_ledger", ".pdf_import_ledger.json", ledger_reader)
