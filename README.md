# 🎯 Roth Conversion Planner

🚀 **[Try the live demo](https://mementorc.github.io/roth_planner/)** — runs entirely in your browser, no install needed.

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
├── engine/                   # pure computation (no Streamlit imports)
│   ├── tax.py                # Federal brackets, SS taxation, deductions
│   ├── niit.py               # Net investment income tax
│   ├── irmaa.py              # Medicare surcharge tiers + lookback
│   ├── aca.py                # ACA marketplace subsidy calculator
│   ├── ira.py                # IRA projection, RMD calculator, SS benefits
│   ├── portfolio_sync/       # FinExtract live-holdings integration (package)
│   ├── scenario.py           # Full multi-year projection engine
│   └── …                     # scenario_compute, tax_return_pdf, data_bridge_*, etc.
├── views/                    # Streamlit pages (each exports render(hh: Household))
│   ├── setup/                # Setup landing page — Parameters / Portfolio / Data Bridge tabs
│   ├── dashboard.py          # IRA trajectory + net benefit overview
│   ├── planner.py            # Interactive 20-year conversion grid
│   └── …                     # sweet_spot, rmd_squeeze, comparator, aca_irmaa, asset_location, portfolio, roth_eligibility, ytd_income
└── tests/                    # ~630 tests split into per-module test_*.py files
```

## Key Concepts

**The Problem**: Without conversions, your combined IRAs grow to ~$11M by age 75.
RMDs force $178K+ withdrawals in the first year alone, pushing you into 22-24% brackets.
By age 85, combined RMDs exceed $840K/year at 35%+ tax rates.

**The Solution**: Convert IRA → Roth during the gap years (61-74) at 10-12% tax rates.
Every dollar converted at 12% saves 12-25% when it would have come out as RMDs later.

**The Squeeze**: After you hit 75, your RMDs + SS fill the bracket before your spouse
can convert. QCDs (charitable distributions) reduce taxable RMDs to free bracket room.

## Modeling Assumptions & Known Gaps

- **Form 8606 (IRA non-deductible basis) — NOT MODELED**: Per IRC §408(d)(2), conversions
  from a Traditional IRA with non-deductible basis are pro-rated — only the pretax fraction
  is taxable. This tool assumes basis = $0 (all Trad IRA dollars are pretax). If you have
  non-deductible contributions tracked on Form 8606, actual taxable conversion income will
  be lower than reported here.

## Disclaimer

This tool is for educational planning purposes only. It is not tax, legal, or financial
advice. Consult a CPA or tax professional before executing any Roth conversion strategy.
Tax laws, brackets, and thresholds may change.
