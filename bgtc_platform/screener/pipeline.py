"""Glue between inputs, data source, engine and report. Used by the CLI, dashboard and scheduler."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from . import calendars as cal
from .config import SESSIONS, Config
from .data import make_source
from .data.base import DataSource
from .data.market import MarketData
from .engine import DayResult, run_day
from .inputs import Ledger, ManualInputs, load_ledger, load_manual_inputs
from .universe import heavyweights, load_members


def default_date(session: str, now_utc: Optional[pd.Timestamp] = None) -> pd.Timestamp:
    """Most recent session date whose close has passed for the run's anchor exchange."""
    anchor = cal.SESSION_ANCHOR_CALENDAR["us" if session == "all" else session]
    return cal.latest_completed_session(anchor, now_utc)


def tickers_for(members: pd.DataFrame, ledger: Ledger, session: str, cfg: Config) -> tuple[list[str], list[str]]:
    """(price tickers, earnings tickers) for a run."""
    sessions = list(SESSIONS) if session == "all" else [session]
    sess = list(members.index[members["session"].isin(sessions)])
    held = [t for t in ledger.fills["bbg_ticker"].unique() if t in members.index]
    heavy = list(heavyweights(members, cfg.staging.heavyweight_top_n).index)
    prices = list(dict.fromkeys(sess + held))
    earnings = list(dict.fromkeys(sess + held + heavy))
    return prices, earnings


def load_market(cfg: Config, as_of: pd.Timestamp, session: str, source: Optional[DataSource] = None,
                members: Optional[pd.DataFrame] = None, ledger: Optional[Ledger] = None,
                manual: Optional[ManualInputs] = None, end: Optional[pd.Timestamp] = None) -> MarketData:
    members = load_members(cfg) if members is None else members
    ledger = load_ledger(cfg) if ledger is None else ledger
    manual = load_manual_inputs(cfg) if manual is None else manual
    source = make_source(cfg) if source is None else source
    px, ev = tickers_for(members, ledger, session, cfg)
    start = as_of - pd.Timedelta(days=cfg.history.lookback_calendar_days)
    return MarketData.load(source, members, manual, cfg, start, end or as_of, tickers=px, earnings_tickers=ev)


def run_session(cfg: Config, as_of: pd.Timestamp, session: str, source: Optional[DataSource] = None,
                now_utc: Optional[pd.Timestamp] = None) -> DayResult:
    members = load_members(cfg)
    ledger = load_ledger(cfg)
    manual = load_manual_inputs(cfg)
    md = load_market(cfg, as_of, session, source, members, ledger, manual)
    now = now_utc or pd.Timestamp.now(tz="UTC")
    return run_day(md.as_of(as_of, now), session, ledger, manual, cfg, now_utc=now, mode="live")
