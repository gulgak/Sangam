"""Verificación del backtest (fase A del plan aprobado)."""
import math

import numpy as np
import pandas as pd
import pytest

from radar import scoring as sc
from radar.backtest import engine as en
from radar.backtest import membership as ms
from radar.backtest import report as rp
from radar.backtest import sec

END = "2026-09-25"


def make_prices(rets: np.ndarray, seed: int = 0, end: str = END) -> dict[str, pd.DataFrame]:
    """rets: [días, acciones] rentabilidades diarias -> precios OHLCV con AdjClose."""
    rng = np.random.default_rng(seed)
    n, m = rets.shape
    idx = pd.bdate_range(end=end, periods=n)
    out = {}
    for j in range(m):
        c = 50 * np.exp(np.cumsum(rets[:, j]))
        spread = np.abs(rng.standard_normal(n)) * 0.01 * c
        out[f"S{j:03d}"] = pd.DataFrame({"Open": c, "High": c + spread, "Low": c - spread, "Close": c,
                                          "AdjClose": c, "Volume": rng.integers(8e5, 1.2e6, n).astype(float)}, index=idx)
    ix = 100 * np.exp(np.cumsum(np.full(n, 0.0003)))
    out["^IDX"] = pd.DataFrame({"Open": ix, "High": ix, "Low": ix, "Close": ix, "AdjClose": ix, "Volume": 1.0}, index=idx)
    return out


def world(kind: str, seed: int, n_days: int = 1500, m: int = 60) -> dict:
    rng = np.random.default_rng(seed)
    if kind == "momentum":
        blocks = -(-n_days // 126)
        drift = np.repeat(rng.normal(0, 0.002, (blocks, m)), 126, axis=0)[:n_days]
    else:
        drift = np.full((n_days, m), 0.0003)
    return make_prices(drift + 0.015 * rng.standard_normal((n_days, m)), seed)


def run_world(prices):
    sig, exe = en.weekly_dates("2022-01-01", "2026-09-18")
    bench = {t: "^IDX" for t in prices if t != "^IDX"}
    prep = en.prepare(prices, bench, sig, exe, index_tickers=["^IDX"])
    ranks, elig = en.signals(prep)
    return prep, ranks, elig, rp.analyze(prep, ranks, elig, "^IDX", full=False)


# ------------------------------------------------------------------- 1. sin datos del futuro

def test_signals_identical_with_or_without_future_prices():
    prices = world("momentum", 3)
    sig, exe = en.weekly_dates("2023-01-01", "2026-09-18")
    bench = {t: "^IDX" for t in prices if t != "^IDX"}
    full_prep = en.prepare(prices, bench, sig, exe)
    full_ranks, _ = en.signals(full_prep)
    for k in (10, 60, 120):
        d = sig[k]
        cut = {t: df[df.index <= d] for t, df in prices.items()}
        cut_prep = en.prepare(cut, bench, sig[: k + 1], exe[: k + 1])
        cut_ranks, _ = en.signals(cut_prep)
        a = [(r["ticker"], r["score"]) for r in full_ranks[k]]
        b = [(r["ticker"], r["score"]) for r in cut_ranks[k]]
        assert a == b and len(a) > 0


def test_backtest_tech_score_equals_daily_radar():
    prices = world("momentum", 4)
    sig, exe = en.weekly_dates("2024-01-01", "2026-09-18")
    bench = {t: "^IDX" for t in prices if t != "^IDX"}
    prep = en.prepare(prices, bench, sig, exe)
    ranks, _ = en.signals(prep)
    k = 50
    d = sig[k]
    for r in ranks[k][:10]:
        df = prices[r["ticker"]]
        m = sc.technical_metrics(df[df.index <= d], prices["^IDX"]["Close"][lambda s: s.index <= d])
        assert sc.is_uptrend(m)
        assert sc.technical_score(m)[0] == r["tech"]


def _facts():
    def q(s, e, v, f, form="10-Q"):
        return {"start": s, "end": e, "val": v, "filed": f, "form": form}
    eps = [
        q("2024-01-01", "2024-03-31", 1.0, "2024-05-01"), q("2024-04-01", "2024-06-30", 1.0, "2024-08-01"),
        q("2024-07-01", "2024-09-30", 1.0, "2024-11-01"),
        q("2024-01-01", "2024-12-31", 4.4, "2025-02-15", "10-K"),               # T4 derivado = 1,4
        q("2025-01-01", "2025-03-31", 2.0, "2025-05-01"),
        q("2025-01-01", "2025-03-31", 9.9, "2026-05-01"),                       # revisión posterior: se ignora
    ]
    return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": eps}}}}}


def test_sec_first_filed_q4_derivation_and_filing_date():
    qs = sec.duration_series(_facts(), sec.EPS_TAGS, "USD/shares")
    assert [round(x[2], 6) for x in qs] == [1.0, 1.0, 1.0, 1.4, 2.0]
    tl = sec.build_timeline(_facts())
    # Antes de presentar el 10-K no hay 4 trimestres
    assert sec.snapshot_at(tl, pd.Timestamp("2025-02-14")) is None
    s1 = sec.snapshot_at(tl, pd.Timestamp("2025-02-15"))
    assert s1["ttm_eps"] == pytest.approx(4.4)
    s2 = sec.snapshot_at(tl, pd.Timestamp("2025-05-02"))
    assert s2["ttm_eps"] == pytest.approx(5.4) and s2["eps_yoy"] == pytest.approx(1.0)
    # Caducidad: sin nuevos trimestres, a los 200 días deja de usarse
    assert sec.snapshot_at(tl, pd.Timestamp("2026-01-01")) is None


def test_sec_split_adjustment():
    splits = pd.Series([4.0], index=[pd.Timestamp("2025-03-01")])
    tl = sec.build_timeline(_facts(), splits)
    s1 = sec.snapshot_at(tl, pd.Timestamp("2025-02-20"))
    assert s1["ttm_eps"] == pytest.approx(4.4 / 4)   # publicado antes del split
    s2 = sec.snapshot_at(tl, pd.Timestamp("2025-05-02"))
    # T2-T4 2024 publicados antes del split (÷4) + T1 2025 ya en acciones nuevas
    assert s2["ttm_eps"] == pytest.approx((1.0 + 1.0 + 1.4) / 4 + 2.0)


def test_info_from_snapshot():
    info = sec.info_from_snapshot({"ttm_eps": 5.0, "last_quarter_end": None, "eps_yoy": 0.2, "rev_yoy": 0.1,
                                   "roe": 0.3, "debt_to_equity": 50.0}, 100.0, "Technology")
    assert info["trailingPE"] == 20.0 and info.get("forwardPE") is None
    f = sc.fundamental_metrics(info, 0.3)
    assert f["peg"] == pytest.approx(1.0)  # PEG histórico = PER / crecimiento


# ------------------------------------------------------------------- 2. rentabilidades

def test_single_stock_portfolio_equals_stock_return():
    px = np.array([[100.0, 10], [110, 11], [99, 12], [120, 12]])
    fresh = np.ones_like(px, bool)
    r = en.simulate([[0, 1]] * 4, px, fresh, top_n=1, cost=0.0)
    np.testing.assert_allclose(r["net"], px[1:, 0] / px[:-1, 0] - 1)


def test_all_selected_equals_equal_weight_mean():
    rng = np.random.default_rng(0)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, (30, 8)), axis=0))
    fresh = np.ones_like(px, bool)
    r = en.simulate([list(range(8))] * 30, px, fresh, top_n=None, cost=0.0)
    np.testing.assert_allclose(r["net"], (px[1:] / px[:-1] - 1).mean(axis=1))


def test_cost_is_exact():
    px = np.array([[100.0, 100], [100, 100], [100, 100]])
    fresh = np.ones_like(px, bool)
    # k=0 compra A (rotación 1), k=1 cambia A por B (rotación 2)
    r = en.simulate([[0], [1], [1]], px, fresh, top_n=1, cost=0.001)
    np.testing.assert_allclose(r["turnover"], [1.0, 2.0])
    np.testing.assert_allclose(r["net"], [-0.001, -0.002])


def test_cash_when_few_candidates():
    px = np.array([[100.0, 100], [110, 100]])
    r = en.simulate([[0]], px, np.ones_like(px, bool), top_n=2, cost=0.0)
    assert r["net"][0] == pytest.approx(0.05) and r["cash"][0] == pytest.approx(0.5)


def test_perf_metrics_by_hand():
    dates = pd.date_range("2020-01-03", periods=53, freq="W-FRI")
    p = en.perf(np.full(52, 0.01), dates)
    years = (dates[52] - dates[0]).days / 365.25
    assert p["cagr"] == pytest.approx((1.01 ** 52) ** (1 / years) - 1)
    assert p["max_dd"] == 0
    dd = en.perf(np.array([0.1, -0.5, 0.2]), dates[:4])["max_dd"]
    assert dd == pytest.approx(-0.5)
    x = np.random.default_rng(1).normal(0.001, 0.01, 400)
    t_simple = x.mean() / (x.std(ddof=0) / math.sqrt(len(x)))
    assert en.newey_west_t(x, 0) == pytest.approx(t_simple)


# ------------------------------------------------------------------- 3. mundos sintéticos

def test_momentum_world_is_detected():
    *_, res = run_world(world("momentum", 11))
    assert res["excess_t_nw"] > 2 and res["ic4_mean"] > 0
    assert res["portfolio"]["cagr"] > res["equal_weight"]["cagr"]


@pytest.mark.parametrize("seed", [21, 22, 23])
def test_random_world_is_not_significant(seed):
    prep, ranks, elig, res = run_world(world("random", seed))
    # Criterio del plan: en un mundo aleatorio el radar no puede ganar de forma significativa.
    assert res["excess_t_nw"] < 2
    # Aislando la selección (top 5 sin liquidez y sin costes): ni gana ni pierde.
    # Diagnóstico con 20 semillas: t medio -0,06, ninguna |t| > 2. Con costes pierde por la
    # rotación, que es lo esperable.
    sel = rp.analyze(prep, ranks, elig, "^IDX", full=False, top_n=5, cost=0.0)
    assert sel["avg_cash"] < 0.05
    assert abs(sel["excess_t_nw"]) < 2


# ------------------------------------------------------------------- composición histórica

def test_members_reconstruction():
    changes = pd.DataFrame({"date": pd.to_datetime(["2020-06-01", "2022-03-01"]),
                            "added": ["NEW1", "NEW2"], "removed": ["OLD1", None]})
    m = ms.members_at(pd.to_datetime(["2019-01-04", "2021-01-01", "2023-01-06"]), {"A", "NEW1", "NEW2"}, changes)
    assert m[pd.Timestamp("2023-01-06")] == {"A", "NEW1", "NEW2"}
    assert m[pd.Timestamp("2021-01-01")] == {"A", "NEW1"}
    assert m[pd.Timestamp("2019-01-04")] == {"A", "OLD1"}


def test_parse_wikipedia_changes_multiindex():
    cols = pd.MultiIndex.from_tuples([("Effective Date", "Effective Date"), ("Added", "Ticker"), ("Added", "Security"),
                                      ("Removed", "Ticker"), ("Removed", "Security"), ("Reason", "Reason")])
    raw = pd.DataFrame([["September 22, 2025", "APP", "AppLovin", "ENPH", "Enphase", "x"],
                        ["June 23, 2025", "brk.b", "x", float("nan"), float("nan"), "y"]], columns=cols)
    out = ms.parse_changes(raw)
    assert list(out["added"]) == ["APP", "BRK-B"] and out["removed"].iloc[1] is None
    assert out["date"].iloc[0] == pd.Timestamp("2025-09-22")


def test_full_mode_uses_point_in_time_fundamentals():
    prices = world("momentum", 5)
    sig, exe = en.weekly_dates("2025-01-01", "2026-09-18")
    bench = {t: "^IDX" for t in prices if t != "^IDX"}
    prep = en.prepare(prices, bench, sig, exe, index_tickers=["^IDX"])
    snap = {"ttm_eps": 2.0, "last_quarter_end": pd.Timestamp("2025-12-31"), "eps_yoy": 0.3,
            "rev_yoy": 0.1, "roe": 0.2, "debt_to_equity": 30.0}
    tl = {t: [(pd.Timestamp("2026-02-01"), snap)] for t in prices}
    ranks, elig = en.signals(prep, None, tl, {}, full=True)
    k_before = int(np.searchsorted(sig, pd.Timestamp("2026-01-30")))
    k_after = int(np.searchsorted(sig, pd.Timestamp("2026-02-06")))
    assert all(r["fund"] is None for r in ranks[k_before])        # aún no presentado
    assert any(r["fund"] is not None for r in ranks[k_after])     # ya presentado
    res = rp.analyze(prep, ranks, elig, "^IDX", full=True)
    assert {c["id"] for c in res["criteria"]} >= {"beats_ew", "significant", "ic", "labels", "drawdown"}


def test_find_changes_table_flat_or_multiindex():
    flat = pd.DataFrame(columns=["Date", "Added Ticker", "Added Security", "Removed Ticker", "Removed Security", "Reason"])
    cur = pd.DataFrame(columns=["Symbol", "Security", "GICS Sector"])
    c, ch = ms._find([cur, flat])
    assert c is cur and ch is flat
    assert ms._find([cur])[1] is None
