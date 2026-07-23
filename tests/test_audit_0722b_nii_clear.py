"""Regression test for audit-0722b: net_inv_income demo-reset leak.

BUG: the manual NIIT input widget key ``net_inv_income`` (shared by the
ACA+IRMAA and Sweet-Spot pages) was missing from
``_clear_personal_session_state``'s ``keys_to_clear`` list in
views/setup/_state.py, so a personal value survived "Reset to demo" and
kept inflating NIIT in demo mode. Same leak class as the ui-5 / C35 keys
already patched in the same list.
"""


class TestNetInvIncomeClearedOnReset:
    """net_inv_income must be cleared by _clear_personal_session_state."""

    def test_clear_removes_net_inv_income_key(self, monkeypatch):
        import views.setup._state as state_mod

        fake_state: dict = {"net_inv_income": 50000}
        monkeypatch.setattr(state_mod.st, "session_state", fake_state)
        state_mod._clear_personal_session_state()

        assert "net_inv_income" not in fake_state
