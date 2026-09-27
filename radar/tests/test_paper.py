"""Verificación de la cartera experimental (plan aprobado, puntos 1-5)."""
import json

import numpy as np
import pandas as pd
import pytest

from radar import paper
from radar.backtest import engine as en
from radar.backtest import momentum as mm
from radar.tests.test_backtest import world


def closes_of(prices):
    return {t: df["Close"] for t, df in prices.items()}


# 1. Igual que el backtest --------------------------------------------------------

@pytest.mark.parametrize("variant", ["V1", "V2"])
def test_live_portfolio_equals_backtest(variant, monkeypatch):
    prices = world("momentum", 8, n_days=900, m=80)
    prices.pop("^IDX")
    closes = closes_of(prices)
    end = max(s.index[-1] for s in closes.values())
    start = pd.Timestamp("2025-06-01")
    sig, exe = mm.month_end_dates("2025-06-01", end.strftime("%Y-%m-%d"))
    keep = exe <= end
    sig, exe = sig[keep], exe[keep]
    prep = mm.prepare(prices, sig, exe)
    run = mm.run_variant(prep, prep.valid.copy(), variant, "none")
    bt_picks = [[prep.tickers[j] for j in p] for p in run["picks"]]
    bt_wealth = np.cumprod(1 + run["sim"]["net"])

    monkeypatch.setitem(paper.PORTFOLIOS, "t", {"variant": variant, "index": "NONE", "title": "t"})
    st = paper.update(None, "t", closes, set(closes), end, start.strftime("%Y-%m-%d"))
    reb = st["rebalances"]
    assert [r["signal_date"] for r in reb] == [d.strftime("%Y-%m-%d") for d in sig]
    for r, p in zip(reb, bt_picks):
        assert r["holdings"] == p
    # valor antes de cada rebalanceo = riqueza acumulada del backtest
    for k in range(1, len(reb)):
        assert reb[k]["value_before"] == pytest.approx(bt_wealth[k - 1], rel=1e-6)


# 2. Valoración a mano --------------------------------------------------------------

def _series(vals, start="2026-01-26"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def test_valuation_cost_and_drift_by_hand(monkeypatch):
    closes = {"A": _series([10, 11, 12, 12]), "B": _series([20, 20, 18, 18]), "C": _series([5, 5, 5, 6])}
    monkeypatch.setattr(paper, "select", lambda *a, **k: ["A", "B", "C"])
    monkeypatch.setitem(paper.PORTFOLIOS, "t", {"variant": "V1", "index": "NONE", "title": "t"})
    # señal viernes 30-ene-2026 (último viernes) -> ejecución lunes 2-feb
    closes = {t: pd.Series([s.iloc[0]] * 5 + list(s.values), index=pd.bdate_range("2026-01-26", periods=len(s) + 5))
              for t, s in closes.items()}
    st = paper.update(None, "t", closes, set(closes), pd.Timestamp("2026-02-05"), "2026-01-01")
    r = st["rebalances"][0]
    assert r["exec_date"] == "2026-02-02"
    # compra con V=1: 3 posiciones de 1/30, rotación 0,1, coste 0,0001
    assert r["turnover"] == pytest.approx(0.1) and r["cost"] == pytest.approx(0.0001)
    v = (1 - 0.0001) / 30
    # 5-feb: A 12/10, B 18/20, C 6/5 ; liquidez 27/30
    exp = v * (12 / 10 + 18 / 20 + 6 / 5) + (1 - 0.0001) * 27 / 30
    assert st["nav"][-1] == ["2026-02-05", pytest.approx(exp, abs=1e-6)]


def test_delisted_stock_valued_at_last_price(monkeypatch):
    s = pd.Series([10.0] * 6 + [12.0], index=pd.bdate_range("2026-01-26", periods=7))
    closes = {"A": s, "B": s.copy()}
    closes["B"] = closes["B"].iloc[:6]  # B deja de cotizar tras la ejecución (2-feb)
    monkeypatch.setattr(paper, "select", lambda *a, **k: ["A", "B"])
    monkeypatch.setitem(paper.PORTFOLIOS, "t", {"variant": "V1", "index": "NONE", "title": "t"})
    st = paper.update(None, "t", closes, set(closes), pd.Timestamp("2026-02-03"), "2026-01-01")
    v = (1 - mm.COST * 2 / 30) / 30
    assert st["nav"][-1][1] == pytest.approx(v * 12 / 10 + v * 1.0 + (1 - mm.COST * 2 / 30) * 28 / 30, abs=1e-6)


# 3. Calendario -------------------------------------------------------------------

def test_rebalance_once_per_month_and_pending_before_start(monkeypatch):
    prices = world("momentum", 9, n_days=700, m=60)
    prices.pop("^IDX")
    closes = closes_of(prices)
    end = max(s.index[-1] for s in closes.values())
    monkeypatch.setitem(paper.PORTFOLIOS, "t", {"variant": "V1", "index": "NONE", "title": "t"})
    # Creada después del último viernes del mes: pendiente hasta el siguiente
    created = (end - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    st = paper.update(None, "t", closes, set(closes), end, created)
    last_sig = mm.month_end_dates("2026-01-01", end.strftime("%Y-%m-%d"))[0][-1]
    if last_sig < pd.Timestamp(created) or last_sig + pd.offsets.BDay(1) > end:
        assert st["status"] == "pending" and st["nav"] == []
    # Dos ejecuciones el mismo día: nada duplicado
    st = paper.update(None, "t", closes, set(closes), end, "2026-03-01")
    n_reb, n_nav = len(st["rebalances"]), len(st["nav"])
    st2 = paper.update(json.loads(json.dumps(st)), "t", closes, set(closes), end, "2026-03-01")
    assert len(st2["rebalances"]) == n_reb and len(st2["nav"]) == n_nav
    assert len({r["signal_date"][:7] for r in st2["rebalances"]}) == n_reb  # uno por mes


def test_edge_calendar_cases():
    # Viernes Santo 2026 = 3-abr (no es el último viernes de abril); último viernes de mayo 2026 = 29
    s = paper.signal_dates_between(pd.Timestamp("2026-04-01"), pd.Timestamp("2026-06-30"))
    assert [d.strftime("%Y-%m-%d") for d in s] == ["2026-04-24", "2026-05-29", "2026-06-26"]
    # Mes con 5 viernes (enero 2026: 2, 9, 16, 23, 30) -> el 30
    assert paper.signal_dates_between(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-31"))[0] == pd.Timestamp("2026-01-30")


def test_holiday_last_friday_uses_previous_session():
    # Si el último viernes es festivo, la señal usa el último cierre disponible (asof)
    idx = pd.bdate_range("2025-01-01", periods=400).drop(pd.Timestamp("2026-01-30"), errors="ignore")
    c = pd.Series(np.linspace(10, 30, len(idx)), index=idx)
    assert paper._price_on(c, pd.Timestamp("2026-01-30")) == c[c.index <= "2026-01-30"].iloc[-1]


# 4. Reglas congeladas ----------------------------------------------------------------

def test_rule_change_archives_and_restarts(monkeypatch):
    prices = world("momentum", 10, n_days=700, m=60)
    prices.pop("^IDX")
    closes = closes_of(prices)
    end = max(s.index[-1] for s in closes.values())
    monkeypatch.setitem(paper.PORTFOLIOS, "t", {"variant": "V1", "index": "NONE", "title": "t"})
    st = paper.update(None, "t", closes, set(closes), end, "2026-03-01")
    assert st["rebalances"]
    fp = paper.rules_fingerprint()
    monkeypatch.setattr(mm, "EXIT", 50)       # cambio de reglas
    assert paper.rules_fingerprint() != fp
    st2 = paper.update(st, "t", closes, set(closes), end, "2026-09-01")
    assert st2["archived"] and st2["archived"][0]["rebalances"] == st["rebalances"]
    assert st2["created"] == "2026-09-01"


def test_unrelated_change_keeps_fingerprint(monkeypatch):
    fp = paper.rules_fingerprint()
    monkeypatch.setattr(en, "STALE_DAYS", 8)   # no forma parte de las reglas de cartera
    monkeypatch.setattr(mm, "VARIANT_TEXT", {**mm.VARIANT_TEXT, "V1": "otro texto"})
    assert paper.rules_fingerprint() == fp


# 5. Robustez -------------------------------------------------------------------------

def test_failed_download_does_not_corrupt(tmp_path, monkeypatch):
    prices = world("momentum", 13, n_days=700, m=60)
    prices.pop("^IDX")
    end = max(df.index[-1] for df in prices.values()).strftime("%Y-%m-%d")
    monkeypatch.setattr(paper, "PORTFOLIOS", {"eeuu": {"variant": "V1", "index": "SPY", "title": "t"}})
    paper.run(prices, list(prices), [], end, tmp_path, today="2026-03-01")
    before = (tmp_path / "eeuu.json").read_text()
    with pytest.raises(Exception):
        paper.run({"X": None, **{t: "roto" for t in prices}}, list(prices), [], end, tmp_path, today="2026-09-27")
    assert (tmp_path / "eeuu.json").read_text() == before


def test_daily_updates_rebalance_only_on_true_last_friday(monkeypatch):
    """Regresión: a mitad de mes, el último viernes 'visto hasta hoy' no es una señal."""
    prices = world("momentum", 14, n_days=900, m=60)
    prices.pop("^IDX")
    closes = closes_of(prices)
    end = max(s.index[-1] for s in closes.values())
    monkeypatch.setitem(paper.PORTFOLIOS, "t", {"variant": "V1", "index": "NONE", "title": "t"})
    st = None
    days = pd.bdate_range(end - pd.Timedelta(days=150), end)
    for d in days:
        st = paper.update(st, "t", {t: s[s.index <= d] for t, s in closes.items()}, set(closes), d, "2026-01-01")
    sigs = [pd.Timestamp(r["signal_date"]) for r in st["rebalances"]]
    assert len({(s.year, s.month) for s in sigs}) == len(sigs)
    for s in sigs:
        assert s.weekday() == 4 and (s + pd.Timedelta(days=7)).month != s.month  # último viernes real
    # Mismo resultado actualizando a diario que de una vez
    once = paper.update(None, "t", closes, set(closes), end, "2026-01-01")
    assert [r["holdings"] for r in once["rebalances"]] == [r["holdings"] for r in st["rebalances"]]
    assert once["nav"][-1][1] == pytest.approx(st["nav"][-1][1], rel=1e-9)


def test_month_end_dates_partial_month():
    sig, _ = mm.month_end_dates("2026-09-01", "2026-09-20")   # el último viernes (25) aún no ha llegado
    assert len(sig) == 0
    sig, _ = mm.month_end_dates("2026-09-01", "2026-09-25")
    assert list(sig.strftime("%Y-%m-%d")) == ["2026-09-25"]
