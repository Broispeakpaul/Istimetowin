"""MarketData: USD conversion and point-in-time views shared by the live engine and the backtest."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ..config import Config
from ..inputs import ManualInputs
from .base import BenchmarkSeries, DataSource

# Quote units that are 1/100 of the major currency.
MINOR_UNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ZAX": ("ZAR", 0.01),
               "ILA": ("ILS", 0.01), "ILs": ("ILS", 0.01)}
OVERRIDE_MATCH_DAYS = 7


def major_unit(ccy: str) -> tuple[str, float]:
    """'GBp' -> ('GBP', 0.01); 'JPY' -> ('JPY', 1.0)."""
    return MINOR_UNITS.get(ccy, (ccy.upper(), 1.0))


def to_usd_frame(local: pd.DataFrame, currency: str, fx: dict[str, pd.Series]) -> pd.DataFrame:
    """Add fx, adj_close_usd, close_usd and value_usd columns. FX is taken as of each bar's date
    (last available rate on or before it), never from a later date."""
    df = local.copy()
    major, scale = major_unit(currency)
    if major == "USD":
        rate = pd.Series(1.0, index=df.index)
    elif major in fx and not fx[major].empty:
        rate = fx[major].sort_index().reindex(df.index.union(fx[major].index)).ffill().reindex(df.index)
    else:
        rate = pd.Series(np.nan, index=df.index)
    df["fx"] = rate * scale  # USD per quote unit
    df["adj_close_usd"] = df["adj_close"] * df["fx"]
    df["close_usd"] = df["close"] * df["fx"]
    df["value_usd"] = df["close_usd"] * df["volume"]
    return df


def merge_earnings(source_events: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    """Overrides replace any source record for the same ticker within +/- 7 days, or are added."""
    cols = ["bbg_ticker", "report_date", "timing", "report_time_local", "source"]
    src = source_events.copy() if source_events is not None and not source_events.empty else pd.DataFrame(columns=cols)
    src["report_date"] = pd.to_datetime(src["report_date"])
    if overrides is None or overrides.empty:
        return src[cols].sort_values(["report_date", "bbg_ticker"]).reset_index(drop=True)
    keep = np.ones(len(src), dtype=bool)
    for _, o in overrides.iterrows():
        near = (src["bbg_ticker"] == o["bbg_ticker"]) & ((src["report_date"] - o["report_date"]).abs().dt.days <= OVERRIDE_MATCH_DAYS)
        keep &= ~near.to_numpy()
    out = pd.concat([src[keep], overrides[cols]], ignore_index=True)
    return out.sort_values(["report_date", "bbg_ticker"]).reset_index(drop=True)


@dataclass
class MarketData:
    members: pd.DataFrame
    prices: dict[str, pd.DataFrame]          # USD-enriched frames per bbg_ticker
    benchmark: BenchmarkSeries
    vix: pd.Series
    earnings: pd.DataFrame
    implied_moves: dict[str, float] = field(default_factory=dict)
    source_name: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_of(self, d, now_utc: Optional[pd.Timestamp] = None) -> "MarketView":
        return MarketView(self, pd.Timestamp(d).normalize(), now_utc)

    @classmethod
    def load(cls, source: DataSource, members: pd.DataFrame, manual: ManualInputs, cfg: Config,
             start, end, tickers: Optional[list[str]] = None,
             earnings_tickers: Optional[list[str]] = None) -> "MarketData":
        """Fetch prices for `tickers` and report dates for `earnings_tickers` (default: same list)."""
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        tickers = list(members.index) if tickers is None else tickers
        earnings_tickers = tickers if earnings_tickers is None else earnings_tickers
        warnings: list[str] = []
        local = source.get_prices(members, tickers, start, end)
        ccys = sorted({major_unit(members.at[t, "currency"])[0] for t in local} - {"USD"})
        fx = source.get_fx(ccys, start, end) if ccys else {}
        prices = {t: to_usd_frame(df, members.at[t, "currency"], fx) for t, df in local.items()}
        bench = source.get_benchmark(start, end)
        if bench.warning:
            warnings.append(bench.warning)
        vix = source.get_vix(start, end)
        horizon_end = end + pd.Timedelta(days=45)
        events = source.get_earnings(members, earnings_tickers, start, horizon_end)
        earnings = merge_earnings(events, manual.override_events())
        implied = source.get_implied_moves(members, tickers)
        warnings.extend(source.warnings.messages)
        return cls(members=members, prices=prices, benchmark=bench, vix=vix, earnings=earnings,
                   implied_moves=implied, source_name=source.name, warnings=warnings)


class MarketView:
    """Everything the engine may see on date t: bars dated on or before t, nothing later.

    Scheduled (future) report dates stay visible because the market knows them in advance.
    """

    def __init__(self, data: MarketData, as_of: pd.Timestamp, now_utc: Optional[pd.Timestamp] = None) -> None:
        self.data = data
        self.as_of = as_of
        self.now_utc = now_utc
        self.members = data.members
        self._cache: dict[str, pd.DataFrame] = {}

    def prices(self, ticker: str) -> Optional[pd.DataFrame]:
        if ticker not in self._cache:
            df = self.data.prices.get(ticker)
            self._cache[ticker] = None if df is None else df.loc[: self.as_of]
        return self._cache[ticker]

    def benchmark(self) -> pd.DataFrame:
        return self.data.benchmark.prices.loc[: self.as_of]

    def vix(self) -> pd.Series:
        return self.data.vix.loc[: self.as_of] if self.data.vix is not None else pd.Series(dtype=float)

    def earnings(self) -> pd.DataFrame:
        return self.data.earnings

    @property
    def benchmark_name(self) -> str:
        return self.data.benchmark.name

    @property
    def benchmark_warning(self) -> str:
        return self.data.benchmark.warning
