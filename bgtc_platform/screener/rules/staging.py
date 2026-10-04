"""Staging list (heavyweights reporting soon) and the forward earnings calendar."""
from __future__ import annotations

import pandas as pd

from ..config import Config
from .events import upcoming_reports
from .implied import ImpliedMoveBook
from .sizing import earnings_hold_cap

STAGING_COLUMNS = ["bbg_ticker", "name", "exchange", "wls_weight", "report_date", "timing", "sessions_until",
                   "our_weight", "active_weight", "implied_move", "earnings_hold_cap", "note"]


def staging_list(view, members: pd.DataFrame, weights: pd.Series, cfg: Config,
                 implied: ImpliedMoveBook) -> tuple[pd.DataFrame, list[str]]:
    s = cfg.staging
    notes = []
    w = members.dropna(subset=["wls_weight"]).sort_values("wls_weight", ascending=False)
    if w.empty:
        return pd.DataFrame(columns=STAGING_COLUMNS), ["Staging: MISSING wls_weight in data/wls_members.csv, so heavyweights cannot be ranked"]
    top = w.head(s.heavyweight_top_n)
    up = upcoming_reports(view.earnings(), members, view.as_of, s.horizon_sessions, tickers=top.index)
    rows = []
    for _, r in up.iterrows():
        t = r["bbg_ticker"]
        im, src = implied.get(t, r["report_date"])
        cap = earnings_hold_cap(im, cfg)
        ours = float(weights.get(t, 0.0) or 0.0)
        rows.append({"bbg_ticker": t, "name": members.at[t, "name"], "exchange": members.at[t, "exchange"],
                     "wls_weight": members.at[t, "wls_weight"], "report_date": r["report_date"], "timing": r["timing"],
                     "sessions_until": r["sessions_until"], "our_weight": ours,
                     "active_weight": ours - members.at[t, "wls_weight"], "implied_move": im,
                     "earnings_hold_cap": cap,
                     "note": (f"tilt idle money here; hold-through cap {cap:.1%} ({src})" if cap is not None
                              else "CONFIRM MANUALLY: implied move before sizing a hold-through")})
    if not s.recommend_cash_staging:
        notes.append("Cash staging is not recommended (config staging.recommend_cash_staging = false); "
                     "idle money should tilt toward the heavyweights listed.")
    return pd.DataFrame(rows, columns=STAGING_COLUMNS), notes


def earnings_calendar(view, members: pd.DataFrame, weights: pd.Series, cfg: Config) -> pd.DataFrame:
    up = upcoming_reports(view.earnings(), members, view.as_of, cfg.calendar_view.horizon_sessions)
    if up.empty:
        return pd.DataFrame(columns=["report_date", "bbg_ticker", "name", "exchange", "session", "timing",
                                     "report_time_local", "sessions_until", "wls_weight", "held", "source"])
    up["name"] = up["bbg_ticker"].map(members["name"])
    up["exchange"] = up["bbg_ticker"].map(members["exchange"])
    up["session"] = up["bbg_ticker"].map(members["session"])
    up["wls_weight"] = up["bbg_ticker"].map(members["wls_weight"])
    up["held"] = up["bbg_ticker"].map(lambda t: float(weights.get(t, 0.0) or 0.0) > 0)
    return up[["report_date", "bbg_ticker", "name", "exchange", "session", "timing", "report_time_local",
               "sessions_until", "wls_weight", "held", "source"]]
