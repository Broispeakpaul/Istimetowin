"""Five-week contest simulator. Each simulated day calls engine.run_day() (the live rule code) for the
asia, europe and us sessions in order, then fills the recommendations at that session's close.

Assumptions (all printed with the results):
* fills at the listing's close on the signal day, with cost_bps added to buys / taken from sells;
* manual checks are assumed PASS (config backtest.manual_checks_assumption), so results are quant-only;
* implied moves come from a trailing proxy (mean absolute day-one move of past reports), labelled PROXY;
* idle cash earns the benchmark return (backtest.idle_cash = benchmark_proxy) or nothing (cash);
* no leverage: buys are cut down to the cash available;
* today's WLS membership is used for the whole period (survivorship bias).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .. import calendars as cal
from ..config import SESSIONS, Config
from ..data.market import MarketData
from ..engine import run_day
from ..inputs import Ledger, ManualInputs
from ..portfolio import build_portfolio
from ..rules import metrics as m
from ..rules.sizing import lot_int

EXIT_PREFIXES = ("EXIT", "TRIM", "HALVE")


@dataclass
class SimResult:
    start: pd.Timestamp
    end: pd.Timestamp
    nav: pd.DataFrame                 # date, nav, cash, invested, benchmark_index
    trades: pd.DataFrame
    summary: dict
    assumptions: list[str] = field(default_factory=list)


def make_implied_proxy(md: MarketData, cfg: Config):
    """Mean absolute day-one move over the previous N reports (past data only). Backtest use only."""
    n = cfg.backtest.implied_move_proxy_reports

    def proxy(ticker: str, report_date) -> Optional[float]:
        if ticker not in md.prices or ticker not in md.members.index:
            return None
        code = md.members.at[ticker, "calendar"]
        frame = md.prices[ticker]
        past = md.earnings[(md.earnings["bbg_ticker"] == ticker) &
                           (md.earnings["report_date"] < pd.Timestamp(report_date) - pd.Timedelta(days=1))].tail(n)
        moves = []
        for _, r in past.iterrows():
            d1 = cal.resolve_day_one(code, r["report_date"], r["timing"], "amc")[0].date
            ret, _ = m.day_one_return(frame, d1)
            if ret.ok:
                moves.append(abs(ret.value))
        return float(np.mean(moves)) if len(moves) >= 2 else None

    return proxy


def contest_days(md: MarketData, start, weeks: int) -> pd.DatetimeIndex:
    """Weekdays from `start` for `weeks` weeks (each listing still trades only on its own sessions)."""
    start = pd.Timestamp(start).normalize()
    return pd.bdate_range(start, start + pd.Timedelta(days=7 * weeks - 1))


def _price(md: MarketData, t: str, d: pd.Timestamp) -> Optional[float]:
    f = md.prices.get(t)
    if f is None or d not in f.index or pd.isna(f.at[d, "close"]):
        return None
    return float(f.at[d, "close"])


def _usd_per_unit(md: MarketData, t: str, d: pd.Timestamp) -> Optional[float]:
    f = md.prices.get(t)
    if f is None or d not in f.index or pd.isna(f.at[d, "fx"]):
        return None
    return float(f.at[d, "fx"])


def simulate_contest(md: MarketData, cfg: Config, start, weeks: Optional[int] = None, session: str = "all",
                     manual: Optional[ManualInputs] = None) -> SimResult:
    weeks = weeks or cfg.backtest.contest_weeks
    sessions = list(SESSIONS) if session == "all" else [session]
    if manual is None:
        from ..inputs import MANUAL_COLUMNS, OVERRIDE_COLUMNS
        manual = ManualInputs(pd.DataFrame(columns=OVERRIDE_COLUMNS), pd.DataFrame(columns=MANUAL_COLUMNS))
    manual = ManualInputs(manual.overrides, manual.checks, manual.tolerance_days,
                          assume=cfg.backtest.manual_checks_assumption if cfg.backtest.manual_checks_assumption == "pass" else None)
    proxy = make_implied_proxy(md, cfg)
    bps = cfg.backtest.cost_bps / 1e4
    fills: list[dict] = []
    account: list[dict] = []
    trades: list[dict] = []
    nav_rows: list[dict] = []
    bench = md.benchmark.prices["adj_close"].dropna()
    days = contest_days(md, start, weeks)
    prev_cash = None

    def ledger() -> Ledger:
        return Ledger.from_frame(pd.DataFrame(fills), pd.DataFrame(account) if account else None)

    for d in days:
        view = md.as_of(d)
        for sess in sessions:
            led = ledger()
            res = run_day(view, sess, led, manual, cfg, mode="backtest", with_analytics=False, implied_proxy=proxy)
            cash = res.portfolio.cash_usd
            # exits first, so the cash is there for new entries
            for _, a in res.alerts.iterrows():
                if not str(a["action"]).startswith(EXIT_PREFIXES) or not a["shares_to_sell"]:
                    continue
                px = _price(md, a["bbg_ticker"], d)
                if px is None:
                    continue
                q = float(a["shares_to_sell"])
                fills.append({"date": d, "bbg_ticker": a["bbg_ticker"], "side": "SELL", "quantity": q,
                              "price_local": px * (1 - bps), "role": "catalyst"})
                cash += q * px * (1 - bps) * (_usd_per_unit(md, a["bbg_ticker"], d) or 0)
                trades.append({"date": d, "session": sess, "bbg_ticker": a["bbg_ticker"], "side": "SELL", "shares": q,
                               "price_local": px, "rule": a["rule"], "detail": a["detail"]})
            buys = [("tranche1", r) for _, r in res.candidates.iterrows()] + \
                   [("tranche2", r) for _, r in res.tranche2.iterrows() if str(r["action"]).startswith("BUY")]
            for kind, r in buys:
                t = r["bbg_ticker"]
                px, fx = _price(md, t, d), _usd_per_unit(md, t, d)
                want = r["shares_tranche1"] if kind == "tranche1" else r["shares"]
                if px is None or not fx or want is None or pd.isna(want) or want <= 0:
                    continue
                lot = lot_int(md.members.at[t, "lot_size"])
                afford = math.floor(max(cash, 0) / (px * (1 + bps) * fx) / lot) * lot
                q = min(int(want), afford)
                note = "" if q == want else f"cut from {int(want)} to {q} shares: cash limit (no leverage)"
                if q <= 0:
                    trades.append({"date": d, "session": sess, "bbg_ticker": t, "side": "SKIP", "shares": 0,
                                   "price_local": px, "rule": kind, "detail": "no cash (no leverage)"})
                    continue
                day_one = r["day_one"]
                fills.append({"date": d, "bbg_ticker": t, "side": "BUY", "quantity": q, "price_local": px * (1 + bps),
                              "role": "catalyst", "tranche": 1 if kind == "tranche1" else 2, "day_one_date": day_one})
                cash -= q * px * (1 + bps) * fx
                trades.append({"date": d, "session": sess, "bbg_ticker": t, "side": "BUY", "shares": q, "price_local": px,
                               "rule": kind, "detail": (r.get("reasons", "") if kind == "tranche1" else r["reason"]) +
                               (f" | {note}" if note else "")})
        # end of day: idle cash earns the benchmark's return for the day it was held (no look-ahead)
        state = build_portfolio(ledger(), view, md.members, cfg)
        accrual = 0.0
        if cfg.backtest.idle_cash == "benchmark_proxy" and prev_cash is not None and d in bench.index:
            b_prev = bench.loc[:d - pd.Timedelta(days=1)]
            if len(b_prev):
                accrual = max(prev_cash, 0.0) * (bench.loc[d] / b_prev.iloc[-1] - 1)
        end_cash = state.cash_usd + accrual
        account.append({"date": d, "cash_usd": end_cash, "nav_usd": ""})
        prev_cash = end_cash
        nav = state.nav_usd + accrual
        nav_rows.append({"date": d, "nav": nav, "cash": end_cash, "invested": nav - end_cash,
                         "benchmark": float(bench.loc[:d].iloc[-1]) if len(bench.loc[:d]) else np.nan,
                         "positions": len(state.holdings)})

    nav = pd.DataFrame(nav_rows)
    tr = pd.DataFrame(trades, columns=["date", "session", "bbg_ticker", "side", "shares", "price_local", "rule", "detail"])
    before = bench.loc[:days[0] - pd.Timedelta(days=1)]
    summary = summarise(nav, tr, cfg, float(before.iloc[-1]) if len(before) else None)
    assumptions = [l.strip("* ").strip() for l in __doc__.splitlines() if l.strip().startswith("*")]
    return SimResult(days[0], days[-1], nav, tr, summary, assumptions)


def summarise(nav: pd.DataFrame, trades: pd.DataFrame, cfg: Config, bench_start: Optional[float] = None) -> dict:
    if nav.empty:
        return {}
    start_nav = cfg.backtest.initial_capital
    ret = nav["nav"].iloc[-1] / start_nav - 1
    b0 = bench_start or nav["benchmark"].iloc[0]  # benchmark close before the first contest day
    bret = nav["benchmark"].iloc[-1] / b0 - 1 if b0 else np.nan
    peak = nav["nav"].cummax()
    dd = float((nav["nav"] / peak - 1).min())
    buys = trades[trades["side"] == "BUY"] if not trades.empty else trades
    return {"start": nav["date"].iloc[0], "end": nav["date"].iloc[-1], "return": ret, "benchmark_return": bret,
            "relative_return": ret - bret if not np.isnan(bret) else np.nan, "max_drawdown": dd,
            "entries": int((buys["rule"] == "tranche1").sum()) if not buys.empty else 0,
            "adds": int((buys["rule"] == "tranche2").sum()) if not buys.empty else 0,
            "exits": int((trades["side"] == "SELL").sum()) if not trades.empty else 0,
            "avg_invested": float((nav["invested"] / nav["nav"]).mean())}


def rolling_contests(md: MarketData, cfg: Config, first_start, last_start, session: str = "all",
                     manual: Optional[ManualInputs] = None) -> pd.DataFrame:
    """Run the contest from every Monday between first_start and last_start."""
    starts = pd.date_range(pd.Timestamp(first_start), pd.Timestamp(last_start), freq="W-MON")
    rows = []
    for s in starts:
        r = simulate_contest(md, cfg, s, session=session, manual=manual)
        rows.append(r.summary)
    return pd.DataFrame(rows)
