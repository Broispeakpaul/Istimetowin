"""Pure metric functions. Inputs are already truncated to the as-of date by MarketView."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .. import calendars as cal

OK, MISSING, PROVISIONAL = "OK", "MISSING", "PROVISIONAL"


@dataclass
class Metric:
    value: Optional[float]
    status: str = OK
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None and self.status != MISSING


def missing(note: str) -> Metric:
    return Metric(None, MISSING, note)


def _num(x) -> Optional[float]:
    return None if x is None or pd.isna(x) else float(x)


def day_one_return(df: Optional[pd.DataFrame], d: pd.Timestamp, calendar_code: str | None = None) -> tuple[Metric, Optional[pd.Timestamp]]:
    """USD return from the previous bar's close to day one's close (adjusted prices)."""
    if df is None or df.empty or d not in df.index:
        return missing(f"no price bar on {d.date()}"), None
    i = df.index.get_loc(d)
    if i == 0:
        return missing("no bar before day one"), None
    prev = df.index[i - 1]
    p0, p1 = _num(df["adj_close_usd"].iloc[i - 1]), _num(df["adj_close_usd"].iloc[i])
    if p0 is None or p1 is None or p0 <= 0:
        return missing("USD price missing (check FX)"), prev
    note = ""
    if calendar_code and cal.previous_session(calendar_code, d) != prev:
        note = f"data gap: previous bar is {prev.date()}"
    return Metric(p1 / p0 - 1.0, OK, note), prev


def benchmark_window_return(bench: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                            bench_calendar: str) -> Metric:
    """Benchmark return from its close on/before `start` to its close on/before `end`.

    PROVISIONAL when the benchmark should have a close on `end` but it is not yet available
    (e.g. an Asia-session run before the WLS close).
    """
    if bench is None or bench.empty:
        return missing("benchmark unavailable")
    s = bench["adj_close"].dropna()
    s0, s1 = s.loc[:start], s.loc[:end]
    if s0.empty or s1.empty:
        return missing("benchmark history too short")
    value = float(s1.iloc[-1] / s0.iloc[-1] - 1.0)
    if cal.is_session(bench_calendar, end) and s.index.max() < end:
        return Metric(value, PROVISIONAL, f"benchmark close for {end.date()} not yet available")
    return Metric(value)


def relative_return(r: float, rb: float, method: str = "difference") -> float:
    if method == "ratio":
        return (1.0 + r) / (1.0 + rb) - 1.0
    return r - rb


def volume_ratio(df: Optional[pd.DataFrame], d: pd.Timestamp, window: int) -> Metric:
    """Day-one volume / mean volume of the `window` sessions before day one."""
    if df is None or d not in df.index:
        return missing("no bar on day one")
    i = df.index.get_loc(d)
    hist = df["volume"].iloc[max(0, i - window): i].dropna()
    if len(hist) < window:
        return missing(f"only {len(hist)} of {window} sessions of volume history")
    avg = float(hist.mean())
    v = _num(df["volume"].iloc[i])
    if v is None or avg <= 0:
        return missing("volume missing or zero")
    return Metric(v / avg)


def adv_usd(df: Optional[pd.DataFrame], d: pd.Timestamp, window: int) -> Metric:
    """Mean daily USD value traded over the `window` sessions before day one."""
    if df is None or d not in df.index:
        return missing("no bar on day one")
    i = df.index.get_loc(d)
    hist = df["value_usd"].iloc[max(0, i - window): i]
    if hist.notna().sum() < window:
        return missing(f"only {int(hist.notna().sum())} of {window} sessions of USD value traded")
    return Metric(float(hist.mean()))


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range on dividend-adjusted OHLC (scaled by adj_close/close)."""
    k = (df["adj_close"] / df["close"]).fillna(1.0)
    h, l, c = df["high"] * k, df["low"] * k, df["adj_close"]
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr_pct(df: Optional[pd.DataFrame], d: pd.Timestamp, window: int = 14, method: str = "wilder",
            include_d: bool = True) -> Metric:
    """ATR as a fraction of the close, as of day d (optionally excluding d's own range)."""
    if df is None or d not in df.index:
        return missing("no bar on sizing date")
    i = df.index.get_loc(d)
    end = i + 1 if include_d else i
    sub = df.iloc[:end]
    tr = true_range(sub).iloc[1:].dropna()
    if len(tr) < window:
        return missing(f"only {len(tr)} true ranges for ATR{window}")
    if method == "wilder":
        atr = float(tr.ewm(alpha=1.0 / window, adjust=False).mean().iloc[-1])
    else:
        atr = float(tr.iloc[-window:].mean())
    px = _num(sub["adj_close"].iloc[-1])
    if not px:
        return missing("no close for ATR%")
    return Metric(atr / px)


def sma_status(series: pd.Series, window: int) -> tuple[Optional[float], Optional[float]]:
    s = series.dropna()
    if len(s) < window:
        return (float(s.iloc[-1]) if len(s) else None), None
    return float(s.iloc[-1]), float(s.iloc[-window:].mean())


def weekly_returns(close: pd.Series, freq: str = "W-FRI") -> pd.Series:
    return close.dropna().resample(freq).last().pct_change().dropna()


def adjusted_beta(stock_close_usd: pd.Series, bench_close: pd.Series, as_of: pd.Timestamp, years: int = 2,
                  freq: str = "W-FRI", raw_weight: float = 0.67, prior_weight: float = 0.33,
                  min_obs: int = 52) -> tuple[Metric, Optional[float]]:
    """Bloomberg-style adjusted beta from weekly USD returns over `years`. Returns (adjusted, raw)."""
    start = as_of - pd.DateOffset(years=years)
    a = weekly_returns(stock_close_usd.loc[start:as_of], freq)
    b = weekly_returns(bench_close.loc[start:as_of], freq)
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) < min_obs:
        return missing(f"{len(j)} weekly obs < {min_obs}"), None
    var = float(np.var(j.iloc[:, 1], ddof=1))
    if var == 0:
        return missing("benchmark variance is zero"), None
    raw = float(np.cov(j.iloc[:, 0], j.iloc[:, 1], ddof=1)[0, 1] / var)
    return Metric(raw_weight * raw + prior_weight * 1.0), raw


def window_rel_return(df: pd.DataFrame, bench: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                      method: str = "difference") -> Optional[float]:
    """Relative USD return between two dates (as-of closes). Used by the event study."""
    s = df["adj_close_usd"].dropna()
    b = bench["adj_close"].dropna()
    s0, s1, b0, b1 = s.loc[:start], s.loc[:end], b.loc[:start], b.loc[:end]
    if min(len(s0), len(s1), len(b0), len(b1)) == 0:
        return None
    return relative_return(s1.iloc[-1] / s0.iloc[-1] - 1, b1.iloc[-1] / b0.iloc[-1] - 1, method)
