"""Setup ▸ Command Center — the sync-everything trigger and per-field
sourced-value governance gate.

Per-field trust/manual-override/confirm governance cards render HERE, in a
generic loop over every field flagged pending review
(``st.session_state["_pending_review"]``, populated by
``engine.data_sources.resolver.resolve()`` via ``app.py``'s
``get_household()``). This is the ONE AND ONLY renderer of these cards —
the owning partials (``views/setup/_partials/_accounts.py``,
``_options.py``, ``_assumptions.py``) deliberately do NOT render them
inline, reversing the Task-4/5/7 relocation. Reason: Classic mode renders
both this tab's body AND the Parameters tab's body every script run
(``st.tabs()`` executes every branch regardless of which tab is visually
selected), so registering the same ``trust_<field>``/``manual_<field>``/
``confirm_<field>`` widget key from both this loop AND an owning partial in
the same run would raise ``DuplicateWidgetID``. See
``tests/test_setup_shell_characterization.py``'s regression test for the
specific before/after proof.

No committed value changes as a result of any module without passing
through ``engine.data_sources.confirm.confirm_field`` — that mutation path
is exercised via ``_render_field_card`` (``views/setup/_partials/_governance.py``),
called only from this module.
"""

from __future__ import annotations

import streamlit as st

from engine.account_attribution import (
    CorruptAccountAttributionError,
    delete_account_override,
    load_account_overrides,
    resolve_account_owner,
    save_account_override,
)
from engine.brokerage_statement_pdf import load_statement_records
from engine.data_sources.candidate_store import CandidateStore
from engine.data_sources.choices import ChoiceMap
from engine.data_sources.committed import CorruptCommittedCacheError, load_committed
from engine.data_sources.paths import CANDIDATE_STORE_PATH, COMMITTED_PATH, TRUST_CHOICES_PATH
from engine.instance_identity import (
    CorruptInstanceOwnerError,
    load_instance_owner,
    save_instance_owner,
)
from engine.pdf_owner import OwnerRole
from models.household import Household
from views._shared import SyncEverythingResult, sync_everything
from views.setup._partials._governance import _render_field_card


def _format_sync_everything_summary(summary: SyncEverythingResult) -> str:
    """Render a one-line "portfolio: N · SS: N · scan: N files, N errors" summary."""
    portfolio = f"portfolio: {summary.portfolio.candidates_recorded} candidates"
    if summary.portfolio.error:
        portfolio += f" (unavailable: {summary.portfolio.error})"

    ss = f"SS: {summary.ss.candidates_recorded} candidates"
    if summary.ss.warnings:
        ss += f" ({'; '.join(summary.ss.warnings)})"

    if summary.scan.result is not None:
        scan = (
            f"scan: {summary.scan.result.files_scanned} files, "
            f"{len(summary.scan.result.errors)} errors"
        )
    else:
        scan = f"scan: skipped ({summary.scan.error})"

    quote = f"quote: {summary.market_quote.candidates_recorded} candidates"
    if summary.market_quote.error:
        quote += f" (unavailable: {summary.market_quote.error})"

    return " · ".join([portfolio, ss, scan, quote])


def _render_attribution_table(instance_owner: str) -> None:
    """List statement-derived accounts with their resolved owner and tax
    status, letting the user set/clear a per-account override. FinExtract
    portfolio accounts are NOT included -- see this task's scope note."""
    by_account = load_statement_records()
    if not by_account:
        return
    overrides = load_account_overrides()
    # Collapsed by default: this is correction UI, not primary flow. Expanded,
    # nine accounts is ~350px of selectboxes wedged between Command Center's
    # owner gate and the two import sections a first-time user actually came
    # for -- it pushed both of them below the fold. The account count rides in
    # the label so the section stays discoverable while closed.
    #
    # An st.expander is not an st.column, so the st.columns([3, 2, 1]) rows
    # below are still nested exactly one level inside the shell's outer
    # column -- unchanged from before, and within Streamlit's one-level limit.
    with st.expander(f"Account attribution ({len(by_account)} accounts)", expanded=False):
        for account_number, rec in sorted(by_account.items()):
            resolved = resolve_account_owner(rec.broker, account_number, overrides, instance_owner)
            col_label, col_owner, col_clear = st.columns([3, 2, 1])
            col_label.caption(f"{account_number} ({rec.broker}, {rec.account_type})")
            choice = col_owner.selectbox(
                f"Owner for {account_number}",
                ["you", "spouse", "household"],
                index=["you", "spouse", "household"].index(resolved),
                key=f"attribution_owner_{account_number}",
                label_visibility="collapsed",
            )
            if choice != resolved:
                try:
                    save_account_override(rec.broker, account_number, choice)
                except CorruptAccountAttributionError as exc:
                    st.error(
                        f"⚠️ Account attribution store at `{exc.path}` is unreadable "
                        "(corrupt or truncated); this override was NOT saved. Restore "
                        "the file from a backup or contact support before retrying."
                    )
                else:
                    # No session-state pop needed here: on the immediate rerun,
                    # resolve_account_owner() will read back the override we just
                    # wrote, so `resolved` will equal `choice` and this keyed
                    # selectbox's already-current session-state value (`choice`,
                    # set by Streamlit when the user interacted with it) matches
                    # it exactly. Desync only arises on the Clear path below,
                    # where the override disappears but the widget key does not.
                    st.rerun()
            if (rec.broker, account_number) in overrides and col_clear.button(
                "Clear", key=f"attribution_clear_{account_number}"
            ):
                try:
                    delete_account_override(rec.broker, account_number)
                except CorruptAccountAttributionError as exc:
                    st.error(
                        f"⚠️ Account attribution store at `{exc.path}` is unreadable "
                        "(corrupt or truncated); this override was NOT cleared. Restore "
                        "the file from a backup or contact support before retrying."
                    )
                else:
                    # Streamlit only honours a keyed widget's index= kwarg on its
                    # FIRST creation; on every later rerun the persisted
                    # session_state[key] value wins over index=, even though
                    # `resolved` (computed above from the now-overrideless store)
                    # has already fallen back to instance_owner. Without this
                    # pop, the next render's `choice` stays the stale overridden
                    # value, `choice != resolved` fires again, and the override
                    # we just deleted gets immediately re-saved -- Clear becomes
                    # a no-op with two redundant disk writes. Popping forces the
                    # widget to re-derive from index=resolved on the next render.
                    st.session_state.pop(f"attribution_owner_{account_number}", None)
                    st.rerun()


def render_command_center(hh: Household) -> None:
    """Render the Setup ▸ Command Center — sync trigger + per-field governance cards.

    Per-field governance cards render here, in a generic loop over every
    pending field (see module docstring) — this tab is the "sync everything"
    action plus the sole reviewer/confirm gate for sourced-value candidates.
    """
    st.header("🎛️ Command Center")

    # Identity used to require an explicit, one-shot, unpreselected answer
    # (index=None) because the choice latched permanently once saved and the
    # gate never reappeared -- a reflexive Save on a spouse's install could
    # irrevocably misattribute their accounts to "you". That is no longer
    # true: identity now defaults to "you" on first use and stays correctable
    # below for as long as this instance exists, so the irreversibility that
    # made no-preselection necessary is gone (see
    # docs/superpowers/specs/2026-08-29-instance-identity-design.md).
    identity_corrupt = False
    try:
        instance_owner = st.session_state.get("instance_owner") or load_instance_owner()
    except CorruptInstanceOwnerError:
        instance_owner, identity_corrupt = None, True

    if instance_owner is None and not identity_corrupt:
        try:
            save_instance_owner(OwnerRole.YOU.value)
        except CorruptInstanceOwnerError:
            identity_corrupt = True
        else:
            instance_owner = OwnerRole.YOU.value
            st.session_state["instance_owner"] = instance_owner

    identity_set = bool(instance_owner)

    if identity_corrupt:
        st.error(
            "⚠️ The instance-identity file is unreadable (corrupt or truncated), "
            "so this instance's owner could not be set. Scanning and syncing "
            "stay unavailable until the file is restored or removed."
        )

    if identity_set:
        current_label = "Me" if instance_owner == OwnerRole.YOU.value else "Spouse"
        with st.expander(f"This planner instance holds: **{current_label}**'s data"):
            st.caption(
                "Controls the default owner assigned to scanned PDFs, which "
                'slot an export labels as "mine", and which person-slot an '
                "imported legacy bundle fills."
            )
            new_choice = st.radio(
                "Which person's data does this planner instance hold?",
                ["Me", "Spouse"],
                index=0 if instance_owner == OwnerRole.YOU.value else 1,
                key="instance_owner_change_choice",
            )
            if st.button("Update", key="instance_owner_change_save"):
                resolved_owner = (
                    OwnerRole.YOU.value if new_choice == "Me" else OwnerRole.SPOUSE.value
                )
                if resolved_owner != instance_owner:
                    save_instance_owner(resolved_owner)
                    st.session_state["instance_owner"] = resolved_owner
                    st.rerun()

    # disabled=True (not hidden) while identity is unset -- a hidden control
    # is indistinguishable from a missing feature (see views/planner.py's
    # column_config disabled=True convention for the same "visible but
    # inert" preference over hiding a widget entirely).
    if st.button("⟳ Sync everything", key="sync_everything_btn", disabled=not identity_set):
        with st.spinner("Syncing all sources…"):
            summary = sync_everything(hh)
        # P4-1: the only other clearer of this Reset-to-demo sentinel
        # (_apply_portfolio_snapshot) has no live caller, so without this an
        # explicit sync would otherwise leave user-defaults autosave
        # (parameters.py/option_exercise.py, gated on the same flag) silently
        # disabled for the rest of the session.
        st.session_state.pop("_suppress_snapshot_autoload", None)
        st.info(_format_sync_everything_summary(summary))

    pending: set[str] = st.session_state.get("_pending_review", set())
    st.metric("Fields awaiting review", len(pending))
    if not pending:
        st.success("All data sources reconciled ✓")
    else:
        store = CandidateStore.load(CANDIDATE_STORE_PATH)
        choices = ChoiceMap.load(TRUST_CHOICES_PATH)
        # audit-0809 #11: a corrupt committed cache degrades to {} here (read-time
        # only) — save_committed() is the actual guard against overwriting it.
        try:
            committed_json = load_committed(COMMITTED_PATH) or {}
        except CorruptCommittedCacheError:
            committed_json = {}

        for field_key in sorted(pending):
            with st.container(border=True):
                _render_field_card(field_key, committed_json, store, choices)

    # Reachable in BOTH branches -- this is why the early return above became
    # an else. instance_owner/identity_set come from Task 5's gate at the top
    # of this function.
    if identity_set:
        _render_attribution_table(instance_owner)
