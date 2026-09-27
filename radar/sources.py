"""Fuentes de datos: Yahoo Finance (real) y un generador sintético (pruebas)."""
from __future__ import annotations

import logging
import time
import zlib

import numpy as np
import pandas as pd

log = logging.getLogger("radar")

INFO_FIELDS = (
    "shortName", "longName", "sector", "industry", "currency", "trailingPE", "forwardPE",
    "trailingEps", "forwardEps", "earningsGrowth", "earningsQuarterlyGrowth", "trailingPegRatio",
    "returnOnEquity", "debtToEquity", "revenueGrowth", "targetMeanPrice", "currentPrice",
    "regularMarketPrice", "numberOfAnalystOpinions", "marketCap", "financialCurrency",
)


def ttm_eps_from_statements(qis: pd.DataFrame | None) -> float | None:
    """Suma del BPA diluido (o básico) de los 4 últimos trimestres publicados."""
    if qis is None or qis.empty:
        return None
    for row in ("Diluted EPS", "Basic EPS"):
        if row in qis.index:
            vals = pd.to_numeric(qis.loc[row], errors="coerce")
            vals = vals[sorted(vals.index, reverse=True)].dropna() if len(vals) else vals
            if len(vals) >= 4:
                return float(vals.iloc[:4].sum())
    return None


class YahooSource:
    """Datos gratuitos de Yahoo vía yfinance (no oficial: puede fallar o limitar)."""

    def __init__(self, chunk: int = 80, pause: float = 1.0):
        import yfinance as yf

        self.yf = yf
        self.chunk = chunk
        self.pause = pause

    def prices(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        for i in range(0, len(tickers), self.chunk):
            batch = tickers[i:i + self.chunk]
            for attempt in range(3):
                try:
                    data = self.yf.download(
                        batch, period="2y", interval="1d", group_by="ticker",
                        auto_adjust=True, threads=True, progress=False,
                    )
                    break
                except Exception as e:
                    log.warning("Lote %d intento %d falló: %s", i, attempt + 1, e)
                    time.sleep(5 * (attempt + 1))
            else:
                continue
            for t in batch:
                try:
                    df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                except KeyError:
                    continue
                df = df.dropna(subset=["Close"])
                if len(df):
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    out[t] = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            time.sleep(self.pause)
        return out

    def info(self, ticker: str) -> dict:
        for attempt in range(3):
            try:
                tk = self.yf.Ticker(ticker)
                raw = tk.info or {}
                out = {k: raw.get(k) for k in INFO_FIELDS}
                try:
                    out["ttmEpsStatements"] = ttm_eps_from_statements(tk.quarterly_income_stmt)
                except Exception as e:
                    log.info("estados %s no disponibles: %s", ticker, e)
                    out["ttmEpsStatements"] = None
                return out
            except Exception as e:
                log.warning("info %s intento %d: %s", ticker, attempt + 1, e)
                time.sleep(3 * (attempt + 1))
        return {}


# --------------------------------------------------------------------------- sintético

def synthetic_ohlcv(n: int, drift: float, vol: float, seed: int, start: float = 100.0,
                    end_date: str = "2026-09-25", vol_boost: float = 1.0) -> pd.DataFrame:
    """Serie diaria reproducible. `drift` = rentabilidad diaria media."""
    rng = np.random.default_rng(seed)
    rets = drift + vol * rng.standard_normal(n)
    close = start * np.exp(np.cumsum(rets))
    spread = np.abs(rng.standard_normal(n)) * vol * close
    idx = pd.bdate_range(end=end_date, periods=n)
    volume = rng.integers(800_000, 1_200_000, n).astype(float)
    volume[-5:] *= vol_boost
    return pd.DataFrame({
        "Open": close * (1 - vol / 4), "High": close + spread, "Low": close - spread,
        "Close": close, "Volume": volume,
    }, index=idx)


class FixtureSource:
    """30 acciones sintéticas con casos conocidos, sin red. Para pruebas y demo."""

    def __init__(self, end_date: str = "2026-09-25"):
        self.end_date = end_date
        self.specs: dict[str, tuple[tuple, dict]] = {}
        base_info = dict(sector="Technology", currency="USD", trailingPE=20.0, forwardPE=17.0,
                         earningsGrowth=0.30, returnOnEquity=0.20, debtToEquity=40.0,
                         revenueGrowth=0.12, numberOfAnalystOpinions=20)
        # Casos nombrados: misma serie alcista (semilla 1, RSI ~62, +11 % en 12 meses)
        # con fundamentales distintos, para aislar el efecto del análisis fundamental.
        up = (0.0012, 0.009, 1.0, 1)
        cases = {
            "SOST": ((0.0012, 0.009, 1.6, 1), dict(shortName="Sostenible SA", earningsGrowth=0.50, trailingPE=18.0, forwardPE=14.0, trailingPegRatio=0.9)),
            "PERX": (up, dict(shortName="Solo PER Corp", earningsGrowth=0.0, trailingPE=45.0, forwardPE=48.0, revenueGrowth=0.01)),
            "LOSS": (up, dict(shortName="Pérdidas Inc", trailingPE=None, trailingEps=-1.2, forwardPE=None, earningsGrowth=None)),
            "BEAR": ((-0.0012, 0.010, 1.0, None), dict(shortName="Bajista AG")),
            "NOFD": (up, {"shortName": "Sin Datos Ltd", "sector": None, "trailingPE": None, "forwardPE": None, "earningsGrowth": None, "returnOnEquity": None, "debtToEquity": None, "revenueGrowth": None}),
        }
        for t, (params, extra) in cases.items():
            self.specs[t] = (params, {**base_info, **extra})
        rng = np.random.default_rng(7)
        sectors = ["Technology", "Healthcare", "Industrials", "Financial Services", "Energy"]
        for k in range(25):
            t = f"SYN{k:02d}"
            drift = float(rng.uniform(-0.001, 0.0018))
            info = {**base_info, "shortName": f"Sintética {k:02d}", "sector": sectors[k % 5],
                    "trailingPE": float(rng.uniform(8, 40)), "forwardPE": float(rng.uniform(8, 40)),
                    "earningsGrowth": float(rng.uniform(-0.2, 0.6)), "targetMeanPrice": None}
            self.specs[t] = ((drift, float(rng.uniform(0.008, 0.02)), 1.0, None), info)
        self.universe = {t: ("US" if i % 2 == 0 else "ES") for i, t in enumerate(self.specs)}
        self.universe["BAD1"] = "US"  # ticker sin datos: debe aparecer en `failed`

    def prices(self, tickers):
        out = {}
        for t in tickers:
            seed = zlib.crc32(t.encode())
            if t in self.specs:
                d, v, vb, fixed_seed = self.specs[t][0]
                out[t] = synthetic_ohlcv(400, d, v, seed=fixed_seed or seed, end_date=self.end_date, vol_boost=vb)
            elif t.startswith("^"):
                out[t] = synthetic_ohlcv(400, 0.0004, 0.008, seed=seed, end_date=self.end_date)
        return out

    def info(self, ticker):
        return dict(self.specs[ticker][1])
