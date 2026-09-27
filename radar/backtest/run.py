"""Backtest de 10 años del Radar de Bolsa (se ejecuta en GitHub Actions).

Capa 1 «Técnica mundial»: universo del radar (fuera de EE. UU., composición actual) +
S&P 500 con su composición histórica; solo puntuación técnica.
Capa 2 «EE. UU. completa»: S&P 500 histórico; técnica + fundamentales de la SEC
publicados en cada fecha (sin PER forward, PEG de analistas ni precio objetivo).

    python -m radar.backtest.run --user-agent "Nombre email@dominio"
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ..universe import BENCHMARKS, HERE
from . import engine as en
from . import membership as ms
from . import report as rp
from . import sec

log = logging.getLogger("radar")
OUT = Path(__file__).resolve().parents[2] / "data" / "radar" / "backtest.json"
START_PRICES = "2015-01-01"
START_SIGNALS = "2016-10-01"
SANITY_TICKERS = ["SPY", "^GSPC", "AAPL", "MSFT", "REP.MC"]
EPS_CHECKS = [("AAPL", 2019), ("MSFT", 2019), ("JPM", 2019)]


def download(tickers: list[str], chunk: int = 60) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    out = {}
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        for attempt in range(3):
            try:
                data = yf.download(batch, start=START_PRICES, interval="1d", group_by="ticker",
                                   auto_adjust=False, actions=True, threads=True, progress=False)
                break
            except Exception as e:
                log.warning("Lote %d intento %d: %s", i, attempt + 1, e)
                time.sleep(5 * (attempt + 1))
        else:
            continue
        for t in batch:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
            except KeyError:
                continue
            df = df.dropna(subset=["Close"])
            if df.empty:
                continue
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df = df.rename(columns={"Adj Close": "AdjClose", "Stock Splits": "Splits"})
            keep = [c for c in ("Open", "High", "Low", "Close", "AdjClose", "Volume", "Splits") if c in df]
            out[t] = df[keep].astype(float)
        log.info("Precios: %d / %d", min(i + chunk, len(tickers)), len(tickers))
        time.sleep(1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-agent", required=True, help="Contacto para la SEC (obligatorio)")
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    t0 = time.time()
    end = a.end or datetime.now(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d")
    sig, exe = en.weekly_dates(START_SIGNALS, end)
    # la última ejecución no puede ser posterior a hoy
    keep = exe <= pd.Timestamp(end)
    sig, exe = sig[keep], exe[keep]

    # Universo
    cur_df, ch_df = ms.fetch_tables()
    current, sectors = ms.current_members_and_sectors(cur_df)
    comp = ms.fetch_historical_components()
    if comp is not None and len(comp):
        members = ms.members_from_components(sig, comp)
        membership_source = "fja05680/sp500 (composición diaria histórica)"
    elif ch_df is not None:
        members = ms.members_at(sig, current, ms.parse_changes(ch_df))
        membership_source = "Wikipedia (historial de cambios)"
    else:
        log.warning("Sin historial de composición: se usa solo la actual (más sesgo)")
        members = {d: frozenset(current) for d in sig}
        membership_source = "solo composición actual"
    us_all = sorted(set().union(*members.values()))
    rows = list(csv.DictReader(open(HERE / "universe.csv", encoding="utf-8")))
    world = {r["ticker"]: r["market"] for r in rows if r["market"] not in ("US", "US_FALLBACK")}
    bench_of = {t: BENCHMARKS[m] for t, m in world.items()}
    bench_of.update({t: "^GSPC" for t in us_all})
    indices = sorted(set(BENCHMARKS.values()) | {"SPY", "ACWI"})
    log.info("S&P 500 histórico: %d tickers; mundo: %d; %d fechas", len(us_all), len(world), len(sig))

    prices = download(us_all + sorted(world) + indices)
    missing_us = [t for t in us_all if t not in prices]
    log.info("Sin precios: %d del S&P histórico (%.0fs)", len(missing_us), time.time() - t0)

    # Fundamentales SEC
    client = sec.SecClient(a.user_agent)
    cik = client.ticker_map()
    wiki_cik = ms.ciks_from_current(cur_df)
    log.info("CIK: %d de la SEC, %d de Wikipedia", len(cik), len(wiki_cik))
    cik = {**wiki_cik, **cik}
    probe = client.companyfacts(320193)  # Apple, para registrar si data.sec.gov responde
    log.info("Prueba data.sec.gov (AAPL): %s", "ok" if probe else "sin respuesta")
    fundamentals, eps_checks = {}, []
    check_facts = {}
    for n, t in enumerate(us_all, 1):
        if t not in cik or t not in prices:
            continue
        facts = client.companyfacts(cik[t])
        if not facts:
            continue
        spl = prices[t].get("Splits")
        spl = spl[spl > 0] if spl is not None else None
        try:
            fundamentals[t] = sec.build_timeline(facts, spl)
        except Exception as e:
            log.warning("timeline %s: %s", t, e)
        if t in {c[0] for c in EPS_CHECKS}:
            check_facts[t] = facts
        if n % 100 == 0:
            log.info("SEC: %d / %d (ok %d, fallos %d, estados %s) %.0fs", n, len(us_all), client.ok, client.fail,
                     client.status, time.time() - t0)
        if client.disabled:
            break
    for t, fy in EPS_CHECKS:
        f = check_facts.get(t)
        eps_checks.append({"ticker": t, "fy": fy,
                           "eps_diluted_as_filed": sec.annual_value(f, sec.EPS_TAGS, fy, "USD/shares") if f else None})

    # Capa 2: EE. UU. completa
    us_prices = {t: prices[t] for t in us_all if t in prices}
    prep_us = en.prepare({**us_prices, **{b: prices[b] for b in ("SPY", "^GSPC") if b in prices}},
                         {t: "^GSPC" for t in us_prices}, sig, exe, index_tickers=["SPY", "^GSPC"])
    ranks_us, elig_us = en.signals(prep_us, members, fundamentals, sectors, full=True)
    layer_us = rp.analyze(prep_us, ranks_us, elig_us, "SPY", full=True)
    layer_us["sensitivity"] = rp.sensitivity(prep_us, ranks_us, elig_us, "SPY", True)
    log.info("Capa EE. UU.: %s (%.0fs)", layer_us["portfolio"], time.time() - t0)

    # Capa 1: técnica mundial
    w_prices = {t: prices[t] for t in list(world) + list(us_prices) if t in prices}
    w_prices.update({b: prices[b] for b in set(bench_of.values()) | {"ACWI"} if b in prices})
    w_members = {d: frozenset(set(world) | set(members[d])) for d in sig}
    prep_w = en.prepare(w_prices, bench_of, sig, exe, index_tickers=["ACWI"])
    ranks_w, elig_w = en.signals(prep_w, w_members, full=False)
    layer_w = rp.analyze(prep_w, ranks_w, elig_w, "ACWI", full=False)
    layer_w["sensitivity"] = rp.sensitivity(prep_w, ranks_w, elig_w, "ACWI", False)
    log.info("Capa mundial: %s", layer_w["portfolio"])

    # Comprobaciones contra la realidad
    sanity = []
    for t in SANITY_TICKERS:
        if t in prices:
            s = prices[t]
            s = s[(s.index >= exe[0]) & (s.index <= exe[-1])]
            if len(s) > 2:
                yrs = (s.index[-1] - s.index[0]).days / 365.25
                sanity.append({"ticker": t, "from": s.index[0].strftime("%Y-%m-%d"), "to": s.index[-1].strftime("%Y-%m-%d"),
                               "price_cagr": (s["Close"].iloc[-1] / s["Close"].iloc[0]) ** (1 / yrs) - 1,
                               "total_return_cagr": (s["AdjClose"].iloc[-1] / s["AdjClose"].iloc[0]) ** (1 / yrs) - 1})

    result = rp._clean({
        "version": 1,
        "generated_at": datetime.now(ZoneInfo("Europe/Madrid")).isoformat(timespec="seconds"),
        "config": {"rebalance": "semanal", "top_n": rp.TOP_N, "cost_per_side": rp.COST,
                   "signal": "cierre del viernes", "execution": "cierre del siguiente día hábil",
                   "risk_free": 0.0, "prices_from": START_PRICES},
        "data": {"membership_source": membership_source,
                 "membership_history": membership_source != "solo composición actual",
                 "sp500_hist_tickers": len(us_all), "sp500_missing_prices": len(missing_us),
                 "missing_examples": missing_us[:40], "world_tickers": len(world),
                 "world_with_prices": sum(t in prices for t in world),
                 "sec_with_fundamentals": len(fundamentals), "sec_ticker_map": len(cik),
                 "sec_status": {str(k): v for k, v in client.status.items()}, "sec_disabled": client.disabled,
                 "price_coverage": sum(t in prices for t in us_all + list(world)) / (len(us_all) + len(world))},
        "sanity": sanity, "eps_checks": eps_checks,
        "layers": {"us_full": layer_us, "world_technical": layer_w},
        "runtime_s": time.time() - t0,
    })
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for name, L in result["layers"].items():
        log.info("%s: %s", name, [(c["id"], c["pass"]) for c in L["criteria"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
