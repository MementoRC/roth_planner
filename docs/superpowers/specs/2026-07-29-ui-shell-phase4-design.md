# UI Shell Phase 4 — Data-Completeness Rollout + Option Exercise Planner Shell

## Context

Phase 1 (PR #399) built a swappable shell/theme system (Classic/Domains/Hub/Contextual/Wizard)
for the Setup domain, to fix scattered/duplicated input fields. Phase 2 (PRs #400, #401) added
an always-on `compute_data_completeness` validator (Setup, field-presence based) plus the Wizard
shell. Phase 3 (PR #402) piloted the pattern on one non-Setup page, YTD Income, adding a
staleness-based sibling validator (`compute_ytd_completeness`) and a bespoke 2-tab Domains
layout — proving the *pattern* (theme-aware `render(hh, theme)` + a page-owned completeness
badge) generalizes, without reusing Setup's shell modules directly (`views/shells/*` remain
Setup-specific, importing `views.setup._partials`/`SETUP_STEP_GROUPS`).

The Phase 3 design spec explicitly deferred the question of extending further: "a follow-up
decision, informed by this pilot's outcome, is needed before going further." The owner has now
made that decision: roll out to all 11 remaining pages.

## Survey findings that shaped this design

A factual survey of all 11 remaining pages (Dashboard, Conversion Planner, Option Exercise
Planner, Exercise Auto-Optimizer, Sweet Spot Finder, RMD Squeeze Analyzer, Scenario Comparator,
ACA+IRMAA Explorer, Asset Location Optimizer, Roth Eligibility, Portfolio) found that **only one
page — Option Exercise Planner — has the scattered, persisted-input problem that motivated the
shell system in the first place.** It writes real `Household` state (`hh.exercise_schedule`) via
per-grant/per-year price and share grids. Every other page is output-heavy (charts, tables,
computed projections); their few widgets are local what-if scratch values that never touch
`Household` (e.g. `net_inv_income`, scenario sliders). Conversion Planner has an editable grid,
but it's explicitly session-only scratch state, never persisted to `hh`. Roth Eligibility already
lost its raw inputs to a read-only Command-Center-redirect in a past change (Command Center W1).

This changes Phase 4's shape from "extend the shell system to 11 pages" to a much smaller,
evidence-driven scope.

## Goals

1. Give every remaining page a consistent, low-cost way to surface "is my underlying household
   data complete?" — reusing the existing Setup validator, not inventing new engine logic.
2. Give Option Exercise Planner — the one page with a genuine scattered-input problem — the same
   validator + shell treatment YTD Income received in Phase 3.

## Non-goals

- Building Hub/Contextual/Wizard-equivalent layouts for Option Exercise Planner. YTD Income only
  got a Domains layout in Phase 3; there's no evidence this page needs more than one alternate
  layout either.
- Multi-shell (Domains/Hub/Contextual) layouts for any of the other 10 pages — they don't have
  the input-scatter problem those shells solve.
- Generalizing `views/shells/*` into a page-agnostic shell framework. Each page-specific shell
  (YTD's, and now Option Exercise Planner's) stays a bespoke dispatcher in its own package,
  matching Phase 3's precedent rather than forcing premature abstraction.
- Changing Conversion Planner's session-only grid into persisted `Household` state, or giving it
  a shell — out of scope; a separate future decision if ever pursued (Approach C, not chosen).

## Architecture

Two independent tracks:

**Track 1 — Universal completeness badge (9 pages + 1 refactor).** A new shared helper
`render_completeness_badge(hh: Household) -> None` in `views/_shared.py` (which already holds
cross-page UI primitives like `render_canonical_field`), wrapping the existing
`compute_data_completeness` and rendering the same caption-only shape (percent-complete, or an
issue-count caption) Dashboard's current bespoke badge already uses — no button, matching
Dashboard's actual current behavior exactly. Dashboard's badge is refactored to call this
helper (dedup, zero behavior change), then the same one-line call is wired into: Conversion
Planner, Sweet Spot Finder, RMD Squeeze Analyzer, Scenario Comparator, ACA+IRMAA Explorer, Asset
Location Optimizer, Roth Eligibility, Portfolio, Exercise Auto-Optimizer. No new engine code.

**Track 2 — Option Exercise Planner shell.** Follows Phase 3's YTD precedent exactly:
package-ify `views/option_exercise.py` into `views/option_exercise/__init__.py` + `_partials/`,
add a new sibling validator `compute_exercise_completeness` in `engine/data_status.py`, and a
`render(hh, theme)` dispatcher supporting Classic (unchanged) + a new Domains 2-tab layout,
reusing the existing global `ui_theme` sidebar control. This page gets its own page-specific
badge (not the Track 1 shared one), matching YTD's precedent of a page-owned validator over its
own state rather than the generic Setup one.

## Components & data flow

`ExerciseSchedule` (`models/exercise_schedule.py`) has no timestamp field at all — a YTD-style
staleness check is not possible. It does have a natural presence/allocation signal already used
elsewhere: `remaining(grant) = grant.shares - total_exercised(grant.key())`
(`engine/exercise_grid.py`'s `remaining_by_key` powers the view's existing "Remaining" column).
`Household.exercise_schedule` is `None` until the user actively edits the page, falling back via
`effective_schedule()` to `default_at_expiry()`, which is always 100%-allocated by construction.

So `compute_exercise_completeness(hh: Household) -> DataCompleteness` follows Setup's
**presence/allocation style**, not YTD's staleness style:

- If the household has zero non-expired `StockGrant`s: `ok` unconditionally (nothing to plan —
  see Error handling).
- Else if `hh.exercise_schedule is None`: one `DataStatusItem(severity="missing", ...)` — "No
  exercise plan confirmed — using default hold-to-expiry allocation."
- Else, for each non-expired `StockGrant` with `remaining(grant) > 0`: one
  `DataStatusItem(severity="missing", field=grant.key(), ...)` per grant — reuses the existing
  `"missing"` severity (`DataStatusItem.severity` is plain `str`; the established vocabulary is
  `"missing"` / `"stale"` / `"conflict"` — no new value is introduced).
- Otherwise: `ok`.

Data flow: `views/option_exercise/__init__.py`'s `render(hh, theme)` computes this once per
render, shows the badge, then dispatches Classic vs. Domains exactly like YTD's
`_render_classic`/`_render_domains` split. Domains splits into "Edit Allocation" (grid + price
inputs) / "Review Impact" (Remaining/mirror tables + projection) tabs.

## Error handling & edge cases

- **Expired-grant skip**: a grant already past its `expiry_year` can no longer be exercised, so
  flagging its unallocated shares as actionable would be misleading. `compute_exercise_completeness`
  mirrors `default_at_expiry`'s own skip condition — only non-expired grants count toward
  `remaining(grant) > 0` checks.
- **No-grants guard**: a household with zero non-expired `StockGrant`s returns `ok`
  unconditionally, preventing a permanent false-positive badge for households without options.
- **Track 1 reuses `compute_data_completeness` as-is** — no new edge cases beyond what
  Setup/Dashboard already handle; this is pure wiring.

## Testing

- New `TestComputeExerciseCompleteness` class in `tests/test_data_status.py` (same file as the
  other two validators — not `test_engine.py`, per this repo's established convention): no-grants
  → ok, no-schedule (`exercise_schedule is None`) → missing, empty-but-not-None schedule
  (`exercise_schedule.is_empty()`, falls through to per-grant check since `effective_schedule()`
  treats both as "no plan") → missing per grant, partially-allocated → missing, expired-grant →
  skipped, fully-allocated → ok.
- Track 1 needs no new engine tests (pure wiring); a full-suite run after each page's one-line
  addition is the verification gate.
- Track 2's Domains layout gets `tests/test_option_exercise_shell.py`, mirroring
  `tests/test_ytd_shell.py`'s `AppTest.from_function` pattern: badge visibility across
  missing/incomplete/ok states, two-tab presence, and a key-stability check across theme
  switches.

## Rollout / batching

Two PRs, split along the track boundary (risk/complexity, not page count):

- **Wave 1 — Badge rollout (Track 1), one PR.** Extract `render_completeness_badge`, refactor
  Dashboard, wire into the other 9 pages. Low risk, near-identical diff repeated 9 times — one PR
  keeps review simple without becoming "one big plan."
- **Wave 2 — Option Exercise Planner shell (Track 2), one PR, structured as a multi-task TDD
  plan** mirroring Phase 3's YTD plan shape: validator (TDD first), package conversion, partial
  extraction, badge + dispatcher wiring, Domains layout, AppTest coverage.

## Open questions for the implementation plan

- Exact partial boundaries within `views/option_exercise.py` (which lines become which
  `_partials/` module) — to be determined by re-reading the current file at plan-writing time,
  same discipline Phase 3's plan used for YTD Income.
- Whether Wave 1's 9-page wiring is one flat task list or grouped sub-tasks — a plan-writing
  concern, not a design one.
