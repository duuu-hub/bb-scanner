from pnl_math import linear_return_pct


def test_linear_long_return_pct():
    assert linear_return_pct(100, 110, "LONG") == 10.0
    assert linear_return_pct(100, 96, "LONG") == -4.0


def test_linear_short_return_pct_is_symmetric():
    assert linear_return_pct(100, 90, "SHORT") == 10.0
    assert linear_return_pct(100, 104, "SHORT") == -4.0


def test_linear_return_rejects_invalid_prices_and_side():
    assert linear_return_pct(0, 100, "LONG") is None
    assert linear_return_pct(100, None, "SHORT") is None
    assert linear_return_pct(100, 90, "SIDEWAYS") is None
