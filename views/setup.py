"""Setup page — household parameters, FinExtract sync, V2 data bridge."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from engine.data_bridge_browser import (
    BROWSER_PRIVKEY_LS_KEY,
    is_pyodide,
    local_storage_get,
    local_storage_remove,
    local_storage_set,
)
from engine.data_bridge_keys import (
    decode_keymaterial,
    load_privkey,
    load_pubkey,
)
from engine.portfolio_sync import (
    AccountSummary,
    EquityGrant,
    Holding,
    MagiSnapshot,
    PortfolioSnapshot,
    apply_dividends_rollup,
    apply_magi,
    apply_option_exercises,
    fetch_dividends_rollup,
    fetch_magi,
    fetch_option_exercises_with_cache,
    fetch_portfolio,
    fetch_tax_return,
    fetch_ytd_snapshot,
    merge_snapshots,
    save_snapshot,
    save_tax_snapshot,
    save_ytd_snapshot,
)
from engine.tax_return_pdf import (
    Form1040ParseError,
    load_pdf_tax_records,
    parse_form_1040_pdf,
    save_pdf_tax_records,
)
from engine.upload_merge import build_user_defaults_session_updates, derive_ira_balances
from models.household import Household


def _build_user_defaults_session_updates(data: dict, *, as_spouse: bool) -> dict:
    """Compute session_state updates from a .user_defaults.json payload.

    Thin wrapper around :func:`engine.upload_merge.build_user_defaults_session_updates`.
    Pure function — returns a ``{session_key: value}`` dict without writing to state.
    See the engine module for full mapping rules.
    """
    return build_user_defaults_session_updates(data, as_spouse=as_spouse)


def _apply_user_defaults_to_session(data: dict, *, as_spouse: bool = False) -> None:
    """Write JSON user-defaults keys into st.session_state.

    When ``as_spouse=True``, cross-maps the file's ``your_*`` fields to the
    receiver's ``spouse_*`` slots and ignores joint / grant fields.
    See :func:`_build_user_defaults_session_updates` for the mapping rules.

    Note for the spouse path: ``get_household()`` reads grant_strikes via
    ``_user_grant_strikes`` from session_state; ``as_spouse=True`` deliberately
    skips that key so the receiver's own grants stay authoritative.
    """
    for key, val in _build_user_defaults_session_updates(data, as_spouse=as_spouse).items():
        st.session_state[key] = val


def _user_defaults_from_session() -> dict:
    """Inverse of _apply_user_defaults_to_session: read session_state → JSON dict."""
    # Mirror of _apply_user_defaults_to_session scalar_keys
    scalar_keys = [
        "your_age",
        "spouse_age",
        "your_ira",
        "spouse_ira",
        "your_ss_fra",
        "spouse_ss_fra",
        "your_ss_start_age",
        "spouse_ss_start_age",
        "your_rmd_start_age",
        "spouse_rmd_start_age",
        "your_fra_age",
        "spouse_fra_age",
        "living_expenses",
        "stock_price_now",
        "aca_benchmark_premium_annual",
        "aca_enhanced_subsidies_active",
        "medicare_part_b_base_monthly",
    ]
    payload: dict = {}
    for k in scalar_keys:
        # Reverse the alias: session stores txn_price, JSON schema expects stock_price_now
        sess_key = "txn_price" if k == "stock_price_now" else k
        if sess_key in st.session_state:
            payload[k] = st.session_state[sess_key]
    strikes = st.session_state.get("_user_grant_strikes")
    if strikes:
        payload["grant_strikes"] = strikes
    overrides = st.session_state.get("account_type_overrides")
    if overrides:
        payload["account_type_overrides"] = overrides
    prior_magi = st.session_state.get("prior_year_magi")
    if prior_magi:
        payload["prior_year_magi"] = {str(k): v for k, v in prior_magi.items()}
    survivor = st.session_state.get("survivor")
    if survivor:
        payload["survivor"] = survivor
    inherited_iras = st.session_state.get("inherited_iras")
    if inherited_iras:
        payload["inherited_iras"] = inherited_iras
    return payload


def _portfolio_snapshot_from_dict(data: dict) -> object:
    """Reconstruct a PortfolioSnapshot from its asdict() JSON form."""
    accounts = []
    for acc_d in data.get("accounts", []):
        holdings = [Holding(**h) for h in acc_d.get("holdings", [])]
        acc_d_clean = {k: v for k, v in acc_d.items() if k != "holdings"}
        accounts.append(AccountSummary(holdings=holdings, **acc_d_clean))
    grants = [EquityGrant(**g) for g in data.get("equity_grants", [])]
    return PortfolioSnapshot(
        accounts=accounts,
        equity_grants=grants,
        txn_shares_held=data.get("txn_shares_held", 0),
        txn_shares_value=data.get("txn_shares_value", 0.0),
        server_available=data.get("server_available", False),
        error=data.get("error"),
    )


def _apply_portfolio_snapshot(incoming: object, *, as_spouse: bool) -> None:
    """Merge a freshly-parsed portfolio snapshot into the session.

    Thin wrapper around :func:`engine.portfolio_sync.merge_snapshots` that
    reads / writes ``st.session_state['portfolio_snapshot']``.
    """
    existing = st.session_state.get("portfolio_snapshot")
    merged = merge_snapshots(existing, incoming, as_spouse=as_spouse)  # type: ignore[arg-type]
    st.session_state["portfolio_snapshot"] = merged


def _clear_personal_session_state() -> None:
    """Reset personal-mode session state to demo defaults."""
    keys_to_clear = [
        "portfolio_snapshot",
        "_user_grant_strikes",
        "your_age",
        "spouse_age",
        "your_ira",
        "spouse_ira",
        "your_ss_fra",
        "spouse_ss_fra",
        "your_ss_start_age",
        "spouse_ss_start_age",
        "your_rmd_start_age",
        "spouse_rmd_start_age",
        "your_fra_age",
        "spouse_fra_age",
        "living_expenses",
        "txn_price",
        "aca_benchmark_premium_annual",
        "aca_enhanced_subsidies_active",
        "medicare_part_b_base_monthly",
        "prior_year_magi",
        "survivor",
        "inherited_iras",
    ]
    for k in keys_to_clear:
        st.session_state.pop(k, None)
    st.session_state.pop("_seeded", None)  # force re-seed from synthetic


def _resolved_pubkey() -> bytes | None:
    """Resolve V2 public key for encryption.

    Order: env/dotfile (:func:`load_pubkey`), then derive from the
    session-state private key (browser paste flow). Returns ``None`` if
    no key is available from any source.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import derive_pubkey

    pk = load_pubkey()
    if pk is not None:
        return pk
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if not priv_b64:
        return None
    try:
        priv_raw = decode_keymaterial(priv_b64)
    except ValueError:
        return None
    return derive_pubkey(priv_raw)


def _resolve_privkey_bytes() -> bytes | None:
    """Resolve V2 private key for decryption.

    Order: session-state pasted key, then disk dotfile/env via
    :func:`load_privkey`. Returns ``None`` if no key is available.
    """
    priv_b64 = st.session_state.get("data_bridge_privkey_b64")
    if priv_b64:
        try:
            return decode_keymaterial(priv_b64)
        except ValueError:
            pass
    return load_privkey()


def _handle_v2_privkey() -> None:
    """Widget for entering and caching the V2 data-bridge private key.

    On stlite/Pyodide, the key is cached in ``localStorage`` under
    ``roth_planner.data_bridge.priv_b64`` so it survives page reloads.
    In all environments, the key lives in ``st.session_state`` under
    ``data_bridge_privkey_b64`` for the duration of the session.
    """
    # Hydrate session_state from localStorage on first paint.
    if "data_bridge_privkey_b64" not in st.session_state:
        cached = local_storage_get(BROWSER_PRIVKEY_LS_KEY)
        if cached:
            st.session_state["data_bridge_privkey_b64"] = cached

    has_key = "data_bridge_privkey_b64" in st.session_state
    # Auto-expand on the public site when no key is set — user needs to act.
    expand = is_pyodide() and not has_key

    with st.expander("\U0001f511 V2 private key", expanded=expand):
        if has_key:
            st.caption("\U0001f510 Private key loaded for this session.")
            if st.button("Clear", key="clear_v2_privkey"):
                st.session_state.pop("data_bridge_privkey_b64", None)
                local_storage_remove(BROWSER_PRIVKEY_LS_KEY)
                st.rerun()
            return
        st.caption(
            "Paste your data-bridge private key (base64). Required to decrypt "
            "uploaded `.json.enc` files and to encrypt exports on the public site."
        )
        key_input = st.text_input(
            "Private key (base64)",
            type="password",
            key="_v2_privkey_input",
            help="From `~/.finextract/data-bridge.priv` on your local host.",
        )
        if st.button("Save", key="save_v2_privkey") and key_input:
            try:
                decode_keymaterial(key_input)
            except ValueError as e:
                st.error(f"Invalid key: {e}")
                return
            val = key_input.strip()
            st.session_state["data_bridge_privkey_b64"] = val
            local_storage_set(BROWSER_PRIVKEY_LS_KEY, val)
            st.success("Private key saved.")
            st.rerun()


def _handle_personal_uploads() -> None:
    """Widget to inject personal defaults + portfolio snapshot from JSON uploads.

    For use in the deployed (stlite) demo where the visitor cannot put files
    next to the app. Local users can ignore this and just keep
    .user_defaults.json + .portfolio_cache.json in cwd.

    Accepts both V1 plaintext ``.json`` and V2 sealed ``.json.enc`` files.
    Encrypted uploads require the V2 private key configured in the
    "\U0001f511 V2 private key" expander (or available on disk).

    Each uploader has a per-file "Whose data?" toggle. "Me" applies the
    payload to the receiver's own slots (current behavior). "Spouse" treats
    the payload as the spouse's planner export from their own perspective,
    cross-maps ``your_*`` fields to the receiver's ``spouse_*`` slots, and
    merges portfolio accounts with ``owner="spouse"`` while preserving the
    receiver's own accounts, grants, and TXN holdings.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import (
        DataBridgeCryptoError,
        open_uploaded_payload,
    )

    with st.expander("\U0001f513 Use my real data (this session)"):
        st.caption(
            "Upload your local files for a personalized session. "
            "Values stay in this browser only; refresh = back to demo. "
            "V2 `.json.enc` files require the private key configured above. "
            'Use the "Whose data?" toggle when uploading your spouse\'s planner export.'
        )
        ud_role = st.radio(
            "Whose .user_defaults.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="ud_role",
        )
        ud_file = st.file_uploader(
            ".user_defaults.json[.enc] (ages, SS, grant strikes)",
            type=["json", "enc"],
            key="ud_upload",
        )
        pc_role = st.radio(
            "Whose .portfolio_cache.json?",
            ["Me", "Spouse"],
            horizontal=True,
            key="pc_role",
        )
        pc_file = st.file_uploader(
            ".portfolio_cache.json[.enc] (FinExtract holdings + grants)",
            type=["json", "enc"],
            key="pc_upload",
        )
        col_a, col_b = st.columns(2)
        if col_a.button("Apply", key="apply_uploads", width="stretch"):
            applied: list[str] = []
            privkey = _resolve_privkey_bytes()
            if ud_file is not None:
                try:
                    raw = ud_file.read()
                    plaintext = open_uploaded_payload(raw, privkey)
                    data = json.loads(plaintext.decode("utf-8"))
                    _apply_user_defaults_to_session(data, as_spouse=(ud_role == "Spouse"))
                    applied.append(f"{ud_file.name} ({ud_role.lower()})")
                except (
                    json.JSONDecodeError,
                    ValueError,
                    TypeError,
                    DataBridgeCryptoError,
                ) as e:
                    st.error(f"Invalid {ud_file.name}: {e}")
            if pc_file is not None:
                try:
                    raw = pc_file.read()
                    plaintext = open_uploaded_payload(raw, privkey)
                    data = json.loads(plaintext.decode("utf-8"))
                    snap = _portfolio_snapshot_from_dict(data)
                    _apply_portfolio_snapshot(snap, as_spouse=(pc_role == "Spouse"))
                    applied.append(f"{pc_file.name} ({pc_role.lower()})")
                except (
                    json.JSONDecodeError,
                    ValueError,
                    TypeError,
                    KeyError,
                    DataBridgeCryptoError,
                ) as e:
                    st.error(f"Invalid {pc_file.name}: {e}")
            if applied:
                st.success(f"Applied: {', '.join(applied)}. Rerunning…")
                st.rerun()
        if col_b.button("Reset to demo", key="reset_demo", width="stretch"):
            _clear_personal_session_state()
            st.success("Reset to demo defaults.")
            st.rerun()


def _handle_personal_exports() -> None:
    """Widget to download local data files for use on the public site.

    When a V2 data-bridge public key is configured (see ``deploy/README.md``),
    exports are sealed with ``crypto_box_seal`` and emitted as ``.json.enc``.
    Otherwise the V1 plaintext export is shown with a deprecation warning.
    """
    # Deferred: nacl unavailable in Pyodide
    from engine.data_bridge_crypto import seal

    with st.expander("📦 Export my data", expanded=False):
        pubkey = _resolved_pubkey()
        defaults = _user_defaults_from_session()
        cache_path = Path(__file__).resolve().parent.parent / ".portfolio_cache.json"

        if pubkey is not None:
            st.caption("🔐 V2 encrypted export active — files are sealed for your private key.")
            if defaults:
                payload = json.dumps(defaults, indent=2, default=str).encode("utf-8")
                st.download_button(
                    label="⬇️ .user_defaults.json.enc",
                    data=seal(payload, pubkey),
                    file_name=".user_defaults.json.enc",
                    mime="application/octet-stream",
                    key="export_user_defaults_enc",
                )
            else:
                st.caption("(Enter your numbers first to enable export.)")
            if cache_path.exists():
                st.download_button(
                    label="⬇️ .portfolio_cache.json.enc",
                    data=seal(cache_path.read_bytes(), pubkey),
                    file_name=".portfolio_cache.json.enc",
                    mime="application/octet-stream",
                    key="export_portfolio_cache_enc",
                )
            else:
                st.caption("(Run Portfolio Sync first to enable cache export.)")
            return

        # No V2 key. Public site → BLOCK V1 entirely (no plaintext leaves browser).
        if is_pyodide():
            st.caption(
                "\U0001f512 No plaintext export available on the public site. "
                "Paste your private key in the '\U0001f511 V2 private key' widget above "
                "to enable encrypted export."
            )
            return

        # V1 plaintext fallback — local host only, deprecated.
        st.caption(
            "Saves to your browser's default downloads folder. Share with the public site for third-party analysis."
        )
        st.warning(
            "⚠️ Plaintext export is deprecated and will be removed in a future release. "
            "Run `pixi run gen-data-bridge-keypair` to enable encrypted export."
        )
        if defaults:
            st.download_button(
                label="⬇️ .user_defaults.json",
                data=json.dumps(defaults, indent=2, default=str),
                file_name=".user_defaults.json",
                mime="application/json",
                key="export_user_defaults",
            )
        else:
            st.caption("(Enter your numbers first to enable export.)")
        if cache_path.exists():
            st.download_button(
                label="⬇️ .portfolio_cache.json",
                data=cache_path.read_bytes(),
                file_name=".portfolio_cache.json",
                mime="application/json",
                key="export_portfolio_cache",
            )
        else:
            st.caption("(Run Portfolio Sync first to enable cache export.)")


def _render_accounts_table(accounts: list[AccountSummary], *, show_owner: bool) -> None:
    """Render a read-only accounts dataframe, or an info banner when empty."""
    if not accounts:
        st.info("No accounts loaded — click Sync above.")
        return
    rows = [
        {
            "account_name": a.account_name,
            "type": a.account_type,
            "market_value": a.total_value,
            **({"owner": a.owner} if show_owner else {}),
        }
        for a in accounts
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_holdings_table(accounts: list[AccountSummary]) -> None:
    """Render a read-only holdings dataframe across the given accounts."""
    rows = [
        {
            "symbol": h.symbol,
            "account": h.account_name,
            "asset_class": h.asset_class,
            "quantity": h.quantity,
            "market_value": h.market_value,
        }
        for a in accounts
        for h in a.holdings
    ]
    if not rows:
        st.info("No holdings loaded — click Sync above.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_survivor_scenario() -> None:
    """Render the Survivor scenario expander in the Joint sub-tab."""
    base_year: int = 2026
    current: dict = st.session_state.get("survivor") or {}

    with st.expander("Survivor scenario (advanced sensitivity)", expanded=False):
        st.caption(
            "Optional. Models death of one spouse mid-projection. "
            "Survivor switches to single-filer brackets, std deduction, and senior bonus "
            "starting death_year + 1. Deceased's IRA rolls to survivor (spousal rollover); "
            "deceased's SS ends. "
            "NOT YET MODELED: SS survivor benefit step-up; inherited-IRA stretch rules."
        )
        enabled = st.checkbox(
            "Enable survivor scenario",
            value=bool(current),
            key="_survivor_enabled",
        )
        if enabled:
            who_options = ["Me", "Spouse"]
            who_default = 0 if current.get("who_dies", "you") == "you" else 1
            who_choice = st.radio(
                "Who dies?",
                who_options,
                index=who_default,
                horizontal=True,
                key="_survivor_who_dies",
            )
            who_dies = "you" if who_choice == "Me" else "spouse"
            death_year = st.number_input(
                "Year of death",
                min_value=base_year,
                max_value=base_year + 30,
                value=int(current.get("death_year", base_year + 5)),
                step=1,
                format="%d",
                help=(
                    "Calendar year in which the spouse dies. "
                    "MFJ filing applies for that year; Single filing begins the following year."
                ),
                key="_survivor_death_year",
            )
            st.session_state["survivor"] = {"who_dies": who_dies, "death_year": int(death_year)}
        else:
            st.session_state["survivor"] = None


def _render_inherited_iras() -> None:
    """Render the Inherited IRAs expander in the Joint sub-tab."""
    base_year: int = 2026

    with st.expander("Inherited IRAs (non-spousal, 10-year rule)", expanded=False):
        st.caption(
            "Model non-spousal inherited IRAs subject to the SECURE Act 10-year rule. "
            "The beneficiary must fully distribute the balance within 10 years of inheritance. "
            "Distributions add to ordinary income (MAGI). "
            "Leave empty if no inheritances are modeled."
        )

        iiras: list[dict] = list(st.session_state.get("inherited_iras") or [])
        to_remove: int | None = None

        for idx, entry in enumerate(iiras):
            col_bal, col_yr, col_owner, col_remove = st.columns([3, 2, 2, 1])
            new_bal = col_bal.number_input(
                "Balance ($)",
                min_value=0,
                max_value=10_000_000,
                value=int(entry.get("balance", 0)),
                step=10_000,
                format="%d",
                key=f"iira_balance_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            new_yr = col_yr.number_input(
                "Year inherited",
                min_value=base_year,
                max_value=base_year + 30,
                value=int(entry.get("inherited_year", base_year + 5)),
                step=1,
                format="%d",
                key=f"iira_year_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            owner_options = ["Me", "Spouse"]
            owner_val = entry.get("owner", "you")
            owner_idx_sel = 0 if owner_val == "you" else 1
            owner_choice = col_owner.radio(
                "Owner",
                owner_options,
                index=owner_idx_sel,
                horizontal=True,
                key=f"iira_owner_{idx}",
                label_visibility="collapsed" if idx > 0 else "visible",
            )
            if col_remove.button("Remove", key=f"iira_remove_{idx}"):
                to_remove = idx
            iiras[idx] = {
                "balance": float(new_bal),
                "inherited_year": int(new_yr),
                "owner": "you" if owner_choice == "Me" else "spouse",
                "growth_rate": float(entry.get("growth_rate", 0.07)),
            }

        if to_remove is not None:
            iiras.pop(to_remove)
            st.session_state["inherited_iras"] = iiras
            st.rerun()

        if st.button("Add inherited IRA", key="iira_add"):
            iiras.append(
                {
                    "balance": 0.0,
                    "inherited_year": base_year + 5,
                    "owner": "you",
                    "growth_rate": 0.07,
                }
            )
            st.session_state["inherited_iras"] = iiras
            st.rerun()

        st.session_state["inherited_iras"] = iiras


_FILING_STATUS_OPTIONS = [
    "married_filing_jointly",
    "single",
    "married_filing_separately",
    "head_of_household",
]

_FILING_STATUS_LABELS = {
    "married_filing_jointly": "Married Filing Jointly",
    "single": "Single",
    "married_filing_separately": "Married Filing Separately",
    "head_of_household": "Head of Household",
}


def _render_pdf_1040_import() -> None:
    """Widget to import MAGI from a TurboTax-exported 1040 PDF.

    Gated behind ``is_pyodide()`` — pdfplumber is not available in the web build.
    Parses the PDF, shows a confirmation preview with the filing-status selectbox
    (parser leaves it None), then on confirm persists the record and writes MAGI
    into session_state["prior_year_magi"].
    """
    with st.expander("📄 Import 1040 PDF (TurboTax export)", expanded=False):
        if is_pyodide():
            st.caption("1040 PDF import requires a local install.")
            return

        st.caption(
            "Upload a TurboTax-exported 1040 PDF to back-fill prior-year MAGI. "
            "Supports tax years 2023 and 2024. "
            "Parsed values are shown for confirmation before saving."
        )
        pdf_file = st.file_uploader(
            "Form 1040 PDF (TurboTax export)",
            type=["pdf"],
            key="pdf_1040_upload",
        )

        if pdf_file is None:
            return

        # Parse on every render while a file is present; cache result in session_state
        # to avoid re-parsing on every widget interaction after the file is loaded.
        cache_key = f"_pdf_1040_parsed_{pdf_file.name}_{pdf_file.size}"
        if cache_key not in st.session_state:
            try:
                with st.spinner("Parsing 1040 PDF…"):
                    rec = parse_form_1040_pdf(pdf_file.read())
                st.session_state[cache_key] = rec
            except Form1040ParseError as exc:
                st.error(f"Could not parse {pdf_file.name}: {exc}")
                return

        rec = st.session_state[cache_key]

        st.write("**Parsed values — please confirm:**")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Tax Year", str(rec.tax_year))
        col_b.metric("AGI", f"${rec.agi:,.0f}")
        col_c.metric("MAGI", f"${rec.magi:,.0f}")
        col_d, col_e = st.columns(2)
        col_d.metric("Tax-Exempt Interest", f"${rec.tax_exempt_interest:,.0f}")
        col_e.metric("FEIE", f"${rec.feie:,.0f}")

        status_idx = (
            _FILING_STATUS_OPTIONS.index(rec.filing_status)
            if rec.filing_status in _FILING_STATUS_OPTIONS
            else 0
        )
        chosen_status = st.selectbox(
            "Filing Status",
            options=_FILING_STATUS_OPTIONS,
            index=status_idx,
            format_func=lambda s: _FILING_STATUS_LABELS.get(s, s),
            key=f"_pdf_1040_filing_status_{rec.tax_year}",
            help="Select the filing status for this return (parser cannot auto-detect checkboxes).",
        )

        if st.button("Save 1040 record", key=f"_pdf_1040_save_{rec.tax_year}"):
            rec.filing_status = chosen_status
            records = load_pdf_tax_records()
            records[rec.tax_year] = rec
            with st.spinner("Saving…"):
                save_pdf_tax_records(records)
            # Direct write — user just confirmed; overrides any existing value
            prior_magi: dict[int, float] = dict(st.session_state.get("prior_year_magi") or {})
            prior_magi[rec.tax_year] = rec.magi
            st.session_state["prior_year_magi"] = prior_magi
            # Clear parse cache so a new upload starts fresh
            st.session_state.pop(cache_key, None)
            st.success(
                f"Saved {rec.tax_year} 1040 record "
                f"(MAGI ${rec.magi:,.0f}, {_FILING_STATUS_LABELS.get(chosen_status, chosen_status)}). "
                "Rerunning…"
            )
            st.rerun()


def _render_prior_year_magi_anchor() -> None:
    """Render the Prior-year filed MAGI anchor expander in the Joint sub-tab."""
    base_year: int = 2026
    with st.expander("Prior-year filed MAGI anchor (IRMAA lookback)", expanded=False):
        st.caption(
            "Optional. Enter actual filed MAGI from your tax return. "
            "The engine will use these values instead of projecting MAGI for the "
            "IRMAA 2-year-lookback "
            f"(years {base_year} and {base_year + 1} IRMAA will be anchored to these). "
            "Leave 0 to use projected MAGI."
        )
        prior_magi: dict[int, float] = dict(st.session_state.get("prior_year_magi") or {})

        v1 = st.number_input(
            f"{base_year - 2} filed MAGI",
            min_value=0,
            max_value=2_000_000,
            value=int(prior_magi.get(base_year - 2, 0)),
            step=1_000,
            format="%d",
            help=(
                f"Filed MAGI from your {base_year - 2} tax return. "
                f"Anchors {base_year} IRMAA via the 2-year lookback."
            ),
        )
        v2 = st.number_input(
            f"{base_year - 1} filed MAGI",
            min_value=0,
            max_value=2_000_000,
            value=int(prior_magi.get(base_year - 1, 0)),
            step=1_000,
            format="%d",
            help=(
                f"Filed MAGI from your {base_year - 1} tax return. "
                f"Anchors {base_year + 1} IRMAA via the 2-year lookback."
            ),
        )

        if v1 > 0:
            prior_magi[base_year - 2] = float(v1)
        else:
            prior_magi.pop(base_year - 2, None)

        if v2 > 0:
            prior_magi[base_year - 1] = float(v2)
        else:
            prior_magi.pop(base_year - 1, None)

        st.session_state["prior_year_magi"] = prior_magi


def _render_grants_section(grants: list[EquityGrant]) -> None:
    """Render equity grants as a dataframe, or an info banner when empty."""
    if not grants:
        st.info("No grants loaded.")
        return
    rows = [
        {
            "grant_id": g.grant_id,
            "type": g.grant_type,
            "grant_date": g.grant_date,
            "shares_granted": g.shares_granted,
            "outstanding": g.outstanding,
            "current_value": g.current_value,
        }
        for g in grants
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _render_portfolio_sub_tabs(
    snap: PortfolioSnapshot | None,
) -> None:
    """Render Me / Spouse / All sub-tabs for the Portfolio tab."""
    me_tab, spouse_tab, all_tab = st.tabs(["Me", "Spouse", "All"])

    if snap is None:
        for tab in (me_tab, spouse_tab, all_tab):
            with tab:
                st.info("No accounts loaded — click Sync above.")
        return

    me_accounts = [a for a in snap.accounts if a.owner == "you"]
    spouse_accounts = [a for a in snap.accounts if a.owner == "spouse"]

    with me_tab:
        st.subheader("Accounts")
        _render_accounts_table(me_accounts, show_owner=False)
        st.subheader("Holdings")
        _render_holdings_table(me_accounts)
        st.subheader("Grants")
        _render_grants_section(snap.equity_grants)

    with spouse_tab:
        st.subheader("Accounts")
        _render_accounts_table(spouse_accounts, show_owner=False)
        st.subheader("Holdings")
        _render_holdings_table(spouse_accounts)
        st.subheader("Grants")
        _render_grants_section([])

    with all_tab:
        st.subheader("Accounts")
        _render_accounts_table(snap.accounts, show_owner=True)
        st.subheader("Holdings")
        _render_holdings_table(snap.accounts)
        st.subheader("Grants")
        _render_grants_section(snap.equity_grants)


def _render_account_type_overrides(snap: PortfolioSnapshot | None) -> None:
    """Render the Account Type Overrides expander."""
    from engine.portfolio_sync import (
        _classify_account,
        _resolve_override,
    )

    with st.expander("🏷️ Account Type Overrides"):
        if snap is None or not snap.accounts:
            st.info("No accounts loaded — sync first to see detected acctIds.")
            return

        st.caption("Changes take effect on next sync.")
        _type_options = ["trad_ira", "roth_ira", "brokerage", "hsa", "403b"]
        _owner_options = ["you", "spouse"]
        overrides: dict[str, str | dict[str, str]] = (
            st.session_state.get("account_type_overrides") or {}
        )

        seen: set[str] = set()
        for acct in snap.accounts:
            acct_id = acct.account_name
            if acct_id in seen:
                continue
            seen.add(acct_id)

            auto_type, _ = _classify_account(acct_id)
            existing = overrides.get(acct_id)
            if existing is not None:
                current_type, current_owner = _resolve_override(existing)
                if not current_type:
                    current_type = auto_type
            else:
                current_type, current_owner = auto_type, "you"
            try:
                type_idx = _type_options.index(current_type)
            except ValueError:
                type_idx = 0
            try:
                owner_idx = _owner_options.index(current_owner)
            except ValueError:
                owner_idx = 0

            col_id, col_auto, col_type, col_owner = st.columns([3, 2, 2, 2])
            col_id.text(acct_id)
            col_auto.caption(f"auto: {auto_type}")
            chosen_type = col_type.selectbox(
                "Type",
                _type_options,
                index=type_idx,
                key=f"_override_type_{acct_id}",
                label_visibility="collapsed",
            )
            chosen_owner = col_owner.selectbox(
                "Owner",
                _owner_options,
                index=owner_idx,
                key=f"_override_owner_{acct_id}",
                label_visibility="collapsed",
            )
            # Write through the nested form so owner is persisted alongside type.
            if "account_type_overrides" not in st.session_state:
                st.session_state["account_type_overrides"] = {}
            st.session_state["account_type_overrides"][acct_id] = {
                "type": chosen_type,
                "owner": chosen_owner,
            }


def render(hh: Household) -> None:
    """Render the Setup page — household parameters, sync, and data bridge."""
    st.title("⚙️ Setup")

    tab_params, tab_portfolio, tab_bridge = st.tabs(
        ["📊 Parameters", "💼 Portfolio", "🔗 Data bridge"]
    )

    # ------------------------------------------------------------------
    # TAB 1: Parameters
    # ------------------------------------------------------------------
    with tab_params:
        _synced = bool(st.session_state.get("portfolio_snapshot"))
        me_sub, spouse_sub, joint_sub = st.tabs(["Me", "Spouse", "Joint"])

        with me_sub:
            st.session_state.your_ira = st.number_input(
                "Your Trad IRA" + (" (synced)" if _synced else ""),
                value=st.session_state.your_ira,
                step=50_000,
                format="%d",
                disabled=_synced,
                help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
            )
            st.session_state.your_age = st.number_input(
                "Your Age",
                value=st.session_state.your_age,
                step=1,
                format="%d",
            )
            st.session_state.your_ss_fra = st.number_input(
                "Your SS at FRA 67 ($/mo)",
                value=st.session_state.your_ss_fra,
                step=100,
                format="%d",
            )
            st.session_state.your_ss_start_age = st.number_input(
                "Your SS claim age",
                min_value=62,
                max_value=70,
                value=st.session_state.get("your_ss_start_age", 70),
                step=1,
                format="%d",
            )
            st.session_state.your_rmd_start_age = st.number_input(
                "Your RMD start age",
                min_value=72,
                max_value=75,
                value=st.session_state.get("your_rmd_start_age", 75),
                step=1,
                format="%d",
                help="73 if born 1951-1959 (SECURE 1.0); 75 if born 1960+ (SECURE 2.0)",
            )
            st.session_state.your_fra_age = st.number_input(
                "Your FRA (Full Retirement Age)",
                min_value=65,
                max_value=70,
                value=st.session_state.get("your_fra_age", 67),
                step=1,
                format="%d",
                help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
            )
            st.session_state.your_aca = st.checkbox(
                "You on ACA Marketplace",
                value=st.session_state.your_aca,
                help="Check if you are enrolled in ACA marketplace (not employer plan)",
            )

        with spouse_sub:
            st.session_state.spouse_ira = st.number_input(
                "Spouse Trad IRA" + (" (synced)" if _synced else ""),
                value=st.session_state.spouse_ira,
                step=50_000,
                format="%d",
                disabled=_synced,
                help="Auto-synced from FinExtract (IRA + 403b)" if _synced else None,
            )
            st.session_state.spouse_age = st.number_input(
                "Spouse Age",
                value=st.session_state.spouse_age,
                step=1,
                format="%d",
            )
            st.session_state.spouse_ss_fra = st.number_input(
                "Spouse SS at FRA 67 ($/mo)",
                value=st.session_state.spouse_ss_fra,
                step=100,
                format="%d",
            )
            st.session_state.spouse_ss_start_age = st.number_input(
                "Spouse SS claim age",
                min_value=62,
                max_value=70,
                value=st.session_state.get("spouse_ss_start_age", 70),
                step=1,
                format="%d",
            )
            st.session_state.spouse_rmd_start_age = st.number_input(
                "Spouse RMD start age",
                min_value=72,
                max_value=75,
                value=st.session_state.get("spouse_rmd_start_age", 75),
                step=1,
                format="%d",
                help="73 if born 1951-1959 (SECURE 1.0); 75 if born 1960+ (SECURE 2.0)",
            )
            st.session_state.spouse_fra_age = st.number_input(
                "Spouse FRA (Full Retirement Age)",
                min_value=65,
                max_value=70,
                value=st.session_state.get("spouse_fra_age", 67),
                step=1,
                format="%d",
                help="67 for born 1960+ (SECURE/SS default); 66 or 66+N/12 for earlier cohorts",
            )
            st.session_state.spouse_aca = st.checkbox(
                "Spouse on ACA Marketplace",
                value=st.session_state.spouse_aca,
                help="Check if spouse is enrolled in ACA marketplace",
            )

        with joint_sub:
            st.session_state.growth_rate = st.slider(
                "Growth Rate %", 3.0, 12.0, st.session_state.growth_rate, 0.5
            )
            st.session_state.living_expenses = st.number_input(
                "Annual Living Expenses",
                value=st.session_state.living_expenses,
                step=5_000,
                format="%d",
            )
            st.session_state.txn_price = st.number_input(
                f"{st.session_state.get('_stock_ticker', 'Stock')} Current Price",
                value=st.session_state.txn_price,
                step=5,
                format="%d",
            )
            st.session_state["aca_benchmark_premium_annual"] = st.number_input(
                "ACA Benchmark Premium ($/yr)",
                min_value=0,
                max_value=60_000,
                value=int(st.session_state.get("aca_benchmark_premium_annual", 21_600.0)),
                step=100,
                format="%d",
                help=(
                    "Annual cost of the 2nd-lowest-cost Silver plan in your state/county "
                    "for your age group. Used to calculate ACA subsidy loss from conversions. "
                    "Varies widely by geography — check healthcare.gov for your area."
                ),
            )
            st.session_state["aca_enhanced_subsidies_active"] = st.checkbox(
                "ACA enhanced subsidies active (ARP/IRA-style)",
                value=st.session_state.get("aca_enhanced_subsidies_active", False),
                help=(
                    "Toggle for sensitivity analysis. Default OFF matches current law "
                    "(ARP enhanced subsidies expired Dec 31, 2025). Turn ON to model "
                    "what-if ARP gets extended."
                ),
            )
            st.session_state["medicare_part_b_base_monthly"] = st.number_input(
                "Medicare Part B Base Premium ($/mo)",
                min_value=0.0,
                max_value=1000.0,
                value=float(st.session_state.get("medicare_part_b_base_monthly", 202.90)),
                step=1.0,
                format="%.2f",
                help=(
                    "Standard Medicare Part B monthly premium (CMS-published; $202.90 in 2026). "
                    "IRMAA surcharges are computed on top of this base."
                ),
            )
            _render_prior_year_magi_anchor()
            _render_pdf_1040_import()
            _render_survivor_scenario()
            _render_inherited_iras()

    # ------------------------------------------------------------------
    # TAB 2: Portfolio
    # ------------------------------------------------------------------
    with tab_portfolio:
        snap: PortfolioSnapshot | None = st.session_state.get("portfolio_snapshot")

        # Sync button + status banner — local install only (not available on Pyodide/stlite)
        if is_pyodide():
            st.caption(
                "Live sync from FinExtract requires a local install. "
                "Use the V2 sealed upload widget on the Data bridge tab "
                "to bring data prepared from a local install instead."
            )
        else:
            _sync = st.button(
                "Sync from FinExtract", help="Pull live holdings from ingestion server"
            )
            if snap is not None:
                st.caption(
                    f"Loaded: {len(snap.accounts)} accounts, {len(snap.equity_grants)} grants"
                )

            if _sync:
                snap = fetch_portfolio(
                    account_type_overrides=st.session_state.get("account_type_overrides") or None,
                )
                if snap.server_available:
                    # Push synced balances into session state
                    _your_ira, _spouse_ira = derive_ira_balances(snap)
                    if _your_ira > 0:
                        st.session_state.your_ira = int(_your_ira)
                    if _spouse_ira > 0:
                        st.session_state.spouse_ira = int(_spouse_ira)
                    # Merge dividend history into holdings before saving snapshot
                    div_rollup = fetch_dividends_rollup()
                    if div_rollup.server_available:
                        snap = apply_dividends_rollup(snap, div_rollup)
                    save_snapshot(snap)
                    st.session_state.portfolio_snapshot = snap
                    # Also sync tax return data
                    tax_snap = fetch_tax_return()
                    if tax_snap.server_available:
                        st.session_state.tax_return_snapshot = tax_snap
                        save_tax_snapshot(tax_snap)
                    # A3: MAGI 2-year history from FinExtract (IRMAA lookback anchor)
                    try:
                        plan_year = datetime.now(UTC).year
                        magi_snap = MagiSnapshot(fetched_at=datetime.now(UTC))
                        for offset in (
                            1,
                            2,
                        ):  # batchTaxYear-1 and batchTaxYear-2 (2-year coverage shipped)
                            apply_magi(magi_snap, fetch_magi(plan_year - offset))
                        if magi_snap.prior_year_magi:
                            existing = dict(st.session_state.get("prior_year_magi") or {})
                            # Gap-fill only: do NOT override manual entries
                            for yr, val in magi_snap.prior_year_magi.items():
                                if yr not in existing or not existing[yr]:
                                    existing[yr] = val
                            st.session_state["prior_year_magi"] = existing
                    except Exception:  # noqa: BLE001 — sync is best-effort, never block on MAGI failure
                        pass
                    # Also sync YTD income data
                    ytd_snap = fetch_ytd_snapshot()
                    # Phase: option exercises — prefer cache equity_sales, fall back to /query
                    exercises = fetch_option_exercises_with_cache(snap)
                    if exercises.server_available:
                        ytd_snap = apply_option_exercises(ytd_snap, exercises, hh)
                        if exercises.captured_at:
                            st.session_state["exercises_captured_at"] = exercises.captured_at
                    if ytd_snap.snapshot_date:
                        st.session_state.ytd_snapshot = ytd_snap
                        save_ytd_snapshot(ytd_snap)
                    st.success(
                        f"Synced: {len(snap.accounts)} accounts, "
                        f"{len(snap.equity_grants)} active grants"
                        + (", tax return data" if tax_snap.server_available else "")
                        + (", YTD income" if ytd_snap.snapshot_date else "")
                        + (", dividend history" if div_rollup.server_available else "")
                        + (", option exercises" if exercises.server_available else "")
                    )
                else:
                    st.error(f"Server unavailable: {snap.error}")
                    snap = st.session_state.get("portfolio_snapshot")

        _render_portfolio_sub_tabs(snap)
        _render_account_type_overrides(snap)

    # ------------------------------------------------------------------
    # TAB 3: Data bridge
    # ------------------------------------------------------------------
    with tab_bridge:
        _handle_v2_privkey()
        _handle_personal_uploads()
        _handle_personal_exports()
