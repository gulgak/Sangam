"""Métricas de una capa del backtest, criterios de fiabilidad y sensibilidad."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import scoring as sc
from . import engine as en

COST = 0.001
TOP_N = 30
SUBPERIODS = [("2016-2019", "2016-10-01", "2019-12-31"), ("2020", "2020-01-01", "2020-12-31"),
              ("2021", "2021-01-01", "2021-12-31"), ("2022", "2022-01-01", "2022-12-31"),
              ("2023-2026", "2023-01-01", "2026-12-31")]


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 5)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _sub(r: np.ndarray, dates: pd.DatetimeIndex, a: str, b: str) -> float | None:
    r = np.asarray(r, float)
    d = dates[: len(r)]
    sel = (d >= pd.Timestamp(a)) & (d <= pd.Timestamp(b))
    if sel.sum() < 4:
        return None
    return float(np.prod(1 + r[sel]) - 1)


def analyze(prep: en.Prepared, ranks, eligible, index_ticker: str | None, full: bool,
            top_n: int = TOP_N, cost: float = COST) -> dict:
    K = len(prep.exec_dates)
    dates = prep.exec_dates
    picks = [[r["j"] for r in rows] for rows in ranks]
    port = en.simulate(picks, prep.px, prep.px_fresh, top_n, cost)
    ew_picks = [list(np.flatnonzero(eligible[k])) for k in range(K)]
    ew = en.simulate(ew_picks, prep.px, prep.px_fresh, None, 0.0)
    idx = en.index_returns(prep.index_px[index_ticker]) if index_ticker in prep.index_px else None

    excess = port["net"] - ew["net"]
    fwd1 = en.forward_returns(prep.px, 1)
    fwd4 = en.forward_returns(prep.px, 4)
    fwd13 = en.forward_returns(prep.px, 13)
    ic4 = en.ic_series(ranks, fwd4)
    ic1 = en.ic_series(ranks, fwd1)
    qs = en.quintiles(ranks, fwd1)
    q_perf = []
    for g in range(qs.shape[1]):
        col = np.nan_to_num(qs[:-1, g])
        q_perf.append(en.perf(col, dates).get("cagr"))

    res = {
        "periods": int(len(port["net"])),
        "start": dates[0].strftime("%Y-%m-%d"), "end": dates[-1].strftime("%Y-%m-%d"),
        "avg_candidates": float(np.mean([len(r) for r in ranks])),
        "avg_universe": float(eligible.sum(axis=1).mean()),
        "portfolio": en.perf(port["net"], dates),
        "portfolio_gross": en.perf(port["gross"], dates),
        "equal_weight": en.perf(ew["net"], dates),
        "index": en.perf(idx, dates) if idx is not None else None,
        "index_ticker": index_ticker,
        "turnover_per_year": float(port["turnover"].mean() * en.PERIODS_PER_YEAR),
        "avg_cash": float(port["cash"].mean()),
        "excess_vs_ew_annual": float(np.mean(excess) * en.PERIODS_PER_YEAR),
        "excess_t_nw": en.newey_west_t(excess, 4),
        "win_rate_vs_ew": float(np.mean(excess > 0)),
        "win_rate_vs_ew_52w": _rolling_win(port["net"], ew["net"], 52),
        "ic4_mean": float(np.nanmean(ic4)), "ic4_pos": float(np.nanmean(ic4[np.isfinite(ic4)] > 0)),
        "ic4_t_nw": en.newey_west_t(ic4, 4),
        "ic1_mean": float(np.nanmean(ic1)),
        "quintile_cagr": q_perf,
        "subperiods": [{"name": n, "portfolio": _sub(port["net"], dates, a, b), "equal_weight": _sub(ew["net"], dates, a, b),
                        "index": _sub(idx, dates, a, b) if idx is not None else None} for n, a, b in SUBPERIODS],
        "curve": {"dates": [d.strftime("%Y-%m-%d") for d in dates[: len(port["net"]) + 1]],
                  "portfolio": _wealth(port["net"]), "equal_weight": _wealth(ew["net"]),
                  "index": _wealth(idx) if idx is not None else None},
    }
    if full:
        lf = en.label_forward(ranks, fwd13)
        diff = lf[sc.LABEL_SUSTAINABLE] - lf[sc.LABEL_UNBACKED]
        res["labels_fwd13"] = {l: {"mean": float(np.nanmean(v)), "n": int(np.isfinite(v).sum())} for l, v in lf.items()}
        res["sust_minus_unbacked_13w"] = {"mean": float(np.nanmean(diff)), "t_nw": en.newey_west_t(diff, 13),
                                          "n": int(np.isfinite(diff).sum())}
        res["fund_coverage_candidates"] = float(np.mean([r["fund"] is not None for rows in ranks for r in rows]) if ranks else 0)
    res["criteria"] = criteria(res, full)
    return _clean(res)


def _wealth(r):
    return [1.0] + list(np.round(np.cumprod(1 + np.asarray(r)), 5))


def _rolling_win(a, b, w):
    wa = np.log1p(a)
    wb = np.log1p(b)
    if len(wa) < w:
        return float("nan")
    ca = np.convolve(wa, np.ones(w), "valid")
    cb = np.convolve(wb, np.ones(w), "valid")
    return float(np.mean(ca > cb))


def criteria(res: dict, full: bool) -> list[dict]:
    """Criterios fijados ANTES de ver resultados (spec aprobada)."""
    p, ew, ix = res["portfolio"], res["equal_weight"], res["index"]
    out = [
        {"id": "beats_ew", "text": "El top 30 bate al universo equiponderado después de costes",
         "value": p["cagr"] - ew["cagr"], "pass": p["cagr"] > ew["cagr"]},
        {"id": "significant", "text": "Exceso de rentabilidad significativo (t Newey-West > 2)",
         "value": res["excess_t_nw"], "pass": bool(res["excess_t_nw"] > 2)},
        {"id": "ic", "text": "IC medio > 0 y positivo en más del 55 % de las semanas",
         "value": res["ic4_mean"], "pass": bool(res["ic4_mean"] > 0 and res["ic4_pos"] > 0.55)},
    ]
    if full:
        d = res["sust_minus_unbacked_13w"]["mean"]
        out.append({"id": "labels", "text": "«Subida sostenible» supera a «Momentum sin respaldo» a 3 meses",
                    "value": d, "pass": bool(d > 0)})
    if ix:
        out.append({"id": "drawdown", "text": "Máxima caída no peor que la del índice + 10 puntos",
                    "value": p["max_dd"] - ix["max_dd"], "pass": bool(p["max_dd"] >= ix["max_dd"] - 0.10)})
    return out


def sensitivity(prep, ranks, eligible, index_ticker, full) -> list[dict]:
    """Solo informativo: no cambia el modelo."""
    out = []
    variants = [("Coste 0,2 %", dict(cost=0.002)), ("Top 20", dict(top_n=20)), ("Top 50", dict(top_n=50))]
    for name, kw in variants:
        r = analyze(prep, ranks, eligible, index_ticker, full, **kw)
        out.append({"name": name, "cagr": r["portfolio"]["cagr"], "ew_cagr": r["equal_weight"]["cagr"],
                    "t": r["excess_t_nw"], "max_dd": r["portfolio"]["max_dd"]})
    if full:
        for name, keep in (("Solo «Subida sostenible»", {sc.LABEL_SUSTAINABLE}),
                           ("Sin «Sobrecomprada» ni «Sin respaldo»", {sc.LABEL_SUSTAINABLE, sc.LABEL_WATCH})):
            rk = [[r for r in rows if r["label"] in keep] for rows in ranks]
            r = analyze(prep, rk, eligible, index_ticker, full)
            out.append({"name": name, "cagr": r["portfolio"]["cagr"], "ew_cagr": r["equal_weight"]["cagr"],
                        "t": r["excess_t_nw"], "max_dd": r["portfolio"]["max_dd"], "avg_cash": r["avg_cash"]})
    return out
