"""DataSource interface. Adapters return raw data only; no trading rules live here.

Canonical identifiers everywhere in the platform are Bloomberg tickers ("7203 JT Equity").
Each adapter maps them to its own symbols using the universe table.

Price frames: index = exchange-local session date (tz-naive, normalised), columns
  open, high, low, close, adj_close, volume
  - prices are in the listing's quote currency (may be a minor unit such as GBp)
  - close/volume are split-adjusted; adj_close is also dividend-adjusted
FX series: USD per one unit of the currency, indexed by date.
Earnings frame columns: bbg_ticker, report_date (local date), timing, report_time_local, source
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

PRICE_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]
EARNINGS_COLUMNS = ["bbg_ticker", "report_date", "timing", "report_time_local", "source"]


class DataUnavailable(RuntimeError):
    """Raised when a source cannot supply a requested series."""


@dataclass
class BenchmarkSeries:
    prices: pd.DataFrame            # PRICE_COLUMNS, USD
    name: str
    warning: str = ""               # non-empty when a fallback is in use


@dataclass
class SourceWarnings:
    messages: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        if msg and msg not in self.messages:
            self.messages.append(msg)


def empty_prices() -> pd.DataFrame:
    return pd.DataFrame(columns=PRICE_COLUMNS, index=pd.DatetimeIndex([], name="date"), dtype=float)


def normalise_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce an adapter frame to the canonical layout (sorted, unique, tz-naive dates)."""
    if df is None or df.empty:
        return empty_prices()
    out = df.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out.index.name = "date"
    for c in PRICE_COLUMNS:
        if c not in out.columns:
            out[c] = out["close"] if c == "adj_close" and "close" in out.columns else float("nan")
    out = out[PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["close"])


class DataSource(ABC):
    name: str = "abstract"

    def __init__(self) -> None:
        self.warnings = SourceWarnings()

    @abstractmethod
    def get_prices(self, members: pd.DataFrame, tickers: list[str], start, end) -> dict[str, pd.DataFrame]:
        """Daily bars per bbg_ticker. Missing tickers are simply absent from the result."""

    @abstractmethod
    def get_fx(self, currencies: list[str], start, end) -> dict[str, pd.Series]:
        """USD per unit of each (major-unit) currency."""

    @abstractmethod
    def get_benchmark(self, start, end) -> BenchmarkSeries:
        """WLS Index in USD, or a clearly-labelled fallback."""

    @abstractmethod
    def get_vix(self, start, end) -> pd.Series:
        """VIX daily closes."""

    @abstractmethod
    def get_earnings(self, members: pd.DataFrame, tickers: list[str], start, end) -> pd.DataFrame:
        """Past and scheduled earnings reports in EARNINGS_COLUMNS layout."""

    def get_implied_moves(self, members: pd.DataFrame, tickers: list[str]) -> dict[str, float]:
        """Implied earnings move per ticker, if the source has one. Default: none."""
        return {}
