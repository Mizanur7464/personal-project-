"""Unit tests for technical indicators."""
from services.indicators import ema, rsi


def test_ema_length():
    prices = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = ema(prices, 3)
    assert len(result) == len(prices)


def test_rsi_range():
    prices = [float(i) for i in range(1, 30)]
    val = rsi(prices, 14)
    assert 0 <= val <= 100


def test_rsi_flat():
    prices = [100.0] * 20
    val = rsi(prices, 14)
    assert val == 100.0
