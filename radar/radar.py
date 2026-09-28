"""Radar de Bolsa diario: criba mundial de acciones en tendencia alcista y
puntuación técnica + fundamental (PER). Genera data/radar/latest.json.

Uso:
    python -m radar.radar --source yahoo            # datos reales
    python -m radar.radar --source fixture --out X  # datos sintéticos (pruebas)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import scoring as sc
from . import paper
from .universe import BENCHMARKS, SP500, load_universe

log = logging.getLogger("radar")
TZ = ZoneInfo("Europe/Madrid")
VERSION = 1
MIN_COVERAGE = 0.5      # por debajo, la ejecución se considera fallida
STALE_DAYS = 7          # sin cotizar en una semana -> excluida
HISTORY_KEEP = 400

MARKET_NAMES = {
    "US": "EE. UU.", "ES": "España", "DE": "Alemania", "FR": "Francia", "GB": "Reino Unido",
    "EU": "Zona euro", "NORD": "Nórdicos", "CH": "Suiza", "JP": "Japón", "HK": "Hong Kong",
    "KR": "Corea", "TW": "Taiwán", "CA": "Canadá", "AU": "Australia", "IN": "India", "BR": "Brasil",
}


def _r(x, nd=4):
    return None if x is None else round(float(x), nd)


def analyse(source, universe: dict[str, str], cutoff: str, extra: tuple = (),
            prices_out: dict | None = None) -> dict:
    t0 = time.time()
    tickers = sorted(universe)
    benches = sorted({BENCHMARKS[m] for m in set(universe.values()) if m in BENCHMARKS} | set(extra))
    log.info("Universo: %d acciones, %d índices", len(tickers), len(benches))
    prices = source.prices(tickers + benches)
    cut = pd.Timestamp(cutoff)
    # Solo sesiones cerradas: se descarta cualquier barra de hoy (Asia puede estar abierta).
    prices = {t: df[df.index < cut] for t, df in prices.items()}
    if prices_out is not None:
        prices_out.update(prices)
    stale_limit = cut - timedelta(days=STALE_DAYS)

    failed, candidates = [], []
    analysed = 0
    for t in tickers:
        df = prices.get(t)
        if df is None or df.empty or df.index[-1] < stale_limit:
            failed.append(t)
            continue
        bench = prices.get(BENCHMARKS.get(universe[t], ""))
        m = sc.technical_metrics(df, bench["Close"] if bench is not None else None)
        if m is None:
            failed.append(t)
            continue
        analysed += 1
        if sc.is_uptrend(m):
            candidates.append((t, m))
    log.info("Con datos: %d / %d. En tendencia alcista: %d", analysed, len(tickers), len(candidates))

    rows = []
    for i, (t, m) in enumerate(candidates, 1):
        info = source.info(t)
        f = sc.fundamental_metrics(info, m["ret_12m"])
        rows.append({"ticker": t, "m": m, "f": f, "info": info})
        if i % 50 == 0:
            log.info("Fundamentales: %d / %d", i, len(candidates))

    medians = sc.sector_medians([r["f"] for r in rows])
    ranking = []
    for r in rows:
        t, m, f, info = r["ticker"], r["m"], r["f"], r["info"]
        f["pe_sector_median"] = medians.get(f["sector"], medians.get("*"))
        tech, tpos, tneg = sc.technical_score(m)
        fund, cov, fpos, fneg = sc.fundamental_score(f)
        lab = sc.label(m, f, tech, fund)
        ranking.append({
            "ticker": t,
            "name": " ".join(str(info.get("longName") or info.get("shortName") or t).split()),
            "market": universe[t],
            "market_name": MARKET_NAMES.get(universe[t], universe[t]),
            "sector": f["sector"],
            "currency": info.get("currency"),
            "price": _r(m["price"], 2),
            "last_date": m["last_date"],
            "score": sc.final_score(tech, fund),
            "tech_score": tech,
            "fund_score": fund,
            "fund_coverage": _r(cov, 2),
            "label": lab,
            "loss_making": f["loss_making"],
            "pe_suspect": f["pe_suspect"],
            "eps_atypical": f["eps_atypical"],
            "summary": sc.summary(m, f, lab),
            "signals_pos": tpos + fpos,
            "signals_neg": tneg + fneg,
            **{k: _r(m[k]) for k in ("ret_1m", "ret_3m", "ret_6m", "ret_12m", "rs_3m", "rs_6m",
                                     "dist_52w_high", "rvol")},
            **{k: _r(m[k], 1) for k in ("rsi", "adx")},
            **{k: _r(f[k], 2) for k in ("pe_trailing", "pe_check", "pe_forward", "peg", "pe_sector_median",
                                        "debt_to_equity")},
            **{k: _r(f[k]) for k in ("eps_growth", "eps_growth_raw", "pe_expansion", "backing", "roe",
                                     "revenue_growth", "target_upside")},
        })
    ranking.sort(key=lambda x: (-x["score"], -x["tech_score"], x["ticker"]))
    for i, x in enumerate(ranking, 1):
        x["rank"] = i

    data_date = max((x["last_date"] for x in ranking), default=None)
    by_label: dict[str, int] = {}
    for x in ranking:
        by_label[x["label"]] = by_label.get(x["label"], 0) + 1
    return {
        "version": VERSION,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "cutoff": cutoff,
        "data_date": data_date,
        "universe_size": len(tickers),
        "analysed": analysed,
        "coverage": _r(analysed / len(tickers) if tickers else 0, 3),
        "in_uptrend": len(candidates),
        "by_label": by_label,
        "failed": failed,
        "weights": {"technical": sc.TECH_WEIGHT, "fundamental": sc.FUND_WEIGHT},
        "markets": {m: MARKET_NAMES.get(m, m) for m in sorted(set(universe.values()))},
        "runtime_s": round(time.time() - t0, 1),
        "ranking": ranking,
    }


def write_outputs(report: dict, out: Path, history_top: int = 30) -> None:
    out.mkdir(parents=True, exist_ok=True)
    hist = out / "history"
    hist.mkdir(exist_ok=True)
    (out / "latest.json").write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    day = report["cutoff"]
    slim = {k: v for k, v in report.items() if k not in ("ranking", "failed")}
    slim["ranking"] = report["ranking"][:history_top]
    (hist / f"{day}.json").write_text(json.dumps(slim, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    idx_path = hist / "index.json"
    dates = json.loads(idx_path.read_text()) if idx_path.exists() else []
    dates = sorted(set(dates) | {day}, reverse=True)
    for old in dates[HISTORY_KEEP:]:
        (hist / f"{old}.json").unlink(missing_ok=True)
    idx_path.write_text(json.dumps(dates[:HISTORY_KEEP]), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=["yahoo", "fixture"], default="yahoo")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data" / "radar"))
    ap.add_argument("--cutoff", help="Fecha de corte AAAA-MM-DD (por defecto hoy en Madrid)")
    ap.add_argument("--offline-universe", action="store_true", help="No consultar Wikipedia")
    ap.add_argument("--limit", type=int, help="Analizar solo N acciones (depuración)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cutoff = a.cutoff or datetime.now(TZ).strftime("%Y-%m-%d")
    if a.source == "fixture":
        from .sources import FixtureSource

        # La serie sintética incluye una barra con fecha de corte (sesión "abierta")
        # que el análisis debe descartar.
        src = FixtureSource(end_date=cutoff)
        universe = src.universe
    else:
        from .sources import YahooSource

        src = YahooSource()
        universe = load_universe(online=not a.offline_universe)
    if a.limit:
        universe = dict(list(universe.items())[: a.limit])

    prices: dict = {}
    report = analyse(src, universe, cutoff, extra=paper.INDEX_TICKERS, prices_out=prices)
    log.info("Cobertura %.0f%%, %d en tendencia, %d fallidas, %.0fs",
             100 * report["coverage"], report["in_uptrend"], len(report["failed"]), report["runtime_s"])
    if report["coverage"] < MIN_COVERAGE:
        log.error("Cobertura insuficiente (%.0f%%): no se publica el informe", 100 * report["coverage"])
        return 2
    write_outputs(report, Path(a.out))
    # Cartera experimental de momentum: aislada, un fallo aquí no afecta al informe del radar.
    if a.source == "yahoo" and SP500 and report.get("data_date"):
        try:
            world = [t for t, m in universe.items() if m != "US"]
            res = paper.run(prices, list(SP500), world, report["data_date"], Path(a.out) / "paper")
            for name, st in res.items():
                log.info("Cartera %s: %s, %d rebalanceos", name, st["status"], len(st["rebalances"]))
        except Exception as e:
            log.exception("Cartera experimental no actualizada: %s", e)
    for x in report["ranking"][:10]:
        log.info("#%d %-10s %5.1f  T%5.1f F%5s  %s", x["rank"], x["ticker"], x["score"],
                 x["tech_score"], x["fund_score"], x["label"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
