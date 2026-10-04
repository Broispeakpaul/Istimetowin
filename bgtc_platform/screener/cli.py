"""Command line.

  python -m screener run --session us|asia|europe|all [--date YYYY-MM-DD]
  python -m screener demo [--date YYYY-MM-DD]            # synthetic data, no network
  python -m screener universe --from-bloomberg           # rebuild data/wls_members.csv on the Terminal
  python -m screener backtest events --start ... --end ...
  python -m screener backtest contest --start ... [--rolling]
  python -m screener dashboard
"""
from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

from . import calendars as cal
from .config import PROJECT_ROOT, load_config


def _cfg(args):
    return load_config(args.config) if args.config else load_config()


def _print_result(res, paths) -> None:
    print(f"\n=== {res.as_of:%Y-%m-%d} session={res.session} source={res.data_source} benchmark={res.benchmark_name} ===")
    for w in res.warnings:
        print(f"WARNING: {w}")
    print(f"Regime: {res.regime.summary}")
    print(f"NAV ${res.portfolio.nav_usd:,.0f} | cash ${res.portfolio.cash_usd:,.0f} | positions {len(res.portfolio.holdings)}")
    if res.analytics and res.analytics.portfolio_beta is not None:
        print(f"Portfolio adjusted beta: {res.analytics.portfolio_beta:.2f}")
    print(f"\nDay-one events evaluated: {len(res.signals)} | candidates: {len(res.candidates)}")
    if not res.candidates.empty:
        cols = ["rank", "bbg_ticker", "day_one", "rel_return", "volume_ratio", "target_weight", "tranche1_weight",
                "shares_tranche1", "action"]
        c = res.candidates[cols].copy()
        for k in ("rel_return", "target_weight", "tranche1_weight"):
            c[k] = c[k].map(lambda v: f"{v:.1%}")
        c["volume_ratio"] = c["volume_ratio"].map(lambda v: f"{v:.1f}x")
        c["day_one"] = pd.to_datetime(c["day_one"]).dt.strftime("%Y-%m-%d")
        print(c.to_string(index=False))
    rej = res.signals[~res.signals.index.isin(res.candidates.index)] if not res.signals.empty else res.signals
    for _, r in rej.iterrows():
        print(f"  - {r['bbg_ticker']}: {r['status']} | {r['reasons'][:180]}")
    if not res.tranche2.empty:
        print("\nSecond tranche:")
        print(res.tranche2[["bbg_ticker", "action", "tranche2_weight", "shares", "reason"]].to_string(index=False))
    act = res.alerts[res.alerts["severity"].isin(["ACTION", "BREACH", "CONFIRM"])]
    if not act.empty:
        print("\nPosition alerts:")
        print(act[["bbg_ticker", "rule", "action", "shares_to_sell", "detail"]].to_string(index=False))
    print(f"\nReport: {paths['html']}\nSignals CSV: {paths['signals_csv']}")


def wait_until_before_close(session: str, minutes: int, now_utc=None, sleep=None):
    """Sleep until `minutes` before today's close of the session's anchor exchange. Returns the session
    date, or None if that exchange has no session today. DST is handled by the exchange calendar."""
    import time
    anchor = cal.SESSION_ANCHOR_CALENDAR["us" if session == "all" else session]
    now = pd.Timestamp.now(tz="UTC") if now_utc is None else now_utc
    local_today = now.tz_convert(cal.get_calendar(anchor).tz).normalize().tz_localize(None)
    if not cal.is_session(anchor, local_today):
        return None
    target = cal.session_close_utc(anchor, local_today) - pd.Timedelta(minutes=minutes)
    wait = (target - now).total_seconds()
    if wait > 0:
        print(f"Waiting until {cal.fmt_hkt(target)} ({minutes} min before the {anchor} close)...", flush=True)
        (sleep or time.sleep)(wait)
    return local_today


def cmd_run(args) -> int:
    from .pipeline import default_date, run_session
    from .report import write_report
    cfg = _cfg(args)
    if args.source:
        cfg.data_source = args.source
    if args.before_close is not None:
        as_of = wait_until_before_close(args.session, args.before_close)
        if as_of is None:
            print(f"No {args.session} session today; nothing to do.")
            return 0
    else:
        as_of = cal.parse_date(args.date) if args.date else default_date(args.session)
    res = run_session(cfg, as_of, args.session)
    paths = write_report(res, cfg)
    _print_result(res, paths)
    return 0


def cmd_demo(args) -> int:
    from .data.fixture import demo_source
    from .data.market import MarketData
    from .engine import run_day
    from .inputs import Ledger, ManualInputs, MANUAL_COLUMNS, OVERRIDE_COLUMNS
    from .report import write_report
    from .universe import load_members
    cfg = _cfg(args)
    cfg.data_source = "fixture"
    members = load_members(cfg)
    as_of = cal.parse_date(args.date)
    src = demo_source(members, end=as_of + pd.Timedelta(days=10))
    manual = ManualInputs(pd.DataFrame(columns=OVERRIDE_COLUMNS), pd.DataFrame(columns=MANUAL_COLUMNS))
    ledger = Ledger.from_frame(pd.DataFrame())
    md = MarketData.load(src, members, manual, cfg, as_of - pd.Timedelta(days=cfg.history.lookback_calendar_days),
                         as_of)
    res = run_day(md.as_of(as_of), args.session, ledger, manual, cfg, mode="demo")
    paths = write_report(res, cfg, Path(cfg.paths.reports_dir) / "demo")
    _print_result(res, paths)
    return 0


def cmd_universe(args) -> int:
    cfg = _cfg(args)
    if not args.from_bloomberg:
        print("Only --from-bloomberg is supported. Maintain data/wls_members.csv by hand otherwise.")
        return 2
    from .data.bloomberg import BloombergAdapter
    from .universe import yahoo_from_bbg
    df = BloombergAdapter(cfg).fetch_members()
    df["yahoo_symbol"] = df["bbg_ticker"].map(lambda t: yahoo_from_bbg(t, cfg))
    out = Path(cfg.paths.members)
    if out.exists():
        backup = out.with_name(out.stem + f"_backup_{pd.Timestamp.now():%Y%m%d_%H%M%S}.csv")
        shutil.copy(out, backup)
        print(f"Backed up existing universe to {backup}")
    df.to_csv(out, index=False)
    print(f"Wrote {len(df)} members to {out}. Review the 'theme' column and HK lot sizes by hand.")
    return 0


def cmd_backtest(args) -> int:
    from .backtest.runner import main as bt_main
    return bt_main(args, _cfg(args))


def cmd_dashboard(args) -> int:
    app = PROJECT_ROOT / "screener" / "dashboard" / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app)]
    print("Starting:", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m screener", description="BGTC 2026 earnings catalyst decision support "
                                "(recommendations only, never places orders)")
    p.add_argument("--config", help="path to config.yaml (default: project config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="daily screen, exits and report")
    r.add_argument("--session", choices=["us", "asia", "europe", "all"], required=True)
    r.add_argument("--date", help="exchange-local session date YYYY-MM-DD (default: latest closed session)")
    r.add_argument("--source", choices=["yahoo", "bloomberg"], help="override data_source in config.yaml")
    r.add_argument("--before-close", type=int, metavar="MIN",
                   help="preliminary run: wait until MIN minutes before today's close, then screen (rows marked PRELIMINARY)")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("demo", help="run on synthetic data (no network) to check the install")
    d.add_argument("--date", default="2025-10-30")
    d.add_argument("--session", choices=["us", "asia", "europe", "all"], default="all")
    d.set_defaults(func=cmd_demo)

    u = sub.add_parser("universe", help="rebuild data/wls_members.csv")
    u.add_argument("--from-bloomberg", action="store_true")
    u.set_defaults(func=cmd_universe)

    b = sub.add_parser("backtest", help="event study and five-week contest simulator")
    bsub = b.add_subparsers(dest="bt_cmd", required=True)
    e = bsub.add_parser("events", help="event study over a date range")
    e.add_argument("--start", required=True)
    e.add_argument("--end", required=True)
    e.add_argument("--session", choices=["us", "asia", "europe", "all"], default="all")
    c = bsub.add_parser("contest", help="five-week contest simulation")
    c.add_argument("--start", required=True, help="first contest day")
    c.add_argument("--end", help="with --rolling: last start date")
    c.add_argument("--rolling", action="store_true", help="simulate every weekly start between --start and --end")
    c.add_argument("--session", choices=["us", "asia", "europe", "all"], default="all")
    for x in (e, c):
        x.add_argument("--source", choices=["yahoo", "bloomberg"])
        x.add_argument("--out", help="output folder (default reports/backtest)")
    b.set_defaults(func=cmd_backtest)

    s = sub.add_parser("dashboard", help="start the Streamlit dashboard")
    s.set_defaults(func=cmd_dashboard)
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles/log files may not be UTF-8
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.func(args)
