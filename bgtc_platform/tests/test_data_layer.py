import numpy as np
import pandas as pd
import pytest

from screener.data.base import BenchmarkSeries, DataSource
from screener.data.bloomberg import BloombergAdapter, parse_bbg_time
from screener.data.cache import CachedSource
from screener.data.fixture import FixtureSource, make_ohlcv
from screener.data.market import MarketData, major_unit, merge_earnings, to_usd_frame
from tests.synthetic import END, START, flat_bench, flat_vix, manual, sessions


def _fixture(members):
    s = sessions()
    prices = {"AAA US Equity": make_ohlcv(s, 100, 0.001), "VOD LN Equity": make_ohlcv(sessions("XLON"), 120, 0.0)}
    fx = {"GBP": pd.Series(1.25, index=sessions("XLON"))}
    return FixtureSource(prices, flat_bench(), flat_vix(), fx=fx)


def test_usd_conversion_handles_pence():
    s = sessions("XLON")[:5]
    df = make_ohlcv(s, 200.0)  # 200 GBp
    out = to_usd_frame(df, "GBp", {"GBP": pd.Series(1.25, index=s)})
    assert out["close_usd"].iloc[0] == pytest.approx(2.50)
    assert major_unit("ZAc") == ("ZAR", 0.01)


def test_fx_is_taken_as_of_bar_date_never_later():
    s = sessions()[:4]
    df = make_ohlcv(s, 100.0)
    fx = pd.Series([0.01, 0.02], index=[s[0], s[3]])  # gap on s[1], s[2]
    out = to_usd_frame(df, "JPY", {"JPY": fx})
    assert out["fx"].tolist() == [0.01, 0.01, 0.01, 0.02]


def test_missing_fx_gives_nan_not_a_guess():
    s = sessions()[:3]
    out = to_usd_frame(make_ohlcv(s, 100.0), "KRW", {})
    assert out["close_usd"].isna().all()


def test_cache_prevents_refetch(cfg, members):
    inner = _fixture(members)
    now = pd.Timestamp("2025-12-01", tz="UTC")
    src = CachedSource(inner, cfg.paths.cache_dir, now=now)
    a = src.get_prices(members, ["AAA US Equity", "VOD LN Equity"], START, END)
    n_calls = inner.calls.count("prices")
    src2 = CachedSource(inner, cfg.paths.cache_dir, now=now)  # a fresh process re-reading the cache
    b = src2.get_prices(members, ["AAA US Equity", "VOD LN Equity"], START, END)
    assert inner.calls.count("prices") == n_calls
    pd.testing.assert_frame_equal(a["AAA US Equity"], b["AAA US Equity"], check_freq=False)
    src2.get_benchmark(START, END); src2.get_benchmark(START, END)
    assert inner.calls.count("benchmark") == 1


def test_cache_fetches_only_the_new_tail(cfg, members):
    inner = _fixture(members)
    src = CachedSource(inner, cfg.paths.cache_dir, now=pd.Timestamp("2025-06-02", tz="UTC"))
    src.get_prices(members, ["AAA US Equity"], START, "2025-05-30")
    later = CachedSource(inner, cfg.paths.cache_dir, now=pd.Timestamp("2025-12-01", tz="UTC"))
    out = later.get_prices(members, ["AAA US Equity"], START, END)
    assert out["AAA US Equity"].index.max() == pd.Timestamp("2025-11-14")
    assert inner.calls.count("prices") == 2


def test_overrides_replace_source_dates():
    src = pd.DataFrame([{"bbg_ticker": "AAA US Equity", "report_date": pd.Timestamp("2025-10-28"), "timing": "UNKNOWN",
                         "report_time_local": "", "source": "yahoo"}])
    ov = manual(overrides=[{"bbg_ticker": "AAA US Equity", "report_date": "2025-10-29", "timing": "amc"}]).override_events()
    out = merge_earnings(src, ov)
    assert len(out) == 1 and out.iloc[0]["timing"] == "AMC" and out.iloc[0]["source"] == "manual override"


def test_market_view_has_no_look_ahead(cfg, members):
    md = MarketData.load(_fixture(members), members, manual(), cfg, START, END, tickers=["AAA US Equity"])
    v = md.as_of("2025-10-30")
    assert v.prices("AAA US Equity").index.max() == pd.Timestamp("2025-10-30")
    assert v.benchmark().index.max() == pd.Timestamp("2025-10-30")
    assert v.vix().index.max() == pd.Timestamp("2025-10-30")


# ---------------------------------------------------------------- Bloomberg adapter (fake blp)
class FakeBlp:
    def __init__(self):
        self.idx = pd.date_range("2025-10-27", periods=5, freq="B")

    def bdh(self, tickers, flds, start, end, adjust=None):
        cols = pd.MultiIndex.from_product([tickers, flds])
        base = 100.0 if adjust != "split" else 101.0
        data = np.tile(np.arange(len(cols), dtype=float) + base, (len(self.idx), 1))
        return pd.DataFrame(data, index=self.idx, columns=cols)

    def bdp(self, tickers, flds):
        tickers = [tickers] if isinstance(tickers, str) else tickers
        return pd.DataFrame({"expected_report_dt": ["2025-11-05"] * len(tickers),
                             "expected_report_time": ["Aft-mkt"] * len(tickers)}, index=tickers)

    def bds(self, ticker, fld):
        return pd.DataFrame({"Year/Period": ["2025:Q3"], "Announcement Date": ["2025-10-29"],
                             "Announcement Time": ["16:05"], "Earnings EPS": [1.0]}, index=[ticker])


def test_bloomberg_adapter_parses_fake_terminal(cfg, members):
    bb = BloombergAdapter(cfg, blp=FakeBlp())
    px = bb.get_prices(members, ["AAA US Equity"], "2025-10-27", "2025-10-31")["AAA US Equity"]
    assert list(px.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert px["close"].iloc[0] != px["adj_close"].iloc[0]  # split-only vs fully adjusted
    assert bb.get_benchmark("2025-10-27", "2025-10-31").warning == ""
    ev = bb.get_earnings(members, ["AAA US Equity"], "2025-01-01", "2025-12-31")
    by_date = ev.set_index("report_date")["timing"]
    assert by_date[pd.Timestamp("2025-10-29")] == "AMC"
    assert by_date[pd.Timestamp("2025-11-05")] == "AMC"


def test_parse_bbg_time():
    assert parse_bbg_time("Bef-mkt") == "BMO"
    assert parse_bbg_time("Aft-mkt") == "AMC"
    assert parse_bbg_time(None) == "UNKNOWN"
    assert parse_bbg_time("08:00") == ""
