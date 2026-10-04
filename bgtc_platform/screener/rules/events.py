"""Earnings events: which members have their day one today, and who reports soon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

from .. import calendars as cal

LOOKBACK_DAYS = 10  # report dates older than this cannot have today as day one


@dataclass(frozen=True)
class DayOneEvent:
    bbg_ticker: str
    report_date: pd.Timestamp
    timing: str
    timing_used: str
    flag: str
    day_one: pd.Timestamp
    source: str
    report_time_local: str = ""


def _member_filter(members: pd.DataFrame, sessions: Iterable[str]) -> pd.DataFrame:
    sessions = set(sessions)
    return members[members["session"].isin(sessions)]


def events_for_ticker(earnings: pd.DataFrame, ticker: str) -> pd.DataFrame:
    return earnings[earnings["bbg_ticker"] == ticker].sort_values("report_date")


def day_one_events(earnings: pd.DataFrame, members: pd.DataFrame, as_of: pd.Timestamp,
                   sessions: Iterable[str], unknown_policy: str = "both") -> list[DayOneEvent]:
    """All (member, report) pairs whose day one is `as_of` on the member's own calendar."""
    as_of = pd.Timestamp(as_of).normalize()
    m = _member_filter(members, sessions)
    e = earnings[earnings["bbg_ticker"].isin(m.index)]
    e = e[(e["report_date"] <= as_of) & (e["report_date"] >= as_of - pd.Timedelta(days=LOOKBACK_DAYS))]
    out = []
    for _, r in e.iterrows():
        code = m.at[r["bbg_ticker"], "calendar"]
        if not cal.is_session(code, as_of):
            continue
        for d1 in cal.resolve_day_one(code, r["report_date"], r["timing"], unknown_policy):
            if d1.date == as_of:
                out.append(DayOneEvent(r["bbg_ticker"], pd.Timestamp(r["report_date"]), str(r["timing"]),
                                       d1.timing_used, d1.flag, d1.date, str(r.get("source", "")),
                                       str(r.get("report_time_local", "") or "")))
    return out


def event_for_day_one(earnings: pd.DataFrame, ticker: str, calendar_code: str, day_one: pd.Timestamp,
                      unknown_policy: str = "both") -> Optional[DayOneEvent]:
    """Find the report whose day one is `day_one` (used for second tranche and core checks)."""
    e = events_for_ticker(earnings, ticker)
    e = e[(e["report_date"] <= day_one) & (e["report_date"] >= day_one - pd.Timedelta(days=LOOKBACK_DAYS))]
    for _, r in e.iterrows():
        for d1 in cal.resolve_day_one(calendar_code, r["report_date"], r["timing"], unknown_policy):
            if d1.date == pd.Timestamp(day_one):
                return DayOneEvent(ticker, pd.Timestamp(r["report_date"]), str(r["timing"]), d1.timing_used,
                                   d1.flag, d1.date, str(r.get("source", "")), str(r.get("report_time_local", "") or ""))
    return None


def upcoming_reports(earnings: pd.DataFrame, members: pd.DataFrame, as_of: pd.Timestamp, horizon_sessions: int,
                     tickers: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """Reports dated after `as_of` and within the next `horizon_sessions` sessions of each listing's calendar."""
    as_of = pd.Timestamp(as_of).normalize()
    e = earnings[_still_ahead(earnings, as_of)]
    if tickers is not None:
        e = e[e["bbg_ticker"].isin(set(tickers))]
    e = e[e["bbg_ticker"].isin(members.index)]
    rows = []
    horizon_cache: dict[str, pd.Timestamp] = {}
    for _, r in e.iterrows():
        code = members.at[r["bbg_ticker"], "calendar"]
        if code not in horizon_cache:
            horizon_cache[code] = cal.next_n_sessions(code, as_of, horizon_sessions)[-1]
        if r["report_date"] <= horizon_cache[code]:
            rows.append({**r.to_dict(), "sessions_until": cal.session_count_between(code, as_of, r["report_date"])})
    cols = list(earnings.columns) + ["sessions_until"]
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["report_date", "bbg_ticker"]).drop_duplicates(["bbg_ticker"]).reset_index(drop=True)


def next_report(earnings: pd.DataFrame, ticker: str, as_of: pd.Timestamp) -> Optional[pd.Series]:
    e = events_for_ticker(earnings, ticker)
    e = e[_still_ahead(e, pd.Timestamp(as_of).normalize())]
    return None if e.empty else e.iloc[0]


def _still_ahead(earnings: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Reports not yet released at the as-of close: later dates, or today after the close / time unknown."""
    today_pending = (earnings["report_date"] == as_of) & earnings["timing"].isin(["AMC", "UNKNOWN"])
    return (earnings["report_date"] > as_of) | today_pending
