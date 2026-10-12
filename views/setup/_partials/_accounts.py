"""Accounts (IRA/Roth/SS-FRA) Setup-domain partial (originally Task 4 of the
ui-shell-theme-toggle plan).

Split out of the original flat ``views/setup/_partials.py`` when that module
grew to ~980 lines (pure mechanical reorganization, no behavior change).

Per-field sourced-value governance cards do NOT render here — they render
exclusively in ``views/setup/command_center.py``'s generic per-pending-field
loop (one owner only, to avoid ``DuplicateWidgetID`` from ``st.tabs()``
executing every tab body every run; see that module's docstring).
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from config.scalar_registry import scalar_default
from engine.data_sources.record import record_ss_fra_candidate
from engine.portfolio_sync import fetch_ssa_snapshot, match_fra_estimate, save_ssa_snapshot
from engine.ssa_statement_pdf import benefit_comparison, load_ssa_pdf_estimates
from engine.upload_merge import synced_account_sums
from models.household import Household
from models.sourced import Source
from views._format import fmt_dollars, md_dollars, ssa_comparison_table

from ._clamp import clamp as _clamp


def _render_ssa_pdf_comparison(container, owner: str, *, fra_monthly: float, fra_age: int) -> None:
    """Show the last applied SSA statement's per-age estimates next to what
    the planner's formula derives from its own FRA amount (*fra_monthly* at
    *fra_age*). Display only -- the model uses the FRA amount. Renders
    nothing when no statement has been applied for *owner* ('you'/'spouse').
    """
    saved = load_ssa_pdf_estimates(owner)
    if saved is None:
        return
    with container.expander(f"SSA statement estimates (dated {saved.statement_date})") as box:
        box.caption(
            "SSA's figures assume continued earnings until you claim; the planner "
            "derives each claim age from the FRA amount above."
        )
        box.dataframe(
            ssa_comparison_table(
                benefit_comparison(saved.estimates, fra_monthly=fra_monthly, fra_age=fra_age)
            ),
            hide_index=True,
        )


def _sync_ssa_for(owner: str, fra_age: int) -> str | None:
    """Fetch, match, and record the FRA SSA benefit for *owner* ('you' or 'spouse').

    Records the matched monthly benefit as a FINEXTRACT_LIVE candidate
    (engine.data_sources.record.record_ss_fra_candidate) instead of writing
    directly to session_state — the value sits pending until confirmed via
    the Setup / Command Center review gate (same freeze-until-confirm seam
    as your_ira/your_roth/stock_price_now). Also caches the raw snapshot.
    Returns a warning message on failure/no-match, or None on success.
    """
    snap = fetch_ssa_snapshot()
    if snap.error:
        return f"SSA sync failed: {snap.error}"
    match = match_fra_estimate(snap.estimates, fra_age)
    if match is None:
        return "No SSA benefit estimate found near the configured FRA age; sync skipped."
    field_key = "your_ss_fra" if owner == "you" else "spouse_ss_fra"
    record_ss_fra_candidate(
        field_key, match.monthly_amount, Source.FINEXTRACT_LIVE, "SSA statement", datetime.now()
    )
    st.session_state[f"ssa_snapshot_{owner}"] = snap
    save_ssa_snapshot(snap, owner=owner)
    return None


def render_ssa_finextract_sync(container, owner: str) -> None:
    """Render one owner's "Sync SS from FinExtract" button (*owner* is
    ``"you"`` or ``"spouse"``). Lifted out of ``render_accounts_partial`` into
    the Household page's Legacy expander (redesign PR 3b); keys and handler
    are unchanged. The spouse button is disabled for a Single filer.
    """
    if owner == "you":
        fra_age = st.session_state.get("your_fra_age", scalar_default("your_fra_age"))
        key, disabled = "_sync_ssa_you_btn", False
    elif owner == "spouse":
        fra_age = st.session_state.get("spouse_fra_age", scalar_default("spouse_fra_age"))
        key = "_sync_ssa_spouse_btn"
        disabled = (
            st.session_state.get("filing_status", scalar_default("filing_status")) == "Single"
        )
    else:
        raise ValueError(f"Unknown owner: {owner!r}")
    if container.button("Sync SS from FinExtract", key=key, disabled=disabled):
        _warning = _sync_ssa_for(owner, fra_age)
        if _warning:
            container.warning(_warning)
        else:
            st.rerun()


_TYPE_NAMES = {"trad_ira": "Trad IRA", "403b": "403(b)", "roth_ira": "Roth IRA"}


def _synced_label_help(label: str, owner: str, kind: str) -> tuple[str, str | None]:
    """Label (with " (synced)" only when the portfolio snapshot has accounts
    feeding this field) and help text naming those accounts. The field itself
    stays editable: an edit becomes a MANUAL value and a differing snapshot
    value goes to Review Changes.
    """
    snap = st.session_state.get("portfolio_snapshot")
    sums = synced_account_sums(snap, owner, kind) if snap else {}
    if not sums:
        return label, None
    parts = ", ".join(f"{_TYPE_NAMES[t]} {fmt_dollars(v)}" for t, v in sums.items())
    what = "Trad IRA + 403(b)" if kind == "pretax" else "Roth IRA"
    help_text = (
        f"Synced: {parts}. This field is the {what} total — edit it if accounts "
        "are missing; Review Changes will ask which value to trust."
    )
    return (
        f"{label} (synced)",
        md_dollars(help_text),
    )


def render_accounts_partial(hh: Household, container, owner: str) -> None:
    """Render one owner's IRA/Roth/SS-FRA accounts widgets.

    ``owner`` is ``"your"`` or ``"spouse"`` — every field here is per-person
    (unlike ``render_household_partial``, there is no ``"joint"`` case).

    ``your_ira``/``your_roth``/``your_ss_fra`` (and the spouse equivalents)
    are all in ``HOUSEHOLD_SCALAR_FIELDS`` — Command Center's governed
    sourced fields — but their trust/manual-override/confirm governance
    cards do NOT render here: they render exclusively in
    ``views/setup/command_center.py``'s generic per-pending-field loop (one
    owner only, to avoid ``DuplicateWidgetID`` — see that module's
    docstring). SS-start-age (``your_ss_start_age``/``spouse_ss_start_age``)
    is a plain scalar, not governed/sourced, but stays co-located with
    SS-FRA per the plan.

    ``value=`` for SS-start-age reads from ``hh.<attr>`` (see
    ``render_household_partial``'s docstring for the general rule) —
    ``Household.__post_init__`` never derives it, so ``hh.<attr>`` always
    equals the live ``session_state.<attr>``. The 6 governed IRA/Roth/SS-FRA
    fields are the DOCUMENTED EXCEPTION to that rule: by the time this
    partial runs, ``hh`` is ``app.py get_household()``'s POST-RESOLVE
    household (``app_res.result.household``), whose governed fields are
    ``SourcedValue`` (a ``float`` subclass carrying provenance) rather than
    a plain ``float``/``int`` — Streamlit's ``number_input`` does an exact
    ``type(value) in (int, float)`` check, so passing ``hh.your_ira``
    directly raises ``StreamlitMixedNumericTypesError`` even though it's
    numerically a float. ``get_household()`` mirrors the resolved value back
    into ``session_state`` (int-coerced) before this partial ever runs, so
    these 6 widgets read ``st.session_state.<attr>`` instead — matching
    their exact pre-Task-4 shape.
    """
    if owner == "your":
        _ira_label, _ira_help = _synced_label_help("Your Trad IRA", "you", "pretax")
        st.session_state.your_ira = container.number_input(
            _ira_label,
            min_value=0,
            value=_clamp(st.session_state.your_ira, 0),
            step=50_000,
            format="%d",
            help=_ira_help,
        )

        _roth_label, _roth_help = _synced_label_help("Your Roth IRA", "you", "roth")
        st.session_state.your_roth = container.number_input(
            _roth_label,
            min_value=0,
            value=_clamp(st.session_state.get("your_roth", 0), 0),
            step=50_000,
            format="%d",
            help=_roth_help,
        )

        _ssa_synced_you = bool(st.session_state.get("ssa_snapshot_you"))
        your_fra_age = st.session_state.get("your_fra_age", scalar_default("your_fra_age"))
        st.session_state.your_ss_fra = container.number_input(
            f"Your SS at FRA {your_fra_age} ($/mo)" + (" (synced)" if _ssa_synced_you else ""),
            min_value=0,  # UU2-UI-06
            value=_clamp(int(round(st.session_state.your_ss_fra)), 0),
            step=100,
            format="%d",
            disabled=_ssa_synced_you,
            help="Auto-synced from FinExtract (SSA benefit estimate)" if _ssa_synced_you else None,
        )
        _render_ssa_pdf_comparison(
            container, "you", fra_monthly=st.session_state.your_ss_fra, fra_age=your_fra_age
        )
        st.session_state.your_ss_start_age = container.number_input(
            "Your SS claim age",
            min_value=62,
            max_value=70,
            value=_clamp(hh.your_ss_start_age, 62, 70),
            step=1,
            format="%d",
        )
        return

    if owner == "spouse":
        _is_single = (
            st.session_state.get("filing_status", scalar_default("filing_status")) == "Single"
        )
        _ira_label, _ira_help = _synced_label_help("Spouse Trad IRA", "spouse", "pretax")
        st.session_state.spouse_ira = container.number_input(
            _ira_label,
            min_value=0,
            value=_clamp(st.session_state.spouse_ira, 0),
            step=50_000,
            format="%d",
            disabled=_is_single,
            help=_ira_help,
        )

        _roth_label, _roth_help = _synced_label_help("Spouse Roth IRA", "spouse", "roth")
        st.session_state.spouse_roth = container.number_input(
            _roth_label,
            min_value=0,
            value=_clamp(st.session_state.get("spouse_roth", 0), 0),
            step=50_000,
            format="%d",
            disabled=_is_single,
            help=_roth_help,
        )

        _ssa_synced_spouse = bool(st.session_state.get("ssa_snapshot_spouse"))
        spouse_fra_age = st.session_state.get("spouse_fra_age", scalar_default("spouse_fra_age"))
        st.session_state.spouse_ss_fra = container.number_input(
            f"Spouse SS at FRA {spouse_fra_age} ($/mo)"
            + (" (synced)" if _ssa_synced_spouse else ""),
            min_value=0,  # UU2-UI-06
            value=_clamp(int(round(st.session_state.spouse_ss_fra)), 0),
            step=100,
            format="%d",
            disabled=_is_single or _ssa_synced_spouse,
            help="Auto-synced from FinExtract (SSA benefit estimate)"
            if _ssa_synced_spouse
            else None,
        )
        if not _is_single:
            _render_ssa_pdf_comparison(
                container,
                "spouse",
                fra_monthly=st.session_state.spouse_ss_fra,
                fra_age=spouse_fra_age,
            )
        st.session_state.spouse_ss_start_age = container.number_input(
            "Spouse SS claim age",
            min_value=62,
            max_value=70,
            value=_clamp(hh.spouse_ss_start_age, 62, 70),
            step=1,
            format="%d",
            disabled=_is_single,
        )
        return

    raise ValueError(f"Unknown owner slice: {owner!r}")
