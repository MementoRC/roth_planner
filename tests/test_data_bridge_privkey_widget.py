"""Saved-vs-unsaved visibility for the V2 private-key widget.

``views/setup/data_bridge.py::_handle_v2_privkey`` writes
``st.session_state["data_bridge_privkey_b64"]`` only on the Save click. A
password field holding typed-but-unsaved text is visually identical to a
saved one (dots either way), so a user who pastes a key and never clicks
Save believes it is already in effect -- and is then told by the export
section that no key is available.

This pins two things:
  1. When the key IS saved (in session), a confirmation caption appears.
  2. When the input box holds text that does NOT match what is stored, a
     warning caption nudges the user to click Save.
  3. Neither state ever passes the key value itself to any st.* output call.

Uses the same ``AppTest.from_function`` idiom as
``tests/test_data_tab_section_order.py``.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

_SAVED_CAPTION = "Private key saved for this session."
_UNSAVED_WARNING = "Key entered but not saved yet"

_FAKE_PRIVKEY = "not-a-real-key-just-test-text-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _render_with_saved_key() -> None:
    import streamlit as st

    from views.setup.data_bridge import _handle_v2_privkey

    st.session_state["data_bridge_privkey_b64"] = "already-saved-value"
    _handle_v2_privkey()


def _render_with_no_key() -> None:
    import streamlit as st

    from views.setup.data_bridge import _handle_v2_privkey

    st.session_state.pop("data_bridge_privkey_b64", None)
    _handle_v2_privkey()


def test_saved_key_shows_a_confirmation_caption() -> None:
    at = AppTest.from_function(_render_with_saved_key)
    at.run()
    assert not at.exception

    captions = [c.value for c in at.caption]
    assert any(_SAVED_CAPTION in c for c in captions)
    # The unsaved-warning must never appear alongside the saved confirmation.
    assert not any(_UNSAVED_WARNING in c for c in captions)
    # The Save/paste widgets are gone once saved -- only Clear remains.
    assert not at.get("text_input")
    assert {b.key for b in at.button} == {"clear_v2_privkey"}


def test_typed_but_unsaved_key_shows_a_warning_not_a_confirmation() -> None:
    at = AppTest.from_function(_render_with_no_key)
    at.run()
    assert not at.exception

    at.text_input(key="_v2_privkey_input").set_value(_FAKE_PRIVKEY)
    at.run()
    assert not at.exception

    captions = [c.value for c in at.caption]
    assert any(_UNSAVED_WARNING in c for c in captions)
    assert not any(_SAVED_CAPTION in c for c in captions)
    # Save has NOT been clicked -- nothing landed in session_state yet.
    assert "data_bridge_privkey_b64" not in at.session_state


def test_empty_input_shows_neither_confirmation_nor_warning() -> None:
    at = AppTest.from_function(_render_with_no_key)
    at.run()
    assert not at.exception

    captions = [c.value for c in at.caption]
    assert not any(_UNSAVED_WARNING in c for c in captions)
    assert not any(_SAVED_CAPTION in c for c in captions)


def test_private_key_value_never_reaches_any_streamlit_output_call() -> None:
    """The comparison must be code-only -- the value itself must never be
    passed to st.caption/st.write/st.markdown/st.text/st.code/st.error/etc.
    """
    at = AppTest.from_function(_render_with_no_key)
    at.run()
    at.text_input(key="_v2_privkey_input").set_value(_FAKE_PRIVKEY)
    at.run()
    assert not at.exception

    rendered_text = "\n".join(c.value for c in at.caption)
    rendered_text += "\n".join(m.value for m in at.markdown)
    assert _FAKE_PRIVKEY not in rendered_text
