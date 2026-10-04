import pandas as pd

from screener import calendars as c


def test_day_one_bmo_same_day_amc_next_session():
    # Thu 30 Oct 2025 on NYSE
    assert c.resolve_day_one("XNYS", "2025-10-30", "BMO")[0].date == pd.Timestamp("2025-10-30")
    assert c.resolve_day_one("XNYS", "2025-10-29", "AMC")[0].date == pd.Timestamp("2025-10-30")
    assert c.resolve_day_one("XNYS", "2025-10-30", "DMH")[0].date == pd.Timestamp("2025-10-31")


def test_day_one_skips_weekend_and_exchange_holiday():
    # Friday AMC -> Monday
    assert c.resolve_day_one("XNYS", "2025-10-31", "AMC")[0].date == pd.Timestamp("2025-11-03")
    # US Thanksgiving 27 Nov 2025: BMO on the holiday -> Friday 28th
    assert c.resolve_day_one("XNYS", "2025-11-27", "BMO")[0].date == pd.Timestamp("2025-11-28")
    # Tokyo: 3 Nov 2025 is Culture Day (holiday). AMC on Fri 31 Oct -> Tue 4 Nov
    assert c.resolve_day_one("XTKS", "2025-10-31", "AMC")[0].date == pd.Timestamp("2025-11-04")


def test_each_exchange_uses_its_own_calendar():
    # 27 Nov 2025 is a US holiday but a normal session in Tokyo
    assert c.resolve_day_one("XTKS", "2025-11-26", "AMC")[0].date == pd.Timestamp("2025-11-27")
    assert c.resolve_day_one("XNYS", "2025-11-26", "AMC")[0].date == pd.Timestamp("2025-11-28")


def test_unknown_timing_returns_both_flagged():
    out = c.resolve_day_one("XNYS", "2025-10-30", "UNKNOWN", "both")
    assert [d.date for d in out] == [pd.Timestamp("2025-10-30"), pd.Timestamp("2025-10-31")]
    assert all(d.flag == "TIMING UNCONFIRMED" for d in out)
    # weekend report: both readings coincide -> one row
    assert len(c.resolve_day_one("XNYS", "2025-11-01", "UNKNOWN", "both")) == 1


def test_classify_timing_from_local_timestamp():
    ny = "America/New_York"
    assert c.classify_timing("XNYS", pd.Timestamp("2025-10-30 07:00", tz=ny)) == "BMO"
    assert c.classify_timing("XNYS", pd.Timestamp("2025-10-29 16:05", tz=ny)) == "AMC"
    assert c.classify_timing("XNYS", pd.Timestamp("2025-10-29 12:00", tz=ny)) == "DMH"
    assert c.classify_timing("XNYS", pd.Timestamp("2025-10-29 00:00", tz=ny)) == "UNKNOWN"
    assert c.classify_timing("XTKS", pd.Timestamp("2025-10-30 15:30", tz="Asia/Tokyo")) == "AMC"


def test_hkt_closes_across_2026_dst_changes():
    # US: EDT until Sun 1 Nov 2026 -> NYSE close 04:00 HKT, then 05:00 HKT
    assert c.session_close_hkt("XNYS", "2026-10-30").strftime("%m-%d %H:%M") == "10-31 04:00"
    assert c.session_close_hkt("XNYS", "2026-11-02").strftime("%m-%d %H:%M") == "11-03 05:00"
    # Europe: BST until Sun 25 Oct 2026 -> LSE close 23:30 HKT, then 00:30 HKT next day
    assert c.session_close_hkt("XLON", "2026-10-23").strftime("%m-%d %H:%M") == "10-23 23:30"
    assert c.session_close_hkt("XLON", "2026-10-26").strftime("%m-%d %H:%M") == "10-27 00:30"
    # HK has no DST
    assert c.session_close_hkt("XHKG", "2026-10-30").strftime("%H:%M") == "16:00"


def test_latest_completed_session_respects_close_time():
    # 03:00 HKT on Sat 31 Oct 2026 is before the NYSE Friday close (04:00 HKT)
    now = pd.Timestamp("2026-10-31 03:00", tz="Asia/Hong_Kong").tz_convert("UTC")
    assert c.latest_completed_session("XNYS", now) == pd.Timestamp("2026-10-29")
    now = pd.Timestamp("2026-10-31 04:30", tz="Asia/Hong_Kong").tz_convert("UTC")
    assert c.latest_completed_session("XNYS", now) == pd.Timestamp("2026-10-30")


def test_session_offset_day_three():
    d1 = pd.Timestamp("2025-10-30")
    assert c.session_offset("XNYS", d1, 2) == pd.Timestamp("2025-11-03")
