# 🎯 Roth Conversion Planner

A Streamlit-based tool for planning multi-year Roth IRA conversions with full tax modeling.

## Features

- **Dashboard**: IRA trajectory comparison, cumulative tax savings, net benefit over time
- **Conversion Planner**: Interactive 20-year grid with per-year conversion inputs
- Full federal tax engine (TCJA/OBBBA permanent brackets)
- Social Security taxation modeling (provisional income test)
- RMD calculations (SECURE 2.0, age 75)
- IRMAA surcharge calculator with 2-year lookback
- ACA subsidy impact for pre-Medicare years (61-64)
- QCD (Qualified Charitable Distribution) modeling
- TXN stock option exercise scheduling
- Brokerage tax drag calculation

## Quick Start

```bash
# 1. Install pixi (if not already installed)
curl -fsSL https://pixi.sh/install.sh | bash

# 2. Install dependencies
pixi install -e dev

# 3. Run the app
pixi run -e dev app

# 4. Run tests
pixi run -e dev test

# 5. Run quality checks
pixi run -e dev quality
```

## Project Structure

```
roth_planner/
├── app.py                    # Streamlit entry point
├── pixi.toml                 # Pixi project & dependency config
├── pyproject.toml             # Python tooling config
├── models/
│   └── household.py          # Household data model (ages, IRAs, SS, grants)
├── engine/
│   ├── tax.py                # Federal brackets, SS taxation, deductions
│   ├── ira.py                # IRA projection, RMD calculator, SS benefits
│   ├── irmaa.py              # Medicare surcharge tiers + lookback
│   ├── aca.py                # ACA marketplace subsidy calculator
│   └── scenario.py           # Full multi-year projection engine
├── pages/
│   ├── dashboard.py          # IRA trajectory + net benefit overview
│   └── planner.py            # Interactive 20-year conversion grid
└── tests/
    └── test_engine.py        # 43 tests against verified spreadsheet numbers
```

## Your Numbers (Pre-Configured)

| Item | You | Spouse |
|------|-----|--------|
| Age (2026) | 61 | 55 |
| Trad IRA | $1,700,000 | $1,700,000 |
| SS at FRA 67 | $3,800/mo | $3,800/mo |
| SS at 70 (delayed) | $56,544/yr | $56,544/yr |
| RMD starts | Age 75 | Age 75 |
| Conv window | 14 years (61-74) | 15 years (60-74) |

Growth: 7% · Living expenses: $30,000/yr · Filing: MFJ (Texas, no state tax)

TXN grants: 2019 ($104.41 × 650), 2020 ($130.52 × 763), 2021 ($169.23 × 450)

## Key Concepts

**The Problem**: Without conversions, your combined IRAs grow to ~$11M by age 75.
RMDs force $178K+ withdrawals in the first year alone, pushing you into 22-24% brackets.
By age 85, combined RMDs exceed $840K/year at 35%+ tax rates.

**The Solution**: Convert IRA → Roth during the gap years (61-74) at 10-12% tax rates.
Every dollar converted at 12% saves 12-25% when it would have come out as RMDs later.

**The Squeeze**: After you hit 75, your RMDs + SS fill the bracket before your spouse
can convert. QCDs (charitable distributions) reduce taxable RMDs to free bracket room.

## Disclaimer

This tool is for educational planning purposes only. It is not tax, legal, or financial
advice. Consult a CPA or tax professional before executing any Roth conversion strategy.
Tax laws, brackets, and thresholds may change.
