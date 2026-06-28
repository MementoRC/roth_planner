"""Tax return snapshot fetch/parse/cache (1040 + Schedule 1)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

if TYPE_CHECKING:
    pass

from engine.secure_io import write_pii_json

from .client import BASE_URL, _flatten_query_rows, _headers
from .shapes import TaxReturnSnapshot


def _parse_tax_rows(
    rows: list[dict[str, Any]],
    year_key: str,
) -> dict[str, float]:
    """Extract amounts from tax return rows for current or prior year."""
    result: dict[str, float] = {}
    for row in rows:
        label = row.get("form_label", "")
        amount = row.get(year_key) or 0
        if not amount:
            continue
        label_lower = label.lower()
        if "wages" in label_lower or "w-2" in label_lower:
            result["wages"] = result.get("wages", 0) + amount
        elif "1099-nec" in label_lower:
            result["nec_income"] = result.get("nec_income", 0) + amount
        elif "investment" in label_lower or "savings" in label_lower:
            result["investment_income"] = result.get("investment_income", 0) + amount
        elif "1099-r" in label_lower or "pension" in label_lower:
            result["ira_distributions"] = result.get("ira_distributions", 0) + amount
        elif ("1099-sa" in label_lower or "hsa" in label_lower) and "contribution" not in label_lower:
            result["hsa_distributions"] = result.get("hsa_distributions", 0) + amount
        elif "miscellaneous" in label_lower or "1099-a" in label_lower or "1099-c" in label_lower:
            result["misc_income"] = result.get("misc_income", 0) + amount
        # Deduction rows
        elif "hsa" in label_lower and "contribution" in label_lower:
            result["hsa_contributions"] = result.get("hsa_contributions", 0) + amount
        elif "ira contribution" in label_lower:
            result["ira_contributions"] = result.get("ira_contributions", 0) + amount
        elif "sales tax" in label_lower:
            result["sales_tax"] = result.get("sales_tax", 0) + amount
        elif "foreign tax" in label_lower:
            result["foreign_tax_credit"] = result.get("foreign_tax_credit", 0) + amount
    return result


def fetch_tax_return() -> TaxReturnSnapshot:
    """Fetch TurboTax income and deduction data from FinExtract."""
    snap = TaxReturnSnapshot()

    try:
        resp = requests.get(f"{BASE_URL}/status", headers=_headers(), timeout=3)
        resp.raise_for_status()
        snap.server_available = True
    except requests.RequestException as e:
        snap.error = str(e)
        return snap

    # Fetch income rows
    income_rows: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            f"{BASE_URL}/query/tax_return",
            params={"data_type": "income"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        income_rows = _flatten_query_rows(resp.json())
    except (requests.RequestException, ValueError):
        pass

    # Fetch deduction rows
    deduction_rows: list[dict[str, Any]] = []
    try:
        resp = requests.get(
            f"{BASE_URL}/query/tax_return",
            params={"data_type": "deductions"},
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        deduction_rows = _flatten_query_rows(resp.json())
    except (requests.RequestException, ValueError):
        pass

    # Parse current year amounts from both income and deduction rows
    all_rows = income_rows + deduction_rows
    parsed = _parse_tax_rows(all_rows, "amount_current")

    snap.wages = parsed.get("wages", 0)
    snap.nec_income = parsed.get("nec_income", 0)
    snap.investment_income = parsed.get("investment_income", 0)
    snap.ira_distributions = parsed.get("ira_distributions", 0)
    snap.hsa_distributions = parsed.get("hsa_distributions", 0)
    snap.misc_income = parsed.get("misc_income", 0)
    snap.hsa_contributions = parsed.get("hsa_contributions", 0)
    snap.ira_contributions = parsed.get("ira_contributions", 0)
    snap.sales_tax = parsed.get("sales_tax", 0)
    snap.foreign_tax_credit = parsed.get("foreign_tax_credit", 0)

    return snap


_TAX_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / ".tax_return_cache.json"


def save_tax_snapshot(snap: TaxReturnSnapshot) -> None:
    """Save tax return snapshot to disk as JSON."""
    write_pii_json(_TAX_CACHE_PATH, asdict(snap))


def load_tax_snapshot() -> TaxReturnSnapshot | None:
    """Load cached tax return snapshot from disk, or None if not available."""
    if not _TAX_CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_TAX_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return TaxReturnSnapshot(**data)
