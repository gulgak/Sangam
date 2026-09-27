"""El cálculo vectorizado del backtest debe coincidir con el del radar diario en cada
fecha, y no puede depender de datos posteriores (sin 'lookahead')."""
import numpy as np
import pandas as pd
import pytest

from radar import scoring as sc
from radar.sources import synthetic_ohlcv


def _cmp(a, b):
    for k in sc.PANEL_COLS:
        x, y = a[k], b[k]
        if x is None or y is None:
            assert x is None and y is None, k
        elif isinstance(x, bool):
            assert x == y, k
        else:
            assert x == pytest.approx(y, rel=1e-9, abs=1e-9), k


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_panel_matches_daily_radar_at_every_sampled_date(seed):
    df = synthetic_ohlcv(600, 0.0006, 0.015, seed=seed)
    bench = synthetic_ohlcv(640, 0.0003, 0.01, seed=99)["Close"]
    bench = bench.drop(bench.index[::17])  # calendario distinto del índice
    panel = sc.technical_panel(df, bench)
    for i in [150, 219, 220, 221, 300, 451, 599]:
        d = df.index[i]
        ref = sc.technical_metrics(df.iloc[: i + 1], bench)
        if ref is None:
            assert panel.loc[d].isna().all()
            continue
        _cmp(sc.panel_row_to_metrics(panel.loc[d], d), ref)


def test_panel_has_no_lookahead():
    df = synthetic_ohlcv(600, 0.0006, 0.015, seed=5)
    bench = synthetic_ohlcv(600, 0.0003, 0.01, seed=6)["Close"]
    full = sc.technical_panel(df, bench)
    cut = df.index[400]
    part = sc.technical_panel(df[df.index <= cut], bench[bench.index <= cut])
    pd.testing.assert_frame_equal(full.loc[:cut], part, rtol=1e-9, atol=1e-9)
    # Alterar el futuro no cambia el pasado
    df2 = df.copy()
    df2.loc[df2.index > cut, ["Open", "High", "Low", "Close"]] *= 3
    pd.testing.assert_frame_equal(sc.technical_panel(df2, bench).loc[:cut], full.loc[:cut])
