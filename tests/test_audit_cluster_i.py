"""Regression tests for cluster I audit findings (F62, F66)."""

import inspect


def test_f62_ira_comment_cites_td9930():
    """F62: IRA divisor table comment must cite IRS T.D. 9930, not SECURE 2.0 as source."""
    import engine.ira as m

    src = inspect.getsource(m)
    assert "T.D. 9930" in src, "engine/ira.py must cite IRS T.D. 9930 as the divisor table source"


def test_f62_ira_comment_not_wrong_age():
    """F62: Comment must not claim table applies from age 72 (SECURE 2.0 start age is 73/75)."""
    import engine.ira as m

    src = inspect.getsource(m)
    # The old wrong comment had "age 72+" — verify it's gone
    assert "age 72+" not in src, "Comment must not claim 'age 72+' as the start age"


def test_f66_aca_comment_not_stale():
    """F66: ACA module must not contain stale pending-legislation language."""
    import engine.aca as m

    src = inspect.getsource(m)
    assert "has not been signed" not in src, "Stale 'has not been signed' language must be removed"
    # Check that OBBBA resolution is documented
    assert "OBBBA" in src, "ACA module should reference OBBBA (P.L. 119-21) resolution"


def test_f66_aca_obbba_did_not_restore():
    """F66: ACA module must document that OBBBA did not restore ARP enhanced subsidies."""
    import engine.aca as m

    src = inspect.getsource(m)
    # The comment should mention OBBBA did not restore them
    assert "did not restore" in src or "not restore" in src, (
        "ACA module must note OBBBA did not restore enhanced subsidies"
    )
