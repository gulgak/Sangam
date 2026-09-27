"""Filtro de tendencia, puntuación técnica y fundamental, etiquetas y resumen.

Base metodológica:
- Momentum a 3-12 meses: Jegadeesh & Titman (1993), J. Finance 48(1), 65-91.
- Cercanía al máximo de 52 semanas: George & Hwang (2004), J. Finance 59(5), 2145-2176.
- RSI / ADX: Wilder (1978). MACD: Appel (1979). Medias: Murphy (1999).
- PEG: Lynch (1989), "One Up on Wall Street".
- Descomposición de la rentabilidad: precio = PER x BPA, por lo que
  (1 + r) = (1 + crecimiento BPA) x (1 + expansión del PER).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import indicators as ind

TECH_WEIGHT = 0.5
FUND_WEIGHT = 0.5
MIN_HISTORY = 220          # sesiones mínimas para SMA200 + pendiente
MIN_FUND_COVERAGE = 0.4    # peso mínimo de datos fundamentales disponibles

LABEL_SUSTAINABLE = "Subida sostenible"
LABEL_UNBACKED = "Momentum sin respaldo"
LABEL_OVERBOUGHT = "Sobrecomprada"
LABEL_WATCH = "En observación"


def _n(x: float, nd: int = 1) -> str:
    """Número con coma decimal (es-ES)."""
    return f"{x:.{nd}f}".replace(".", ",")


def _f(x):
    """Convierte a float finito o None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------------- técnico

def technical_metrics(df: pd.DataFrame, bench_close: pd.Series | None) -> dict | None:
    """df con columnas Open, High, Low, Close, Volume indexado por fecha."""
    df = df.dropna(subset=["Close"])
    if len(df) < MIN_HISTORY:
        return None
    close, high, low, vol = df["Close"], df["High"], df["Low"], df["Volume"]
    sma20, sma50, sma200 = ind.sma(close, 20), ind.sma(close, 50), ind.sma(close, 200)
    macd_line, macd_sig, macd_hist = ind.macd(close)
    rsi = ind.rsi(close)
    adx = ind.adx(high, low, close)
    last_252 = close.iloc[-252:]
    high_52w = float(high.iloc[-252:].max())
    vol50 = vol.iloc[-50:].mean()

    m = {
        "price": float(close.iloc[-1]),
        "last_date": df.index[-1].strftime("%Y-%m-%d"),
        "sma20": _f(sma20.iloc[-1]),
        "sma50": _f(sma50.iloc[-1]),
        "sma200": _f(sma200.iloc[-1]),
        "sma200_20d_ago": _f(sma200.iloc[-21]),
        "golden_cross_60d": bool(((sma50 > sma200) & (sma50.shift(1) <= sma200.shift(1))).iloc[-60:].any()),
        "rsi": _f(rsi.iloc[-1]),
        "macd": _f(macd_line.iloc[-1]),
        "macd_signal": _f(macd_sig.iloc[-1]),
        "macd_hist": _f(macd_hist.iloc[-1]),
        "macd_hist_prev": _f(macd_hist.iloc[-2]),
        "adx": _f(adx.iloc[-1]),
        "rvol": _f(vol.iloc[-5:].mean() / vol50) if vol50 and vol50 > 0 else None,
        "high_52w": high_52w,
        "dist_52w_high": _f(close.iloc[-1] / high_52w - 1) if high_52w else None,
        "low_52w": float(last_252.min()),
        "ret_1m": ind.pct_change_over(close, 21),
        "ret_3m": ind.pct_change_over(close, 63),
        "ret_6m": ind.pct_change_over(close, 126),
        "ret_12m": ind.pct_change_over(close, 252),
        "rs_3m": None,
        "rs_6m": None,
    }
    if bench_close is not None:
        b = bench_close.dropna()
        b = b[b.index <= df.index[-1]]
        for key, days in (("rs_3m", 63), ("rs_6m", 126)):
            br = ind.pct_change_over(b, days)
            sr = m["ret_3m" if days == 63 else "ret_6m"]
            if br is not None and sr is not None:
                m[key] = sr - br
    return m


def is_uptrend(m: dict) -> bool:
    """Precio > SMA50 > SMA200 y rentabilidad positiva a 1 y 3 meses."""
    need = (m.get("sma50"), m.get("sma200"), m.get("ret_1m"), m.get("ret_3m"))
    if any(v is None for v in need):
        return False
    return m["price"] > m["sma50"] > m["sma200"] and m["ret_1m"] > 0 and m["ret_3m"] > 0


def technical_score(m: dict) -> tuple[float, list[str], list[str]]:
    pos, neg = [], []
    s = 0.0
    # Tendencia (25)
    if m["price"] > (m["sma50"] or math.inf):
        s += 8
    if (m["sma50"] or 0) > (m["sma200"] or math.inf):
        s += 8
        pos.append("SMA50 por encima de SMA200")
    if m["sma200"] and m["sma200_20d_ago"] and m["sma200"] > m["sma200_20d_ago"]:
        s += 5
    if m["sma20"] and m["price"] > m["sma20"]:
        s += 4
    if m["golden_cross_60d"]:
        pos.append("Cruce dorado reciente (60 sesiones)")
    # RSI (15)
    r = m["rsi"]
    if r is not None:
        if 50 <= r <= 70:
            s += 15
            pos.append(f"RSI {_n(r, 0)}: impulso sano")
        elif 45 <= r < 50 or 70 < r <= 75:
            s += 8
        elif r > 75:
            neg.append(f"RSI {_n(r, 0)}: sobrecompra")
        else:
            neg.append(f"RSI {_n(r, 0)}: impulso débil")
    # MACD (10)
    if m["macd"] is not None and m["macd_signal"] is not None and m["macd"] > m["macd_signal"]:
        s += 6
        pos.append("MACD sobre su señal")
    elif m["macd"] is not None:
        neg.append("MACD bajo su señal")
    if m["macd_hist"] is not None and m["macd_hist_prev"] is not None and m["macd_hist"] > m["macd_hist_prev"]:
        s += 4
    # ADX (10)
    a = m["adx"]
    if a is not None:
        if a > 25:
            s += 10
            pos.append(f"ADX {_n(a, 0)}: tendencia fuerte")
        elif a >= 20:
            s += 6
        else:
            neg.append(f"ADX {_n(a, 0)}: tendencia débil")
    # Volumen relativo (10)
    rv = m["rvol"]
    if rv is not None:
        if rv >= 1.3:
            s += 10
            pos.append(f"Volumen {_n(rv)}x su media")
        elif rv >= 1.0:
            s += 6
        else:
            s += 2
    # Máximo de 52 semanas (15)
    d = m["dist_52w_high"]
    if d is not None:
        if d >= -0.05:
            s += 15
            pos.append("Cerca de máximos de 52 semanas")
        elif d >= -0.10:
            s += 10
        elif d >= -0.20:
            s += 5
    # Fuerza relativa frente a su índice (15)
    beats = [x for x in (m["rs_3m"], m["rs_6m"]) if x is not None and x > 0]
    if len(beats) == 2:
        s += 15
        pos.append("Bate a su índice a 3 y 6 meses")
    elif len(beats) == 1:
        s += 8
    elif m["rs_3m"] is not None:
        neg.append("Peor que su índice")
    return round(s, 1), pos, neg


# --------------------------------------------------------------------------- fundamental

def fundamental_metrics(info: dict, ret_12m: float | None) -> dict:
    tpe = _f(info.get("trailingPE"))
    fpe = _f(info.get("forwardPE"))
    teps = _f(info.get("trailingEps"))
    losing = (teps is not None and teps < 0) or (tpe is not None and tpe < 0)
    if losing:
        tpe = None
    eps_g = _f(info.get("earningsGrowth"))
    if eps_g is None:
        eps_g = _f(info.get("earningsQuarterlyGrowth"))
    peg = _f(info.get("trailingPegRatio"))
    if peg is None and tpe and tpe > 0 and eps_g and eps_g > 0:
        peg = tpe / (eps_g * 100)
    price = _f(info.get("currentPrice")) or _f(info.get("regularMarketPrice"))
    target = _f(info.get("targetMeanPrice"))

    backing = pe_expansion = None
    if ret_12m is not None and eps_g is not None and eps_g > -1:
        pe_expansion = (1 + ret_12m) / (1 + eps_g) - 1
        if ret_12m > 0:
            backing = max(0.0, min(1.0, math.log1p(eps_g) / math.log1p(ret_12m))) if eps_g > 0 else 0.0

    return {
        "loss_making": losing,
        "pe_trailing": tpe,
        "pe_forward": fpe,
        "peg": peg,
        "eps_growth": eps_g,
        "pe_expansion": pe_expansion,
        "backing": backing,
        "roe": _f(info.get("returnOnEquity")),
        "debt_to_equity": _f(info.get("debtToEquity")),
        "revenue_growth": _f(info.get("revenueGrowth")),
        "target_upside": (target / price - 1) if target and price else None,
        "analysts": _f(info.get("numberOfAnalystOpinions")),
        "sector": info.get("sector") or "Desconocido",
        "pe_sector_median": None,
    }


def sector_medians(rows: list[dict], min_group: int = 5) -> dict[str, float]:
    """Mediana del PER trailing positivo por sector; '*' = mediana global."""
    pes = [(r["sector"], r["pe_trailing"]) for r in rows if r.get("pe_trailing") and r["pe_trailing"] > 0]
    out = {}
    if pes:
        out["*"] = float(np.median([p for _, p in pes]))
    by = {}
    for sec, pe in pes:
        by.setdefault(sec, []).append(pe)
    for sec, vals in by.items():
        if len(vals) >= min_group:
            out[sec] = float(np.median(vals))
    return out


def fundamental_score(f: dict) -> tuple[float | None, float, list[str], list[str]]:
    """Devuelve (puntuación 0-100 renormalizada o None, cobertura, pros, contras)."""
    pts, avail = 0.0, 0.0
    pos, neg = [], []
    tpe, fpe = f["pe_trailing"], f["pe_forward"]
    losing = f.get("loss_making", False)

    if losing:
        avail += 15 + 15
        neg.append("Empresa en pérdidas (PER negativo)")
    else:
        # PER forward < PER actual (15)
        if tpe and fpe:
            avail += 15
            if fpe < tpe * 0.95:
                pts += 15
                pos.append(f"PER forward {_n(fpe)} < PER actual {_n(tpe)}: se espera más beneficio")
            elif fpe <= tpe:
                pts += 8
            else:
                neg.append(f"PER forward {_n(fpe)} > actual {_n(tpe)}: se espera menos beneficio")
        # PER frente al sector (15)
        med = f["pe_sector_median"]
        if tpe and med:
            avail += 15
            ratio = tpe / med
            if ratio < 0.8:
                pts += 15
                pos.append(f"PER {_n(tpe)} por debajo de su sector ({_n(med)})")
            elif ratio < 1.0:
                pts += 11
            elif ratio < 1.3:
                pts += 6
            elif ratio < 2.0:
                pts += 2
                neg.append(f"PER {_n(tpe)} caro frente a su sector ({_n(med)})")
            else:
                neg.append(f"PER {_n(tpe)} muy caro frente a su sector ({_n(med)})")
    # PEG (20)
    peg = f["peg"]
    if losing:
        avail += 20
    elif peg is not None and peg > 0:
        avail += 20
        if peg < 1:
            pts += 20
            pos.append(f"PEG {_n(peg, 2)}: crecimiento barato")
        elif peg < 1.5:
            pts += 15
        elif peg < 2:
            pts += 8
        elif peg < 3:
            pts += 3
        else:
            neg.append(f"PEG {_n(peg, 2)}: caro para su crecimiento")
    # Respaldo de la subida por beneficios (25)
    b, eg = f["backing"], f["eps_growth"]
    if b is not None:
        avail += 25
        if b >= 0.999:
            pts += 25
            pos.append(f"Subida respaldada: BPA {eg:+.0%} ≥ subida del precio")
        elif b >= 0.5:
            pts += 15
            pos.append(f"Subida respaldada en {b:.0%} por el BPA ({eg:+.0%})")
        elif b > 0:
            pts += 6
            neg.append(f"Solo el {b:.0%} de la subida viene del BPA: sube sobre todo el PER")
        else:
            neg.append(f"BPA {eg:+.0%}: la subida es solo expansión del PER")
    # ROE (8)
    if f["roe"] is not None:
        avail += 8
        if f["roe"] > 0.15:
            pts += 8
            pos.append(f"ROE {f['roe']:.0%}")
        elif f["roe"] > 0.08:
            pts += 4
    # Deuda/patrimonio (7) — no aplica a banca/seguros
    if f["debt_to_equity"] is not None and f["sector"] != "Financial Services":
        avail += 7
        de = f["debt_to_equity"]  # Yahoo lo da en %
        if de < 50:
            pts += 7
        elif de < 100:
            pts += 4
        elif de < 200:
            pts += 1
        else:
            neg.append(f"Deuda/patrimonio alta ({_n(de / 100)}x)")
    # Crecimiento de ingresos (5)
    if f["revenue_growth"] is not None:
        avail += 5
        if f["revenue_growth"] > 0.10:
            pts += 5
        elif f["revenue_growth"] > 0:
            pts += 3
        else:
            neg.append(f"Ingresos a la baja ({f['revenue_growth']:+.0%})")
    # Potencial según analistas (5)
    if f["target_upside"] is not None:
        avail += 5
        u = f["target_upside"]
        if u > 0.15:
            pts += 5
            pos.append(f"Precio objetivo analistas +{u:.0%}")
        elif u > 0.05:
            pts += 3
        elif u < 0:
            neg.append(f"Ya supera el precio objetivo medio ({u:+.0%})")

    coverage = avail / 100
    if avail == 0 or coverage < MIN_FUND_COVERAGE:
        return None, coverage, pos, neg
    return round(100 * pts / avail, 1), coverage, pos, neg


# --------------------------------------------------------------------------- final

def final_score(tech: float, fund: float | None) -> float:
    """Sin datos fundamentales suficientes se usa 50 (neutral) en esa mitad."""
    return round(TECH_WEIGHT * tech + FUND_WEIGHT * (fund if fund is not None else 50.0), 1)


def label(m: dict, f: dict, tech: float, fund: float | None) -> str:
    rsi = m.get("rsi")
    extended = m.get("sma50") and m["price"] > m["sma50"] * 1.25
    if (rsi is not None and rsi > 75) or extended:
        return LABEL_OVERBOUGHT
    losing = f.get("loss_making", False)
    b = f.get("backing")
    if losing or (b is not None and b < 0.5):
        return LABEL_UNBACKED
    if b is not None and b >= 0.5 and fund is not None and fund >= 55 and tech >= 60:
        return LABEL_SUSTAINABLE
    return LABEL_WATCH


def summary(m: dict, f: dict, lab: str) -> str:
    parts = []
    r12 = m.get("ret_12m")
    rsi = m.get("rsi")
    d = m.get("dist_52w_high")
    tech_bits = []
    if rsi is not None:
        tech_bits.append(f"RSI {_n(rsi, 0)}")
    if d is not None:
        tech_bits.append("en máximos" if d >= -0.01 else f"a {abs(d):.0%} de máximos")
    parts.append("Tendencia alcista" + (f" ({', '.join(tech_bits)})" if tech_bits else ""))
    eg, b = f.get("eps_growth"), f.get("backing")
    if r12 is not None and eg is not None:
        if b is not None and b >= 0.5:
            parts.append(f"la subida de {r12:+.0%} en 12 meses la respalda un BPA {eg:+.0%}")
        elif r12 > 0:
            parts.append(f"sube {r12:+.0%} en 12 meses pero el BPA va {eg:+.0%}: pesa la expansión del PER")
    tpe, fpe = f.get("pe_trailing"), f.get("pe_forward")
    if tpe and tpe > 0 and fpe:
        comp = "<" if fpe < tpe else "≥"
        parts.append(f"PER {_n(tpe)} → forward {_n(fpe)} ({comp})")
    elif f.get("loss_making"):
        parts.append("sin beneficios (PER negativo)")
    txt = "; ".join(parts) + "."
    if lab == LABEL_OVERBOUGHT:
        txt += " Ojo: sobrecomprada, mejor esperar retroceso."
    return txt[0].upper() + txt[1:]
