"""Data-bridge import preview: what a file declares and what Apply would change.

Spec ``docs/superpowers/specs/2026-09-20-import-data-ownership-design.md`` §5,
stage 4. Renders an :class:`engine.bridge_import.ImportPlan` and collects the
user's choices, in the same script run as the Apply button beneath it, so
nothing is written until Apply is clicked.

The labels here are ABSOLUTE: the ``you`` slot is "Your data" (the ``your_*``
fields) on either person's planner, exactly as ``Household`` stores it. What
the caption states comes from the file itself, never from this machine's
instance identity.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from engine.bridge_import import ImportPlan, SlotPlan

SLOT_ORDER: tuple[str, ...] = ("you", "spouse")
SLOT_LABELS: dict[str, str] = {"you": "Your data", "spouse": "Spouse's data"}
_EXPORTER_LABELS: dict[str, str] = {"you": "You", "spouse": "Spouse"}
_V4_SLOT_LABELS: dict[str, str] = {
    "spouse": "Spouse's slot",
    "you": "Your slot — restore my own backup",
}
# Long overwrite lists are truncated; the counts above them stay exact.
_MAX_LISTED = 12


def _fmt(value: Any) -> str:
    """A compact, human-readable rendering of one preview value."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:g}"
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


def _change_lines(writes: dict[str, tuple[Any, Any]]) -> list[str]:
    lines = [f"- `{k}`: {_fmt(old)} → {_fmt(new)}" for k, (old, new) in sorted(writes.items())]
    if len(lines) > _MAX_LISTED:
        hidden = len(lines) - _MAX_LISTED
        lines = [*lines[:_MAX_LISTED], f"- …and {hidden} more"]
    return lines


def render_v4_slot_picker() -> str:
    """Absolute slot picker for a legacy v4 file, which cannot declare its own slot.

    Defaults to Spouse (the user's rule for legacy files); "Your slot" is how a
    user restores their own backup.
    """
    st.caption(
        "This is an older (v4) file. It does not say whose data it holds, so "
        "choose the slot it lands in:"
    )
    choice = st.radio(
        "Import this file into",
        list(_V4_SLOT_LABELS),
        index=0,
        key="import_v4_slot",
        format_func=_V4_SLOT_LABELS.__getitem__,
    )
    return str(choice)


def slot_details_markdown(sp: SlotPlan) -> str:
    """The markdown for one slot's changes.

    Sections are separated by blank lines: a line right after a list item is
    a lazy continuation of that item, which merged the sections that follow
    into the last change ("300,000 Accounts: 1 incoming") and hid them.
    """
    blocks: list[str] = []
    if sp.scalar_writes:
        blocks.append(
            "\n".join(
                [
                    f"**{len(sp.scalar_writes)} setting(s) would change:**",
                    *_change_lines(sp.scalar_writes),
                ]
            )
        )
    if sp.accounts_incoming or sp.accounts_replaced or sp.accounts_removed:
        blocks.append(
            f"**Accounts:** {sp.accounts_incoming} incoming — replaces "
            f"{sp.accounts_replaced}, removes {sp.accounts_removed} held here"
        )
    if sp.ledger_rows:
        blocks.append(f"**Statement ledger:** {sp.ledger_rows} row(s) replaced")
    if sp.ytd_writes:
        blocks.append(
            "\n".join(["**Year-to-date figures would change:**", *_change_lines(sp.ytd_writes)])
        )
    if sp.grants_replaced:
        blocks.append("**Stock-option grants:** replaced by the file's")
    return "\n\n".join(blocks) if blocks else "Nothing here would change."


def _render_slot_details(sp: SlotPlan, *, show_freshness: bool) -> None:
    label = SLOT_LABELS[sp.slot]
    st.markdown(slot_details_markdown(sp))
    if sp.stale is True:
        st.warning(
            f"⚠️ This copy of **{label}** was captured {sp.incoming_captured_at}, "
            f"older than the copy this planner holds ({sp.local_captured_at}). "
            "Importing it would replace newer figures with older ones — untick "
            "it to keep yours."
        )
    elif sp.stale is None and show_freshness:
        st.caption("Freshness unknown — one or both sides have no capture time to compare.")


def render_import_preview(plan: ImportPlan) -> frozenset[str]:
    """Render *plan* and return the slots the user has selected for import.

    A v5 file gets one checkbox per slot it carries. Only the exporter's own
    slot is selected by default: a real export always carries the exporter's
    copy of the OTHER person too, which may be stale, so importing it is
    opt-in -- one Apply click must never overwrite the receiver's own figures.
    A v4 file carries exactly one slot, already chosen by
    :func:`render_v4_slot_picker`, so there is nothing to deselect.
    """
    ordered = [s for s in SLOT_ORDER if s in plan.slots]
    if plan.version == "v5" and plan.declared_by is not None:
        contents = ", ".join(SLOT_LABELS[s] for s in ordered)
        st.caption(
            f"This file was exported by **{_EXPORTER_LABELS[plan.declared_by]}** "
            f"and contains: {contents}."
        )

    selected: set[str] = set()
    for slot in ordered:
        sp = plan.slots[slot]
        if plan.version == "v5":
            is_exporters = slot == plan.declared_by
            if st.checkbox(
                f"Import {SLOT_LABELS[slot]}", value=is_exporters, key=f"import_slot_{slot}"
            ):
                selected.add(slot)
            if not is_exporters and plan.declared_by is not None:
                # No possessive built from the exporter label: "You's copy"
                # read wrongly. The caption above already names the exporter.
                st.caption(
                    f"This is a copy of {SLOT_LABELS[slot]} held by the exporter, not "
                    "the original — tick it only to restore it (e.g. both people "
                    "from your own backup)."
                )
        else:
            st.markdown(f"**Into {SLOT_LABELS[slot]}**")
            selected.add(slot)
        _render_slot_details(sp, show_freshness=plan.version == "v5")

    household = plan.household
    if household.accounts_added or household.accounts_upserted:
        st.caption(
            f"Joint (household) accounts: {household.accounts_added} added, "
            f"{household.accounts_upserted} updated — never removed."
        )
    if plan.joint_writes:
        st.markdown(
            "\n".join(
                ["**Household settings taken from the file:**", *_change_lines(plan.joint_writes)]
            )
        )
    if plan.joint_conflicts:
        listed = "; ".join(
            f"{c['field']} (yours {_fmt(c['mine'])} · theirs {_fmt(c['theirs'])})"
            for c in plan.joint_conflicts
        )
        st.caption(f"Household settings that differ — yours are kept: {listed}")
    for warning in plan.warnings:
        st.caption(f"ℹ️ {warning}")
    return frozenset(selected)
