"""audit-1008 Wave W14: Setup-view defects (F-32, F-34, F-65, F-85).

* F-32 — with filing status Single, the spouse widgets render the ZEROED
  ``apply_single_filer`` values and used to write them back into
  ``session_state``, so toggling back to MFJ lost the spouse's age, ACA and
  workplace-plan settings.
* F-34 — merely rendering the Account Type Overrides panel stamped
  ``owner="you"`` (and the name-scan type) on every account as a persisted
  override, which beats the FinExtract owner hint on the next sync.
* F-65 — "Reset to demo" left ``portfolio_kept_newer`` behind; its
  "Use anyway" button then fell back to ``load_snapshot()`` and reloaded the
  personal portfolio cache into the demo session.
* F-85 — the April-1 required-beginning-date rule is IRC §401(a)(9)(C)(i),
  not (C)(ii) (which is the 5%-owner exception).

All figures are synthetic.
"""

from __future__ import annotations

import contextlib
import inspect

from streamlit.testing.v1 import AppTest

# --- F-32: Single-filer render must not overwrite the stored spouse fields ---


def _render_spouse_slice(filing_status: str) -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_household_partial
    from views.setup.parameters import apply_single_filer

    # The user's real (stored) spouse settings, as they were before toggling Single.
    st.session_state.setdefault("filing_status", filing_status)
    st.session_state.setdefault("spouse_age", 58)
    st.session_state.setdefault("spouse_has_workplace_plan", True)
    st.session_state.setdefault("spouse_aca", True)
    hh = Household(
        filing_status=filing_status,
        spouse_age=58,
        spouse_has_workplace_plan=True,
        spouse_aca_enrolled=True,
    )
    # app.get_household() applies the single-filer zeroing to the DERIVED household.
    render_household_partial(apply_single_filer(hh), st, "spouse")


def _render_spouse_toggle_back() -> None:
    import streamlit as st

    from models.household import Household
    from views.setup._partials import render_household_partial
    from views.setup.parameters import apply_single_filer

    st.session_state["filing_status"] = "MFJ"  # the radio's write earlier this run
    st.session_state.setdefault("spouse_age", 58)
    st.session_state.setdefault("spouse_has_workplace_plan", True)
    st.session_state.setdefault("spouse_aca", True)
    stale = Household(
        filing_status="Single",
        spouse_age=58,
        spouse_has_workplace_plan=True,
        spouse_aca_enrolled=True,
    )
    render_household_partial(apply_single_filer(stale), st, "spouse")


class TestF32SingleFilerDoesNotClobberSpouse:
    def test_single_render_keeps_stored_spouse_fields(self, clean_command_center_caches):
        at = AppTest.from_function(_render_spouse_slice, kwargs={"filing_status": "Single"})
        at.run()
        assert not at.exception
        assert at.session_state["spouse_age"] == 58
        assert at.session_state["spouse_has_workplace_plan"] is True
        assert at.session_state["spouse_aca"] is True

    def test_single_render_shows_spouse_widgets_disabled(self, clean_command_center_caches):
        at = AppTest.from_function(_render_spouse_slice, kwargs={"filing_status": "Single"})
        at.run()
        age = next(w for w in at.number_input if w.label == "Spouse Age")
        assert age.disabled

    def test_toggle_back_run_restores_stored_values(self, clean_command_center_caches):
        """The run in which the user flips Single -> MFJ: the radio has already
        written MFJ, but ``hh`` was built (zeroed) at the top of the run."""
        at = AppTest.from_function(_render_spouse_toggle_back)
        at.run()
        assert not at.exception
        assert at.session_state["spouse_age"] == 58
        assert at.session_state["spouse_has_workplace_plan"] is True
        assert at.session_state["spouse_aca"] is True

    def test_mfj_render_still_writes_spouse_fields(self, clean_command_center_caches):
        """Non-regression: the controlled MFJ path keeps writing widget values."""
        at = AppTest.from_function(_render_spouse_slice, kwargs={"filing_status": "MFJ"})
        at.run()
        assert not at.exception
        age = next(w for w in at.number_input if w.label == "Spouse Age")
        age.set_value(61).run()
        assert at.session_state["spouse_age"] == 61


# --- F-34: rendering the overrides panel must not stamp owner/type overrides ---


def _render_overrides(existing: dict | None) -> None:
    import streamlit as st

    from engine.portfolio_sync import AccountSummary, PortfolioSnapshot
    from views.setup._partials._portfolio import _render_account_type_overrides

    if existing is not None:
        st.session_state.setdefault("account_type_overrides", existing)
    snap = PortfolioSnapshot(
        accounts=[
            AccountSummary(
                account_type="trad_ira",
                owner="spouse",
                account_name="Rollover IRA 000111",
                total_value=400_000.0,
            ),
            AccountSummary(
                account_type="trad_ira",
                owner="you",
                account_name="Traditional IRA 000222",
                total_value=600_000.0,
            ),
        ],
        server_available=True,
    )
    _render_account_type_overrides(snap, st)


class TestF34OverridesPanelDoesNotStampOwner:
    def test_render_writes_no_overrides_for_untouched_accounts(self, clean_command_center_caches):
        at = AppTest.from_function(_render_overrides, kwargs={"existing": None})
        at.run()
        assert not at.exception
        # AppTest's SafeSessionState proxy has no .get(); keep the membership test.
        key = "account_type_overrides"
        stored = (at.session_state[key] if key in at.session_state else {}) or {}  # noqa: SIM401
        assert "Rollover IRA 000111" not in stored, (
            "rendering stamped an override on the spouse's account"
        )
        assert "Traditional IRA 000222" not in stored

    def test_owner_selectbox_defaults_to_account_owner(self, clean_command_center_caches):
        at = AppTest.from_function(_render_overrides, kwargs={"existing": None})
        at.run()
        owner_box = next(s for s in at.selectbox if s.key == "_override_owner_Rollover IRA 000111")
        assert owner_box.value == "spouse"

    def test_user_change_is_persisted(self, clean_command_center_caches):
        at = AppTest.from_function(_render_overrides, kwargs={"existing": None})
        at.run()
        owner_box = next(
            s for s in at.selectbox if s.key == "_override_owner_Traditional IRA 000222"
        )
        owner_box.set_value("spouse").run()
        stored = at.session_state["account_type_overrides"]
        assert stored["Traditional IRA 000222"] == {"type": "trad_ira", "owner": "spouse"}

    def test_existing_override_is_kept(self, clean_command_center_caches):
        existing = {"Rollover IRA 000111": {"type": "brokerage", "owner": "spouse"}}
        at = AppTest.from_function(_render_overrides, kwargs={"existing": existing})
        at.run()
        stored = at.session_state["account_type_overrides"]
        assert stored["Rollover IRA 000111"] == {"type": "brokerage", "owner": "spouse"}


# --- F-65: Reset to demo must clear portfolio_kept_newer ---------------------


class TestF65ResetClearsKeptNewer:
    def test_reset_pops_portfolio_kept_newer(self, monkeypatch):
        import views.setup._state as state_mod

        fake_state = {"portfolio_kept_newer": ["PERSONAL-SENTINEL"]}
        monkeypatch.setattr(state_mod.st, "session_state", fake_state)
        monkeypatch.setattr(state_mod, "clear_user_defaults", lambda: None)
        state_mod._clear_personal_session_state()
        assert "portfolio_kept_newer" not in fake_state

    def test_use_anyway_does_not_reload_cache_after_reset(self, monkeypatch):
        """Even if a stale pair survives, "Use anyway" must not pull the on-disk
        personal cache into a session that was reset to demo."""
        import views.setup._partials._portfolio as pmod
        from engine.portfolio_sync import AccountSummary

        kept = AccountSummary(
            account_type="trad_ira", owner="you", account_name="A", as_of="2026-09-30"
        )
        rejected = AccountSummary(
            account_type="trad_ira", owner="you", account_name="A", as_of="2026-08-31"
        )
        state = {
            "portfolio_kept_newer": [(kept, rejected)],
            "_suppress_snapshot_autoload": True,
        }
        loads: list[int] = []

        class _C:
            def warning(self, *_a, **_k):
                return None

            def button(self, *_a, **_k):
                return True

        class _RerunError(Exception):
            pass

        def _rerun():
            raise _RerunError

        monkeypatch.setattr(pmod.st, "session_state", state)
        monkeypatch.setattr(pmod.st, "rerun", _rerun)
        monkeypatch.setattr(pmod, "load_snapshot", lambda: loads.append(1))
        monkeypatch.setattr(pmod, "save_snapshot", lambda _s: None)
        with contextlib.suppress(_RerunError):
            pmod._render_kept_newer(_C())
        assert loads == [], "Use anyway reloaded the personal cache after Reset to demo"


# --- F-85: the April-1 rule is §401(a)(9)(C)(i) ------------------------------


class TestF85DeferFirstRmdCitation:
    def test_help_text_cites_c_i(self):
        from views.setup._partials import _household

        src = inspect.getsource(_household)
        assert "§401(a)(9)(C)(ii)" not in src
        assert src.count("§401(a)(9)(C)(i):") == 2

    def test_model_comment_cites_c_i(self):
        import models.household as hmod

        src = inspect.getsource(hmod)
        assert "§401(a)(9)(C)(ii)" not in src
