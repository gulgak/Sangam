"""Composición histórica del S&P 500 reconstruida hacia atrás desde la lista actual y el
historial de cambios de Wikipedia. Reduce (no elimina) el sesgo de supervivencia."""
from __future__ import annotations

import io
import logging

import pandas as pd

log = logging.getLogger("radar")

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

GICS_TO_YAHOO = {"Financials": "Financial Services"}  # único sector con regla propia en scoring


def _norm(t) -> str | None:
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return None
    t = str(t).strip().upper().replace(".", "-")
    return t or None


def _flat(t: pd.DataFrame) -> str:
    return " ".join(" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns).lower()


def _find(tables):
    current = next((t for t in tables if "Symbol" in t.columns and "GICS Sector" in t.columns), None)
    changes = next((t for t in tables if "added" in _flat(t) and "removed" in _flat(t)), None)
    return current, changes


def fetch_tables(user_agent: str = "Mozilla/5.0 (Sangam radar)"):
    """(tabla actual, tabla de cambios o None). Registra las tablas si no encuentra la de cambios."""
    import requests

    h = {"User-Agent": user_agent}
    html = requests.get(URL, headers=h, timeout=30).text
    tables = pd.read_html(io.StringIO(html))
    current, changes = _find(tables)
    if changes is None:
        log.warning("Tabla de cambios no encontrada en la página; tablas: %s",
                    [(t.shape, _flat(t)[:120]) for t in tables])
        api = ("https://en.wikipedia.org/w/api.php?action=parse&page=List_of_S%26P_500_companies"
               "&prop=text&format=json&formatversion=2")
        try:
            html2 = requests.get(api, headers=h, timeout=30).json()["parse"]["text"]
            c2, changes = _find(pd.read_html(io.StringIO(html2)))
            current = current if current is not None else c2
        except Exception as e:
            log.warning("API de Wikipedia: %s", e)
    if current is None:
        raise RuntimeError("No encuentro la tabla de componentes actuales del S&P 500")
    return current, changes


def parse_changes(changes: pd.DataFrame) -> pd.DataFrame:
    """Normaliza la tabla de cambios a columnas date / added / removed."""
    cols = [" ".join(str(x) for x in (c if isinstance(c, tuple) else (c,)) if "Unnamed" not in str(x)).strip()
            for c in changes.columns]
    df = changes.copy()
    df.columns = cols

    def pick(*words):
        for c in cols:
            if all(w.lower() in c.lower() for w in words):
                return c
        raise KeyError(words)

    out = pd.DataFrame({
        "date": pd.to_datetime(df[pick("date")], errors="coerce"),
        "added": df[pick("added", "ticker")].map(_norm),
        "removed": df[pick("removed", "ticker")].map(_norm),
    })
    out = out.dropna(subset=["date"]).astype(object)
    return out.where(out.notna(), None)


def members_at(dates, current: set[str], changes: pd.DataFrame) -> dict[pd.Timestamp, frozenset]:
    """{fecha: miembros}. Un cambio con fecha efectiva > d se deshace para obtener la
    composición en d (se quita lo añadido y se repone lo eliminado)."""
    ch = changes.sort_values("date", ascending=False).reset_index(drop=True)
    members = set(current)
    out = {}
    i = 0
    for d in sorted(pd.to_datetime(list(dates)), reverse=True):
        while i < len(ch) and ch.loc[i, "date"] > d:
            a, r = ch.loc[i, "added"], ch.loc[i, "removed"]
            if isinstance(a, str):
                members.discard(a)
            if isinstance(r, str):
                members.add(r)
            i += 1
        out[d] = frozenset(members)
    return out


def current_members_and_sectors(current: pd.DataFrame) -> tuple[set[str], dict[str, str]]:
    syms = current["Symbol"].map(_norm)
    sectors = {s: GICS_TO_YAHOO.get(g, g) for s, g in zip(syms, current["GICS Sector"])}
    return set(syms.dropna()), sectors


HIST_REPO = "https://api.github.com/repos/fja05680/sp500/contents"


def fetch_historical_components(user_agent: str = "Mozilla/5.0 (Sangam radar)") -> pd.DataFrame | None:
    """Composición diaria histórica del S&P 500 (dataset público fja05680/sp500).
    Devuelve columnas date / tickers (lista) o None si no está disponible."""
    import requests

    import os

    h = {"User-Agent": user_agent}
    if os.environ.get("GITHUB_TOKEN"):  # evita el límite de la API anónima en Actions
        h["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        files = requests.get(HIST_REPO, headers=h, timeout=30).json()
        if not isinstance(files, list):
            log.warning("Dataset histórico: respuesta inesperada de la API: %s", str(files)[:200])
            return None
        cands = sorted(f["name"] for f in files if f["name"].startswith("S&P 500 Historical Components")
                       and f["name"].endswith(".csv"))
        if not cands:
            log.warning("Dataset histórico: no hay CSV de componentes")
            return None
        url = next(f["download_url"] for f in files if f["name"] == cands[-1])
        raw = pd.read_csv(io.StringIO(requests.get(url, headers={"User-Agent": user_agent}, timeout=60).text))
        log.info("Dataset histórico: %s (%d filas)", cands[-1], len(raw))
        return parse_components(raw)
    except Exception as e:
        log.warning("Dataset histórico no disponible: %s", e)
        return None


def parse_components(raw: pd.DataFrame) -> pd.DataFrame:
    date_col = next(c for c in raw.columns if "date" in c.lower())
    tick_col = next(c for c in raw.columns if "ticker" in c.lower())
    out = pd.DataFrame({
        "date": pd.to_datetime(raw[date_col], errors="coerce"),
        "tickers": raw[tick_col].map(lambda s: frozenset(t for t in (_norm(x) for x in str(s).split(",")) if t)),
    }).dropna(subset=["date"]).sort_values("date")
    return out.reset_index(drop=True)


def members_from_components(dates, comp: pd.DataFrame) -> dict[pd.Timestamp, frozenset]:
    """Composición vigente en cada fecha (última fila con fecha <= d)."""
    idx = pd.DatetimeIndex(comp["date"])
    out = {}
    for d in pd.to_datetime(list(dates)):
        i = idx.searchsorted(d, side="right") - 1
        out[d] = comp["tickers"].iloc[i] if i >= 0 else frozenset()
    return out


def ciks_from_current(current: pd.DataFrame) -> dict[str, int]:
    col = next((c for c in current.columns if str(c).strip().upper() == "CIK"), None)
    if col is None:
        return {}
    out = {}
    for sym, cik in zip(current["Symbol"].map(_norm), current[col]):
        try:
            out[sym] = int(cik)
        except (TypeError, ValueError):
            pass
    return out
