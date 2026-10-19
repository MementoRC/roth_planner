"""Generic synthetic defaults for public/demo use.

Personal overrides live in `.user_defaults.py` (gitignored) — see
`config.loader.load_defaults` for the resolution order.

Do NOT add household-specific values here. Keep this file safe to
make public.
"""

from config.scalar_registry import scalar_default_by_config_key as _scalar
from models.grants import StockGrant

# The 14 keys below are also in engine.upload_merge.SCALAR_KEYS and carry a
# ScalarSpec in config.scalar_registry.SCALAR_REGISTRY — sourced from there
# so the value is written once (issue #498). Values are UNCHANGED; only the
# origin moved. Do not add the other 18 SCALAR_KEYS scalars (growth_rate,
# filing_status, etc.) here — they stay registry-only, per that survey.
DEFAULTS: dict = {
    # Demographics — generic mid-career couple
    "your_age": _scalar("your_age"),
    "spouse_age": _scalar("spouse_age"),
    "your_has_workplace_plan": _scalar("your_has_workplace_plan"),
    "spouse_has_workplace_plan": _scalar("spouse_has_workplace_plan"),
    # M3 (audit-0720): default-off; when True AND spouse is >10 yrs younger,
    # RMDs use the IRS Joint & Last Survivor Table (Table II) instead of the
    # Uniform Lifetime Table (Table III). See engine/ira.py.
    "spouse_is_sole_beneficiary": _scalar("spouse_is_sole_beneficiary"),
    "your_ira": _scalar("your_ira"),
    "spouse_ira": _scalar("spouse_ira"),
    "your_roth": _scalar("your_roth"),
    "spouse_roth": _scalar("spouse_roth"),
    "your_ss_fra": _scalar("your_ss_fra"),
    "spouse_ss_fra": _scalar("spouse_ss_fra"),
    "living_expenses": _scalar("living_expenses"),
    # Employer / equity comp — fictional Acme Corp
    "employer_name": "Acme Corp",
    "stock_ticker": _scalar("stock_ticker"),
    "stock_price_now": _scalar("stock_price_now"),
    "stock_price_late": 120,
    "grants": [
        StockGrant(2020, 50.0, 1000, 2030),
        StockGrant(2021, 60.0, 500, 2031),
        StockGrant(2022, 75.0, 500, 2032),
    ],
    # Strike prices keyed by grant year (string).  Used by the JSON override
    # path and the FinExtract grant JOIN in app.py.  Mirrors the synthetic
    # grants above so the two paths stay in sync for demo use.
    "grant_strikes": {
        "2020": 50.0,
        "2021": 60.0,
        "2022": 75.0,
    },
}
