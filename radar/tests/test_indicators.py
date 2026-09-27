import numpy as np
import pandas as pd
import pytest

from radar import indicators as ind

# Ejemplo de RSI(14) de StockCharts ChartSchool ("Relative Strength Index (RSI)"),
# que reproduce el método de Wilder (1978).
SC_CLOSE = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03,
            45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45,
            45.78, 45.35, 44.03, 44.18, 44.22, 44.57, 43.42, 42.66, 43.13]
SC_RSI = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42,
          39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def test_rsi_matches_stockcharts_reference():
    r = ind.rsi(pd.Series(SC_CLOSE), 14)
    assert r.iloc[:14].isna().all()
    np.testing.assert_allclose(r.iloc[14:].to_numpy(), SC_RSI, atol=0.1)


def test_rsi_flat_is_neutral_and_pure_uptrend_is_100():
    assert ind.rsi(pd.Series([10.0] * 30)).iloc[-1] == 50
    assert ind.rsi(pd.Series(np.arange(1, 31, dtype=float))).iloc[-1] == 100


def test_sma_simple():
    s = ind.sma(pd.Series([1.0, 2, 3, 4, 5]), 3)
    assert s.isna().sum() == 2
    assert s.iloc[-1] == pytest.approx(4.0)


def test_macd_on_linear_series_converges_to_7_slope():
    # EMA_n de una recta de pendiente s va (n-1)/2 * s por detrás: MACD -> (25-11)/2 * s = 7s
    slope = 0.5
    line, sig, hist = ind.macd(pd.Series(100 + slope * np.arange(600, dtype=float)))
    assert line.iloc[-1] == pytest.approx(7 * slope, rel=1e-6)
    assert sig.iloc[-1] == pytest.approx(7 * slope, rel=1e-6)
    assert hist.iloc[-1] == pytest.approx(0, abs=1e-6)


def test_adx_strong_trend_and_flat():
    n = 200
    close = pd.Series(100 + np.arange(n, dtype=float))
    assert ind.adx(close + 0.5, close - 0.5, close).iloc[-1] == pytest.approx(100, abs=0.5)
    flat = pd.Series([100.0] * n)
    assert ind.adx(flat + 0.5, flat - 0.5, flat).iloc[-1] == 0


def test_adx_range_on_random_walk():
    rng = np.random.default_rng(1)
    c = pd.Series(100 * np.exp(np.cumsum(0.01 * rng.standard_normal(300))))
    a = ind.adx(c * 1.01, c * 0.99, c).dropna()
    assert ((a >= 0) & (a <= 100)).all()


def test_pct_change_over():
    s = pd.Series([100.0, 110, 121])
    assert ind.pct_change_over(s, 2) == pytest.approx(0.21)
    assert ind.pct_change_over(s, 5) is None
