"""Local parquet cache so reruns do not refetch.

Layout: <cache_dir>/<source>/<kind>/<safe key>.parquet plus a JSON manifest that records the
date range covered and when it was fetched. Bars older than the last cached date are treated
as final; the last few days are refetched so a partial (intraday) bar gets replaced.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from .base import BenchmarkSeries, DataSource, normalise_price_frame

REFRESH_TAIL_DAYS = 5


def safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", key).strip("_") or "_"


class ParquetStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.manifest: dict = json.loads(self.manifest_path.read_text("utf-8")) if self.manifest_path.exists() else {}

    def _path(self, kind: str, key: str) -> Path:
        p = self.root / kind / f"{safe_key(key)}.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def read(self, kind: str, key: str) -> pd.DataFrame | None:
        p = self._path(kind, key)
        return pd.read_parquet(p) if p.exists() else None

    def write(self, kind: str, key: str, df: pd.DataFrame, meta: dict) -> None:
        df.to_parquet(self._path(kind, key))
        self.manifest[f"{kind}/{key}"] = meta
        self.manifest_path.write_text(json.dumps(self.manifest, indent=1, default=str), "utf-8")

    def meta(self, kind: str, key: str) -> dict:
        return self.manifest.get(f"{kind}/{key}", {})


class CachedSource(DataSource):
    """Wraps any DataSource with a parquet cache. Same interface, so the engine never knows."""

    def __init__(self, inner: DataSource, cache_dir: Path, earnings_ttl_hours: float = 12,
                 now: pd.Timestamp | None = None) -> None:
        super().__init__()
        self.inner = inner
        self.name = inner.name
        self.warnings = inner.warnings
        self.store = ParquetStore(Path(cache_dir) / inner.name)
        self.earnings_ttl = pd.Timedelta(hours=earnings_ttl_hours)
        self._now = now

    @property
    def now(self) -> pd.Timestamp:
        return self._now if self._now is not None else pd.Timestamp.now(tz="UTC")

    # -- generic covered-range logic -------------------------------------------------
    def _need(self, kind: str, key: str, start: pd.Timestamp, end: pd.Timestamp):
        """Return (cached frame or None, fetch_start or None)."""
        cached = self.store.read(kind, key)
        meta = self.store.meta(kind, key)
        if cached is None or not meta:
            return None, start
        c_start, c_end = pd.Timestamp(meta["start"]), pd.Timestamp(meta["end"])
        fetched_at = pd.Timestamp(meta["fetched_at"])
        if start < c_start:
            return None, start  # need older history: refetch whole range
        # The cache is complete up to the day it was fetched (minus a safety tail).
        complete_to = min(c_end, fetched_at.tz_convert(None).normalize() - pd.Timedelta(days=1))
        if end <= complete_to:
            return cached, None
        return cached, max(start, complete_to - pd.Timedelta(days=REFRESH_TAIL_DAYS))

    def _merge_write(self, kind, key, cached, fresh, start, end):
        frames = [f for f in (cached, fresh) if f is not None and not f.empty]
        if not frames:
            return None
        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        meta = self.store.meta(kind, key)
        m_start = min(pd.Timestamp(meta["start"]), start) if meta and cached is not None else start
        m_end = max(pd.Timestamp(meta["end"]), end) if meta and cached is not None else end
        self.store.write(kind, key, df, {"start": m_start, "end": m_end, "fetched_at": self.now})
        return df

    # -- interface -------------------------------------------------------------------
    def get_prices(self, members, tickers, start, end):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        out, to_fetch = {}, {}
        for t in tickers:
            cached, fetch_from = self._need("prices", t, start, end)
            if fetch_from is None:
                out[t] = cached.loc[start:end]
            else:
                to_fetch.setdefault(fetch_from, []).append((t, cached))
        for fetch_from, items in to_fetch.items():
            fresh = self.inner.get_prices(members, [t for t, _ in items], fetch_from, end)
            for t, cached in items:
                df = self._merge_write("prices", t, cached, fresh.get(t), start, end)
                if df is not None:
                    out[t] = normalise_price_frame(df).loc[start:end]
        return out

    def get_fx(self, currencies, start, end):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        out = {}
        for ccy in currencies:
            cached, fetch_from = self._need("fx", ccy, start, end)
            if fetch_from is None:
                out[ccy] = cached["rate"].loc[start:end]
                continue
            fresh = self.inner.get_fx([ccy], fetch_from, end).get(ccy)
            fresh_df = fresh.to_frame("rate") if fresh is not None else None
            df = self._merge_write("fx", ccy, cached, fresh_df, start, end)
            if df is not None:
                out[ccy] = df["rate"].loc[start:end]
        return out

    def get_benchmark(self, start, end):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        cached, fetch_from = self._need("benchmark", "benchmark", start, end)
        meta = self.store.meta("benchmark", "benchmark")
        if fetch_from is None:
            return BenchmarkSeries(cached.loc[start:end], meta.get("name", ""), meta.get("warning", ""))
        fresh = self.inner.get_benchmark(fetch_from, end)
        if cached is not None and meta.get("name") != fresh.name:
            cached = None  # benchmark identity changed (e.g. VT -> WLS); do not mix series
        df = self._merge_write("benchmark", "benchmark", cached, fresh.prices, start, end)
        self.store.manifest["benchmark/benchmark"].update({"name": fresh.name, "warning": fresh.warning})
        self.store.manifest_path.write_text(json.dumps(self.store.manifest, indent=1, default=str), "utf-8")
        return BenchmarkSeries(normalise_price_frame(df).loc[start:end], fresh.name, fresh.warning)

    def get_vix(self, start, end):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        cached, fetch_from = self._need("vix", "vix", start, end)
        if fetch_from is None:
            return cached["close"].loc[start:end]
        fresh = self.inner.get_vix(fetch_from, end)
        df = self._merge_write("vix", "vix", cached, fresh.to_frame("close") if fresh is not None else None, start, end)
        return df["close"].loc[start:end] if df is not None else pd.Series(dtype=float)

    def get_earnings(self, members, tickers, start, end):
        """Earnings calendars change often, so they use a time-to-live instead of a date range."""
        frames, stale = [], []
        for t in tickers:
            cached = self.store.read("earnings", t)
            meta = self.store.meta("earnings", t)
            fresh_enough = meta and (self.now - pd.Timestamp(meta["fetched_at"])) < self.earnings_ttl
            if cached is not None and fresh_enough:
                frames.append(cached)
            else:
                stale.append(t)
        if stale:
            fresh = self.inner.get_earnings(members, stale, start, end)
            for t in stale:
                part = fresh[fresh["bbg_ticker"] == t] if not fresh.empty else fresh
                part = part.reset_index(drop=True)
                self.store.write("earnings", t, part.astype({"report_time_local": str}),
                                 {"start": start, "end": end, "fetched_at": self.now})
                frames.append(part)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=["bbg_ticker", "report_date", "timing", "report_time_local", "source"])
        df = pd.concat(frames, ignore_index=True)
        df["report_date"] = pd.to_datetime(df["report_date"])
        return df

    def get_implied_moves(self, members, tickers):
        return self.inner.get_implied_moves(members, tickers)
