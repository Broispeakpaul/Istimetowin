"""In-memory synthetic data source for tests and offline demos.

Every report built from this source carries a "SYNTHETIC DATA" banner. It must never be used
for trading decisions.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ..calendars import get_calendar, sessions_in_range
from .base import EARNINGS_COLUMNS, BenchmarkSeries, DataSource, normalise_price_frame

SYNTHETIC_WARNING = "SYNTHETIC DATA: generated test data, not market prices. Do not trade on this report."


def make_ohlcv(sessions: pd.DatetimeIndex, start_price: float = 100.0, daily_ret: float | np.ndarray = 0.0,
               range_pct: float = 0.02, volume: float | np.ndarray = 1_000_000.0,
               shocks: Optional[dict] = None) -> pd.DataFrame:
    """Deterministic bars. `shocks` maps date -> (return, volume multiple)."""
    n = len(sessions)
    rets = np.full(n, daily_ret, dtype=float) if np.isscalar(daily_ret) else np.asarray(daily_ret, dtype=float)
    vols = np.full(n, volume, dtype=float) if np.isscalar(volume) else np.asarray(volume, dtype=float)
    rets = rets.copy()
    vols = vols.copy()
    for d, (r, vm) in (shocks or {}).items():
        i = sessions.get_loc(pd.Timestamp(d))
        rets[i] = r
        vols[i] = vols[i] * vm
    rets[0] = 0.0
    close = start_price * np.cumprod(1 + rets)
    prev = np.concatenate([[close[0]], close[:-1]])
    open_ = prev * (1 + rets * 0.6)
    high = np.maximum(open_, close) * (1 + range_pct / 2)
    low = np.minimum(open_, close) * (1 - range_pct / 2)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "adj_close": close,
                         "volume": vols}, index=pd.DatetimeIndex(sessions, name="date"))


class FixtureSource(DataSource):
    name = "fixture"

    def __init__(self, prices: dict[str, pd.DataFrame], benchmark: pd.DataFrame, vix: pd.Series,
                 fx: Optional[dict[str, pd.Series]] = None, earnings: Optional[pd.DataFrame] = None,
                 implied_moves: Optional[dict[str, float]] = None, benchmark_name: str = "WLS Index (synthetic)") -> None:
        super().__init__()
        self._prices = {k: normalise_price_frame(v) for k, v in prices.items()}
        self._bench = normalise_price_frame(benchmark)
        self._vix = vix
        self._fx = fx or {}
        self._earn = earnings if earnings is not None else pd.DataFrame(columns=EARNINGS_COLUMNS)
        self._implied = implied_moves or {}
        self._bench_name = benchmark_name
        self.calls: list[str] = []
        self.warnings.add(SYNTHETIC_WARNING)

    def get_prices(self, members, tickers, start, end):
        self.calls.append("prices")
        return {t: self._prices[t].loc[pd.Timestamp(start):pd.Timestamp(end)] for t in tickers if t in self._prices}

    def get_fx(self, currencies, start, end):
        self.calls.append("fx")
        return {c: self._fx[c].loc[pd.Timestamp(start):pd.Timestamp(end)] for c in currencies if c in self._fx}

    def get_benchmark(self, start, end):
        self.calls.append("benchmark")
        return BenchmarkSeries(self._bench.loc[pd.Timestamp(start):pd.Timestamp(end)], self._bench_name)

    def get_vix(self, start, end):
        self.calls.append("vix")
        return self._vix.loc[pd.Timestamp(start):pd.Timestamp(end)]

    def get_earnings(self, members, tickers, start, end):
        self.calls.append("earnings")
        e = self._earn
        if e.empty:
            return e
        e = e.copy()
        e["report_date"] = pd.to_datetime(e["report_date"])
        return e[e["bbg_ticker"].isin(tickers) & (e["report_date"] >= pd.Timestamp(start)) & (e["report_date"] <= pd.Timestamp(end))]

    def get_implied_moves(self, members, tickers):
        return {t: v for t, v in self._implied.items() if t in tickers}


def demo_source(members: pd.DataFrame, end="2025-10-31", seed: int = 7) -> FixtureSource:
    """A synthetic world for the demo: random-walk prices plus a few planted earnings reactions."""
    rng = np.random.default_rng(seed)
    end = pd.Timestamp(end)
    start = end - pd.Timedelta(days=900)
    prices, events = {}, []
    for i, (t, row) in enumerate(members.iterrows()):
        sess = sessions_in_range(row["calendar"], start, end)
        rets = rng.normal(0.0004, 0.015, len(sess))
        vol = rng.uniform(0.6, 1.4, len(sess)) * 3_000_000
        df = make_ohlcv(sess, start_price=50 + 10 * i, daily_ret=rets, volume=vol)
        prices[t] = df
        # one planted report ~ every 5th name, landing in the last fortnight
        if i % 5 == 0 and len(sess) > 30:
            rd = sess[-1 - (i % 7)]
            k = sess.get_loc(rd)
            bump = 0.10 + 0.01 * (i % 4)
            prices[t] = make_ohlcv(sess, start_price=50 + 10 * i, daily_ret=rets, volume=vol, shocks={sess[k]: (bump, 3.5)})
            events.append({"bbg_ticker": t, "report_date": rd, "timing": "BMO", "report_time_local": "07:00", "source": "fixture"})
        # a scheduled report in the next week
        if i % 3 == 0:
            fut = get_calendar(row["calendar"]).sessions_window(sess[-1], 6)[-1 - (i % 4)]
            events.append({"bbg_ticker": t, "report_date": fut, "timing": "AMC", "report_time_local": "16:30", "source": "fixture"})
    bsess = sessions_in_range("XNYS", start, end)
    bench = make_ohlcv(bsess, start_price=100, daily_ret=rng.normal(0.0003, 0.008, len(bsess)), volume=0.0)
    vix = pd.Series(rng.uniform(14, 22, len(bsess)), index=bsess)
    fx_ccys = {"JPY": 0.0067, "HKD": 0.128, "EUR": 1.08, "GBP": 1.27, "KRW": 0.00072, "TWD": 0.031, "CHF": 1.13,
               "CAD": 0.72, "AUD": 0.65, "DKK": 0.145, "SEK": 0.095, "CNY": 0.14, "INR": 0.012}
    fx = {c: pd.Series(v * np.cumprod(1 + rng.normal(0, 0.003, len(bsess))), index=bsess) for c, v in fx_ccys.items()}
    return FixtureSource(prices, bench, vix, fx=fx, earnings=pd.DataFrame(events, columns=EARNINGS_COLUMNS))
