"""CLI entry for `python -m screener backtest ...`."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import calendars as cal
from ..config import Config
from ..data import make_source
from ..data.market import MarketData
from ..inputs import Ledger, load_manual_inputs
from ..pipeline import tickers_for
from ..universe import load_members
from .event_study import event_study, table_by_reaction, table_filter_steps
from .simulator import rolling_contests, simulate_contest
from .tables import compare

CAVEATS = [
    "Quant-only: manual checks 1 and 5 are assumed PASS (no history of those confirmations).",
    "Survivorship bias: today's WLS membership is applied to the whole period.",
    "Report dates and timing come from the data source; Yahoo timing is often missing (inferred from volume in the event study).",
    "Benchmark: results vs VT when running on Yahoo, vs WLS Index on Bloomberg.",
]


def _pct(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if any(k in c for k in ("mean_", "median_", "hit_rate", "return", "drawdown", "invested")):
            out[c] = out[c].map(lambda v: "" if pd.isna(v) else f"{v:+.2%}" if "hit" not in c else f"{v:.0%}")
    return out


def load(cfg: Config, start, end, session: str, extra_days: int = 60) -> MarketData:
    members = load_members(cfg)
    manual = load_manual_inputs(cfg)
    px, ev = tickers_for(members, Ledger.from_frame(pd.DataFrame()), session, cfg)
    s = pd.Timestamp(start) - pd.Timedelta(days=cfg.history.lookback_calendar_days)
    e = min(pd.Timestamp(end) + pd.Timedelta(days=extra_days), pd.Timestamp.now().normalize())
    return MarketData.load(make_source(cfg), members, manual, cfg, s, e, tickers=px, earnings_tickers=px)


def main(args, cfg: Config) -> int:
    if getattr(args, "source", None):
        cfg.data_source = args.source
    out = Path(args.out or Path(cfg.paths.reports_dir) / "backtest")
    out.mkdir(parents=True, exist_ok=True)
    manual = load_manual_inputs(cfg)
    ours: dict[str, pd.DataFrame] = {}

    if args.bt_cmd == "events":
        md = load(cfg, args.start, args.end, args.session)
        df = event_study(md, cfg, manual, args.start, args.end, args.session)
        tag = f"{cal.parse_date(args.start):%Y%m%d}_{cal.parse_date(args.end):%Y%m%d}"
        df.to_csv(out / f"events_{tag}.csv", index=False)
        ours["reaction_buckets"] = table_by_reaction(df, cfg)
        ours["filter_steps"] = table_filter_steps(df, cfg)
        for k, t in ours.items():
            t.to_csv(out / f"{k}_{tag}.csv", index=False)
        print(f"\nEvents: {len(df)} reports ({args.start} to {args.end}), benchmark {md.benchmark.name}")
        print("\nForward relative return by day-one reaction bucket:")
        print(_pct(ours["reaction_buckets"]).to_string(index=False))
        print("\nWhat each entry filter adds:")
        print(_pct(ours["filter_steps"]).to_string(index=False))
    else:
        end = args.end if args.rolling else args.start
        md = load(cfg, args.start, pd.Timestamp(end) + pd.Timedelta(days=7 * cfg.backtest.contest_weeks), args.session)
        if args.rolling:
            roll = rolling_contests(md, cfg, args.start, args.end, args.session, manual)
            ours["contest_rolling"] = roll
            roll.to_csv(out / f"contest_rolling_{cal.parse_date(args.start):%Y%m%d}_{cal.parse_date(args.end):%Y%m%d}.csv", index=False)
            print(_pct(roll).to_string(index=False))
            if not roll.empty:
                print(f"\nWindows: {len(roll)} | mean relative return {roll['relative_return'].mean():+.2%} | "
                      f"beat benchmark in {(roll['relative_return'] > 0).mean():.0%}")
        else:
            r = simulate_contest(md, cfg, args.start, session=args.session, manual=manual)
            tag = f"{r.start:%Y%m%d}"
            r.nav.to_csv(out / f"contest_nav_{tag}.csv", index=False)
            r.trades.to_csv(out / f"contest_trades_{tag}.csv", index=False)
            ours["contest_rolling"] = pd.DataFrame([r.summary])
            print(f"\nContest {r.start:%Y-%m-%d} to {r.end:%Y-%m-%d} ({cfg.backtest.contest_weeks} weeks)")
            for k, v in r.summary.items():
                if isinstance(v, pd.Timestamp):
                    v = v.strftime("%Y-%m-%d")
                print(f"  {k:>18}: {v:+.2%}" if isinstance(v, float) else f"  {k:>18}: {v}")
            print("\nTrades:")
            print(r.trades.drop(columns=["detail"]).to_string(index=False) if not r.trades.empty else "  none")
            print("\nAssumptions:\n  - " + "\n  - ".join(r.assumptions))

    comp, notes = compare(ours)
    if not comp.empty:
        comp.to_csv(out / "playbook_comparison.csv", index=False)
        print("\nComparison with the playbook:")
        print(comp.to_string(index=False))
    for n in notes:
        print("NOTE:", n)
    print("\nCaveats:\n  - " + "\n  - ".join(CAVEATS))
    print(f"\nOutputs in {out}")
    return 0
