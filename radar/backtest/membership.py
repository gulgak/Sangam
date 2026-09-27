"""Composición histórica del S&P 500 reconstruida hacia atrás desde la lista actual y el
historial de cambios de Wikipedia. Reduce (no elimina) el sesgo de supervivencia."""
from __future__ import annotations

import io

import pandas as pd

URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

GICS_TO_YAHOO = {"Financials": "Financial Services"}  # único sector con regla propia en scoring


def _norm(t) -> str | None:
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return None
    t = str(t).strip().upper().replace(".", "-")
    return t or None


def fetch_tables(user_agent: str = "Mozilla/5.0 (Sangam radar)"):
    import requests

    html = requests.get(URL, headers={"User-Agent": user_agent}, timeout=30).text
    tables = pd.read_html(io.StringIO(html))
    current = next(t for t in tables if "Symbol" in t.columns and "GICS Sector" in t.columns)
    changes = next(t for t in tables if isinstance(t.columns, pd.MultiIndex) or
                   any("Added" in str(c) for c in t.columns))
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
