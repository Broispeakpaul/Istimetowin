"""Event study: every past report in the universe, run through the live entry checks, plus what
happened afterwards. Uses rules.entry.evaluate_event, the same function as the daily engine."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import calendars as cal
from ..config import SESSIONS, Config
from ..data.market import MarketData
from ..inputs import ManualInputs
from ..rules import metrics as m
from ..rules.entry import QUANT_CHECKS, evaluate_event
from ..rules.events import DayOneEvent


def _forward_rel(frame: pd.DataFrame, bench: pd.DataFrame, code: str, d1: pd.Timestamp, h: int, method: str):
    try:
        end = cal.session_offset(code, d1, h)
    except (IndexError, KeyError):
        return np.nan
    if frame.index.max() < end or bench.index.max() < end:
        return np.nan  # not enough future data yet
    v = m.window_rel_return(frame, bench, d1, end, method)
    return np.nan if v is None else v


def event_study(md: MarketData, cfg: Config, manual: ManualInputs, start, end, session: str = "all") -> pd.DataFrame:
    """One row per report: entry-check results on day one and forward relative returns from the day-one close.

    Unknown report timing: both readings are evaluated and the one with the larger day-one volume is kept,
    labelled 'timing inferred from volume'. That inference is a backtest convenience; the live engine never does it.
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    sessions = list(SESSIONS) if session == "all" else [session]
    members = md.members
    e = md.earnings
    e = e[(e["report_date"] >= start) & (e["report_date"] <= end) & e["bbg_ticker"].isin(members.index)]
    bench_full = md.benchmark.prices
    rows = []
    for _, r in e.iterrows():
        t = r["bbg_ticker"]
        mem = members.loc[t]
        if mem["session"] not in sessions or t not in md.prices:
            continue
        frame = md.prices[t]
        readings = cal.resolve_day_one(mem["calendar"], r["report_date"], r["timing"], "both")
        best = None
        for d1 in readings:
            view = md.as_of(d1.date)
            ev = DayOneEvent(t, r["report_date"], r["timing"], d1.timing_used, d1.flag, d1.date, str(r["source"]))
            sig = evaluate_event(view, mem, ev, cfg, manual)
            if best is None or (sig.volume_ratio or 0) > (best.volume_ratio or 0):
                best = sig
        if best is None:
            continue
        if len(readings) > 1:
            best.timing_flag = "timing inferred from volume (backtest only)"
        row = best.to_row()
        row["quant_pass"] = all(c.status == "PASS" for c in best.checks if c.name in QUANT_CHECKS)
        for h in cfg.backtest.forward_horizons:
            row[f"fwd_rel_{h}"] = _forward_rel(frame, bench_full, mem["calendar"], best.day_one, h,
                                               cfg.entry.relative_return_method)
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    edges = cfg.backtest.reaction_buckets
    labels = [f"{a:+.0%} to {b:+.0%}" if abs(a) < 1 and abs(b) < 1 else (f"< {b:+.0%}" if abs(a) >= 1 else f">= {a:+.0%}")
              for a, b in zip(edges[:-1], edges[1:])]
    df["reaction_bucket"] = pd.cut(df["rel_return"], bins=edges, labels=labels, right=False)
    return df


def _summ(g: pd.DataFrame, horizons: list[int]) -> dict:
    out = {"events": len(g)}
    for h in horizons:
        col = g[f"fwd_rel_{h}"].dropna()
        out[f"mean_rel_{h}d"] = col.mean() if len(col) else np.nan
        out[f"median_rel_{h}d"] = col.median() if len(col) else np.nan
        out[f"hit_rate_{h}d"] = (col > 0).mean() if len(col) else np.nan
    return out


def table_by_reaction(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Forward relative return by day-one reaction bucket (all events)."""
    hz = cfg.backtest.forward_horizons
    if df.empty:
        return pd.DataFrame()
    rows = [{"reaction_bucket": str(k), **_summ(g, hz)} for k, g in df.groupby("reaction_bucket", observed=True)]
    rows.append({"reaction_bucket": "ALL", **_summ(df, hz)})
    return pd.DataFrame(rows)


def table_filter_steps(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """What each entry filter adds: events left and their forward returns after each step."""
    hz = cfg.backtest.forward_horizons
    if df.empty:
        return pd.DataFrame()
    steps = [("all reports", pd.Series(True, index=df.index)),
             (f"rel return >= {cfg.entry.min_day_one_rel_return:+.0%}", df["chk_rel_return"] == "PASS")]
    steps.append((f"+ volume >= {cfg.entry.min_volume_ratio:g}x", steps[-1][1] & (df["chk_volume_ratio"] == "PASS")))
    steps.append((f"+ ADV >= ${cfg.entry.min_adv_usd / 1e6:,.0f}m (all quant checks)", steps[-1][1] & (df["chk_adv_usd"] == "PASS")))
    return pd.DataFrame([{"filter_step": name, **_summ(df[mask], hz)} for name, mask in steps])
