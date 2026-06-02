# Deploy infrastructure

## stlite + GitHub Pages

The Roth Planner runs as a static site via [stlite](https://github.com/whitphx/stlite) — Streamlit in WebAssembly via Pyodide. No server required.

### Local preview

```bash
python deploy/build_stlite.py --out-dir _site
python -m http.server --directory _site 8000
# open http://localhost:8000
```

### Production deploy

`.github/workflows/deploy-stlite.yml` runs on every push to `development`, regenerates `index.html`, and publishes to GitHub Pages. Enable Pages in repo settings → Pages → Source: "GitHub Actions".

### Caveats

- **First load is slow** (~10-30s) — Pyodide + streamlit + pandas + plotly download once, then cache in the browser.
- **FinExtract sync fails publicly** — the planner tries `http://127.0.0.1:7890` which the browser cannot reach. Existing graceful degradation shows cached data or a "no data" message.
- **Personal data stays local** — public demo runs on synthetic `Acme Corp` defaults (config/defaults.py); your real values live in gitignored `.user_defaults.py` and never enter the deployed bundle.
- **stlite version** — pin via `--stlite-version <N>` in the workflow if upstream breaks compat. Default: `0.75.0`.

## Personal mode

The deployed demo runs on synthetic Acme Corp defaults. For your real
numbers, two paths:

### Local

Drop `.user_defaults.json` and (optionally) `.portfolio_cache.json` next
to the app. `pixi run streamlit run app.py` picks them up at startup.

### Deployed (stlite)

Use the "🔓 Use my real data" expander in the sidebar to upload both
files. Values stay in your browser session; refresh = back to demo.

### .user_defaults.json schema

```json
{
  "your_age": 61,
  "spouse_age": 55,
  "your_ss_fra": 3800,
  "spouse_ss_fra": 3800,
  "living_expenses": 30000,
  "employer_name": "Texas Instruments",
  "stock_ticker": "TXN",
  "grant_strikes": {
    "2019": 104.41,
    "2020": 130.52,
    "2021": 169.23
  }
}
```

All keys optional. Balances, outstanding grants, and TXN current price
come from `.portfolio_cache.json` or FinExtract sync — don't duplicate
them in `.user_defaults.json`.
