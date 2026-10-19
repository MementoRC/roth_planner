"""RED gate for audit-0823 models-views/M4 — "Reset to demo" leaves personal data behind.

``views/setup/_state.py::_clear_personal_session_state`` implements "Reset to
demo" by popping a HARDCODED list of ``st.session_state`` keys. The list is
incomplete, and has been patched by string-append three times already
(audit-0705 ui-5, audit-0721 C35, audit-0722b ``net_inv_income``) without the
mechanism ever changing. Two classes still leak:

1. STATIC keys that were simply never added -- headed by ``income_events``,
   the key this finding was filed against, and including ``conv_plan_*``,
   the user's real per-year conversion plan.

2. DYNAMIC key families where the account number / row index / field name is
   baked into the key NAME itself (``iira_balance_{idx}``,
   ``_override_type_{acct_id}``, ``attribution_owner_{account_number}``,
   ``trust_{field}`` / ``manual_{field}``, ``yc_``/``sc_``/``qcd_``/``sp_qcd_``).
   No hardcoded list can EVER catch these -- which is why three rounds of
   appending strings did not close the leak. ``views/planner.py:212-214``
   already sweeps four of these families by prefix, so prefix-sweeping is
   established practice in this codebase, not novel design.

Why ``income_events`` matters beyond display -- the materiality chain:
  ``views/ytd_income/_partials/_event_log.py:16`` reads
  ``st.session_state.get("income_events", list(ytd.income_events))``, so the
  SESSION key wins over the freshly-cleared demo snapshot. Those surviving
  events are then summed at ``_manual_entry.py:146-150`` into
  conversions_done / spouse_conversions_done / distributions_done, overlaid at
  ``:156-177`` as ira_conversions_ytd / spouse_ira_conversions_ytd /
  ira_distributions_ytd, and written BACK to ``st.session_state.ytd_snapshot``
  at ``:179`` -- the very key the reset just popped. Those totals then feed
  ``fixed_gross`` in ``engine/scenario_autofill.py:285-293``, changing the
  demo household's conversion room. Real dollars, not cosmetics.

HARNESS NOTE (why not AppTest): the headline test uses a direct session_state
harness rather than ``streamlit.testing.v1.AppTest``. ``_clear_personal_session_state``
is invoked from a Setup-page button while ``income_events`` is written from the
YTD page, so an AppTest proof would have to drive a cross-page navigation
sequence -- brittle, and it would test the router rather than the reset. The
direct harness matches the existing precedent in
``tests/test_audit_0722b_nii_clear.py`` and asserts the REAL read expression
from ``_event_log.py:16`` verbatim, which is the thing that actually leaks.

NOTE (audit-1018 W9): the reset used to delete ``.user_defaults.json``; it now sets
the demo-mode marker instead and leaves every personal file on disk.
"""

import pytest

from models.ytd_income import IncomeEvent, YTDSnapshot, sum_income_events

# --- A + B + C: static personal keys absent from keys_to_clear -------------
# Each entry is (key, write-site) so a failure names the code that creates it.
STATIC_PERSONAL_KEYS = [
    # A -- the filed finding
    ("income_events", "views/ytd_income/_partials/_event_log.py:38,64"),
    # B -- the user's real per-year conversion plan (dict[year -> dollars])
    ("conv_plan_your", "views/planner.py:199,207,219"),
    ("conv_plan_spouse", "views/planner.py:200,208,220"),
    ("conv_plan_qcd", "views/planner.py:201,209,221"),
    ("conv_plan_spouse_qcd", "views/planner.py:202,210,222"),
    # C -- other confirmed static personal keys
    ("_pdf_1040_scanned", "views/_shared.py:119"),
    ("statement_by_account", "views/ytd_income/_partials/_sync_scan.py:178,290"),
    ("koinly_report", "views/ytd_income/_partials/_sync_scan.py:373"),
    ("statement_folder_path", "views/ytd_income/_partials/_sync_scan.py:146"),
    ("ytd_draft", "views/ytd_drafts.py DRAFT_KEY (unsaved manual-entry values)"),
    ("ytd_draft_meta", "views/ytd_drafts.py DRAFT_META_KEY (unsaved as-of/note)"),
    ("ytd_manual_owner", "views/ytd_income/_partials/_manual_entry.py (_select_owner)"),
    ("ytd_manual_owner_choice", "views/ytd_income/_partials/_manual_entry.py (_select_owner)"),
    ("exercises_captured_at", "views/setup/portfolio.py:142"),
    ("_survivor_who_dies", "views/setup/_partials/_assumptions.py:124"),
    ("_survivor_death_year", "views/setup/_partials/_assumptions.py:140"),
    ("_hh_filing_status_choice", "views/setup/_partials/_household.py:74"),
    ("_stock_ticker", "app.py:83"),
    ("_generated_pub_b64", "views/setup/data_bridge.py:140"),
    ("_generated_priv_b64", "views/setup/data_bridge.py:141"),
    ("_export_recipient_pubkey", "views/setup/data_bridge.py:489"),
    ("export_bundle", "views/setup/data_bridge.py:578"),
    ("bundle_upload", "views/setup/data_bridge.py:286"),
    # E -- redesign PR 3c: optimizer result and the two plan editors
    ("_auto_opt_result", "views/optimizer_panel.py RESULT_KEY (the user's grants and plan)"),
    ("_auto_opt_sig", "views/optimizer_panel.py SIG_KEY (grant fingerprint)"),
    ("_auto_opt_flash", "views/optimizer_panel.py FLASH_KEY"),
    ("conv_grid_editor", "views/conversion_state.py GRID_KEY (per-year conversion edits)"),
    ("oe_grid_editor", "views/option_exercise/_partials/_helpers.py _GRID_EDITOR_KEY"),
    ("_oe_shares_state", "views/option_exercise/_partials/_helpers.py _SHARES_STATE_KEY"),
]

# --- D: dynamic key families -- personal data encoded in the key NAME ------
# (sample_key, prefix, write-site). A static list structurally cannot catch these.
DYNAMIC_PERSONAL_KEYS = [
    ("yc_2026", "yc_", "views/conversion_state.py (clear_conversion_widgets)"),
    ("sc_2026", "sc_", "views/conversion_state.py (clear_conversion_widgets)"),
    ("qcd_2026", "qcd_", "views/conversion_state.py (clear_conversion_widgets)"),
    ("sp_qcd_2026", "sp_qcd_", "views/conversion_state.py (clear_conversion_widgets)"),
    ("iira_balance_0", "iira_balance_", "views/setup/_partials/_assumptions.py:170"),
    ("iira_year_0", "iira_year_", "views/setup/_partials/_assumptions.py:182"),
    ("iira_rate_0", "iira_rate_", "views/setup/_partials/_assumptions.py:192"),
    ("iira_owner_0", "iira_owner_", "views/setup/_partials/_assumptions.py:203"),
    ("_override_type_ACCT123", "_override_type_", "views/setup/_partials/_portfolio.py:179"),
    ("_override_owner_ACCT123", "_override_owner_", "views/setup/_partials/_portfolio.py:186"),
    ("attribution_owner_Z9876", "attribution_owner_", "views/setup/command_center.py:90"),
    ("attribution_clear_Z9876", "attribution_clear_", "views/setup/command_center.py:112"),
    (
        "account_type_confirm_X1",
        "account_type_confirm_",
        "views/ytd_income/_partials/_sync_scan.py:311",
    ),
    ("trust_wages_2026", "trust_", "views/setup/_partials/_governance.py:195"),
    ("manual_wages_2026", "manual_", "views/setup/_partials/_governance.py:206"),
    (
        "ytd_w_household_tax_exempt_interest_ytd",
        "ytd_w_",
        "views/ytd_income/_partials/_manual_entry.py (_widget_key)",
    ),
    (
        "ytd_w_spouse_wages_ytd",
        "ytd_w_",
        "views/ytd_income/_partials/_manual_entry.py (_widget_key)",
    ),
    ("ytd_manual_as_of_you", "ytd_manual_as_of_", "views/ytd_income/_partials/_manual_entry.py"),
    ("ytd_manual_note_spouse", "ytd_manual_note_", "views/ytd_income/_partials/_manual_entry.py"),
]

# Keys that must SURVIVE the reset. instance_owner is machine/install identity
# (app.py:84-93, engine.instance_identity, PR #452) -- deliberately NOT household
# data. The rest are pure interface state.
MUST_SURVIVE_KEYS = [
    ("instance_owner", "you"),
    ("instance_owner_change_choice", "you"),
    ("nav_page", "conversions"),
]

# Spot-check that keys ALREADY in keys_to_clear still get cleared -- proves the
# fix does not accidentally drop existing coverage.
ALREADY_CLEARED_KEYS = [
    "ytd_snapshot",
    "portfolio_snapshot",
    "net_inv_income",
    "prior_year_magi",
    "survivor",
]


@pytest.fixture
def run_clear(monkeypatch):
    """Return ``run(seed) -> surviving_state``.

    Monkeypatches ``st.session_state`` to a plain dict (``_clear_personal_session_state``
    only needs ``.pop`` and ``__setitem__``). The demo-mode marker path is
    redirected to tmp by ``tests/conftest.py``.
    """
    import views.setup._state as state_mod

    def run(seed: dict) -> dict:
        fake_state = dict(seed)
        monkeypatch.setattr(state_mod.st, "session_state", fake_state)
        state_mod._clear_personal_session_state()
        return fake_state

    return run


class TestIncomeEventsLeak:
    """Headline: the personal income-event log must not survive Reset to demo."""

    def test_income_events_cleared_and_rederives_to_zero(self, run_clear):
        """A $50,000 personal conversion must not reappear in the demo household.

        Asserts BOTH halves of the leak:
          (a) the session key is gone, and
          (b) the REAL read expression from _event_log.py:16 -- which prefers the
              session key over the demo snapshot -- now re-derives to $0.00.
        """
        personal_event = IncomeEvent(
            date="2026-03-15", amount=50_000.0, kind="conversion", owner="you"
        )
        surviving = run_clear(
            {
                "income_events": [personal_event],
                "ytd_snapshot": YTDSnapshot(tax_year=2026, ira_conversions_ytd=50_000.0),
            }
        )

        assert "income_events" not in surviving, (
            "personal income-event log survived Reset to demo; _event_log.py:16 "
            "prefers this session key over the cleared demo snapshot"
        )

        # Reproduce _event_log.py:16 verbatim against a fresh DEMO snapshot.
        demo_ytd = YTDSnapshot(tax_year=2026)
        events_seen = surviving.get("income_events", list(demo_ytd.income_events))

        # Reproduce _manual_entry.py:148-150 -- what actually reaches ytd_snapshot.
        assert sum_income_events(events_seen, kind="conversion", owner="you") == 0.0
        assert sum_income_events(events_seen, kind="conversion", owner="spouse") == 0.0
        assert sum_income_events(events_seen, kind="distribution") == 0.0


class TestStaticPersonalKeysCleared:
    """Every confirmed static personal key must be popped by the reset."""

    @pytest.mark.parametrize(
        ("key", "write_site"),
        STATIC_PERSONAL_KEYS,
        ids=[k for k, _ in STATIC_PERSONAL_KEYS],
    )
    def test_static_key_cleared(self, run_clear, key, write_site):
        surviving = run_clear({key: "PERSONAL-SENTINEL"})
        assert key not in surviving, f"{key!r} (written at {write_site}) survived Reset to demo"


class TestDynamicPersonalKeyFamiliesCleared:
    """Key families whose NAME embeds personal data -- unreachable by a static list."""

    @pytest.mark.parametrize(
        ("sample_key", "prefix", "write_site"),
        DYNAMIC_PERSONAL_KEYS,
        ids=[p for _, p, _ in DYNAMIC_PERSONAL_KEYS],
    )
    def test_dynamic_key_cleared(self, run_clear, sample_key, prefix, write_site):
        surviving = run_clear({sample_key: "PERSONAL-SENTINEL"})
        assert sample_key not in surviving, (
            f"{sample_key!r} (family {prefix!r}, written at {write_site}) survived "
            "Reset to demo; a hardcoded key list structurally cannot catch this family"
        )


class TestNonRegression:
    """Must pass BOTH before and after the fix -- proves the harness works and
    that the fix does not over-clear."""

    @pytest.mark.parametrize(
        ("key", "value"), MUST_SURVIVE_KEYS, ids=[k for k, _ in MUST_SURVIVE_KEYS]
    )
    def test_out_of_scope_key_survives(self, run_clear, key, value):
        surviving = run_clear({key: value})
        assert surviving.get(key) == value, (
            f"{key!r} must SURVIVE Reset to demo -- it is machine/install identity "
            "or pure interface state, not household data"
        )

    @pytest.mark.parametrize("key", ALREADY_CLEARED_KEYS)
    def test_already_listed_key_still_cleared(self, run_clear, key):
        surviving = run_clear({key: "PERSONAL-SENTINEL"})
        assert key not in surviving

    def test_suppress_snapshot_autoload_is_set(self, run_clear):
        surviving = run_clear({})
        assert surviving.get("_suppress_snapshot_autoload") is True

    def test_seeded_flag_is_popped(self, run_clear):
        surviving = run_clear({"_seeded": True})
        assert "_seeded" not in surviving

    def test_demo_mode_is_entered(self, run_clear):
        """The on-disk complement of the reset is the demo-mode marker (audit-1018 D2)."""
        from engine.demo_mode import is_demo_mode

        run_clear({})
        assert is_demo_mode()
