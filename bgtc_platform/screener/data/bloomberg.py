"""Bloomberg adapter (xbbg over blpapi). Only works on a machine with a logged-in Terminal.

Check your university's terminal policy before pulling Bloomberg data into a script, and keep
cached Bloomberg data on the lab machine. Field names come from config.yaml ('bloomberg:');
verify each with FLDS <GO> before first use. This adapter was written without Terminal access
and is covered by tests against a fake `blp` object only, so expect to adjust column names.
"""
from __future__ import annotations

import re

import pandas as pd

from ..calendars import TIMING_AMC, TIMING_BMO, TIMING_DMH, TIMING_UNKNOWN, classify_timing, get_calendar
from ..config import Config
from .base import EARNINGS_COLUMNS, BenchmarkSeries, DataSource, DataUnavailable, normalise_price_frame


def _blp():
    try:
        from xbbg import blp
    except ImportError as exc:  # pragma: no cover
        raise DataUnavailable("xbbg is not installed (pip install xbbg blpapi) or no Terminal session") from exc
    return blp


def _snake(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def parse_bbg_time(value) -> str:
    """Map Bloomberg announcement-time text to a timing code."""
    v = str(value or "").strip().lower()
    if not v or v in ("nan", "none"):
        return TIMING_UNKNOWN
    if "bef" in v or "pre" in v or "bmo" in v:
        return TIMING_BMO
    if "aft" in v or "post" in v or "amc" in v:
        return TIMING_AMC
    if "dur" in v or "intra" in v:
        return TIMING_DMH
    return ""  # a clock time: classify against the session times


class BloombergAdapter(DataSource):
    name = "bloomberg"

    def __init__(self, cfg: Config, blp=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.f = cfg.bloomberg
        self._blp = blp  # injectable for tests

    @property
    def blp(self):
        if self._blp is None:
            self._blp = _blp()
        return self._blp

    def _bdh(self, tickers, fields, start, end, adjust=None) -> pd.DataFrame:
        kw = {"adjust": adjust} if adjust else {}
        df = self.blp.bdh(tickers, fields, pd.Timestamp(start).strftime("%Y-%m-%d"),
                          pd.Timestamp(end).strftime("%Y-%m-%d"), **kw)
        return pd.DataFrame(df)

    def get_prices(self, members, tickers, start, end):
        if not tickers:
            return {}
        pf = self.f.price_fields
        flds = [pf["open"], pf["high"], pf["low"], pf["close"], pf["volume"]]
        adj = self._bdh(tickers, flds, start, end, adjust="all")        # dividend + split adjusted
        spl = self._bdh(tickers, [pf["close"], pf["volume"]], start, end, adjust="split")
        out = {}
        for t in tickers:
            if adj.empty or t not in adj.columns.get_level_values(0):
                continue
            a = adj[t]
            df = pd.DataFrame({"open": a.get(pf["open"]), "high": a.get(pf["high"]), "low": a.get(pf["low"]),
                               "adj_close": a.get(pf["close"])}, index=a.index)
            if not spl.empty and t in spl.columns.get_level_values(0):
                df["close"] = spl[t].get(pf["close"])
                df["volume"] = spl[t].get(pf["volume"])
            else:
                df["close"], df["volume"] = df["adj_close"], a.get(pf["volume"])
            out[t] = normalise_price_frame(df)
        missing = sorted(set(tickers) - set(out))
        if missing:
            self.warnings.add(f"Bloomberg returned no prices for {len(missing)} ticker(s): {', '.join(missing[:10])}")
        return out

    def get_fx(self, currencies, start, end):
        out = {}
        for ccy in currencies:
            if ccy == "USD":
                continue
            tk = self.f.fx_ticker_template.format(ccy=ccy)
            df = self._bdh([tk], [self.f.price_fields["close"]], start, end)
            if df.empty:
                self.warnings.add(f"FX {tk} unavailable; {ccy} listings will be MISSING")
                continue
            s = df[tk][self.f.price_fields["close"]] if isinstance(df.columns, pd.MultiIndex) else df.iloc[:, 0]
            s.index = pd.DatetimeIndex(pd.to_datetime(s.index)).normalize()
            out[ccy] = s.astype(float)
        return out

    def _single(self, ticker, start, end) -> pd.DataFrame:
        pf = self.f.price_fields
        flds = [pf["open"], pf["high"], pf["low"], pf["close"]]
        df = self._bdh([ticker], flds, start, end)
        if df.empty:
            return df
        d = df[ticker] if isinstance(df.columns, pd.MultiIndex) else df
        return normalise_price_frame(pd.DataFrame({"open": d.get(pf["open"]), "high": d.get(pf["high"]),
                                                   "low": d.get(pf["low"]), "close": d.get(pf["close"]),
                                                   "adj_close": d.get(pf["close"]), "volume": 0.0}, index=d.index))

    def get_benchmark(self, start, end):
        df = self._single(self.cfg.benchmark.bloomberg_ticker, start, end)
        if df.empty:
            raise DataUnavailable(f"{self.cfg.benchmark.bloomberg_ticker} returned no data")
        return BenchmarkSeries(df, name=self.cfg.benchmark.bloomberg_ticker)

    def get_vix(self, start, end):
        df = self._single(self.cfg.benchmark.vix_bloomberg, start, end)
        return df["close"] if not df.empty else pd.Series(dtype=float)

    def get_earnings(self, members, tickers, start, end):
        rows = []
        for t in tickers:
            if t not in members.index:
                continue
            code = members.at[t, "calendar"]
            tz = get_calendar(code).tz
            try:
                hist = pd.DataFrame(self.blp.bds(t, self.f.earnings_history_field))
            except Exception as exc:
                self.warnings.add(f"Bloomberg earnings history failed for {t}: {exc.__class__.__name__}")
                hist = pd.DataFrame()
            hist.columns = [_snake(c) for c in hist.columns]
            date_col = next((c for c in hist.columns if "announcement_date" in c or c.endswith("ann_dt")), None)
            time_col = next((c for c in hist.columns if "announcement_time" in c or c.endswith("ann_tm")), None)
            if date_col:
                for _, r in hist.iterrows():
                    rows.append(self._row(t, code, tz, r[date_col], r.get(time_col) if time_col else None, "bloomberg EE"))
            try:
                exp = pd.DataFrame(self.blp.bdp(t, [self.f.expected_report_date_field, self.f.expected_report_time_field]))
                exp.columns = [_snake(c) for c in exp.columns]
                d = exp.iloc[0].get(_snake(self.f.expected_report_date_field)) if not exp.empty else None
                tm = exp.iloc[0].get(_snake(self.f.expected_report_time_field)) if not exp.empty else None
                if d is not None and not pd.isna(d):
                    rows.append(self._row(t, code, tz, d, tm, "bloomberg expected"))
            except Exception as exc:
                self.warnings.add(f"Bloomberg expected report date failed for {t}: {exc.__class__.__name__}")
        df = pd.DataFrame([r for r in rows if r], columns=EARNINGS_COLUMNS)
        if df.empty:
            return df
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        df = df[(df["report_date"] >= s) & (df["report_date"] <= e)]
        return df.drop_duplicates(["bbg_ticker", "report_date"], keep="first")

    @staticmethod
    def _row(t, code, tz, date_value, time_value, source):
        d = pd.to_datetime(date_value, errors="coerce")
        if pd.isna(d):
            return None
        d = d.normalize()
        timing = parse_bbg_time(time_value)
        time_txt = ""
        if timing == "":
            m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(time_value))
            if m:
                local = pd.Timestamp(d.year, d.month, d.day, int(m.group(1)), int(m.group(2))).tz_localize(tz)
                timing, time_txt = classify_timing(code, local), local.strftime("%H:%M")
            else:
                timing = TIMING_UNKNOWN
        return {"bbg_ticker": t, "report_date": d, "timing": timing, "report_time_local": time_txt, "source": source}

    def get_implied_moves(self, members, tickers):
        fld = self.f.implied_move_field
        if not fld or not tickers:
            return {}
        df = pd.DataFrame(self.blp.bdp(tickers, [fld]))
        df.columns = [_snake(c) for c in df.columns]
        col = _snake(fld)
        out = {}
        for t in tickers:
            if t in df.index and col in df.columns and not pd.isna(df.at[t, col]):
                v = float(df.at[t, col])
                out[t] = v / 100.0 if v > 1 else v
        return out

    # ------------------------------------------------------------------ universe export
    def fetch_members(self) -> pd.DataFrame:
        """Build a wls_members.csv frame from MEMB data (INDX_MWEIGHT + descriptive fields)."""
        idx = self.cfg.benchmark.bloomberg_ticker
        m = pd.DataFrame(self.blp.bds(idx, self.f.members_field))
        m.columns = [_snake(c) for c in m.columns]
        tcol = next(c for c in m.columns if "ticker" in c)
        wcol = next(c for c in m.columns if "weight" in c)
        tickers = [f"{str(x).strip()} Equity" if not str(x).strip().endswith("Equity") else str(x).strip() for x in m[tcol]]
        info = pd.DataFrame(self.blp.bdp(tickers, ["NAME", "COUNTRY_ISO", "GICS_SECTOR_NAME", "GICS_INDUSTRY_NAME"]))
        info.columns = [_snake(c) for c in info.columns]
        rows = []
        for tk, w in zip(tickers, m[wcol]):
            r = info.loc[tk] if tk in info.index else {}
            industry = str(r.get("gics_industry_name", "") or "")
            rows.append({"bbg_ticker": tk, "yahoo_symbol": "", "name": r.get("name", ""),
                         "country": r.get("country_iso", ""), "exchange": tk.split()[1] if len(tk.split()) > 2 else "",
                         "sector": r.get("gics_sector_name", ""), "theme": industry_to_theme(industry),
                         "wls_weight": float(w) / 100.0})
        return pd.DataFrame(rows)


THEME_BY_INDUSTRY = {
    "semiconductors & semiconductor equipment": "semiconductors",
    "semiconductors": "semiconductors",
    "software": "software",
    "it services": "software",
}


def industry_to_theme(industry: str) -> str:
    i = industry.strip().lower()
    return THEME_BY_INDUSTRY.get(i, _snake(i) or "unclassified")
