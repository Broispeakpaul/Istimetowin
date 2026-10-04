"""Holdings derived from the fills ledger, plus portfolio analytics (weights, beta, themes, active weights)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import calendars as cal
from .config import Config
from .data.market import major_unit
from .inputs import Ledger
from .rules import metrics as m

HOLDING_COLUMNS = ["bbg_ticker", "name", "exchange", "session", "calendar", "currency", "theme", "theme_group", "role",
                   "quantity", "avg_cost_usd", "entry_date", "day_one_date", "tranches", "thesis_break",
                   "price_local", "price_usd", "price_date", "stale_price", "market_value_usd", "weight",
                   "pos_return", "bench_return", "rel_return", "distance_to_stop", "bench_entry_level", "lot_size"]


@dataclass
class PortfolioState:
    holdings: pd.DataFrame
    cash_usd: float
    nav_usd: float
    cash_source: str
    warnings: list[str] = field(default_factory=list)

    @property
    def weights(self) -> pd.Series:
        return self.holdings.set_index("bbg_ticker")["weight"] if not self.holdings.empty else pd.Series(dtype=float)

    def weight_of(self, ticker: str) -> float:
        w = self.weights
        return float(w.get(ticker, 0.0) or 0.0)


def _asof(series: pd.Series, d) -> Optional[float]:
    s = series.dropna().loc[:pd.Timestamp(d)]
    return None if s.empty else float(s.iloc[-1])


def _fill_fx(row, frame: Optional[pd.DataFrame], currency: str) -> Optional[float]:
    """USD per quote unit for a fill: ledger fx (USD per major unit) x minor-unit scale, else market FX."""
    major, scale = major_unit(currency)
    if major == "USD":
        return 1.0 * scale
    if not pd.isna(row["fx_to_usd"]):
        return float(row["fx_to_usd"]) * scale
    if frame is not None:
        return _asof(frame["fx"], row["date"])
    return None


def build_portfolio(ledger: Ledger, view, members: pd.DataFrame, cfg: Config) -> PortfolioState:
    as_of = view.as_of
    led = ledger.as_of(as_of)
    bench = view.benchmark()["adj_close"]
    warnings: list[str] = []
    rows = []
    buys_usd = sells_usd = 0.0
    for t, fills in led.fills.groupby("bbg_ticker", sort=False):
        if t not in members.index:
            warnings.append(f"{t} in positions.csv is not in the WLS universe file")
            continue
        mem = members.loc[t]
        frame = view.prices(t)
        qty = cost = bench_w = 0.0
        entry_date = day_one = None
        tranches: set[int] = set()
        for _, f in fills.iterrows():
            fx = _fill_fx(f, frame, mem["currency"])
            if fx is None or pd.isna(f["price_local"]):
                warnings.append(f"{t}: fill on {f['date'].date()} has no price/FX; excluded from cost basis")
                continue
            usd = f["quantity"] * f["price_local"] * fx
            if f["side"] == "BUY":
                if qty <= 0:
                    qty = cost = bench_w = 0.0
                    entry_date, day_one, tranches = f["date"], None, set()
                b = _asof(bench, f["date"])
                bench_w += usd * (b if b is not None else np.nan)
                qty += f["quantity"]
                cost += usd
                buys_usd += usd
                if not pd.isna(f["tranche"]):
                    tranches.add(int(f["tranche"]))
                if day_one is None and not pd.isna(f["day_one_date"]):
                    day_one = f["day_one_date"]
            else:
                sells_usd += usd
                if qty > 0:
                    frac = min(f["quantity"] / qty, 1.0)
                    cost *= 1 - frac
                    bench_w *= 1 - frac
                    qty -= min(f["quantity"], qty)
        if qty <= 1e-9:
            continue
        role = fills["role"].iloc[-1]
        thesis = bool(fills["thesis_break"].iloc[-1])
        px_local = px_usd = None
        px_date = None
        if frame is not None and not frame.empty:
            last = frame.dropna(subset=["close"]).iloc[-1]
            px_local, px_date = float(last["close"]), frame.dropna(subset=["close"]).index[-1]
            px_usd = None if pd.isna(last["close_usd"]) else float(last["close_usd"])
        expected = cal.session_on_or_before(mem["calendar"], as_of)
        stale = px_date is None or px_date < expected
        if stale:
            warnings.append(f"{t}: latest price is {px_date.date() if px_date is not None else 'MISSING'} "
                            f"(expected {expected.date()})")
        avg_cost = cost / qty if qty else None
        bench_entry = bench_w / cost if cost else None
        bench_now = _asof(bench, as_of)
        pos_ret = (px_usd / avg_cost - 1) if (px_usd and avg_cost) else None
        b_ret = (bench_now / bench_entry - 1) if (bench_now and bench_entry and not np.isnan(bench_entry)) else None
        rel = m.relative_return(pos_ret, b_ret, cfg.entry.relative_return_method) if (pos_ret is not None and b_ret is not None) else None
        rows.append({
            "bbg_ticker": t, "name": mem["name"], "exchange": mem["exchange"], "session": mem["session"],
            "calendar": mem["calendar"], "currency": mem["currency"], "theme": mem["theme"], "theme_group": mem["theme_group"],
            "role": role, "quantity": qty, "avg_cost_usd": avg_cost, "entry_date": entry_date, "day_one_date": day_one or entry_date,
            "tranches": ",".join(str(x) for x in sorted(tranches)), "thesis_break": thesis,
            "price_local": px_local, "price_usd": px_usd, "price_date": px_date, "stale_price": stale,
            "market_value_usd": qty * px_usd if px_usd else None, "pos_return": pos_ret, "bench_return": b_ret,
            "rel_return": rel, "distance_to_stop": (rel - cfg.exits.relative_stop) if rel is not None else None,
            "bench_entry_level": bench_entry, "lot_size": mem["lot_size"],
        })
    h = pd.DataFrame(rows, columns=HOLDING_COLUMNS)

    acct = led.account
    if acct is not None and not acct.empty and acct["cash_usd"].notna().any():
        snap = acct.dropna(subset=["cash_usd"]).iloc[-1]
        after = led.fills[led.fills["date"] > snap["date"]]
        cash, src = float(snap["cash_usd"]), f"account.csv {snap['date'].date()}"
        if not after.empty:
            adj = Ledger.from_frame(after)
            # recompute cash flow for fills after the snapshot
            flow = 0.0
            for _, f in adj.fills.iterrows():
                if f["bbg_ticker"] not in members.index:
                    continue
                fx = _fill_fx(f, view.prices(f["bbg_ticker"]), members.at[f["bbg_ticker"], "currency"]) or 0.0
                usd = f["quantity"] * (f["price_local"] or 0.0) * fx
                flow += usd if f["side"] == "SELL" else -usd
            cash += flow
            src += f" + {len(after)} later fill(s)"
    else:
        cash, src = cfg.backtest.initial_capital - buys_usd + sells_usd, "initial capital - fills (no account.csv)"
    mv = float(h["market_value_usd"].fillna(0.0).sum())
    nav = cash + mv
    if h["market_value_usd"].isna().any():
        warnings.append("NAV excludes holdings with a MISSING price")
    h["weight"] = h["market_value_usd"] / nav if nav > 0 else np.nan
    if cash < -1.0:
        warnings.append(f"Cash is negative (${cash:,.0f}): leverage is not allowed. Check positions.csv/account.csv")
    return PortfolioState(holdings=h, cash_usd=cash, nav_usd=nav, cash_source=src, warnings=warnings)


# --------------------------------------------------------------------------- analytics


@dataclass
class Analytics:
    betas: pd.DataFrame
    portfolio_beta: Optional[float]
    beta_coverage: float
    theme_exposure: pd.DataFrame
    active_weights: pd.DataFrame
    notes: list[str] = field(default_factory=list)


def portfolio_analytics(state: PortfolioState, view, members: pd.DataFrame, cfg: Config) -> Analytics:
    b = cfg.beta
    bench = view.benchmark()["adj_close"]
    rows = []
    for _, h in state.holdings.iterrows():
        frame = view.prices(h["bbg_ticker"])
        if frame is None:
            rows.append({"bbg_ticker": h["bbg_ticker"], "weight": h["weight"], "beta_adj": None, "beta_raw": None,
                         "beta_note": "MISSING prices"})
            continue
        adj, raw = m.adjusted_beta(frame["adj_close_usd"], bench, view.as_of, b.years, b.frequency,
                                   b.raw_weight, b.prior_weight, b.min_observations)
        rows.append({"bbg_ticker": h["bbg_ticker"], "weight": h["weight"], "beta_adj": adj.value, "beta_raw": raw,
                     "beta_note": adj.note or "ok"})
    betas = pd.DataFrame(rows, columns=["bbg_ticker", "weight", "beta_adj", "beta_raw", "beta_note"])
    have = betas.dropna(subset=["beta_adj", "weight"])
    covered = float(have["weight"].sum()) if not have.empty else 0.0
    total = float(betas["weight"].fillna(0).sum()) if not betas.empty else 0.0
    port_beta = float((have["weight"] * have["beta_adj"]).sum()) if not have.empty else (0.0 if betas.empty else None)
    notes = []
    if total and covered < total - 1e-9:
        notes.append(f"Portfolio beta covers {covered:.1%} of {total:.1%} invested (missing betas excluded)")
    notes.append("Cash and unexplained NAV carry beta 0")

    h = state.holdings
    if h.empty:
        themes = pd.DataFrame(columns=["theme_group", "names", "weight", "tickers", "cap_weight", "cap_names"])
    else:
        themes = h.groupby("theme_group").agg(names=("bbg_ticker", "nunique"), weight=("weight", "sum"),
                                              tickers=("bbg_ticker", lambda x: ", ".join(sorted(x)))).reset_index()
        themes["cap_weight"] = cfg.themes.max_weight_per_theme
        themes["cap_names"] = cfg.themes.max_names_per_theme
        themes = themes.sort_values("weight", ascending=False)

    top = members.dropna(subset=["wls_weight"]).sort_values("wls_weight", ascending=False).head(10)
    w = state.weights
    active = pd.DataFrame({"bbg_ticker": top.index, "name": top["name"].values, "wls_weight": top["wls_weight"].values,
                           "our_weight": [float(w.get(t, 0.0) or 0.0) for t in top.index]})
    active["active_weight"] = active["our_weight"] - active["wls_weight"]
    if top.empty:
        notes.append("Active weights vs WLS top 10: MISSING wls_weight in data/wls_members.csv")
    return Analytics(betas, port_beta, covered, themes, active, notes)
