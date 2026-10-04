"""Helpers that build synthetic markets for rule tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from screener.calendars import sessions_in_range
from screener.data.fixture import FixtureSource, make_ohlcv
from screener.data.market import MarketData
from screener.inputs import ManualInputs, MANUAL_COLUMNS, OVERRIDE_COLUMNS

START = pd.Timestamp("2023-06-01")
END = pd.Timestamp("2025-11-14")


def sessions(code="XNYS", start=START, end=END):
    return sessions_in_range(code, start, end)


def flat_bench(code="XNYS", ret=0.0, start=START, end=END):
    s = sessions(code, start, end)
    return make_ohlcv(s, start_price=100.0, daily_ret=ret, volume=0.0)


def flat_vix(level=15.0, code="XNYS", start=START, end=END):
    s = sessions(code, start, end)
    return pd.Series(level, index=s)


def manual(checks=None, overrides=None, assume=None) -> ManualInputs:
    c = pd.DataFrame(checks or [], columns=MANUAL_COLUMNS).fillna("")
    o = pd.DataFrame(overrides or [], columns=OVERRIDE_COLUMNS).fillna("")
    return ManualInputs(overrides=o, checks=c, assume=assume)


def market(cfg, members, prices, earnings=None, bench=None, vix=None, fx=None, man=None, implied=None):
    src = FixtureSource(prices, bench if bench is not None else flat_bench(), vix if vix is not None else flat_vix(),
                        fx=fx, earnings=pd.DataFrame(earnings or [], columns=["bbg_ticker", "report_date", "timing", "report_time_local", "source"]),
                        implied_moves=implied)
    return MarketData.load(src, members, man or manual(), cfg, START, END, tickers=list(prices))
