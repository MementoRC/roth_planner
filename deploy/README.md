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
- **stlite version** — pin via `--stlite-version <N>` in the workflow if upstream breaks compat. Default: `0.76.0`.
