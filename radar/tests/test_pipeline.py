import json

from radar import radar
from radar import scoring as sc

REQUIRED = {
    "ticker": str, "name": str, "market": str, "market_name": str, "sector": str,
    "price": float, "last_date": str, "score": float, "tech_score": float, "label": str,
    "summary": str, "signals_pos": list, "signals_neg": list, "rank": int, "loss_making": bool,
}
NULLABLE_NUM = ["fund_score", "ret_1m", "ret_3m", "ret_6m", "ret_12m", "rsi", "adx", "rvol",
                "dist_52w_high", "rs_3m", "rs_6m", "pe_trailing", "pe_forward", "peg",
                "pe_sector_median", "eps_growth", "pe_expansion", "backing", "roe",
                "debt_to_equity", "revenue_growth", "target_upside"]


def run(tmp_path, cutoff="2026-09-28"):
    code = radar.main(["--source", "fixture", "--out", str(tmp_path), "--cutoff", cutoff])
    assert code == 0
    return json.loads((tmp_path / "latest.json").read_text())


def test_end_to_end_fixture(tmp_path):
    rep = run(tmp_path)
    assert rep["universe_size"] == 31 and "BAD1" in rep["failed"]
    assert rep["coverage"] >= 0.9
    assert rep["data_date"] <= rep["cutoff"]
    rk = rep["ranking"]
    assert len(rk) == rep["in_uptrend"] > 0
    assert [x["rank"] for x in rk] == list(range(1, len(rk) + 1))
    assert all(a["score"] >= b["score"] for a, b in zip(rk, rk[1:]))
    for x in rk:
        for k, typ in REQUIRED.items():
            assert isinstance(x[k], typ), (x["ticker"], k)
        for k in NULLABLE_NUM:
            assert x[k] is None or isinstance(x[k], (int, float)), (x["ticker"], k)
        assert 0 <= x["score"] <= 100
        assert x["label"] in (sc.LABEL_SUSTAINABLE, sc.LABEL_UNBACKED, sc.LABEL_OVERBOUGHT, sc.LABEL_WATCH)
    by = {x["ticker"]: x for x in rk}
    assert "BEAR" not in by
    # Misma serie de precios, distintos fundamentales:
    assert by["SOST"]["label"] == sc.LABEL_SUSTAINABLE
    assert by["PERX"]["label"] == sc.LABEL_UNBACKED
    assert by["SOST"]["score"] > by["NOFD"]["score"] > by["PERX"]["score"]
    assert by["LOSS"]["loss_making"] and by["LOSS"]["pe_trailing"] is None
    assert by["NOFD"]["fund_score"] is None


def test_history_written_and_deduplicated(tmp_path):
    run(tmp_path, "2026-09-28")
    run(tmp_path, "2026-09-29")
    run(tmp_path, "2026-09-29")
    idx = json.loads((tmp_path / "history" / "index.json").read_text())
    assert idx == ["2026-09-29", "2026-09-28"]
    day = json.loads((tmp_path / "history" / "2026-09-29.json").read_text())
    assert len(day["ranking"]) <= 30 and "failed" not in day


def test_todays_partial_bar_is_dropped(tmp_path):
    # La serie sintética tiene una barra el lunes 28 (día de corte): debe descartarse
    # y la última sesión analizada ha de ser el viernes 25.
    rep = run(tmp_path, "2026-09-28")
    assert rep["data_date"] == "2026-09-25"
