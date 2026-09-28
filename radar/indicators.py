"""Indicadores técnicos clásicos sobre series de pandas.

Referencias:
- RSI y ADX: J. Welles Wilder Jr., "New Concepts in Technical Trading Systems" (1978).
- MACD: Gerald Appel (1979); parámetros estándar 12/26/9.
- Medias móviles: J. Murphy, "Technical Analysis of the Financial Markets" (1999).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n, min_periods=n).mean()


def ema(close: pd.Series, n: int) -> pd.Series:
    return close.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder_smooth(values: pd.Series, n: int) -> pd.Series:
    """Media de Wilder: la primera es la media simple de n valores, luego
    avg_t = (avg_{t-1} * (n-1) + x_t) / n."""
    out = pd.Series(np.nan, index=values.index, dtype=float)
    arr = values.to_numpy(dtype=float)
    valid = np.flatnonzero(~np.isnan(arr))
    if len(valid) < n:
        return out
    start = valid[0]
    if start + n > len(arr):
        return out
    avg = arr[start:start + n].mean()
    res = out.to_numpy(copy=True)
    res[start + n - 1] = avg
    for i in range(start + n, len(arr)):
        x = arr[i]
        if np.isnan(x):
            x = 0.0
        avg = (avg * (n - 1) + x) / n
        res[i] = avg
    return pd.Series(res, index=values.index)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """RSI de Wilder. Serie plana (sin ganancias ni pérdidas) -> 50 (neutral)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = _wilder_smooth(gain, n)
    avg_loss = _wilder_smooth(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100 - 100 / (1 + rs)
    out = out.where(avg_loss != 0, 100.0)
    flat = (avg_gain == 0) & (avg_loss == 0)
    out = out.where(~flat, 50.0)
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Devuelve (macd, señal, histograma)."""
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """ADX de Wilder. Sin movimiento direccional (DX indefinido) -> 0."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    atr = _wilder_smooth(tr, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100 * _wilder_smooth(plus_dm, n) / atr
        minus_di = 100 * _wilder_smooth(minus_dm, n) / atr
        di_sum = plus_di + minus_di
        dx = 100 * (plus_di - minus_di).abs() / di_sum
    dx = dx.where(di_sum != 0, 0.0).where(atr.notna())
    return _wilder_smooth(dx, n)


def pct_change_over(close: pd.Series, days: int) -> float | None:
    """Rentabilidad simple de los últimos `days` días hábiles."""
    if len(close) <= days:
        return None
    past = close.iloc[-days - 1]
    if not past or np.isnan(past):
        return None
    return float(close.iloc[-1] / past - 1)
