"""Motor del backtest: señales semanales con el mismo `scoring` del radar diario,
cartera top-N con costes, referencias y estadísticas.

Cronología (sin datos del futuro):
- Señal con el cierre del viernes d (lo que el radar mostraría el lunes a las 8:00).
- Compra al cierre del siguiente día hábil (lunes) y mantenimiento hasta el lunes siguiente.
- Fundamentales: solo los presentados a la SEC en o antes de d.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import scoring as sc
from . import sec

STALE_DAYS = 7
PERIODS_PER_YEAR = 52


# --------------------------------------------------------------------------- preparación

@dataclass
class Prepared:
    tickers: list[str]
    signal_dates: pd.DatetimeIndex
    exec_dates: pd.DatetimeIndex
    cols: dict[str, np.ndarray]          # métrica -> [fecha, ticker]
    valid: np.ndarray                    # [fecha, ticker] fila técnica válida y reciente
    close_unadj: np.ndarray              # [fecha, ticker] cierre sin ajuste de dividendos (para el PER)
    px: np.ndarray                       # [ejecución, ticker] cierre ajustado (rentabilidad total)
    px_fresh: np.ndarray                 # [ejecución, ticker] hay cotización reciente
    index_px: dict[str, np.ndarray] = field(default_factory=dict)


def weekly_dates(start: str, end: str) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Viernes de señal y lunes (siguiente día hábil) de ejecución."""
    sig = pd.date_range(start, end, freq="W-FRI")
    exe = sig + pd.offsets.BDay(1)
    return sig, exe


def _adjusted(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["Close"])
    if "AdjClose" not in df:
        return df
    f = (df["AdjClose"] / df["Close"]).fillna(1.0)
    out = df.copy()
    for c in ("Open", "High", "Low", "Close"):
        out[c] = df[c] * f
    return out


def _asof(index: pd.DatetimeIndex, values: np.ndarray, when: pd.DatetimeIndex, stale_days: int | None):
    pos = index.searchsorted(when, side="right") - 1
    ok = pos >= 0
    vals = np.full(len(when), np.nan)
    vals[ok] = values[pos[ok]]
    fresh = ok.copy()
    if stale_days is not None:
        age = np.full(len(when), np.inf)
        age[ok] = (when[ok] - index[pos[ok]]).days
        fresh &= age <= stale_days
    return vals, fresh


def prepare(prices: dict[str, pd.DataFrame], bench_of: dict[str, str], signal_dates, exec_dates,
            index_tickers: list[str] = ()) -> Prepared:
    benches = set(bench_of.values()) | set(index_tickers)
    tickers = sorted(t for t in prices if t not in benches and not t.startswith("^"))
    sd, ed = pd.DatetimeIndex(signal_dates), pd.DatetimeIndex(exec_dates)
    n, m, k = len(sd), len(tickers), len(ed)
    cols = {c: np.full((n, m), np.nan) for c in sc.PANEL_COLS}
    valid = np.zeros((n, m), bool)
    close_unadj = np.full((n, m), np.nan)
    px = np.full((k, m), np.nan)
    px_fresh = np.zeros((k, m), bool)
    bench_adj = {b: _adjusted(prices[b])["Close"] for b in set(bench_of.values()) if b in prices}
    for j, t in enumerate(tickers):
        raw = prices[t].dropna(subset=["Close"])
        if raw.empty:
            continue
        adj = _adjusted(raw)
        panel = sc.technical_panel(adj, bench_adj.get(bench_of.get(t, "")))
        pos = panel.index.searchsorted(sd, side="right") - 1
        ok = pos >= 0
        age = np.full(n, np.inf)
        age[ok] = (sd[ok] - panel.index[pos[ok]]).days
        ok &= age <= STALE_DAYS
        for c in sc.PANEL_COLS:
            v = panel[c].to_numpy()
            cols[c][ok, j] = v[pos[ok]]
        valid[:, j] = ok & ~np.isnan(cols["price"][:, j])
        close_unadj[:, j], _ = _asof(raw.index, raw["Close"].to_numpy(), sd, STALE_DAYS)
        px[:, j], px_fresh[:, j] = _asof(adj.index, adj["Close"].to_numpy(), ed, STALE_DAYS)
    idx_px = {}
    for t in index_tickers:
        if t in prices and not prices[t].dropna(subset=["Close"]).empty:
            a = _adjusted(prices[t])
            idx_px[t], _ = _asof(a.index, a["Close"].to_numpy(), ed, STALE_DAYS)
    return Prepared(tickers, sd, ed, cols, valid, close_unadj, px, px_fresh, idx_px)


# --------------------------------------------------------------------------- señales

def signals(prep: Prepared, members: dict | None = None, fundamentals: dict | None = None,
            sectors: dict | None = None, full: bool = False) -> tuple[list[list[dict]], np.ndarray]:
    """Para cada fecha: ranking de candidatas en tendencia (dicts ordenados por nota) y
    la máscara de acciones elegibles (universo de esa fecha con datos)."""
    fundamentals = fundamentals or {}
    sectors = sectors or {}
    n, m = prep.valid.shape
    eligible = prep.valid.copy()
    if members is not None:
        for i, d in enumerate(prep.signal_dates):
            mem = members.get(d, frozenset())
            eligible[i] &= np.array([t in mem for t in prep.tickers])
    C = prep.cols
    with np.errstate(invalid="ignore"):
        # Prefiltro vectorizado equivalente a sc.is_uptrend (se vuelve a comprobar abajo)
        up = (C["price"] > C["sma50"]) & (C["sma50"] > C["sma200"]) & (C["ret_1m"] > 0) & (C["ret_3m"] > 0)
    ranks = []
    for i, d in enumerate(prep.signal_dates):
        rows = []
        for j in np.flatnonzero(eligible[i] & up[i]):
            mt = {c: sc._f(prep.cols[c][i, j]) for c in sc.PANEL_COLS}
            mt["golden_cross_60d"] = bool(mt["golden_cross_60d"])
            if not sc.is_uptrend(mt):
                continue
            t = prep.tickers[j]
            info = {}
            if full:
                snap = sec.snapshot_at(fundamentals.get(t, []), d)
                info = sec.info_from_snapshot(snap, sc._f(prep.close_unadj[i, j]), sectors.get(t))
            rows.append({"j": j, "ticker": t, "m": mt, "f": sc.fundamental_metrics(info, mt["ret_12m"])})
        med = sc.sector_medians([r["f"] for r in rows])
        for r in rows:
            f = r["f"]
            f["pe_sector_median"] = med.get(f["sector"], med.get("*"))
            tech, _, _ = sc.technical_score(r["m"])
            fund, cov, _, _ = sc.fundamental_score(f) if full else (None, 0.0, [], [])
            r.update(tech=tech, fund=fund, cov=cov,
                     score=sc.final_score(tech, fund) if full else tech,
                     label=sc.label(r["m"], f, tech, fund))
            del r["m"], r["f"]
        rows.sort(key=lambda x: (-x["score"], -x["tech"], x["ticker"]))
        ranks.append(rows)
    return ranks, eligible


# --------------------------------------------------------------------------- cartera

def simulate(picks: list[list[int]], px: np.ndarray, px_fresh: np.ndarray, top_n: int | None,
             cost: float) -> dict:
    """Cartera equiponderada rebalanceada en cada ejecución.
    picks[k]: índices de acciones por orden de preferencia en la ejecución k.
    top_n=None: todas las de picks[k] (referencia equiponderada). Con menos candidatas
    que top_n, el resto queda en liquidez (rentabilidad 0)."""
    K = px.shape[0]
    gross, net, turnover, cash, nhold = [], [], [], [], []
    w_prev: dict[int, float] = {}
    for k in range(K - 1):
        chosen = [j for j in picks[k] if px_fresh[k, j] and np.isfinite(px[k, j])]
        if top_n is not None:
            chosen = chosen[:top_n]
        n_slots = top_n if top_n is not None else max(len(chosen), 1)
        w = {j: 1.0 / n_slots for j in chosen}
        names = set(w) | set(w_prev)
        to = sum(abs(w.get(j, 0.0) - w_prev.get(j, 0.0)) for j in names)
        r = {}
        for j in w:
            p1 = px[k + 1, j]
            r[j] = p1 / px[k, j] - 1 if np.isfinite(p1) else 0.0
        g = sum(w[j] * r[j] for j in w)
        c = cost * to
        gross.append(g)
        net.append((1 - c) * (1 + g) - 1)
        turnover.append(to)
        cash.append(1 - sum(w.values()))
        nhold.append(len(w))
        # pesos tras la deriva de la semana
        w_prev = {j: w[j] * (1 + r[j]) / (1 + g) for j in w} if (1 + g) > 0 else {}
    return {"gross": np.array(gross), "net": np.array(net), "turnover": np.array(turnover),
            "cash": np.array(cash), "holdings": np.array(nhold)}


def index_returns(px_index: np.ndarray) -> np.ndarray:
    r = px_index[1:] / px_index[:-1] - 1
    return np.where(np.isfinite(r), r, 0.0)


def forward_returns(px: np.ndarray, h: int) -> np.ndarray:
    out = np.full_like(px, np.nan)
    if h < len(px):
        out[:-h] = px[h:] / px[:-h] - 1
    return out


# --------------------------------------------------------------------------- estadística

def perf(r: np.ndarray, dates: pd.DatetimeIndex, ppy: int = PERIODS_PER_YEAR) -> dict:
    """CAGR sobre fechas reales, volatilidad y Sharpe anualizados (tipo libre = 0), máxima caída.
    `ppy`: periodos por año (52 semanal, 12 mensual)."""
    r = np.asarray(r, float)
    if len(r) == 0:
        return {}
    wealth = np.cumprod(1 + r)
    years = (dates[len(r)] - dates[0]).days / 365.25 if len(dates) > len(r) else len(r) / ppy
    cagr = wealth[-1] ** (1 / years) - 1 if years > 0 and wealth[-1] > 0 else float("nan")
    vol = float(np.std(r, ddof=1) * math.sqrt(ppy)) if len(r) > 1 else float("nan")
    peak = np.maximum.accumulate(np.concatenate([[1.0], wealth]))
    dd = np.concatenate([[1.0], wealth]) / peak - 1
    return {"cagr": float(cagr), "vol": vol,
            "sharpe": float(np.mean(r) * ppy / vol) if vol and vol > 0 else float("nan"),
            "max_dd": float(dd.min()), "total": float(wealth[-1] - 1), "years": float(years)}


def newey_west_t(x: np.ndarray, lags: int) -> float:
    """t de la media con error estándar Newey-West (robusto a autocorrelación)."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return float("nan")
    e = x - x.mean()
    s = e @ e / n
    for L in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 5:
        return float("nan")
    ra = pd.Series(a[ok]).rank().to_numpy()
    rb = pd.Series(b[ok]).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def label_forward(ranks, fwd: np.ndarray) -> dict[str, np.ndarray]:
    """Rentabilidad futura media por etiqueta en cada fecha (NaN si no hay)."""
    labels = [sc.LABEL_SUSTAINABLE, sc.LABEL_WATCH, sc.LABEL_UNBACKED, sc.LABEL_OVERBOUGHT]
    out = {l: np.full(len(ranks), np.nan) for l in labels}
    for k, rows in enumerate(ranks):
        if k >= len(fwd):
            break
        for l in labels:
            v = [fwd[k, r["j"]] for r in rows if r["label"] == l and np.isfinite(fwd[k, r["j"]])]
            if v:
                out[l][k] = float(np.mean(v))
    return out


def quintiles(ranks, fwd1: np.ndarray, q: int = 5) -> np.ndarray:
    """[periodo, quintil] rentabilidad media de la semana siguiente (quintil q-1 = mejor nota)."""
    out = np.full((len(ranks), q), np.nan)
    for k, rows in enumerate(ranks):
        if k >= len(fwd1) or len(rows) < 2 * q:
            continue
        scores = np.array([r["score"] for r in rows])
        rets = np.array([fwd1[k, r["j"]] for r in rows])
        order = np.argsort(scores, kind="stable")
        for g, idx in enumerate(np.array_split(order, q)):
            v = rets[idx]
            v = v[np.isfinite(v)]
            if len(v):
                out[k, g] = v.mean()
    return out


def ic_series(ranks, fwd: np.ndarray) -> np.ndarray:
    out = np.full(len(ranks), np.nan)
    for k, rows in enumerate(ranks):
        if k >= len(fwd) or len(rows) < 10:
            continue
        out[k] = spearman(np.array([r["score"] for r in rows]), np.array([fwd[k, r["j"]] for r in rows]))
    return out
