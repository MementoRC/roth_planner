"""Every store that lives in the database: its namespace and legacy JSON file.

``engine.store.get_store()`` imports this module before the one-time JSON
import. That way the registry is complete whichever store module the app
imported first. Store modules import their namespace constant from here.
"""

from __future__ import annotations

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
