"""render_readonly_input / edit_link_button (redesign PR 2)."""

from streamlit.testing.v1 import AppTest


def _app():
    from views._shared import render_readonly_input

    render_readonly_input(
        "Additional net investment income",
        "$25,000 to $30,000/yr",
        key="nii",
        page="assumptions",
        where="Profile › Assumptions",
    )


def test_shows_value_with_escaped_dollar_and_source():
    at = AppTest.from_function(_app)
    at.run()
    assert not at.exception
    # "$" must be escaped or two of them on a page render as LaTeX.
    assert [m.value for m in at.markdown] == ["**\\$25,000 to \\$30,000/yr**"]
    assert any(c.value == "set in Profile › Assumptions" for c in at.caption)


def test_edit_button_navigates():
    at = AppTest.from_function(_app)
    at.run()
    btn = at.button(key="edit_nii")
    assert btn.label == "Edit in Profile › Assumptions →"
    btn.click().run()
    assert at.session_state["nav_page"] == "assumptions"
