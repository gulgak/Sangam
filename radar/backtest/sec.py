"""Fundamentales históricos 'point-in-time' desde SEC EDGAR (XBRL companyfacts).

Cada dato se usa solo a partir de su fecha de presentación (`filed`) y con el valor
tal como se publicó la primera vez (sin revisiones posteriores).
Documentación: https://www.sec.gov/edgar/sec-api-documentation
"""
from __future__ import annotations

import bisect
import logging
import time

import pandas as pd

log = logging.getLogger("radar")

FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"}
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasic"]
NI_TAGS = ["NetIncomeLoss"]
REV_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax"]
EQ_TAGS = ["StockholdersEquity"]
DEBT_TAGS = ["LongTermDebt", "LongTermDebtNoncurrent"]
MAX_QUARTER_AGE_DAYS = 200   # fundamentales más viejos que esto -> no se usan


def _entries(facts: dict, tags: list[str], unit: str) -> list[tuple[int, dict]]:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = []
    for prio, tag in enumerate(tags):
        for e in gaap.get(tag, {}).get("units", {}).get(unit, []):
            if e.get("form") in FORMS and e.get("filed") and e.get("end") and e.get("val") is not None:
                out.append((prio, e))
    return out


def duration_series(facts: dict, tags: list[str], unit: str = "USD") -> list[tuple]:
    """Trimestres [(start, end, val, filed)] con el primer valor publicado. El 4.º
    trimestre, que casi nunca se publica suelto, se deriva: anual - (T1+T2+T3)."""
    quarters: dict = {}
    annual: dict = {}
    for prio, e in _entries(facts, tags, unit):
        if not e.get("start"):
            continue
        s, en, f = pd.Timestamp(e["start"]), pd.Timestamp(e["end"]), pd.Timestamp(e["filed"])
        days = (en - s).days
        target = quarters if 70 <= days <= 110 else annual if 340 <= days <= 390 else None
        if target is None:
            continue
        key = (s, en)
        cur = target.get(key)
        # prioridad de etiqueta primero; dentro de la misma, la primera publicación
        if cur is None or (prio, f) < (cur[0], cur[2]):
            target[key] = (prio, float(e["val"]), f)
    q = {k: (v[1], v[2]) for k, v in quarters.items()}
    by_end = {k[1]: k for k in q}
    for (s, en), (_, aval, afiled) in annual.items():
        if any(abs((en - e2).days) <= 10 for e2 in by_end):
            continue  # ya hay T4 publicado
        inside = sorted({k for k in q if k[0] >= s - pd.Timedelta(days=10) and k[1] <= en - pd.Timedelta(days=70)},
                        key=lambda k: k[1])
        if len(inside) != 3:
            continue
        q4 = aval - sum(q[k][0] for k in inside)
        filed = max([afiled] + [q[k][1] for k in inside])
        q[(inside[-1][1], en)] = (q4, filed)
    return sorted(((s, e, v, f) for (s, e), (v, f) in q.items()), key=lambda x: x[1])


def instant_series(facts: dict, tags: list[str], unit: str = "USD") -> list[tuple]:
    """Saldos [(end, val, filed)] con el primer valor publicado."""
    best: dict = {}
    for prio, e in _entries(facts, tags, unit):
        if e.get("start"):
            continue
        en, f = pd.Timestamp(e["end"]), pd.Timestamp(e["filed"])
        cur = best.get(en)
        if cur is None or (prio, f) < (cur[0], cur[2]):
            best[en] = (prio, float(e["val"]), f)
    return sorted(((en, v[1], v[2]) for en, v in best.items()), key=lambda x: x[0])


def annual_value(facts: dict, tags: list[str], fy_end_year: int, unit: str) -> float | None:
    """Valor anual (primera publicación) del ejercicio que termina en `fy_end_year`."""
    best = None
    for prio, e in _entries(facts, tags, unit):
        if not e.get("start"):
            continue
        s, en, f = pd.Timestamp(e["start"]), pd.Timestamp(e["end"]), pd.Timestamp(e["filed"])
        if 340 <= (en - s).days <= 390 and en.year == fy_end_year:
            if best is None or (prio, f) < best[:2]:
                best = (prio, f, float(e["val"]))
    return best[2] if best else None


def split_factor_after(splits: pd.Series | None, when: pd.Timestamp) -> float:
    """Producto de los splits posteriores a `when` (para expresar el BPA en acciones de hoy,
    igual que los precios ajustados por splits de Yahoo)."""
    if splits is None or splits.empty:
        return 1.0
    s = splits[(splits.index > when) & (splits > 0)]
    return float(s.prod()) if len(s) else 1.0


def _ttm(quarters: list[tuple]) -> tuple[float | None, pd.Timestamp | None]:
    if len(quarters) < 4:
        return None, None
    last4 = quarters[-4:]
    span = (last4[-1][1] - last4[0][1]).days
    if not 250 <= span <= 300:
        return None, None
    return sum(x[2] for x in last4), last4[-1][1]


def _yoy(quarters: list[tuple]) -> float | None:
    if not quarters:
        return None
    _, end, val, _ = quarters[-1]
    for _, e2, v2, _ in reversed(quarters[:-1]):
        if 350 <= (end - e2).days <= 380:
            return (val - v2) / v2 if v2 > 0 else None
    return None


def build_timeline(facts: dict, splits: pd.Series | None = None) -> list[tuple[pd.Timestamp, dict]]:
    """Lista ordenada [(fecha_presentación, instantánea)] con lo que se sabía ese día."""
    eps = duration_series(facts, EPS_TAGS, "USD/shares")
    eps = [(s, e, v / split_factor_after(splits, f), f) for s, e, v, f in eps]
    ni = duration_series(facts, NI_TAGS)
    rev = duration_series(facts, REV_TAGS)
    eq = instant_series(facts, EQ_TAGS)
    debt = instant_series(facts, DEBT_TAGS)
    events = sorted({x[3] for x in eps + ni + rev} | {x[2] for x in eq + debt})
    out = []
    for F in events:
        k_eps = sorted((x for x in eps if x[3] <= F), key=lambda x: x[1])
        k_ni = sorted((x for x in ni if x[3] <= F), key=lambda x: x[1])
        k_rev = sorted((x for x in rev if x[3] <= F), key=lambda x: x[1])
        k_eq = [x for x in eq if x[2] <= F]
        k_debt = [x for x in debt if x[2] <= F]
        ttm_eps, last_end = _ttm(k_eps)
        ttm_ni, _ = _ttm(k_ni)
        equity = k_eq[-1][1] if k_eq else None
        d = k_debt[-1][1] if k_debt else None
        out.append((F, {
            "ttm_eps": ttm_eps,
            "last_quarter_end": last_end,
            "eps_yoy": _yoy(k_eps),
            "rev_yoy": _yoy(k_rev),
            "roe": ttm_ni / equity if ttm_ni is not None and equity and equity > 0 else None,
            "debt_to_equity": d / equity * 100 if d is not None and equity and equity > 0 else None,
        }))
    return out


def snapshot_at(timeline: list[tuple], when: pd.Timestamp) -> dict | None:
    """Última instantánea presentada en o antes de `when` y no caducada."""
    if not timeline:
        return None
    i = bisect.bisect_right([t for t, _ in timeline], when) - 1
    if i < 0:
        return None
    snap = timeline[i][1]
    lq = snap.get("last_quarter_end")
    if lq is None or (when - lq).days > MAX_QUARTER_AGE_DAYS:
        return None
    return snap


def info_from_snapshot(snap: dict | None, close_unadj: float | None, sector: str | None) -> dict:
    """Traduce una instantánea al formato `info` de Yahoo que usa `scoring`.
    PER forward, PEG de analistas y precio objetivo no existen en histórico: se dejan vacíos."""
    info = {"sector": sector, "currentPrice": close_unadj}
    if not snap:
        return info
    eps = snap["ttm_eps"]
    info.update({
        "trailingEps": eps,
        "trailingPE": close_unadj / eps if eps and eps > 0 and close_unadj else None,
        "earningsGrowth": snap["eps_yoy"],
        "revenueGrowth": snap["rev_yoy"],
        "returnOnEquity": snap["roe"],
        "debtToEquity": snap["debt_to_equity"],
    })
    return info


class SecClient:
    """Cliente mínimo de EDGAR respetando su límite (10 peticiones/s) y su User-Agent."""

    def __init__(self, user_agent: str, pause: float = 0.12, breaker: int = 25):
        import requests

        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.pause = pause
        self.breaker = breaker
        self.ok = 0
        self.fail = 0
        self.status: dict[int, int] = {}
        self.disabled = False

    def _get(self, url: str):
        """403/404 son definitivos (sin reintento). Si las primeras `breaker` peticiones
        fallan todas, el cliente se desactiva para no agotar el tiempo de la ejecución."""
        if self.disabled:
            return None
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=60)
                time.sleep(self.pause)
                self.status[r.status_code] = self.status.get(r.status_code, 0) + 1
                if r.status_code in (403, 404):
                    break
                r.raise_for_status()
                self.ok += 1
                return r.json()
            except Exception as e:
                log.warning("SEC %s intento %d: %s", url, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
        self.fail += 1
        if self.ok == 0 and self.fail >= self.breaker:
            log.error("SEC: %d fallos seguidos sin ningún éxito (%s); se desactiva", self.fail, self.status)
            self.disabled = True
        return None

    def _get_text(self, url: str) -> str | None:
        try:
            r = self.s.get(url, timeout=60)
            time.sleep(self.pause)
            log.info("SEC %s -> %s", url, r.status_code)
            return r.text if r.ok else None
        except Exception as e:
            log.warning("SEC %s: %s", url, e)
            return None

    def ticker_map(self) -> dict[str, int]:
        data = self._get("https://www.sec.gov/files/company_tickers.json") or {}
        out = {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in data.values()}
        if not out:
            txt = self._get_text("https://www.sec.gov/include/ticker.txt") or ""
            for line in txt.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit():
                    out[parts[0].upper().replace(".", "-")] = int(parts[1])
        return out

    def companyfacts(self, cik: int) -> dict | None:
        return self._get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
