"""Generic synthetic defaults for public/demo use.

Personal overrides live in `.user_defaults.py` (gitignored) — see
`config.loader.load_defaults` for the resolution order.

Do NOT add household-specific values here. Keep this file safe to
make public.
"""

from models.grants import StockGrant

DEFAULTS: dict = {
    # Demographics — generic mid-career couple
    "your_age": 55,
    "spouse_age": 53,
    "your_has_workplace_plan": True,
    "spouse_has_workplace_plan": False,
    "your_ira": 500_000,
    "spouse_ira": 500_000,
    "your_roth": 0,
    "spouse_roth": 0,
    "your_ss_fra": 2_500,
    "spouse_ss_fra": 2_500,
    "living_expenses": 60_000,
    # Employer / equity comp — fictional Acme Corp
    "employer_name": "Acme Corp",
    "stock_ticker": "ACME",
    "stock_price_now": 100,
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
