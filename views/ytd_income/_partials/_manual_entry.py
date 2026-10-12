from datetime import date

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import is_pyodide
from engine.tax import LTCG_RATES_MFJ
from models.household import Household
from models.ytd_income import (
    MANUAL_HOUSEHOLD_FIELDS,
    YTDSnapshot,
    manual_key,
    split_manual_key,
    sum_income_events,
)
from views._format import fmt_dollars, fmt_pct
from views._shared import operator_slot
from views.ytd_drafts import (
    DRAFT_KEY,
    DRAFT_META_KEY,
    FIELD_LABELS,
    FLOAT_FIELDS,
    OWNER_LABELS,
    commit_drafts,
    describe_keys,
    put_draft,
    reconcile_drafts,
    shown,
)
from views.ytd_income._partials._event_log import render_event_log_partial

OWNER_WIDGET_KEY = "ytd_manual_owner"
# Non-widget mirror of the toggle, so the choice survives leaving the page.
OWNER_CHOICE_KEY = "ytd_manual_owner_choice"
_SAVED_FLASH_KEY = "_ytd_manual_saved"
_STALE_FLASH_KEY = "_ytd_manual_stale"
# Per-person fields this form edits. nqo_exercise_ytd is per-person too but is
# deliberately import-only (it is already inside W-2 wages).
_FORM_OWNED_FIELDS = ("wages_ytd", "federal_withholding_ytd", "qualified_dividends_ytd")


def _mark_manual_entry_explicit() -> None:
    """``on_change`` callback for the checkbox below -- records that the user
    touched this control directly, so a later successful sync/scan/import
    knows not to override their choice (see
    ``views._shared.auto_deselect_manual_entry``).
    """
    st.session_state["ytd_manual_entry_explicit"] = True


def _widget_key(key: str) -> str:
    return "ytd_w_" + key.replace(".", "_")


def _as_of_key(owner: str) -> str:
    return f"ytd_manual_as_of_{owner}"


def _note_key(owner: str) -> str:
    return f"ytd_manual_note_{owner}"


def _iso_date(value: object) -> str:
    return value.isoformat() if isinstance(value, date) else date.today().isoformat()


def _clean_note(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _owner_choices(hh: Household) -> list[str]:
    # Mirrors the event log's Whose selector (_event_log.py:25).
    return ["you"] if hh.filing_status == "Single" else ["you", "spouse"]


def _default_owner(hh: Household) -> str:
    choices = _owner_choices(hh)
    owner = operator_slot()
    return owner if owner in choices else choices[0]


def _saved_snapshot() -> YTDSnapshot:
    return st.session_state.get("ytd_snapshot") or YTDSnapshot()


# --- callbacks: run BEFORE the script on the next rerun ----------------------


def _on_value_edit(key: str) -> None:
    st.session_state[DRAFT_KEY] = put_draft(
        st.session_state.get(DRAFT_KEY),
        key,
        float(st.session_state[_widget_key(key)]),
        _saved_snapshot(),
    )


def _on_meta_edit(owner: str) -> None:
    meta = {o: dict(m) for o, m in (st.session_state.get(DRAFT_META_KEY) or {}).items()}
    meta[owner] = {
        "as_of": _iso_date(st.session_state.get(_as_of_key(owner))),
        "note": _clean_note(st.session_state.get(_note_key(owner))),
    }
    st.session_state[DRAFT_META_KEY] = meta


def _on_owner_change() -> None:
    st.session_state[OWNER_CHOICE_KEY] = st.session_state[OWNER_WIDGET_KEY]


def _rendered_keys(owner: str) -> list[str]:
    return [manual_key(f, owner) for f in _FORM_OWNED_FIELDS] + [
        manual_key(f) for f in MANUAL_HOUSEHOLD_FIELDS
    ]


def _save(owner: str) -> None:
    # Sweep the rendered widgets as well: an edit and the Save click can reach
    # the server in the same rerun, and callback order is not guaranteed.
    for key in _rendered_keys(owner):
        if _widget_key(key) in st.session_state:
            _on_value_edit(key)
    if _as_of_key(owner) in st.session_state:
        _on_meta_edit(owner)
    ytd = _saved_snapshot()
    live, stale = reconcile_drafts(st.session_state.get(DRAFT_KEY), ytd)
    if live:
        st.session_state["ytd_snapshot"] = commit_drafts(
            ytd,
            live,
            st.session_state.get(DRAFT_META_KEY) or {},
            current_owner=owner,
            today=date.today().isoformat(),
        )
    st.session_state.pop(DRAFT_KEY, None)
    st.session_state.pop(DRAFT_META_KEY, None)
    st.session_state[_SAVED_FLASH_KEY] = True
    st.session_state[_STALE_FLASH_KEY] = stale


def _discard() -> None:
    st.session_state.pop(DRAFT_KEY, None)
    st.session_state.pop(DRAFT_META_KEY, None)


# --- rendering ---------------------------------------------------------------


def _select_owner(hh: Household) -> str:
    choices = _owner_choices(hh)
    default = _default_owner(hh)
    if len(choices) == 1:
        return default
    chosen = st.session_state.get(OWNER_CHOICE_KEY)
    st.session_state[OWNER_WIDGET_KEY] = chosen if chosen in choices else default
    picked = st.radio(
        "Whose numbers?",
        choices,
        format_func=OWNER_LABELS.__getitem__,
        horizontal=True,
        key=OWNER_WIDGET_KEY,
        on_change=_on_owner_change,
        help="Wages, federal tax withheld and qualified dividends are kept per person. "
        "Every other field is a household total.",
    )
    return picked if picked in choices else default


def _number(
    label: str,
    key: str,
    saved: float,
    drafts: dict,
    *,
    step: float,
    help: str | None = None,  # noqa: A002 -- matches st.number_input; an AST test reads it
    min_value: float | None = None,
) -> None:
    """A manual-entry number input whose value lives in the drafts.

    Re-seeded before creation on every run, from the draft or else the saved
    value -- that is what restores it after Streamlit drops an unrendered
    widget's state, and what keeps it from showing a value an import replaced.
    """
    field_name = split_manual_key(key)[1]
    entry = drafts.get(key)
    value = entry["value"] if entry else shown(field_name, saved)
    is_float = field_name in FLOAT_FIELDS
    st.session_state[_widget_key(key)] = float(value) if is_float else int(value)
    st.number_input(
        label,
        key=_widget_key(key),
        step=float(step) if is_float else int(step),
        format="%.2f" if is_float else "%d",
        min_value=min_value,
        help=help,
        on_change=_on_value_edit,
        args=(key,),
    )


def _dollars_md(value: float) -> str:
    # Escaped: two bare "$" in one markdown string render as LaTeX.
    return fmt_dollars(value).replace("$", "\\$")


def _breakdown_caption(ytd: YTDSnapshot) -> str:
    parts = []
    for f in _FORM_OWNED_FIELDS:
        if f == "federal_withholding_ytd":
            # Redesign PR 1b: each person's total = base + UBS component.
            you, spouse = ytd.withholding_total("you"), ytd.withholding_total("spouse")
        else:
            you, spouse = ytd.owner_value("you", f), ytd.owner_value("spouse", f)
        parts.append(
            f"{FIELD_LABELS[f]} {_dollars_md(you + spouse)} "
            f"(You {_dollars_md(you)} + Spouse {_dollars_md(spouse)})"
        )
    return "Household totals: " + " · ".join(parts)


def _mark_rows(ytd: YTDSnapshot) -> list[dict[str, str]]:
    rows = []
    for key, mark in sorted(ytd.manual_marks.items()):
        owner, field_name = split_manual_key(key)
        rows.append(
            {
                "Field": FIELD_LABELS.get(field_name, field_name),
                "Whose": OWNER_LABELS[owner],
                "Value": fmt_dollars(mark.value),
                "As of": mark.as_of,
                "Note": mark.note,
            }
        )
    return rows


def _render_draft_status(drafts: dict, stale: list[str]) -> None:
    if st.session_state.pop(_SAVED_FLASH_KEY, False) is True:
        st.success("Saved.")
    flashed = st.session_state.pop(_STALE_FLASH_KEY, None)
    stale = stale + (flashed if isinstance(flashed, list) else [])
    if stale:
        st.warning(
            "These values changed since you started editing them (an import updated "
            "them), so your unsaved edits were discarded: " + "; ".join(describe_keys(stale)) + "."
        )
    if drafts:
        # The browser (stlite) build has no persistent storage: a reload loses
        # saved entries too, so "Save to keep them" would be a false promise there.
        keep = (
            "Click **Save entries** to apply them. In this browser version a full page "
            "reload also loses saved entries."
            if is_pyodide()
            else "Click **Save entries** to keep them."
        )
        st.warning(
            "You have unsaved changes: "
            + "; ".join(describe_keys(drafts))
            + ". They are kept while you move around the app, but a full page reload "
            "loses them. " + keep
        )


def _render_save_discard(owner: str, drafts: dict) -> None:
    save_col, discard_col = st.columns([1, 1])
    with save_col:
        st.button(
            "Save entries", type="primary", key="ytd_manual_save", on_click=_save, args=(owner,)
        )
    with discard_col:
        if drafts:
            st.button("Discard changes", key="ytd_manual_discard", on_click=_discard)


def render_manual_entry_partial(hh: Household) -> YTDSnapshot:
    manual = st.checkbox(
        "Manual entry",
        value=st.session_state.get("ytd_manual_entry", True),
        key="ytd_manual_entry",
        on_change=_mark_manual_entry_explicit,
    )

    # Get existing snapshot or create empty
    ytd: YTDSnapshot = st.session_state.get("ytd_snapshot", YTDSnapshot())
    drafts, stale = reconcile_drafts(st.session_state.get(DRAFT_KEY), ytd)
    st.session_state[DRAFT_KEY] = drafts

    if not manual:
        # Unsaved drafts stay reachable when manual entry is switched off
        # (e.g. auto-deselected by a sync): warn and offer Save / Discard.
        _render_draft_status(drafts, stale)
        if drafts:
            _render_save_discard(_default_owner(hh), drafts)
        return ytd

    owner = _select_owner(hh)
    if not ytd.by_owner:
        # Same migration the data bridge applies (bridge_import R5): a legacy
        # household total belongs to the instance operator.
        ytd = ytd.attribute_unowned(_default_owner(hh))
    multi = len(_owner_choices(hh)) > 1
    _render_draft_status(drafts, stale)
    if multi:
        st.caption(
            f"Wages, federal tax withheld and qualified dividends below are "
            f"{OWNER_LABELS[owner]}'s. Every other field is a household total."
        )

    meta = (st.session_state.get(DRAFT_META_KEY) or {}).get(owner, {})
    st.session_state[_as_of_key(owner)] = date.fromisoformat(
        meta.get("as_of") or date.today().isoformat()
    )
    st.session_state[_note_key(owner)] = meta.get("note", "")
    as_of_col, note_col = st.columns([1, 3])
    with as_of_col:
        st.date_input(
            "As of",
            key=_as_of_key(owner),
            max_value=date.today(),
            on_change=_on_meta_edit,
            args=(owner,),
            help="The date these figures are current to, such as the paystub date. "
            "An import only replaces a hand-entered value when its data is at "
            "least this recent.",
        )
    with note_col:
        st.text_input(
            "Source note (optional)",
            key=_note_key(owner),
            on_change=_on_meta_edit,
            args=(owner,),
            placeholder="e.g. paystub dated 9/30",
        )

    def own(f: str) -> str:
        return manual_key(f, owner)

    def hh_key(f: str) -> str:
        return manual_key(f)

    col1, col2, col3 = st.columns(3)
    with col1:
        _number(
            "Wages YTD", own("wages_ytd"), ytd.owner_value(owner, "wages_ytd"), drafts, step=5_000
        )
        _number(
            "1099-NEC / Self-Employment Income YTD",
            hh_key("nec_income_ytd"),
            ytd.nec_income_ytd,
            drafts,
            step=1_000,
            help="Self-employment or contractor income year-to-date.",
        )
        _number(
            "Long-Term Capital Gains YTD",
            hh_key("ltcg_ytd"),
            ytd.ltcg_ytd,
            drafts,
            step=10_000,
            help="From stop-loss triggers, mutual fund distributions, etc.",
        )
    with col2:
        _number(
            "Short-Term Capital Gains YTD", hh_key("stcg_ytd"), ytd.stcg_ytd, drafts, step=5_000
        )
        div_col1, div_col2 = st.columns(2)
        with div_col1:
            _number(
                "Qualified dividends YTD",
                own("qualified_dividends_ytd"),
                ytd.owner_value(owner, "qualified_dividends_ytd"),
                drafts,
                step=500,
                help=f"Taxed at LTCG rates ({fmt_pct(LTCG_RATES_MFJ[0], 0)}/{fmt_pct(LTCG_RATES_MFJ[1], 0)}/{fmt_pct(LTCG_RATES_MFJ[2], 0)}); counts toward MAGI but not ordinary brackets.",
            )
        with div_col2:
            _number(
                "Ordinary dividends YTD",
                hh_key("ordinary_dividends_ytd"),
                ytd.ordinary_dividends_ytd,
                drafts,
                step=500,
                help="Taxed as ordinary income; stacks into brackets and SS taxation.",
            )
    with col3:
        _number("Interest YTD", hh_key("interest_ytd"), ytd.interest_ytd, drafts, step=1_000)
        _number(
            "Tax-exempt (muni) interest YTD",
            hh_key("tax_exempt_interest_ytd"),
            ytd.tax_exempt_interest_ytd,
            drafts,
            step=1_000,
            help="Muni bond interest — counts toward MAGI/IRMAA and SS provisional income, not ordinary brackets.",
        )
        _number(
            "Federal Tax Withheld YTD",
            own("federal_withholding_ytd"),
            ytd.owner_value(owner, "federal_withholding_ytd"),
            drafts,
            step=1_000,
            help="Paycheck (W-2) federal income tax withheld year-to-date; counts as "
            "'Already paid' toward safe-harbor. Withholding on option exercises from a "
            "UBS CSV is added on top automatically.",
        )
        ubs = ytd.ubs_withholding_by_owner.get(owner, 0.0)
        if ubs:
            st.caption(
                f"+ {_dollars_md(ubs)} withheld on option exercises (UBS CSV, read-only) "
                f"= {_dollars_md(ytd.withholding_total(owner))} total"
            )
        _number(
            "Estimated tax payments YTD",
            hh_key("estimated_payments_ytd"),
            ytd.estimated_payments_ytd,
            drafts,
            step=1_000,
            help="Quarterly estimated tax payments (Form 1040-ES) made year-to-date; counts as 'Already paid' toward safe-harbor.",
        )

    st.markdown("##### Above-the-line adjustments")
    st.caption(
        "These reduce MAGI (IRMAA/NIIT/ACA) AND ordinary bracket room — they lower AGI "
        "before either is computed."
    )
    atl_col1, atl_col2 = st.columns(2)
    with atl_col1:
        _number(
            "HSA contribution YTD",
            hh_key("hsa_contribution_ytd"),
            ytd.hsa_contribution_ytd,
            drafts,
            step=500,
            min_value=0,
            help="Deductible HSA contribution (Form 8889). Above-the-line: lowers AGI/MAGI and widens bracket room.",
        )
    with atl_col2:
        _number(
            "Deductible IRA contribution YTD",
            hh_key("deductible_ira_contribution_ytd"),
            ytd.deductible_ira_contribution_ytd,
            drafts,
            step=500,
            min_value=0,
            help="Deductible traditional-IRA contribution (Schedule 1). Above-the-line: lowers AGI/MAGI and widens bracket room.",
        )

    st.markdown("##### Crypto (from Koinly)")
    st.caption(
        "These numbers come from a Koinly tax report (short-term gains / "
        "long-term gains / income). Short-term gains and income are ordinary-rate "
        "and hit brackets + MAGI; long-term gains are preferential-rate (MAGI + NIIT, "
        "not brackets); income (staking/DeFi/airdrops) hits brackets + MAGI but not NIIT, "
        "except its lending-interest part, which is also subject to NIIT."
    )
    crypto_col1, crypto_col2, crypto_col3, crypto_col4 = st.columns(4)
    with crypto_col1:
        _number(
            "Crypto short-term gains YTD",
            hh_key("crypto_stcg_ytd"),
            ytd.crypto_stcg_ytd,
            drafts,
            step=100,
            help="Koinly short-term capital gains. Ordinary-rate: hits brackets, MAGI, and NIIT.",
        )
    with crypto_col2:
        _number(
            "Crypto long-term gains YTD",
            hh_key("crypto_ltcg_ytd"),
            ytd.crypto_ltcg_ytd,
            drafts,
            step=100,
            help="Koinly long-term capital gains. Preferential-rate: hits MAGI and NIIT but not ordinary brackets.",
        )
    with crypto_col3:
        _number(
            "Crypto income YTD (staking/DeFi)",
            hh_key("crypto_income_ytd"),
            ytd.crypto_income_ytd,
            drafts,
            step=100,
            help="Koinly income report (staking, DeFi, airdrops). Ordinary income: hits brackets and MAGI.",
        )
    with crypto_col4:
        _number(
            "Of which: lending interest",
            hh_key("crypto_interest_ytd"),
            ytd.crypto_interest_ytd,
            drafts,
            step=100,
            min_value=0.0,
            help="The Koinly 'Lending interest' part of crypto income. Already inside the "
            "crypto income figure (not added again); it also counts as net investment "
            "income for NIIT. Capped at crypto income.",
        )

    _render_save_discard(owner, drafts)

    if multi:
        st.caption(_breakdown_caption(ytd))
    if ytd.manual_marks:
        st.markdown("##### Entered by hand")
        st.dataframe(pd.DataFrame(_mark_rows(ytd)), hide_index=True, width="stretch")
        st.caption(
            "Values not listed here came from an import or a statement. An import "
            "replaces a hand-entered value only when its data is at least as recent "
            "as that value's 'As of' date."
        )

    income_events = render_event_log_partial(hh, ytd)

    conversions_done = sum_income_events(income_events, kind="conversion", owner="you")
    spouse_conversions_done = sum_income_events(income_events, kind="conversion", owner="spouse")
    distributions_done = sum_income_events(income_events, kind="distribution")

    # Overlay the event-log totals onto the previously-persisted snapshot
    # (audit-0805 C42): a fresh YTDSnapshot(...) here would drop every field
    # this form has no widget for (e.g. nqo_exercise_ytd). Field values are
    # written only by Save (redesign PR 1b).
    ytd = ytd.overlay(
        tax_year=hh.base_year,
        ira_conversions_ytd=conversions_done,
        spouse_ira_conversions_ytd=spouse_conversions_done,
        ira_distributions_ytd=distributions_done,
        income_events=income_events,
        manually_entered=True,
    ).with_snapshot_date()

    st.session_state.ytd_snapshot = ytd
    return ytd
