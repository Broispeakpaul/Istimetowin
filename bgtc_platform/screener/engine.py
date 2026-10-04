"""The daily rule engine. The CLI, the dashboard and the backtest all call run_day().

Recommendations only. Nothing here places an order or talks to a broker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from . import calendars as cal
from .config import SESSIONS, Config
from .inputs import Ledger, ManualInputs
from .portfolio import Analytics, PortfolioState, build_portfolio, portfolio_analytics
from .rules import metrics as m
from .rules.entry import (CANDIDATE_STATES, ST_CANDIDATE, ST_CONFIRM_C1, ST_CONFIRM_REGIME, EventSignal,
                          apply_regime, evaluate_event)
from .rules.events import day_one_events, event_for_day_one, next_report
from .rules.exits import evaluate_exits
from .rules.implied import ImpliedMoveBook, ProxyFn
from .rules.regime import RegimeState, evaluate_regime
from .rules.sizing import second_tranche_decision, shares_for, size_position
from .rules.staging import earnings_calendar, staging_list
from .rules.themes import ThemeBook

ACTION_BUY = "BUY TRANCHE 1 NEAR CLOSE"
ACTION_BUY_IF_C1 = "BUY TRANCHE 1 ONLY IF CHECK 1 CONFIRMED"
ACTION_BUY_IF_REGIME = "CONFIRM REGIME, THEN BUY TRANCHE 1"


@dataclass
class DayResult:
    as_of: pd.Timestamp
    session: str
    mode: str
    generated_at_utc: pd.Timestamp
    data_source: str
    benchmark_name: str
    warnings: list[str]
    regime: RegimeState
    signals: pd.DataFrame
    candidates: pd.DataFrame
    tranche2: pd.DataFrame
    alerts: pd.DataFrame
    portfolio: PortfolioState
    analytics: Optional[Analytics]
    staging: pd.DataFrame
    calendar: pd.DataFrame
    session_closes: pd.DataFrame
    notes: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        p = self.portfolio
        return {
            "nav_usd": p.nav_usd, "cash_usd": p.cash_usd, "cash_weight": p.cash_usd / p.nav_usd if p.nav_usd else None,
            "cash_source": p.cash_source, "positions": len(p.holdings),
            "portfolio_beta": self.analytics.portfolio_beta if self.analytics else None,
            "events_evaluated": len(self.signals), "candidates": len(self.candidates),
        }


def _sessions(session: str) -> list[str]:
    return list(SESSIONS) if session == "all" else [session]


def _lot_note(lot, exchange: str) -> str:
    if lot is None or pd.isna(lot):
        return "CONFIRM BOARD LOT (lot_size missing in wls_members.csv)" if exchange == "HK" else ""
    return f"lot {int(lot)}"


def run_day(view, session: str, ledger: Ledger, manual: ManualInputs, cfg: Config, *,
            now_utc: Optional[pd.Timestamp] = None, mode: str = "live", with_analytics: bool = True,
            implied_proxy: ProxyFn | None = None) -> DayResult:
    """Apply every playbook rule for one session date. `view` must be a MarketView (no look-ahead)."""
    as_of = view.as_of
    members = view.members
    sessions = _sessions(session)
    implied = ImpliedMoveBook(manual, view.data.implied_moves, implied_proxy if mode == "backtest" else None)
    notes: list[str] = []

    state = build_portfolio(ledger, view, members, cfg)
    regime = evaluate_regime(view, cfg)

    # ---- 1. day-one events and entry checks ------------------------------------------------
    sigs: list[EventSignal] = []
    for ev in day_one_events(view.earnings(), members, as_of, sessions, cfg.earnings.unknown_timing):
        mem = members.loc[ev.bbg_ticker]
        prelim = (mode == "live" and now_utc is not None
                  and not cal.is_session_closed(mem["calendar"], as_of, now_utc))
        sig = evaluate_event(view, mem, ev, cfg, manual, preliminary=prelim)
        apply_regime(sig, regime.derisk)
        sigs.append(sig)

    # ---- 2. rank, size, theme caps, cash ---------------------------------------------------
    sigs.sort(key=lambda s: (s.status not in CANDIDATE_STATES, -(s.rel_return if s.rel_return is not None else -9)))
    book = ThemeBook(state.holdings, cfg)
    cash_left = state.cash_usd
    staging_names = state.holdings[state.holdings["role"] == "staging"]["bbg_ticker"].tolist() if not state.holdings.empty else []
    rows = []
    rank = 0
    for sig in sigs:
        row = sig.to_row()
        row.update({"rank": None, "target_weight": None, "tranche1_weight": None, "tranche2_weight": None,
                    "shares_tranche1": None, "sizing_note": "", "theme_note": "", "funding_note": "", "action": "",
                    "current_weight": state.weight_of(sig.bbg_ticker)})
        if sig.status in CANDIDATE_STATES:
            nr = next_report(view.earnings(), sig.bbg_ticker, as_of)
            holds = nr is not None and nr["report_date"] <= cal.next_n_sessions(
                members.at[sig.bbg_ticker, "calendar"], as_of, cfg.sizing.earnings_hold_lookahead_sessions)[-1]
            im, im_src = implied.get(sig.bbg_ticker, nr["report_date"]) if holds else (None, "")
            size = size_position(sig.atr_pct, cfg, holds, im, im_src)
            row["sizing_note"] = "; ".join(size.notes)
            cur = row["current_weight"]
            dec = book.propose(sig.bbg_ticker, sig.theme_group, size.target, cur)
            row["theme_note"] = dec.reason
            if not dec.ok:
                row["status"] = "REJECTED (THEME CAP)"
                row["reasons"] = sig.reasons + " | FAIL theme cap: " + dec.reason
            elif cur >= dec.weight - 1e-9:
                row["status"] = "ALREADY HELD"
                row["action"] = f"no buy: already {cur:.1%} >= target {dec.weight:.1%}"
            else:
                book.accept(sig.bbg_ticker, sig.theme_group, dec.weight, cur)
                rank += 1
                t1 = min(dec.weight * cfg.sizing.tranche_split[0], dec.weight - cur)
                row.update({"rank": rank, "target_weight": dec.weight, "tranche1_weight": t1,
                            "tranche2_weight": dec.weight - cur - t1})
                lot = members.at[sig.bbg_ticker, "lot_size"]
                row["shares_tranche1"] = shares_for(t1, state.nav_usd, sig.close_usd, lot)
                lot_note = _lot_note(lot, sig.exchange)
                if lot_note.startswith("CONFIRM"):
                    row["sizing_note"] += "; " + lot_note
                need = t1 * state.nav_usd
                if need > cash_left + 1e-6:
                    short = need - max(cash_left, 0.0)
                    src = (f"sell staging positions first ({', '.join(staging_names)})" if staging_names
                           else "no staging positions: trim lowest-conviction holdings")
                    row["funding_note"] = f"FUNDING NEEDED ${short:,.0f}: {src}. No leverage."
                cash_left -= need
                row["action"] = {ST_CANDIDATE: ACTION_BUY, ST_CONFIRM_C1: ACTION_BUY_IF_C1,
                                 ST_CONFIRM_REGIME: ACTION_BUY_IF_REGIME}[sig.status]
                if sig.preliminary:
                    row["action"] += " (PRELIMINARY)"
                if sig.provisional:
                    row["action"] += " (PROVISIONAL)"
        rows.append(row)
    signals = pd.DataFrame(rows)
    candidates = signals[signals["rank"].notna()].sort_values("rank") if not signals.empty else signals

    # ---- 3. second tranche on day three ----------------------------------------------------
    t2_rows = []
    h = state.holdings
    if not h.empty:
        for _, p in h[(h["role"] == "catalyst") & h["session"].isin(sessions)].iterrows():
            if "2" in str(p["tranches"]).split(",") or p["day_one_date"] is None or pd.isna(p["day_one_date"]):
                continue
            code = p["calendar"]
            d1 = pd.Timestamp(p["day_one_date"])
            if not cal.is_session(code, d1):
                continue
            d3 = cal.session_offset(code, d1, cfg.sizing.second_tranche_session - 1)
            if d3 != as_of:
                continue
            frame = view.prices(p["bbg_ticker"])
            ev = event_for_day_one(view.earnings(), p["bbg_ticker"], code, d1, cfg.earnings.unknown_timing)
            rd = ev.report_date if ev else d1
            c5 = manual.check_status(p["bbg_ticker"], rd, "check5")
            low1 = float(frame.at[d1, "low"]) if frame is not None and d1 in frame.index else None
            close3 = float(frame.at[as_of, "close"]) if frame is not None and as_of in frame.index else None
            action, why = second_tranche_decision(close3, low1, c5, regime.derisk, cfg)
            atr = m.atr_pct(frame, d1, cfg.sizing.atr_window, cfg.sizing.atr_method, cfg.sizing.atr_include_day_one)
            size = size_position(atr.value if atr.ok else None, cfg)
            cur = p["weight"] if not pd.isna(p["weight"]) else 0.0
            t2 = None
            if size.target is not None:
                t2 = max(min(size.target * cfg.sizing.tranche_split[1], cfg.sizing.max_weight - cur), 0.0)
                dec = book.propose(p["bbg_ticker"], p["theme_group"], cur + t2, cur)
                if not dec.ok:
                    action, why, t2 = "SKIP", why + "; theme cap: " + dec.reason, 0.0
                else:
                    t2 = dec.weight - cur
                    if action.startswith("BUY"):
                        book.accept(p["bbg_ticker"], p["theme_group"], dec.weight, cur)
            t2_rows.append({"bbg_ticker": p["bbg_ticker"], "name": p["name"], "day_one": d1, "day_three": as_of,
                            "check5": c5, "action": action, "reason": why, "current_weight": cur,
                            "tranche2_weight": t2, "shares": shares_for(t2, state.nav_usd, p["price_usd"], p["lot_size"])
                            if action.startswith("BUY") else 0})
    tranche2 = pd.DataFrame(t2_rows, columns=["bbg_ticker", "name", "day_one", "day_three", "check5", "action", "reason",
                                              "current_weight", "tranche2_weight", "shares"])

    # ---- 4. exits --------------------------------------------------------------------------
    held = h[h["session"].isin(sessions)] if not h.empty else h
    alerts = pd.DataFrame([a.to_row() for a in evaluate_exits(view, held, members, cfg, implied)],
                          columns=["bbg_ticker", "rule", "action", "severity", "current_weight", "target_weight",
                                   "shares_to_sell", "detail"])

    # ---- 5. staging, calendar, analytics ---------------------------------------------------
    staging, st_notes = staging_list(view, members, state.weights, cfg, implied)
    notes.extend(st_notes)
    calendar = earnings_calendar(view, members, state.weights, cfg)
    analytics = portfolio_analytics(state, view, members, cfg) if with_analytics else None
    if regime.derisk and analytics and analytics.portfolio_beta is not None:
        gap = analytics.portfolio_beta - cfg.regime.target_beta
        notes.append(f"DE-RISK: portfolio beta {analytics.portfolio_beta:.2f}; move toward {cfg.regime.target_beta:.1f} "
                     f"({'reduce' if gap > 0 else 'raise'} beta by {abs(gap):.2f}, starting with the highest-beta names)")

    closes = []
    for code in sorted(set(members[members["session"].isin(sessions)]["calendar"])):
        if cal.is_session(code, as_of):
            c = cal.session_close_utc(code, as_of)
            status = "closed" if now_utc is None or c <= now_utc else "OPEN at run time (PRELIMINARY)"
            closes.append({"calendar": code, "session_date": as_of, "close_hkt": cal.fmt_hkt(c), "status": status})
        else:
            closes.append({"calendar": code, "session_date": as_of, "close_hkt": "", "status": "no session (holiday/weekend)"})
    warnings = list(dict.fromkeys(view.data.warnings + state.warnings))
    return DayResult(as_of=as_of, session=session, mode=mode, generated_at_utc=now_utc or pd.Timestamp.now(tz="UTC"),
                     data_source=view.data.source_name, benchmark_name=view.benchmark_name, warnings=warnings,
                     regime=regime, signals=signals, candidates=candidates, tranche2=tranche2, alerts=alerts,
                     portfolio=state, analytics=analytics, staging=staging, calendar=calendar,
                     session_closes=pd.DataFrame(closes), notes=notes)
