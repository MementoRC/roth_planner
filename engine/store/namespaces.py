"""Every store that lives in the database: its namespace and legacy JSON file.

``engine.store.get_store()`` imports this module before the one-time JSON
import. That way the registry is complete whichever store module the app
imported first. Store modules import their namespace constant from here.
"""

from __future__ import annotations

from engine.store.legacy_files import declare_store

PORTFOLIO = declare_store("portfolio", ".portfolio_cache.json")
YTD = declare_store("ytd", ".ytd_cache.json")
