"""Yahoo Finance adapter (yfinance). For development and testing at home.

Limits: no WLS Index (VT ETF stands in, with a warning), report timing is often missing,
no revenue/guidance/revision data (those stay CONFIRM MANUALLY), and an unofficial API
that can change or rate-limit without notice.
"""
from __future__ import annotations

import logging

import pandas as pd

from ..calendars import classify_timing, get_calendar
from ..config import Config
from .base import (EARNINGS_COLUMNS, BenchmarkSeries, DataSource, DataUnavailable,
                   normalise_price_frame)

log = logging.getLogger(__name__)

_RENAME = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
           "Adj Close": "adj_close", "Volume": "volume"}


def _yf():
    try:
        import yfinance as yf  # imported lazily so tests never touch the network
    except ImportError as exc:  # pragma: no cover
        raise DataUnavailable("yfinance is not installed: pip install yfinance") from exc
    return yf


class YahooAdapter(DataSource):
    name = "yahoo"

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg

    def _download(self, symbols: list[str], start, end) -> dict[str, pd.DataFrame]:
        if not symbols:
            return {}
        yf = _yf()
        end_excl = pd.Timestamp(end) + pd.Timedelta(days=1)  # yfinance end is exclusive
        raw = yf.download(symbols, start=pd.Timestamp(start).strftime("%Y-%m-%d"),
                          end=end_excl.strftime("%Y-%m-%d"), auto_adjust=False, actions=False,
                          group_by="ticker", progress=False, threads=True)
        out = {}
        for s in symbols:
            try:
                df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = df.rename(columns=_RENAME).dropna(how="all")
            if not df.empty:
                out[s] = normalise_price_frame(df)
        return out

    def get_prices(self, members, tickers, start, end):
        sym = {t: members.at[t, "yahoo_symbol"] for t in tickers if t in members.index and members.at[t, "yahoo_symbol"]}
        missing = sorted(set(tickers) - set(sym))
        if missing:
            self.warnings.add(f"No Yahoo symbol for {len(missing)} member(s): {', '.join(missing[:10])}")
        frames = self._download(sorted(set(sym.values())), start, end)
        out = {t: frames[s] for t, s in sym.items() if s in frames}
        failed = sorted(t for t, s in sym.items() if s not in frames)
        if failed:
            self.warnings.add(f"Yahoo returned no prices for {len(failed)} ticker(s): {', '.join(failed[:10])}")
        return out

    def get_fx(self, currencies, start, end):
        out = {}
        for ccy in currencies:
            if ccy == "USD":
                continue
            frames = self._download([f"{ccy}USD=X"], start, end)
            if frames:
                out[ccy] = frames[f"{ccy}USD=X"]["close"]
            else:
                self.warnings.add(f"FX {ccy}USD unavailable from Yahoo; {ccy} listings will be MISSING")
        return out

    def get_benchmark(self, start, end):
        sym = self.cfg.benchmark.fallback_yahoo
        frames = self._download([sym], start, end)
        if sym not in frames:
            raise DataUnavailable(f"Benchmark fallback {sym} unavailable from Yahoo")
        warning = (f"BENCHMARK FALLBACK: {sym} ETF is standing in for the WLS Index. Yahoo has no WLS data; "
                   f"relative returns are approximate. Use the Bloomberg adapter for decisions.")
        return BenchmarkSeries(frames[sym], name=sym, warning=warning)

    def get_vix(self, start, end):
        sym = self.cfg.benchmark.vix_yahoo
        frames = self._download([sym], start, end)
        if sym not in frames:
            self.warnings.add("VIX unavailable from Yahoo; regime check will be MISSING")
            return pd.Series(dtype=float)
        return frames[sym]["close"]

    def get_earnings(self, members, tickers, start, end):
        yf = _yf()
        rows = []
        for t in tickers:
            if t not in members.index:
                continue
            sym = members.at[t, "yahoo_symbol"]
            code = members.at[t, "calendar"]
            try:
                ed = yf.Ticker(sym).get_earnings_dates(limit=self.cfg.earnings.history_limit)
            except Exception as exc:  # network / parsing problems are reported, not raised
                self.warnings.add(f"Yahoo earnings dates failed for {t} ({sym}): {exc.__class__.__name__}")
                continue
            if ed is None or len(ed) == 0:
                continue
            tz = get_calendar(code).tz
            for ts in ed.index:
                ts = pd.Timestamp(ts)
                local = ts.tz_convert(tz) if ts.tzinfo else ts.tz_localize(tz)
                rows.append({"bbg_ticker": t, "report_date": local.normalize().tz_localize(None),
                             "timing": classify_timing(code, local),
                             "report_time_local": local.strftime("%H:%M") if local.time().hour or local.time().minute else "",
                             "source": "yahoo"})
        df = pd.DataFrame(rows, columns=EARNINGS_COLUMNS)
        if df.empty:
            return df
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        return df[(df["report_date"] >= s) & (df["report_date"] <= e)].drop_duplicates(["bbg_ticker", "report_date"])
