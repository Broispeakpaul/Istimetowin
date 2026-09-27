"""Price/volume indicators on daily OHLCV frames.

Frames use columns Open, High, Low, Close, Volume and a date index,
which is what IBGateway.daily_bars() returns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    true_range = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def avg_dollar_volume(df: pd.DataFrame, window: int = 20) -> float:
    dv = (df["Close"] * df["Volume"]).tail(window)
    return float(dv.mean()) if len(dv) else 0.0


def trailing_return(df: pd.DataFrame, lookback: int) -> float:
    closes = df["Close"]
    if len(closes) <= lookback:
        return float("nan")
    return float(closes.iloc[-1] / closes.iloc[-1 - lookback] - 1)


def rs_raw(df: pd.DataFrame, lookbacks: tuple[int, int] = (63, 126)) -> float:
    """Blend of 3- and 6-month returns, weighting the recent quarter more."""
    short, long = lookbacks
    r_short = trailing_return(df, short)
    r_long = trailing_return(df, long)
    if np.isnan(r_long):
        return r_short
    return 0.6 * r_short + 0.4 * r_long


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Map each key to its 0-1 percentile rank among finite values."""
    finite = {k: v for k, v in values.items() if v is not None and np.isfinite(v)}
    if not finite:
        return {k: float("nan") for k in values}
    s = pd.Series(finite)
    ranks = s.rank(pct=True, method="average")
    return {k: float(ranks.get(k, float("nan"))) for k in values}


def snapshot(df: pd.DataFrame, cfg) -> dict:
    """Latest indicator values used by the screener, signals and dashboard."""
    close = df["Close"]
    return {
        "last": float(close.iloc[-1]),
        "adv_usd": avg_dollar_volume(df),
        "ret_3m": trailing_return(df, cfg.rs_lookbacks[0]),
        "ret_6m": trailing_return(df, cfg.rs_lookbacks[1]),
        "rs_raw": rs_raw(df, cfg.rs_lookbacks),
        "sma20": _last(sma(close, cfg.pullback_ma)),
        "sma50": _last(sma(close, cfg.exit_ma)),
        "sma200": _last(sma(close, 200)),
        "atr": _last(atr(df, cfg.atr_period)),
    }


def _last(series: pd.Series) -> float:
    return float(series.iloc[-1]) if len(series) else float("nan")
