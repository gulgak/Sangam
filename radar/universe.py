"""Universo de acciones y el índice de referencia de cada mercado."""
from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

log = logging.getLogger("radar")

HERE = Path(__file__).parent

BENCHMARKS = {
    "US": "^GSPC", "ES": "^IBEX", "DE": "^GDAXI", "FR": "^FCHI", "GB": "^FTSE",
    "EU": "^STOXX50E", "NORD": "^STOXX", "CH": "^SSMI", "JP": "^N225", "HK": "^HSI",
    "KR": "^KS11", "TW": "^TWII", "CA": "^GSPTSE", "AU": "^AXJO", "IN": "^NSEI", "BR": "^BVSP",
}

WIKI = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol"),
    "ndx": ("https://en.wikipedia.org/wiki/Nasdaq-100", "Ticker"),
}


def _wiki_tickers(url: str, column: str) -> list[str]:
    import pandas as pd
    import requests

    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Sangam radar)"}, timeout=30).text
    for table in pd.read_html(io.StringIO(html)):
        if column in table.columns:
            ticks = [str(t).strip().replace(".", "-") for t in table[column].dropna()]
            if len(ticks) >= 90:
                return ticks
    raise ValueError(f"No encuentro la columna {column} en {url}")


def load_universe(online: bool = True) -> dict[str, str]:
    """Devuelve {ticker: mercado}. Con `online`, EE. UU. = S&P 500 + Nasdaq-100 de
    Wikipedia; si falla, se usa la lista de respaldo del CSV."""
    rows = list(csv.DictReader(open(HERE / "universe.csv", encoding="utf-8")))
    out = {r["ticker"]: r["market"] for r in rows if r["market"] != "US_FALLBACK"}
    fallback = [r["ticker"] for r in rows if r["market"] == "US_FALLBACK"]
    us: list[str] = []
    if online:
        for name, (url, col) in WIKI.items():
            try:
                us += _wiki_tickers(url, col)
            except Exception as e:  # red o cambio de formato en Wikipedia
                log.warning("Universo %s no disponible (%s); uso respaldo", name, e)
    if not us:
        us = fallback
    for t in us:
        out.setdefault(t, "US")
    return out
