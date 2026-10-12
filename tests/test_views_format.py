"""Tests for view-layer formatting helpers."""

from views._format import fmt_dollars, fmt_dollars_short, fmt_pct


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


def test_fmt_dollars_short_default_m_suffix():
    assert fmt_dollars_short(1_500_000) == "$1.5M"


def test_fmt_dollars_short_m_two_decimals():
    assert fmt_dollars_short(1_500_000, decimals=2) == "$1.50M"


def test_fmt_dollars_short_k_explicit():
    assert fmt_dollars_short(250_000, decimals=0, suffix="K") == "$250K"


def test_fmt_dollars_short_auto_m():
    assert fmt_dollars_short(2_300_000, suffix="auto") == "$2.3M"


def test_fmt_dollars_short_auto_k():
    assert fmt_dollars_short(2_300, suffix="auto") == "$2.3K"


def test_fmt_dollars_short_auto_plain():
    assert fmt_dollars_short(523, decimals=0, suffix="auto") == "$523"


def test_fmt_dollars_short_none():
    assert fmt_dollars_short(None) == "$0"


def test_fmt_dollars_short_nan():
    assert fmt_dollars_short(float("nan")) == "$0"


def test_fmt_pct_zero():
    assert fmt_pct(0.0) == "0.0%"


def test_fmt_pct_basic_one_decimal():
    assert fmt_pct(0.0419) == "4.2%"


def test_fmt_pct_no_decimals():
    assert fmt_pct(0.0419, decimals=0) == "4%"


def test_fmt_pct_two_decimals():
    assert fmt_pct(0.0419, decimals=2) == "4.19%"


def test_fmt_pct_positive_with_sign():
    assert fmt_pct(0.05, sign=True) == "+5.0%"


def test_fmt_pct_negative_with_sign():
    assert fmt_pct(-0.05, sign=True) == "-5.0%"


def test_fmt_pct_none_coerces_to_zero():
    assert fmt_pct(None) == "0.0%"


def test_fmt_pct_nan_coerces_to_zero():
    assert fmt_pct(float("nan")) == "0.0%"


def test_fmt_pct_one_renders_as_hundred():
    assert fmt_pct(1.0) == "100.0%"
