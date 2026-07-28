# UI Shell Phase 3 — YTD Income Pilot (Validator Extension + Domains Layout)

**Date**: 2026-07-27
**Status**: Design approved by owner, pending spec review
**Predecessor work**: [[ui_shell_theme_toggle]] — Phase 1 (PR #399, Setup shells), Phase 2 (PR #400 validator, PR #401 Wizard)

## Background

Phase 1 built four swappable layout "shells" (Domains/Hub/Contextual/Wizard, plus the
original Classic) over the Setup domain's composable field partials
(`views/setup/_partials/`), selected live via a `ui_theme` sidebar control in `app.py`.
Phase 2 added an always-on `DataCompleteness` validator (`engine/data_status.py`)
consumed by a Dashboard badge, plus a Wizard shell sequencing Setup's 5 steps.

Phase 2's own retrospective note flagged one thing not to lose: the validator should be
usable by **any** shell (or no shell at all), not gated behind Wizard-only logic. The
Dashboard badge already proves this — it's a ~20-line addition directly inside
`views/dashboard.py`, independent of shell choice.

The deferred "Phase 3" scope was to extend shells + the validator to the other 12 pages
"if Phase 2 proves the mechanism out." That judgment call had not been made. This spec
scopes a single pilot page — **YTD Income & Headroom** (`views/ytd_income.py`) — chosen
by the owner, to test whether the pattern actually transfers before committing further.

## Investigation findings that shaped this design

A structural survey of `views/ytd_income.py` (1077 lines) found it does **not** resemble
Setup's structure:

- Setup had 5 discrete, independently-completable governed-field groups. YTD's input
  side is really 2 chunks: a single entangled FinExtract-sync/PDF-scan workflow
  (~375 lines, side-effecting) and one flat 13-field manual-entry form (~135 lines).
- ~40% of the page (headroom/tax-bracket/IRMAA visualizations, ~415 lines) is pure
  output with no analog in Setup's all-input partials.
- `YTDSnapshot` (the data model backing this page) lives in `st.session_state`, not as
  `Household` attributes, and has list-typed fields (`income_events`, `gain_events`).
  It carries **no** provenance/candidate/trust-until-confirm seam the way Setup's
  Command Center governance does.
- Given this, forcing all 4 Setup shell variants onto YTD would mostly just relocate
  chart blocks around one input blob — not offer meaningfully different data-entry
  ergonomics. This was surfaced to the owner, who chose to scope down.

For the validator: Setup's `compute_data_completeness` flags missing/conflicted/stale
*scalar* `Household` fields via candidate/provenance comparison. YTD's manual-entry
fields are numeric and **0 is a legitimate value** (most fields legitimately start at 0
in January) — an "is it populated" heuristic would false-positive constantly. YTD does
have one relevant timestamp already: `YTDSnapshot.snapshot_date: str` ("ISO date of
last update"). The owner chose **staleness-only** as the completeness signal, keyed off
this existing field — no per-field timestamps, no new persisted schema.

## Goals

1. Prove (or disprove) that the shell/theme pattern generalizes beyond Setup, on one
   real page, without over-building.
2. Fix the reuse gap Phase 2 flagged for itself: make the validator's badge pattern
   usable on a page whose data isn't `Household`-attribute-backed.
3. Ship one new layout for YTD that's actually motivated by the page's real structure
   (input-workflow vs. output-review), not a forced copy of Setup's 4 variants.

## Non-goals

- Building Hub, Contextual, or Wizard variants for YTD (survey found no structural fit;
  Contextual's distinguishing feature — a status bar — is being made shell-agnostic
  here instead of shell-exclusive).
- Extending shells or the validator to any of the other 11 pages — this is a single-page
  pilot; a follow-up decision (informed by this pilot's outcome) is needed before
  going further.
- Any change to tax/engine computation logic. This is view-layer plus one new small,
  pure engine function.
- Per-field staleness/provenance tracking for `YTDSnapshot`. Snapshot-level only.

## Architecture

Two decoupled additions:

1. **Validator**: `compute_ytd_completeness(snapshot, *, now)` in
   `engine/data_status.py`, a sibling to `compute_data_completeness` (not a
   generalization of it — the Household/candidate machinery doesn't apply here and
   shouldn't be stretched to pretend it does). Reuses the existing `DataStatusItem`
   and `DataCompleteness` dataclasses so downstream badge-rendering code is
   structurally identical to Dashboard's.
2. **Domains layout for YTD**: `views/ytd_income.py` becomes a package
   (`views/ytd_income/`), mirroring Setup's own `_partials/` split. Its `render(hh)`
   is a thin dispatcher reading the *existing* global `st.session_state["ui_theme"]`
   (no new UI control). `"Domains"` → two tabs; every other theme value → today's flat
   layout, byte-for-byte unchanged in behavior.

## Components

- `engine/data_status.py`:
  - `+ YTD_STALE_AFTER_DAYS: int = 14` (module constant)
  - `+ compute_ytd_completeness(snapshot: YTDSnapshot, *, now: datetime) -> DataCompleteness`
    — single check: `snapshot.snapshot_date` empty or older than the threshold ⇒ one
    `DataStatusItem` (severity `stale`, or `missing` if `snapshot_date` is empty);
    otherwise zero issues.
- `views/ytd_income/__init__.py` (new, replaces flat `views/ytd_income.py`):
  - `render(hh)`: renders the completeness badge unconditionally (same ~20-line pattern
    as `views/dashboard.py`'s existing badge integration), then branches on
    `st.session_state.get("ui_theme")`: `"Domains"` → `_render_domains(hh)`, else →
    `_render_classic(hh)`.
  - `_render_classic(hh)`: today's section order, composed from the extracted partials
    below (no behavior change from current `views/ytd_income.py`).
  - `_render_domains(hh)`: two `st.tabs`: **"Update Your Data"** (sync/scan + manual
    entry + event log partials) and **"Review Headroom"** (analysis partial).
- `views/ytd_income/_partials/_sync_scan.py` — FinExtract sync + PDF scan + owner
  resolution, extracted verbatim (~375 ln).
- `views/ytd_income/_partials/_manual_entry.py` — 13-field manual entry form, extracted
  verbatim (~135 ln).
- `views/ytd_income/_partials/_event_log.py` — Roth conversion/distribution event log
  form + dataframe + delete, extracted verbatim (~85 ln).
- `views/ytd_income/_partials/_analysis.py` — all output sections (gain-events
  drill-down, headroom summary, dividend/interest impact, NQO breakdown, capital gains
  breakdown, tax bracket position, room-for-conversions, IRMAA impact/tier table),
  extracted verbatim as one block (~415 ln) — not split further, since both layouts
  show identical output content, just in a different tab/position.
- The Conversion-Planner integration toggle (~20 ln) stays part of whichever partial it
  currently sits adjacent to; it is not treated as a governance-relevant field.

No new `views/shells/` file. A binary flat-vs-tabs choice doesn't warrant the dispatch
abstraction Setup needed for 4+ variants.

## Data flow

1. `app.py`'s existing `ui_theme` sidebar `selectbox` (session-local, `key="ui_theme"`)
   is unchanged — no new control added. YTD's `render(hh)` simply reads the same
   session-state key Setup's shells already read.
2. Badge: `compute_ytd_completeness(st.session_state["ytd_snapshot"], now=datetime.now())`
   is called at the top of `render(hh)` regardless of theme value, then rendered as a
   single-line caption if `not completeness.is_complete` — mirroring Dashboard's
   integration shape exactly.
3. No engine/tax computation changes anywhere in this pilot.

## Error handling / edge cases

- Empty/never-saved `snapshot_date` (e.g., a fresh household that hasn't touched this
  page yet) is treated as `missing`, not a crash on date parsing.
- All datetime comparisons use naive `datetime.now()`, consistent with this project's
  existing provenance-timestamp convention (see project memory — no tz-aware code paths
  introduced).
- Partial extraction must preserve exact Streamlit widget `key=` values used today, so
  switching `ui_theme` between Classic and Domains mid-session does not lose any
  in-progress entered values (same discipline Phase 1 applied to Setup's partials).

## Testing

- `tests/test_engine.py`: new unit tests for `compute_ytd_completeness` — fresh/empty
  snapshot → `missing`; snapshot updated today → `ok`; snapshot at/just past the
  14-day threshold → `stale` (boundary case).
- Streamlit `AppTest`-based live check (same tool Phase 1/2 used) confirming: the badge
  renders in both Classic and Domains modes; the Domains tab split preserves every
  field and its behavior; switching `ui_theme` mid-session does not drop entered data.
- No existing golden/scenario test should shift — no engine/tax logic changes.

## Rollout / decision point after this pilot

This spec covers the YTD pilot only. Once shipped, the owner should re-evaluate
against real usage before deciding whether/how to extend either the validator pattern
or shell layouts to any of the other 11 pages — no commitment to further pages is made
here.
