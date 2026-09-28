import math

import pytest

from radar import scoring as sc
from radar.sources import synthetic_ohlcv

BASE_INFO = dict(sector="Technology", trailingPE=20.0, forwardPE=17.0, earningsGrowth=0.3,
                 returnOnEquity=0.2, debtToEquity=40.0, revenueGrowth=0.12)


def tech(**over):
    m = dict(price=110.0, sma20=105.0, sma50=100.0, sma200=90.0, sma200_20d_ago=88.0,
             golden_cross_60d=False, rsi=62.0, macd=1.0, macd_signal=0.5, macd_hist=0.5,
             macd_hist_prev=0.4, adx=28.0, rvol=1.4, dist_52w_high=-0.02, ret_1m=0.05,
             ret_3m=0.12, ret_6m=0.2, ret_12m=0.40, rs_3m=0.05, rs_6m=0.08)
    m.update(over)
    return m


def fund(info, ret_12m=0.40, med=25.0):
    f = sc.fundamental_metrics(info, ret_12m)
    f["pe_sector_median"] = med
    return f


def classify(m, info):
    f = fund(info, m["ret_12m"])
    t, *_ = sc.technical_score(m)
    fs, *_ = sc.fundamental_score(f)
    return sc.label(m, f, t, fs), t, fs, f


def test_rise_backed_by_eps_is_sustainable():
    lab, t, fs, f = classify(tech(), {**BASE_INFO, "earningsGrowth": 0.40})
    assert f["backing"] == pytest.approx(1.0)
    assert f["pe_expansion"] == pytest.approx(0.0, abs=1e-9)
    assert lab == sc.LABEL_SUSTAINABLE
    assert t == 100 and fs >= 55


def test_rise_from_pe_expansion_only_is_unbacked():
    lab, _, _, f = classify(tech(), {**BASE_INFO, "earningsGrowth": 0.0})
    assert f["backing"] == 0.0
    assert f["pe_expansion"] == pytest.approx(0.40)
    assert lab == sc.LABEL_UNBACKED


def test_partial_backing_share():
    # precio +40 %, BPA +10 %: log(1.1)/log(1.4) ≈ 28 % de respaldo
    _, _, _, f = classify(tech(), {**BASE_INFO, "earningsGrowth": 0.10})
    assert f["backing"] == pytest.approx(math.log(1.1) / math.log(1.4))


def test_rsi_82_is_overbought_even_if_backed():
    lab, *_ = classify(tech(rsi=82.0), {**BASE_INFO, "earningsGrowth": 0.40})
    assert lab == sc.LABEL_OVERBOUGHT


def test_price_far_above_sma50_is_overbought():
    lab, *_ = classify(tech(price=130.0, sma50=100.0), {**BASE_INFO, "earningsGrowth": 0.40})
    assert lab == sc.LABEL_OVERBOUGHT


def test_below_sma200_is_filtered_out():
    assert sc.is_uptrend(tech())
    assert not sc.is_uptrend(tech(price=85.0))
    assert not sc.is_uptrend(tech(sma50=80.0))
    assert not sc.is_uptrend(tech(ret_1m=-0.01))


def test_uptrend_filter_on_real_series():
    up = sc.technical_metrics(synthetic_ohlcv(400, 0.0015, 0.008, seed=3), None)
    down = sc.technical_metrics(synthetic_ohlcv(400, -0.0015, 0.008, seed=3), None)
    assert sc.is_uptrend(up) and not sc.is_uptrend(down)


def test_short_history_returns_none():
    assert sc.technical_metrics(synthetic_ohlcv(150, 0.001, 0.01, seed=1), None) is None


@pytest.mark.parametrize("info", [
    {},
    {"trailingPE": None, "forwardPE": None},
    {"trailingPE": "Infinity", "forwardPE": float("nan")},
    {"trailingEps": -2.0, "trailingPE": None},
    {"trailingPE": -12.0},
])
def test_missing_or_negative_data_never_crashes(info):
    f = fund(info)
    fs, cov, pos, neg = sc.fundamental_score(f)
    assert fs is None or 0 <= fs <= 100
    assert 0 <= cov <= 1
    lab = sc.label(tech(), f, 80, fs)
    assert lab in (sc.LABEL_SUSTAINABLE, sc.LABEL_UNBACKED, sc.LABEL_OVERBOUGHT, sc.LABEL_WATCH)
    assert isinstance(sc.summary(tech(), f, lab), str)


def test_loss_making_is_flagged_and_unbacked():
    f = fund({"trailingEps": -2.0, "trailingPE": None, "returnOnEquity": -0.1, "revenueGrowth": 0.3})
    assert f["loss_making"] and f["pe_trailing"] is None
    fs, *_ = sc.fundamental_score(f)
    assert sc.label(tech(), f, 80, fs) == sc.LABEL_UNBACKED


def test_score_renormalised_to_available_weight():
    # Solo ROE (8) + deuda (7) + ingresos (5) + PER/forward (15) + sector (15) = 50 % de peso
    f = fund({"trailingPE": 10.0, "forwardPE": 8.0, "returnOnEquity": 0.3,
              "debtToEquity": 10.0, "revenueGrowth": 0.2}, ret_12m=None, med=25.0)
    fs, cov, *_ = sc.fundamental_score(f)
    assert cov == pytest.approx(0.5)
    assert fs == 100.0


def test_no_fundamentals_uses_neutral_half():
    fs, cov, *_ = sc.fundamental_score(fund({}, ret_12m=None))
    assert fs is None
    assert sc.final_score(80, None) == 65.0


def test_sector_medians_need_min_group():
    rows = [{"sector": "A", "pe_trailing": p} for p in (10, 12, 14, 16, 18)] + \
           [{"sector": "B", "pe_trailing": 50}, {"sector": "B", "pe_trailing": None}]
    med = sc.sector_medians(rows)
    assert med["A"] == 14 and "B" not in med and med["*"] == 15


def test_financials_skip_debt_ratio():
    f = fund({**BASE_INFO, "sector": "Financial Services", "debtToEquity": 900.0})
    _, _, _, neg = sc.fundamental_score(f)
    assert not any("Deuda" in n for n in neg)


# --- Doble control del PER y tope de crecimiento -------------------------------------

def test_pe_matching_statements_is_not_suspect():
    f = fund({**BASE_INFO, "currentPrice": 100.0, "trailingPE": 20.0, "ttmEpsStatements": 5.1,
              "currency": "USD", "financialCurrency": "USD"})
    assert f["pe_check"] == pytest.approx(100 / 5.1) and not f["pe_suspect"]


def test_pe_mismatch_is_suspect_and_skips_per_components():
    # Caso Repsol: Yahoo 9,98 frente a ~13,9 por estados financieros
    info = {**BASE_INFO, "currentPrice": 30.75, "trailingPE": 9.98, "forwardPE": 8.18,
            "ttmEpsStatements": 2.22, "currency": "EUR", "financialCurrency": "EUR"}
    f = fund(info, med=18.0)
    assert f["pe_suspect"]
    fs, cov, pos, neg = sc.fundamental_score(f)
    assert any("PER dudoso" in x for x in neg)
    assert not any(x.startswith(("PER ", "PEG")) for x in pos)
    ok = fund({**info, "trailingPE": 13.85}, med=18.0)
    assert sc.fundamental_score(ok)[1] > cov  # sin sospecha, los componentes del PER cuentan
    assert sc.sector_medians([f]) == {}


def test_pence_listing_and_foreign_reporting_currency():
    gbp = fund({**BASE_INFO, "currentPrice": 1500.0, "trailingPE": 15.0, "ttmEpsStatements": 1.0,
                "currency": "GBp", "financialCurrency": "GBP"})
    assert gbp["pe_check"] == pytest.approx(15.0) and not gbp["pe_suspect"]
    usd = fund({**BASE_INFO, "currentPrice": 50.0, "trailingPE": 15.0, "ttmEpsStatements": 1.0,
                "currency": "EUR", "financialCurrency": "USD"})
    assert usd["pe_check"] is None and not usd["pe_suspect"]


def test_atypical_eps_growth_is_capped():
    f = fund({**BASE_INFO, "earningsGrowth": 4.945}, ret_12m=1.16)
    assert f["eps_atypical"] and f["eps_growth_raw"] == pytest.approx(4.945)
    assert f["eps_growth"] == sc.EPS_GROWTH_CAP
    assert f["pe_expansion"] == pytest.approx(2.16 / 2.5 - 1)
    _, _, _, neg = sc.fundamental_score(f)
    assert any("atípico" in x for x in neg)
    assert "tope" in sc.summary(tech(ret_12m=1.16), f, sc.LABEL_SUSTAINABLE)


def test_suspect_pe_cannot_be_labelled_sustainable():
    info = {**BASE_INFO, "earningsGrowth": 0.40, "currentPrice": 30.0, "trailingPE": 4.7,
            "ttmEpsStatements": 1.3, "currency": "BRL", "financialCurrency": "BRL"}
    lab, t, fs, f = classify(tech(), info)
    assert f["pe_suspect"] and fs >= 55 and lab == sc.LABEL_WATCH
