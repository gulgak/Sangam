"""Comprobación con datos reales (punto 7 del plan): la cartera en vivo reproduce
exactamente el motor del backtest sobre los mismos precios. Escribe en una carpeta de
prueba que NO se publica. Uso: python -m radar.paper_check"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import paper
from .backtest import momentum as mm
from .sources import YahooSource
from .universe import SP500, load_universe

log = logging.getLogger("radar")
START = "2025-10-01"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    t0 = time.time()
    universe = load_universe(online=True)
    world = [t for t, m in universe.items() if m != "US"]
    tickers = sorted(set(SP500) | set(world)) + list(paper.INDEX_TICKERS)
    prices = YahooSource().prices(tickers)
    cut = pd.Timestamp(datetime.now(paper.TZ).strftime("%Y-%m-%d"))
    prices = {t: df[df.index < cut] for t, df in prices.items()}
    data_date = max(df.index[-1] for df in prices.values())
    t_dl = time.time() - t0
    closes = {t: paper._adj_close(df) for t, df in prices.items()}
    ok = True
    out = {}
    for name, univ in (("eeuu", set(SP500)), ("mundo", set(SP500) | set(world))):
        t1 = time.time()
        st = paper.update(None, name, closes, univ, data_date, START)
        t_paper = time.time() - t1
        # Motor del backtest con los mismos precios y universo
        cfg = paper.PORTFOLIOS[name]
        sig, exe = mm.month_end_dates(START, data_date.strftime("%Y-%m-%d"))
        keep = exe <= data_date
        sig, exe = sig[keep], exe[keep]
        prep = mm.prepare({t: prices[t] for t in univ if t in prices}, sig, exe)
        run = mm.run_variant(prep, prep.valid.copy(), cfg["variant"], "none")
        bt = [[prep.tickers[j] for j in p] for p in run["picks"]]
        live = [r["holdings"] for r in st["rebalances"]]
        same = live == bt
        wealth = np.cumprod(1 + run["sim"]["net"])
        vb = [r["value_before"] for r in st["rebalances"][1:]]
        same_val = bool(np.allclose(vb, wealth[: len(vb)], rtol=1e-6)) if vb else True
        ok &= same and same_val
        log.info("%s: %d rebalanceos; carteras idénticas al backtest: %s; valores idénticos: %s; %.1fs",
                 name, len(live), same, same_val, t_paper)
        out[name] = {"rebalances": len(live), "same_holdings": same, "same_values": same_val,
                     "seconds": round(t_paper, 1), "last_holdings": live[-1] if live else [],
                     "nav": st["nav"][-1] if st["nav"] else None}
    try:  # informativo: coincidencia con la cartera actual del backtest de 10 años
        bt10 = json.loads((Path(__file__).resolve().parent.parent / "data/radar/backtest.json").read_text())["momentum"]
        for name, key in (("eeuu", "us"), ("mundo", "world")):
            cur = set(bt10[key]["current_portfolio"])
            live = set(out[name]["last_holdings"])
            out[name]["overlap_with_10y_backtest"] = f"{len(cur & live)}/{len(cur)}"
            log.info("%s: coincidencia con la cartera actual del backtest de 10 años: %d/%d (difiere por el"
                     " historial del margen de permanencia)", name, len(cur & live), len(cur))
    except Exception as e:
        log.warning("Sin backtest.json para comparar: %s", e)
    log.info("Descarga %.0fs; total %.0fs; resultado %s", t_dl, time.time() - t0, "OK" if ok else "FALLO")
    Path(tempfile.gettempdir(), "paper_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
