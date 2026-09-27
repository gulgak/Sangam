"""Cartera experimental de momentum: seguimiento en vivo con dinero ficticio.

Reglas CONGELADAS, idénticas a las validadas en el backtest (radar/backtest/momentum.py):
- Mundo: V1 (momentum 12-1). EE. UU.: V2 (momentum 12-1 + cercanía a máximos de 52 s).
- Señal con el cierre del último viernes de cada mes; compra al cierre del siguiente día hábil.
- Entra en el top 30; se mantiene mientras siga en el top 60; coste 0,1 % por operación.
Si cambia la huella de estas reglas, el experimento se archiva y empieza de nuevo.
NO es una recomendación de inversión.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .backtest import engine as en
from .backtest import momentum as mm

log = logging.getLogger("radar")
TZ = ZoneInfo("Europe/Madrid")
PORTFOLIOS = {
    "mundo": {"variant": "V1", "index": "ACWI", "title": "Mundo · momentum 12-1"},
    "eeuu": {"variant": "V2", "index": "SPY", "title": "EE. UU. · momentum 12-1 + máximos"},
}
INDEX_TICKERS = ("SPY", "ACWI")


def rules_fingerprint() -> str:
    """Huella de las reglas: parámetros + código de las funciones que deciden la cartera."""
    parts = [json.dumps({"lookback": mm.LOOKBACK, "skip": mm.SKIP, "entry": mm.ENTRY, "exit": mm.EXIT,
                         "cost": mm.COST, "portfolios": {k: v["variant"] for k, v in PORTFOLIOS.items()}},
                        sort_keys=True)]
    for fn in (mm.momentum_series, mm.proximity_series, mm.scores, mm.select_with_buffer, mm.month_end_dates):
        parts.append(inspect.getsource(fn))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def signal_dates_between(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    sig, _ = mm.month_end_dates((start - pd.Timedelta(days=40)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    return [d for d in sig if start <= d <= end]


def _adj_close(df: pd.DataFrame) -> pd.Series:
    return en._adjusted(df)["Close"].dropna()


def _price_on(series: pd.Series, when: pd.Timestamp) -> float | None:
    """Cierre en `when` o la última sesión anterior (máx. 7 días)."""
    s = series[series.index <= when]
    if s.empty or (when - s.index[-1]).days > en.STALE_DAYS:
        return None
    return float(s.iloc[-1])


def _value_positions(positions: dict[str, float], base: dict[str, float], closes: dict[str, pd.Series],
                     when: pd.Timestamp) -> float:
    """Valor en `when` de posiciones {ticker: valor en la ejecución} con precio base conocido.
    Encadenar precios ajustados recoge los dividendos. Sin cotización: último precio conocido."""
    total = 0.0
    for t, v0 in positions.items():
        s = closes.get(t)
        p = None
        if s is not None:
            q = s[s.index <= when]
            p = float(q.iloc[-1]) if len(q) else None
        total += v0 * (p / base[t]) if p and base.get(t) else v0
    return total


def select(closes: dict[str, pd.Series], eligible: set[str], variant: str, signal: pd.Timestamp,
           held: list[str]) -> list[str]:
    """Cartera nueva en la fecha de señal con las mismas funciones del backtest."""
    prices = {t: pd.DataFrame({"Close": s}) for t, s in closes.items() if t in eligible}
    sig = pd.DatetimeIndex([signal])
    prep = mm.prepare(prices, sig, sig + pd.offsets.BDay(1))
    sc = mm.scores(prep, prep.valid.copy(), variant)
    idx = {t: j for j, t in enumerate(prep.tickers)}
    picks = mm.select_with_buffer(sc, initial=[idx[t] for t in held if t in idx])
    return [prep.tickers[j] for j in picks[0]]


def new_state(name: str, today: str, fp: str) -> dict:
    cfg = PORTFOLIOS[name]
    return {"name": name, "title": cfg["title"], "variant": cfg["variant"], "variant_text": mm.VARIANT_TEXT[cfg["variant"]],
            "index": cfg["index"], "rules_fp": fp, "created": today, "status": "pending",
            "nav": [], "index_nav": [], "ew_nav": [], "rebalances": [], "positions": {}, "base": {}, "cash": 0.0,
            "ew": {}, "ew_base": {}, "index_pos": None, "archived": []}


def update(state: dict | None, name: str, closes: dict[str, pd.Series], eligible: set[str],
           data_date: pd.Timestamp, today: str) -> dict:
    """Avanza el seguimiento hasta `data_date` (último cierre disponible). Idempotente."""
    fp = rules_fingerprint()
    if state is None:
        state = new_state(name, today, fp)
    elif state.get("rules_fp") != fp:
        log.warning("Cartera %s: las reglas han cambiado; se archiva y empieza de nuevo", name)
        archived = state.get("archived", []) + [{k: state[k] for k in ("created", "rules_fp", "nav", "index_nav",
                                                                        "ew_nav", "rebalances") if k in state}
                                                  | {"ended": today}]
        state = new_state(name, today, fp)
        state["archived"] = archived
    idx_t = state["index"]
    created = pd.Timestamp(state["created"])
    done = {r["signal_date"] for r in state["rebalances"]}
    for sdate in signal_dates_between(created, data_date):
        edate = sdate + pd.offsets.BDay(1)
        key = sdate.strftime("%Y-%m-%d")
        if key in done or edate > data_date:
            continue
        # 1) valor de la cartera y de las referencias en la ejecución, antes de rebalancear
        V = _value_positions(state["positions"], state["base"], closes, edate) + state["cash"] if state["positions"] or state["cash"] else 1.0
        Vew = _value_positions(state["ew"], state["ew_base"], closes, edate) if state["ew"] else 1.0
        # 2) nueva cartera con datos hasta la señal
        cut = {t: s[s.index <= sdate] for t, s in closes.items()}
        held = list(state["positions"])
        new = select(cut, eligible, state["variant"], sdate, held)
        px = {t: _price_on(closes[t], edate) for t in new}
        new = [t for t in new if px[t]]
        w_old = {t: _value_positions({t: v}, state["base"], closes, edate) / V for t, v in state["positions"].items()}
        w_new = {t: 1.0 / mm.ENTRY for t in new}
        turnover = sum(abs(w_new.get(t, 0) - w_old.get(t, 0)) for t in set(w_new) | set(w_old))
        cost = mm.COST * turnover * V
        V2 = V - cost
        state["positions"] = {t: V2 / mm.ENTRY for t in new}
        state["base"] = {t: px[t] for t in new}
        state["cash"] = V2 * (1 - len(new) / mm.ENTRY)
        ew_names = [t for t in sorted(eligible) if t in closes and _price_on(closes[t], edate)
                    and len(cut.get(t, [])) > 0]
        state["ew"] = {t: Vew / len(ew_names) for t in ew_names} if ew_names else {}
        state["ew_base"] = {t: _price_on(closes[t], edate) for t in ew_names}
        if state["index_pos"] is None and idx_t in closes:
            state["index_pos"] = {"value": 1.0, "base": _price_on(closes[idx_t], edate)}
        state["rebalances"].append({
            "signal_date": key, "exec_date": edate.strftime("%Y-%m-%d"),
            "in": sorted(set(new) - set(held)), "out": sorted(set(held) - set(new)),
            "holdings": new, "prices": {t: round(px[t], 4) for t in new},
            "turnover": round(turnover, 4), "cost": round(cost, 6), "value_before": round(V, 6),
        })
        state["status"] = "active"
        log.info("Cartera %s: rebalanceo %s -> %s (%d entradas, %d salidas)", name, key, edate.date(),
                 len(set(new) - set(held)), len(set(held) - set(new)))
    # 3) valoración diaria
    if state["status"] == "active":
        d = data_date.strftime("%Y-%m-%d")
        nav = _value_positions(state["positions"], state["base"], closes, data_date) + state["cash"]
        ew = _value_positions(state["ew"], state["ew_base"], closes, data_date) if state["ew"] else None
        ix = None
        if state["index_pos"] and idx_t in closes:
            p = _price_on(closes[idx_t], data_date)
            ix = state["index_pos"]["value"] * p / state["index_pos"]["base"] if p else None
        for key, val in (("nav", nav), ("ew_nav", ew), ("index_nav", ix)):
            series = [x for x in state[key] if x[0] != d]
            if val is not None:
                series.append([d, round(val, 6)])
            state[key] = sorted(series)
    state["last_update"] = today
    return state


def run(prices: dict[str, pd.DataFrame], sp500: list[str], world: list[str], data_date: str,
        out_dir: Path, today: str | None = None) -> dict:
    """Actualiza las dos carteras y escribe data/radar/paper/<nombre>.json."""
    today = today or datetime.now(TZ).strftime("%Y-%m-%d")
    closes = {t: _adj_close(df) for t, df in prices.items() if df is not None and not df.empty}
    universes = {"eeuu": set(sp500), "mundo": set(sp500) | set(world)}
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    for name in PORTFOLIOS:  # primero se calculan todas; solo se escribe si ninguna falla
        path = out_dir / f"{name}.json"
        state = json.loads(path.read_text()) if path.exists() else None
        result[name] = update(state, name, closes, universes[name], pd.Timestamp(data_date), today)
    for name, state in result.items():
        (out_dir / f"{name}.json").write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                                               encoding="utf-8")
    return result
