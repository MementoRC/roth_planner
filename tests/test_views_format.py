"""Tests for view-layer formatting helpers."""

from views._format import fmt_dollars


def test_fmt_dollars_zero():
    assert fmt_dollars(0) == "$0"


def test_fmt_dollars_positive_no_decimals():
    assert fmt_dollars(1234) == "$1,234"


def test_fmt_dollars_negative_no_decimals():
    assert fmt_dollars(-1234) == "$-1,234"


def test_fmt_dollars_with_decimals():
    assert fmt_dollars(1234.567, decimals=2) == "$1,234.57"


def test_fmt_dollars_positive_with_sign():
    assert fmt_dollars(500, sign=True) == "$+500"


def test_fmt_dollars_negative_with_sign():
    assert fmt_dollars(-500, sign=True) == "$-500"


def test_fmt_dollars_none():
    assert fmt_dollars(None) == "$0"


def test_fmt_dollars_nan():
    assert fmt_dollars(float("nan")) == "$0"


def test_fmt_dollars_large_with_separators():
    assert fmt_dollars(1_234_567) == "$1,234,567"
