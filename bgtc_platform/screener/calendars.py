"""Exchange calendars, day-one resolution and Hong Kong time helpers.

All timestamps are kept in UTC internally and shown in Hong Kong time. Daylight saving
(US: 1 Nov 2026, Europe: 25 Oct 2026) comes from exchange_calendars and the tz database.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import pandas as pd

HKT = ZoneInfo("Asia/Hong_Kong")
CALENDAR_START = "2012-01-01"

# Calendar whose close defines "the session is over" for each daily run.
SESSION_ANCHOR_CALENDAR = {"asia": "XHKG", "europe": "XLON", "us": "XNYS"}

TIMING_BMO = "BMO"       # before market open
TIMING_AMC = "AMC"       # after market close
TIMING_DMH = "DMH"       # during market hours
TIMING_UNKNOWN = "UNKNOWN"
VALID_TIMINGS = {TIMING_BMO, TIMING_AMC, TIMING_DMH, TIMING_UNKNOWN}


@lru_cache(maxsize=None)
def get_calendar(code: str) -> xc.ExchangeCalendar:
    return xc.get_calendar(code, start=CALENDAR_START)


def _day(d) -> pd.Timestamp:
    return pd.Timestamp(d).normalize().tz_localize(None) if pd.Timestamp(d).tzinfo else pd.Timestamp(d).normalize()


def is_session(code: str, d) -> bool:
    cal = get_calendar(code)
    d = _day(d)
    if d < cal.first_session or d > cal.last_session:
        return False
    return bool(cal.is_session(d))


def session_on_or_after(code: str, d) -> pd.Timestamp:
    return get_calendar(code).date_to_session(_day(d), direction="next")


def session_on_or_before(code: str, d) -> pd.Timestamp:
    return get_calendar(code).date_to_session(_day(d), direction="previous")


def next_session(code: str, d) -> pd.Timestamp:
    """First session strictly after date d."""
    d = _day(d)
    s = session_on_or_after(code, d)
    return get_calendar(code).next_session(s) if s == d else s


def previous_session(code: str, d) -> pd.Timestamp:
    """Last session strictly before date d."""
    d = _day(d)
    s = session_on_or_before(code, d)
    return get_calendar(code).previous_session(s) if s == d else s


def session_offset(code: str, session, n: int) -> pd.Timestamp:
    """The session n sessions after `session` (n may be negative)."""
    cal = get_calendar(code)
    s = _day(session)
    idx = cal.sessions.get_loc(s)
    return cal.sessions[idx + n]


def sessions_in_range(code: str, start, end) -> pd.DatetimeIndex:
    cal = get_calendar(code)
    start, end = _day(start), _day(end)
    if end < start:
        return pd.DatetimeIndex([])
    return cal.sessions_in_range(max(start, cal.first_session), min(end, cal.last_session))


def next_n_sessions(code: str, after, n: int) -> pd.DatetimeIndex:
    """The next n sessions strictly after `after`."""
    first = next_session(code, after)
    cal = get_calendar(code)
    idx = cal.sessions.get_loc(first)
    return cal.sessions[idx: idx + n]


def session_count_between(code: str, start, end) -> int:
    """Number of sessions in (start, end]; 0 if end <= start."""
    if _day(end) <= _day(start):
        return 0
    return len(sessions_in_range(code, next_session(code, start), end))


def session_open_utc(code: str, d) -> pd.Timestamp:
    return get_calendar(code).session_open(_day(d))


def session_close_utc(code: str, d) -> pd.Timestamp:
    return get_calendar(code).session_close(_day(d))


def to_hkt(ts) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(HKT)


def session_close_hkt(code: str, d) -> pd.Timestamp:
    return to_hkt(session_close_utc(code, d))


def fmt_hkt(ts) -> str:
    return to_hkt(ts).strftime("%Y-%m-%d %H:%M HKT")


def latest_completed_session(code: str, now_utc: Optional[pd.Timestamp] = None) -> pd.Timestamp:
    """Most recent session of `code` whose close is at or before now."""
    now = pd.Timestamp.now(tz="UTC") if now_utc is None else pd.Timestamp(now_utc).tz_convert("UTC")
    d = session_on_or_before(code, now.tz_convert(get_calendar(code).tz).date())
    while session_close_utc(code, d) > now:
        d = previous_session(code, d)
    return d


def is_session_closed(code: str, d, now_utc: Optional[pd.Timestamp] = None) -> bool:
    now = pd.Timestamp.now(tz="UTC") if now_utc is None else pd.Timestamp(now_utc)
    return session_close_utc(code, d) <= now


# --------------------------------------------------------------------------- day one


def classify_timing(code: str, report_ts_local: Optional[pd.Timestamp]) -> str:
    """Classify a report timestamp (exchange-local wall time) as BMO / AMC / DMH / UNKNOWN.

    Midnight exactly is treated as "no time given" (Yahoo returns dates without times this way).
    """
    if report_ts_local is None or pd.isna(report_ts_local):
        return TIMING_UNKNOWN
    ts = pd.Timestamp(report_ts_local)
    if ts.time() == time(0, 0):
        return TIMING_UNKNOWN
    cal = get_calendar(code)
    if ts.tzinfo is None:
        ts = ts.tz_localize(cal.tz)
    d = ts.tz_convert(cal.tz).normalize().tz_localize(None)
    if not is_session(code, d):
        return TIMING_BMO  # weekend/holiday release: the next session is the first full one
    if ts < session_open_utc(code, d):
        return TIMING_BMO
    if ts >= session_close_utc(code, d):
        return TIMING_AMC
    return TIMING_DMH


@dataclass(frozen=True)
class DayOne:
    date: pd.Timestamp
    timing_used: str
    flag: str = ""


def resolve_day_one(code: str, report_date, timing: str, unknown_policy: str = "both") -> list[DayOne]:
    """Day one = first full session after the report, on the listing's own calendar.

    BMO -> the report date if it is a session, else the next session.
    AMC / DMH -> the next session after the report date.
    UNKNOWN -> depends on `unknown_policy`; 'both' returns both possibilities, each flagged.
    """
    timing = (timing or TIMING_UNKNOWN).upper()
    if timing not in VALID_TIMINGS:
        timing = TIMING_UNKNOWN
    rd = _day(report_date)
    bmo_day = session_on_or_after(code, rd)
    amc_day = next_session(code, rd)
    if timing == TIMING_BMO:
        return [DayOne(bmo_day, TIMING_BMO)]
    if timing in (TIMING_AMC, TIMING_DMH):
        return [DayOne(amc_day, timing)]
    flag = "TIMING UNCONFIRMED"
    if unknown_policy == "bmo":
        return [DayOne(bmo_day, TIMING_BMO, flag)]
    if unknown_policy == "amc":
        return [DayOne(amc_day, TIMING_AMC, flag)]
    if bmo_day == amc_day:
        return [DayOne(bmo_day, "BMO/AMC", flag)]
    return [DayOne(bmo_day, TIMING_BMO, flag), DayOne(amc_day, TIMING_AMC, flag)]


def as_date(d) -> date:
    return _day(d).date()


def parse_date(s) -> pd.Timestamp:
    if isinstance(s, (datetime, date, pd.Timestamp)):
        return _day(s)
    return _day(pd.Timestamp(str(s)))
