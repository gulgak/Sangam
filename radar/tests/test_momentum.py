"""Verificación de la señal rediseñada (plan aprobado, puntos 1-6)."""
import numpy as np
import pandas as pd
import pytest

from radar.backtest import engine as en
from radar.backtest import momentum as mm
from radar.tests.test_backtest import make_prices, world


# 1. Momentum 12-1 ---------------------------------------------------------------

def test_momentum_formula_by_hand():
    idx = pd.bdate_range("2020-01-01", periods=300)
    c = pd.Series(np.arange(1, 301, dtype=float), index=idx)
    m = mm.momentum_series(c)
    t = 299
    assert m.iloc[t] == pytest.approx(c.iloc[t - 21] / c.iloc[t - 252] - 1)
    assert m.iloc[:252].isna().all()


def test_last_month_does_not_change_signal():
    idx = pd.bdate_range("2020-01-01", periods=400)
    c = pd.Series(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, 400))), index=idx)
    c2 = c.copy()
    c2.iloc[-21:] *= 3  # alterar las 21 últimas sesiones
    assert mm.momentum_series(c).iloc[-1] == pytest.approx(mm.momentum_series(c2).iloc[-1])


def test_momentum_signal_has_no_lookahead():
    prices = world("momentum", 7)
    sig, exe = mm.month_end_dates("2022-01-01", "2026-09-18")
    full = mm.prepare(prices, sig, exe, index_tickers=["^IDX"])
    k = 30
    cut = {t: df[df.index <= sig[k]] for t, df in prices.items()}
    part = mm.prepare(cut, sig[: k + 1], exe[: k + 1], index_tickers=["^IDX"])
    np.testing.assert_allclose(full.mom[k], part.mom[k], equal_nan=True)
    np.testing.assert_allclose(full.prox[k], part.prox[k], equal_nan=True)


def test_month_end_dates_are_last_fridays():
    sig, exe = mm.month_end_dates("2024-01-01", "2024-04-30")
    assert list(sig.strftime("%Y-%m-%d")) == ["2024-01-26", "2024-02-23", "2024-03-29", "2024-04-26"]
    assert list(exe.strftime("%Y-%m-%d"))[0] == "2024-01-29"


# 2. Filtro de mercado ------------------------------------------------------------

def test_market_filter_switches_exactly_at_the_cross():
    idx = pd.bdate_range("2020-01-01", periods=500)
    c = pd.Series(np.r_[np.linspace(100, 200, 350), np.linspace(200, 120, 150)], index=idx)
    sma = c.rolling(200).mean()
    first_below = idx[np.flatnonzero((c < sma).to_numpy())[0]]
    dates = pd.DatetimeIndex([first_below - pd.Timedelta(days=1), first_below, first_below + pd.Timedelta(days=1)])
    on = mm.market_on(c, dates)
    assert list(on) == [True, False, False]
    # Futuro alterado: el pasado no cambia
    c2 = c.copy()
    c2[c2.index > first_below] = 1000
    assert list(mm.market_on(c2, dates[:2])) == [True, False]


# 3. Margen de permanencia ------------------------------------------------------

def test_buffer_keeps_until_rank_60_and_refills():
    n = 100
    s0 = np.arange(n, 0, -1, dtype=float)          # acción 0 la mejor
    s1 = s0.copy()
    s1[24] = s0[44]                                  # la 25.ª baja a ~45.º: se mantiene
    s1[10] = -1                                      # la 11.ª cae al último puesto: sale
    picks = mm.select_with_buffer(np.vstack([s0, s1]))
    assert picks[0] == list(range(30))
    assert 24 in picks[1] and 10 not in picks[1]
    assert len(picks[1]) == 30 and 30 in picks[1]   # hueco cubierto por la mejor no incluida
    s2 = s1.copy()
    s2[24] = -2                                      # ahora cae fuera del top 60
    assert 24 not in mm.select_with_buffer(np.vstack([s0, s1, s2]))[2]


def test_buffer_never_exceeds_30_and_cuts_turnover():
    rng = np.random.default_rng(3)
    sc = np.cumsum(rng.normal(0, 1, (60, 200)), axis=0)  # puntuaciones persistentes
    wb = mm.select_with_buffer(sc)
    nb = mm.select_with_buffer(sc, exit_=30)
    assert max(len(p) for p in wb) <= 30
    churn = lambda P: sum(len(set(a) ^ set(b)) for a, b in zip(P, P[1:]))
    assert churn(wb) < churn(nb)


def test_invest_false_goes_to_cash():
    sc = np.tile(np.arange(50, 0, -1, dtype=float), (3, 1))
    picks = mm.select_with_buffer(sc, invest=np.array([True, False, True]))
    assert picks[1] == [] and len(picks[2]) == 30


# 4. Separación entre periodos -----------------------------------------------------

def test_variant_choice_ignores_post_split_data():
    prices = world("momentum", 12, n_days=2600)
    sig, exe = mm.month_end_dates("2017-06-01", "2026-09-18")
    prep = mm.prepare(prices, sig, exe, index_tickers=["^IDX"])
    elig = prep.valid.copy()
    a = mm.evaluate(prep, elig, "^IDX")
    # Destrozar todos los precios desde el corte
    bad = {t: df.copy() for t, df in prices.items()}
    for df in bad.values():
        m = df.index >= mm.SPLIT
        df.loc[m, ["Open", "High", "Low", "Close", "AdjClose"]] *= np.linspace(0.2, 5, m.sum())[:, None]
    prep2 = mm.prepare(bad, sig, exe, index_tickers=["^IDX"])
    b = mm.evaluate(prep2, prep2.valid.copy(), "^IDX")
    assert a["chosen"] == b["chosen"]
    assert a["design_sharpe"] == pytest.approx(b["design_sharpe"])
    assert a["validation"][a["chosen"]]["months"] > 0
    # Diseño = periodos que terminan antes del corte; validación = señales desde el corte.
    K = len(exe)
    n_design = int((exe[1:K] < mm.SPLIT).sum())
    n_valid = int((sig[: K - 1] >= mm.SPLIT).sum())
    assert a["design"]["V1"]["months"] == n_design and a["validation"]["V1"]["months"] == n_valid
    assert n_design + n_valid <= K - 2  # periodos de transición excluidos


# 5. Mundos sintéticos --------------------------------------------------------------

def _eval_world(kind, seed):
    prices = world(kind, seed, n_days=1500, m=120)
    sig, exe = mm.month_end_dates("2022-01-01", "2026-09-18")
    prep = mm.prepare(prices, sig, exe, index_tickers=["^IDX"])
    r = mm.run_variant(prep, prep.valid, "V1", "^IDX")
    K = len(exe)
    ew = en.simulate([list(np.flatnonzero(prep.valid[k])) for k in range(K)], prep.px, prep.px_fresh, None, 0.0)
    return r, ew


def test_momentum_world_detected_by_v1():
    r, ew = _eval_world("momentum", 11)
    ex = r["sim"]["net"] - ew["net"]
    assert en.newey_west_t(ex, 3) > 2


def test_random_world_not_significant_without_costs():
    """20 mundos aleatorios: sin sesgo sistemático. Con ~56 meses el t Newey-West está
    sobredimensionado (diagnóstico: ~15 % de |t| > 2 frente al 5 % nominal); se exige
    media |t| baja y frecuencia de falsos positivos acotada."""
    ts = []
    for seed in range(30, 50):
        prices = world("random", seed, n_days=1500, m=120)
        sig, exe = mm.month_end_dates("2022-01-01", "2026-09-18")
        prep = mm.prepare(prices, sig, exe, index_tickers=["^IDX"])
        sim = en.simulate(mm.select_with_buffer(mm.scores(prep, prep.valid, "V1")), prep.px, prep.px_fresh, 30, 0.0)
        K = len(exe)
        ew = en.simulate([list(np.flatnonzero(prep.valid[k])) for k in range(K)], prep.px, prep.px_fresh, None, 0.0)
        ts.append(en.newey_west_t(sim["net"] - ew["net"], 3))
    ts = np.array(ts)
    assert abs(ts.mean()) < 1
    assert np.mean(ts > 2) <= 0.25
