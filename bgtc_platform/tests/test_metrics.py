import numpy as np
import pandas as pd
import pytest

from screener.rules import metrics as m
from screener.data.market import to_usd_frame
from tests.synthetic import D1, flat_bench, sessions, stock


def usd(df):
    return to_usd_frame(df, "USD", {})


def test_day_one_return_and_benchmark_window():
    df = usd(stock(ret_d1=0.12))
    r, prev = m.day_one_return(df, D1, "XNYS")
    assert r.value == pytest.approx(0.12) and prev == pd.Timestamp("2025-10-29")
    b = flat_bench()
    b.loc[D1:, "adj_close"] *= 1.02
    rb = m.benchmark_window_return(b, prev, D1, "XNYS")
    assert rb.value == pytest.approx(0.02) and rb.status == m.OK
    assert m.relative_return(0.12, 0.02) == pytest.approx(0.10)
    assert m.relative_return(0.12, 0.02, "ratio") == pytest.approx(1.12 / 1.02 - 1)


def test_benchmark_not_yet_closed_is_provisional():
    b = flat_bench().loc[:"2025-10-29"]
    rb = m.benchmark_window_return(b, pd.Timestamp("2025-10-29"), D1, "XNYS")
    assert rb.status == m.PROVISIONAL


def test_volume_ratio_excludes_day_one_from_average():
    df = usd(stock(vol_mult=2.5))
    assert m.volume_ratio(df, D1, 50).value == pytest.approx(2.5)


def test_volume_ratio_needs_full_history():
    df = usd(stock()).iloc[-200:]
    df = df.loc[pd.Timestamp("2025-09-15"):]
    assert m.volume_ratio(df, D1, 50).status == m.MISSING


def test_adv_usd():
    df = usd(stock(price=100.0, volume=1_000_000))
    assert m.adv_usd(df, D1, 50).value == pytest.approx(100e6)


def test_atr_pct_flat_two_percent_range():
    df = usd(stock(ret_d1=0.0, vol_mult=1.0, range_pct=0.02))
    assert m.atr_pct(df, D1, 14, "wilder").value == pytest.approx(0.02, rel=1e-6)
    assert m.atr_pct(df, D1, 14, "simple").value == pytest.approx(0.02, rel=1e-6)


def test_atr_including_gap_day_is_larger():
    df = usd(stock(ret_d1=0.10))
    with_d1 = m.atr_pct(df, D1, 14, "wilder", include_d=True).value
    without = m.atr_pct(df, D1, 14, "wilder", include_d=False).value
    assert with_d1 > without


def test_adjusted_beta_matches_bloomberg_formula():
    s = sessions()
    rng = np.random.default_rng(1)
    br = rng.normal(0, 0.01, len(s))
    bench = pd.Series(100 * np.cumprod(1 + br), index=s)
    wk_b = bench.resample("W-FRI").last()
    # build a stock whose weekly returns are exactly 1.5x the benchmark's
    wk_s = 50 * np.cumprod(1 + 1.5 * wk_b.pct_change().fillna(0))
    stock_daily = wk_s.reindex(s, method="ffill")
    adj, raw = m.adjusted_beta(stock_daily, bench, s[-1])
    assert raw == pytest.approx(1.5, rel=2e-3)  # holiday weeks shift a few Friday labels
    assert adj.value == pytest.approx(0.67 * raw + 0.33, rel=1e-9)


def test_beta_missing_with_short_history():
    s = sessions()[-100:]
    x = pd.Series(np.linspace(1, 2, len(s)), index=s)
    adj, raw = m.adjusted_beta(x, x, s[-1])
    assert adj.status == m.MISSING and raw is None
