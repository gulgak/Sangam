"""Señal rediseñada: momentum 12-1 con rebalanceo mensual y margen de permanencia.

Variantes registradas ANTES de ver resultados (spec aprobada):
- V1: momentum 12-1 = P(t-21) / P(t-252) - 1 (se excluye el último mes).
      Jegadeesh & Titman (1993); Asness, Moskowitz & Pedersen (2013).
- V2: media de los percentiles de V1 y de la cercanía al máximo de 52 semanas.
      George & Hwang (2004).
- V3: V1 + filtro de mercado: si el índice < su media de 200 sesiones, liquidez.
      Faber (2007); Daniel & Moskowitz (2016).
Comunes: entra en el top 30, se mantiene mientras siga en el top 60
(Novy-Marx & Velikov, 2016). Coste 0,1 % por operación.
Elección de variante: mejor Sharpe neto en el periodo de diseño (hasta 2021);
veredicto: una sola evaluación en validación (desde 2022).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import engine as en

log = logging.getLogger("radar")

LOOKBACK = 252
SKIP = 21
ENTRY = 30
EXIT = 60
COST = 0.001
SPLIT = pd.Timestamp("2022-01-01")
PPY = 12
VARIANTS = ("V1", "V2", "V3")
VARIANT_TEXT = {
    "V1": "Momentum 12-1",
    "V2": "Momentum 12-1 + cercanía a máximos de 52 semanas",
    "V3": "Momentum 12-1 + filtro de mercado (índice > media 200)",
}


def month_end_dates(start: str, end: str) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Último viernes de cada mes (señal) y siguiente día hábil (ejecución)."""
    fr = pd.date_range(start, end, freq="W-FRI")
    sig = pd.DatetimeIndex(pd.Series(fr, index=fr).groupby(fr.to_period("M")).max().values)
    return sig, sig + pd.offsets.BDay(1)


def momentum_series(close: pd.Series) -> pd.Series:
    """P(t-SKIP) / P(t-LOOKBACK) - 1 por sesiones de la propia acción (causal)."""
    c = close.dropna()
    return (c.shift(SKIP) / c.shift(LOOKBACK) - 1).where(c.shift(LOOKBACK) > 0)


def proximity_series(close: pd.Series) -> pd.Series:
    """Precio / máximo de cierre de 252 sesiones (1 = en máximos)."""
    c = close.dropna()
    hi = c.rolling(LOOKBACK, min_periods=LOOKBACK).max()
    return c / hi


def market_on(index_close: pd.Series, dates: pd.DatetimeIndex) -> np.ndarray:
    """True si el índice está por encima de su SMA200 al cierre de cada fecha (asof)."""
    c = index_close.dropna()
    ok = (c > c.rolling(200, min_periods=200).mean()).astype(float).where(c.rolling(200).count() >= 200)
    vals, fresh = en._asof(c.index, ok.to_numpy(), dates, en.STALE_DAYS)
    return np.where(fresh & np.isfinite(vals), vals > 0.5, True)  # sin dato -> invertido


@dataclass
class MomPrepared:
    tickers: list[str]
    signal_dates: pd.DatetimeIndex
    exec_dates: pd.DatetimeIndex
    mom: np.ndarray          # [fecha, ticker]
    prox: np.ndarray
    valid: np.ndarray        # dato reciente y con historia suficiente
    px: np.ndarray           # [ejecución, ticker] cierre ajustado
    px_fresh: np.ndarray
    index_px: dict


def prepare(prices: dict[str, pd.DataFrame], signal_dates, exec_dates, index_tickers=()) -> MomPrepared:
    sd, ed = pd.DatetimeIndex(signal_dates), pd.DatetimeIndex(exec_dates)
    tickers = sorted(t for t in prices if t not in index_tickers and not t.startswith("^"))
    n, m, k = len(sd), len(tickers), len(ed)
    mom = np.full((n, m), np.nan)
    prox = np.full((n, m), np.nan)
    valid = np.zeros((n, m), bool)
    px = np.full((k, m), np.nan)
    px_fresh = np.zeros((k, m), bool)
    for j, t in enumerate(tickers):
        df = prices[t]
        if df is None or df.dropna(subset=["Close"]).empty:
            continue
        c = en._adjusted(df)["Close"].dropna()
        mo, f1 = en._asof(c.index, momentum_series(c).to_numpy(), sd, en.STALE_DAYS)
        pr, _ = en._asof(c.index, proximity_series(c).to_numpy(), sd, en.STALE_DAYS)
        mom[:, j], prox[:, j] = mo, pr
        valid[:, j] = f1 & np.isfinite(mo)
        px[:, j], px_fresh[:, j] = en._asof(c.index, c.to_numpy(), ed, en.STALE_DAYS)
    idx = {}
    for t in index_tickers:
        if t in prices and prices[t] is not None:
            c = en._adjusted(prices[t])["Close"].dropna()
            idx[t] = {"close": c, "px": en._asof(c.index, c.to_numpy(), ed, en.STALE_DAYS)[0]}
    return MomPrepared(tickers, sd, ed, mom, prox, valid, px, px_fresh, idx)


def _pct_rank(x: np.ndarray) -> np.ndarray:
    s = pd.Series(x)
    return s.rank(pct=True).to_numpy()


def scores(prep: MomPrepared, eligible: np.ndarray, variant: str) -> np.ndarray:
    """[fecha, ticker] puntuación (mayor = mejor); NaN si no elegible."""
    out = np.full(prep.mom.shape, np.nan)
    for i in range(len(prep.signal_dates)):
        e = eligible[i] & np.isfinite(prep.mom[i])
        if variant == "V2":
            e &= np.isfinite(prep.prox[i])
            if e.sum():
                out[i, e] = (_pct_rank(prep.mom[i, e]) + _pct_rank(prep.prox[i, e])) / 2
        else:
            out[i, e] = prep.mom[i, e]
    return out


def select_with_buffer(score: np.ndarray, entry: int = ENTRY, exit_: int = EXIT,
                       invest: np.ndarray | None = None) -> list[list[int]]:
    """Cartera en cada fecha: se mantienen las posiciones que sigan en el top `exit_`
    y se completan hasta `entry` con las mejores que no están. `invest[i]=False` -> liquidez."""
    held: list[int] = []
    out = []
    for i in range(score.shape[0]):
        if invest is not None and not invest[i]:
            held = []
            out.append([])
            continue
        s = score[i]
        ok = np.flatnonzero(np.isfinite(s))
        order = ok[np.argsort(-s[ok], kind="stable")]
        rank = {j: r for r, j in enumerate(order)}
        keep = [j for j in held if j in rank and rank[j] < exit_]
        fill = [j for j in order if j not in keep][: max(0, entry - len(keep))]
        held = keep + fill
        out.append(list(held))
    return out


def run_variant(prep: MomPrepared, eligible: np.ndarray, variant: str, index_ticker: str) -> dict:
    sc = scores(prep, eligible, variant)
    invest = None
    if variant == "V3" and index_ticker in prep.index_px:
        invest = market_on(prep.index_px[index_ticker]["close"], prep.signal_dates)
    picks = select_with_buffer(sc, invest=invest)
    sim = en.simulate(picks, prep.px, prep.px_fresh, ENTRY, COST)
    return {"score": sc, "picks": picks, "sim": sim, "invest": invest}


def choose_variant(design_net: dict[str, np.ndarray], design_dates: pd.DatetimeIndex) -> tuple[str, dict]:
    """Mejor Sharpe neto en el periodo de diseño. Solo recibe datos de diseño."""
    sharpes = {v: en.perf(r, design_dates, PPY).get("sharpe", float("nan")) for v, r in design_net.items()}
    best = max(sharpes, key=lambda v: (np.nan_to_num(sharpes[v], nan=-1e9), -VARIANTS.index(v)))
    return best, sharpes


def ic_monthly(sc: np.ndarray, px: np.ndarray) -> np.ndarray:
    fwd = en.forward_returns(px, 1)
    out = np.full(sc.shape[0], np.nan)
    for i in range(min(sc.shape[0], fwd.shape[0])):
        out[i] = en.spearman(sc[i], fwd[i])
    return out


def evaluate(prep: MomPrepared, eligible: np.ndarray, index_ticker: str) -> dict:
    K = len(prep.exec_dates)
    ew = en.simulate([list(np.flatnonzero(eligible[k])) for k in range(K)], prep.px, prep.px_fresh, None, 0.0)
    idx_r = en.index_returns(prep.index_px[index_ticker]["px"]) if index_ticker in prep.index_px else None
    # Diseño: periodos que TERMINAN antes del corte (ningún precio de 2022).
    # Validación: periodos cuya señal es del corte en adelante. El de transición queda fuera.
    design = np.asarray(prep.exec_dates[1:K] < SPLIT)
    valid = np.asarray(prep.signal_dates[: K - 1] >= SPLIT)
    nd, nv = int(design.sum()), int(valid.sum())
    d_dates = prep.exec_dates[: nd + 1]
    v_dates = prep.exec_dates[K - 1 - nv:]

    runs = {v: run_variant(prep, eligible, v, index_ticker) for v in VARIANTS}
    chosen, design_sharpes = choose_variant({v: r["sim"]["net"][design] for v, r in runs.items()}, d_dates)
    chosen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("Variante elegida con el periodo de diseño: %s (Sharpe %s) a las %s; ahora se evalúa la validación",
             chosen, {k: round(v, 3) for k, v in design_sharpes.items()}, chosen_at)

    def block(mask, dates, v):
        r = runs[v]
        net, ex = r["sim"]["net"][mask], r["sim"]["net"][mask] - ew["net"][mask]
        ic = ic_monthly(r["score"], prep.px)[: K - 1][mask]
        return {
            "portfolio": en.perf(net, dates, PPY),
            "portfolio_gross": en.perf(r["sim"]["gross"][mask], dates, PPY),
            "equal_weight": en.perf(ew["net"][mask], dates, PPY),
            "index": en.perf(idx_r[mask], dates, PPY) if idx_r is not None else None,
            "excess_annual": float(np.mean(ex) * PPY),
            "excess_t_nw": en.newey_west_t(ex, 3),
            "ic_mean": float(np.nanmean(ic)) if np.isfinite(ic).any() else float("nan"),
            "ic_pos": float(np.mean(ic[np.isfinite(ic)] > 0)) if np.isfinite(ic).any() else float("nan"),
            "turnover_per_year": float(r["sim"]["turnover"][mask].mean() * PPY),
            "avg_cash": float(r["sim"]["cash"][mask].mean()),
            "months": int(mask.sum()),
        }

    res = {
        "chosen": chosen, "chosen_text": VARIANT_TEXT[chosen], "chosen_at": chosen_at,
        "design_sharpe": design_sharpes,
        "split": SPLIT.strftime("%Y-%m-%d"),
        "design": {v: block(design, d_dates, v) for v in VARIANTS},
        "validation": {v: block(valid, v_dates, v) for v in VARIANTS},
    }
    val = res["validation"][chosen]
    p, e, ix = val["portfolio"], val["equal_weight"], val["index"]
    res["criteria"] = [
        {"id": "beats_ew", "text": "Bate al universo equiponderado después de costes",
         "value": p["cagr"] - e["cagr"], "pass": bool(p["cagr"] > e["cagr"])},
        {"id": "significant", "text": "Exceso de rentabilidad significativo (t Newey-West > 2)",
         "value": val["excess_t_nw"], "pass": bool(val["excess_t_nw"] > 2)},
        {"id": "ic", "text": "IC medio > 0 y positivo en más del 55 % de los meses",
         "value": val["ic_mean"], "pass": bool(val["ic_mean"] > 0 and val["ic_pos"] > 0.55)},
    ]
    if ix:
        res["criteria"].append({"id": "drawdown", "text": "Máxima caída no peor que la del índice + 10 puntos",
                                "value": p["max_dd"] - ix["max_dd"], "pass": bool(p["max_dd"] >= ix["max_dd"] - 0.10)})
    res["curve"] = {
        "dates": [d.strftime("%Y-%m-%d") for d in prep.exec_dates[: K]],
        "portfolio": [1.0] + list(np.cumprod(1 + runs[chosen]["sim"]["net"])),
        "equal_weight": [1.0] + list(np.cumprod(1 + ew["net"])),
        "index": [1.0] + list(np.cumprod(1 + idx_r)) if idx_r is not None else None,
    }
    last = runs[chosen]["picks"][-1] if runs[chosen]["picks"] else []
    res["current_portfolio"] = [prep.tickers[j] for j in last]
    return res
