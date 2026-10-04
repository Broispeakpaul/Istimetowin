"""Combine screen, signals, regime and sizing into one rebalance plan.

The planner is pure (no IB calls): it takes account state + price history
and returns what to buy, sell, and which stops to set. The IB gateway then
turns OrderIntents into real orders, and the dashboard displays the plan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .catalysts import Catalyst, CatalystType
from .config import StrategyConfig
from .risk import Phase, Regime, competition_phase, market_regime, position_size
from .screener import ScreenResult, screen_universe
from .signals import entry_signal, exit_reasons, stop_distance


@dataclass
class Holding:
    symbol: str
    shares: float
    avg_cost: float
    stop_price: float | None = None   # highest active protective stop, if any
    stop_qty: float = 0.0             # shares covered by protective stops
    trailing: bool = False            # protective order is a TRAIL order
    trail_pct: float | None = None


@dataclass
class OptionSpec:
    min_expiry: date
    target_strike: float
    budget: float
    right: str = "C"


@dataclass
class OrderIntent:
    symbol: str
    action: str                       # BUY / SELL
    quantity: float
    order_type: str                   # LMT, MKT, STP, TRAIL
    reason: str
    limit_price: float | None = None
    stop_price: float | None = None
    trail_pct: float | None = None
    attach_stop: float | None = None  # BUY only: protective child stop
    attach_trail_pct: float | None = None
    modify_stops: bool = False        # STP/TRAIL: replace existing protective orders
    sec_type: str = "STK"
    option: OptionSpec | None = None


@dataclass
class CandidateRow:
    symbol: str
    catalyst: str
    conviction: int
    score: float
    last: float
    rs_pct: float
    adv_usd: float
    trigger: str | None
    detail: str
    status: str
    entry: float | None = None
    stop: float | None = None
    shares: int = 0
    risk_pct: float = 0.0
    position_pct: float = 0.0


@dataclass
class PositionRow:
    symbol: str
    shares: float
    avg_cost: float
    last: float
    pnl_pct: float
    position_pct: float
    stop: float | None
    action: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class Plan:
    today: date
    regime: Regime
    phase: Phase
    week: int | None
    equity: float
    cash: float
    invested: float
    max_invested: float
    candidates: list[CandidateRow]
    positions: list[PositionRow]
    orders: list[OrderIntent]
    notes: list[str]


def _score(res: ScreenResult, today: date, cfg: StrategyConfig) -> float:
    cat = res.catalyst
    strength = cat.strength(today, cfg.catalyst_max_age_days) if cat else 0.0
    rs = res.metrics.get("rs_pct", float("nan"))
    if cat and cat.type is CatalystType.CASH_MA or math.isnan(rs):
        rs = 0.5
    adv = max(res.metrics.get("adv_usd", 0.0), 1.0)
    liquidity = min(max(math.log10(adv / cfg.min_avg_dollar_volume) / math.log10(20), 0.0), 1.0)
    return round(0.5 * strength + 0.3 * rs + 0.2 * liquidity, 4)


def build_plan(
    today: date,
    cfg: StrategyConfig,
    equity: float,
    cash: float,
    holdings: dict[str, Holding],
    bars: dict[str, pd.DataFrame],
    regime_bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame | None,
    catalysts: dict[str, Catalyst],
    market_caps: dict[str, float] | None = None,
    cap_in_range: set[str] | None = None,
) -> Plan:
    regime = market_regime(regime_bars, cfg)
    phase, week = competition_phase(cfg, today)
    screen = {r.symbol: r for r in screen_universe(bars, benchmark_bars, catalysts, cfg, today,
                                                              market_caps, cap_in_range)}
    orders: list[OrderIntent] = []
    notes: list[str] = []
    positions: list[PositionRow] = []
    max_invested = equity * (1 - regime.cash_target)

    # ---- Existing positions: exits, stops, tranche adds -------------------
    kept: list[tuple[PositionRow, Holding]] = []
    for sym, h in holdings.items():
        df = bars.get(sym)
        if df is None or df.empty:
            notes.append(f"{sym}: no price data, position left untouched")
            continue
        last = float(df["Close"].iloc[-1])
        cat = catalysts.get(sym)
        value = h.shares * last
        pos_pct = value / equity if equity else 0.0
        pnl = last / h.avg_cost - 1 if h.avg_cost else 0.0
        atr_v = screen[sym].metrics.get("atr", float("nan")) if sym in screen else float("nan")
        base_stop = h.stop_price or round(h.avg_cost * (1 - stop_distance(h.avg_cost, atr_v, cfg)), 2)

        reasons = exit_reasons(df, cat, cfg, today, pos_pct, base_stop)
        row = PositionRow(sym, h.shares, h.avg_cost, last, pnl, pos_pct, base_stop, "HOLD", reasons)
        positions.append(row)
        if reasons:
            row.action = "EXIT"
            orders.append(OrderIntent(sym, "SELL", h.shares, "MKT", "; ".join(reasons)))
            continue

        new_stop = base_stop
        if phase is Phase.HARVEST:
            new_stop = max(base_stop, round(last * (1 - cfg.harvest_stop_pct), 2))
        trail = cfg.harvest_stop_pct if phase is Phase.HARVEST else cfg.trailing_pct

        if cfg.use_trailing_stop:
            if not h.trailing or h.stop_qty < h.shares or (h.trail_pct or 1.0) > trail + 1e-9:
                orders.append(OrderIntent(sym, "SELL", h.shares, "TRAIL", "set trailing stop",
                                          trail_pct=trail, modify_stops=True))
                row.action = "SET TRAIL"
        elif h.stop_qty < h.shares or h.stop_price is None:
            orders.append(OrderIntent(sym, "SELL", h.shares, "STP", "protective stop missing",
                                      stop_price=new_stop, modify_stops=True))
            row.action = "SET STOP"
        elif new_stop > (h.stop_price or 0) + 0.005:
            orders.append(OrderIntent(sym, "SELL", h.shares, "STP", "harvest phase: tighten stop",
                                      stop_price=new_stop, modify_stops=True))
            row.action = "TIGHTEN STOP"
        row.stop = new_stop
        kept.append((row, h))

    invested = sum(r.shares * r.last for r, _ in kept)

    # ---- Regime filter: trim the weakest names if over the exposure cap ---
    if invested > max_invested:
        for row, h in sorted(kept, key=lambda t: t[0].pnl_pct):
            if invested <= max_invested:
                break
            row.action = "EXIT"
            row.reasons.append(f"REGIME_TRIM ({regime.label}, cash target {regime.cash_target:.0%})")
            orders = [o for o in orders if o.symbol != row.symbol]
            orders.append(OrderIntent(row.symbol, "SELL", h.shares, "MKT", row.reasons[-1]))
            invested -= row.shares * row.last
        kept = [(r, h) for r, h in kept if r.action != "EXIT"]

    budget = max(min(max_invested - invested, cash), 0.0)
    can_deploy = phase in (Phase.DEPLOY, Phase.OPEN)

    # Add the next tranche to winners that still pass the screen.
    if can_deploy and cfg.tranches > 1:
        for row, h in kept:
            res = screen.get(row.symbol)
            if not res or not res.passed or row.pnl_pct <= 0 or row.stop is None:
                continue
            size = position_size(equity, row.last, row.stop, res.catalyst.conviction, cfg,
                                 budget + row.shares * row.last)
            add = int(size.shares - row.shares)
            if add >= max(1, 0.1 * size.shares) and add * row.last <= budget:
                limit = round(row.last * (1 + cfg.limit_offset_pct), 2)
                orders.append(OrderIntent(row.symbol, "BUY", add, "LMT", "add tranche to winner",
                                          limit_price=limit, attach_stop=row.stop))
                row.action = "ADD TRANCHE"
                budget -= add * limit

    # ---- New entries ------------------------------------------------------
    slots = cfg.max_positions - len(kept)
    candidates: list[CandidateRow] = []
    ranked = sorted(
        (r for r in screen.values() if r.catalyst and r.symbol not in holdings),
        key=lambda r: (r.passed, _score(r, today, cfg)),
        reverse=True,
    )
    for res in ranked:
        cat = res.catalyst
        m = res.metrics
        row = CandidateRow(res.symbol, cat.type.value, cat.conviction, _score(res, today, cfg),
                           m.get("last", float("nan")), m.get("rs_pct", float("nan")),
                           m.get("adv_usd", 0.0), None, "", "")
        candidates.append(row)
        if not res.passed:
            row.status, row.detail = "SCREENED OUT", "; ".join(res.fails)
            continue
        sig = entry_signal(bars[res.symbol], cat, cfg, today)
        row.trigger, row.detail = sig.trigger, sig.detail
        if sig.trigger is None:
            row.status = "WATCH"
            continue

        days = cat.days_to_event(today)
        binary_soon = days is not None and 0 <= days <= cfg.binary_entry_blackout_days
        use_options = cfg.options_enabled and cat.use_options and cat.event_date is not None

        if phase is Phase.FINISHED:
            row.status = "COMPETITION OVER"
            continue
        if phase is Phase.SETUP:
            row.status = "READY (setup weeks: no orders)"
            continue
        if phase is Phase.HARVEST and not (cat.conviction >= 3 and cat.is_imminent(today, cfg.harvest_imminent_days)):
            row.status = "SKIP (harvest weeks: not imminent/high conviction)"
            continue
        if slots <= 0:
            row.status = f"NO SLOT (max {cfg.max_positions} positions)"
            continue
        if binary_soon and not use_options:
            row.status = f"SKIP (binary event {cat.event_date}, use options or wait)"
            continue

        if use_options:
            prem = equity * cfg.option_budget_pct
            if prem > budget:
                row.status = "NO CASH"
                continue
            spec = OptionSpec(cat.event_date + timedelta(days=cfg.option_days_after_event),
                              round(row.last * (1 + cfg.option_otm_pct), 2), prem)
            orders.append(OrderIntent(res.symbol, "BUY", 0, "LMT",
                                      f"{sig.trigger}: long call through {cat.event_date}",
                                      sec_type="OPT", option=spec))
            row.status, row.risk_pct, row.position_pct = "BUY CALLS", cfg.option_budget_pct, cfg.option_budget_pct
            budget -= prem
            slots -= 1
            continue

        entry = round(row.last * (1 + cfg.limit_offset_pct), 2)
        dist = stop_distance(entry, m.get("atr", float("nan")), cfg)
        stop = round(entry * (1 - dist), 2)
        size = position_size(equity, entry, stop, cat.conviction, cfg, budget)
        is_deal = cat.type is CatalystType.CASH_MA
        shares = size.shares if is_deal or cfg.tranches <= 1 else math.ceil(size.shares / cfg.tranches)
        if shares <= 0:
            row.status = f"NO CASH (limited by {size.limited_by})"
            continue
        row.entry, row.stop, row.shares = entry, stop, shares
        row.risk_pct = shares * (entry - stop) / equity
        row.position_pct = shares * entry / equity
        tranche_note = "" if shares == size.shares else f" (tranche 1/{cfg.tranches} of {size.shares})"
        row.status = "BUY" + tranche_note
        orders.append(OrderIntent(
            res.symbol, "BUY", shares, "LMT", f"{sig.trigger}: {sig.detail}",
            limit_price=entry,
            attach_stop=None if cfg.use_trailing_stop else stop,
            attach_trail_pct=cfg.trailing_pct if cfg.use_trailing_stop else None,
        ))
        budget -= shares * entry
        slots -= 1

    total_after = len(kept) + sum(1 for o in orders if o.action == "BUY" and o.symbol not in holdings)
    if can_deploy and total_after < cfg.min_positions:
        notes.append(f"{total_after} positions after this plan (target {cfg.min_positions}-{cfg.max_positions}). "
                     "Don't force it: add catalysts to the CSV rather than weak names.")
    if not regime.risk_on:
        notes.append(f"{cfg.regime_symbol} below its {cfg.regime_ma}DMA: holding {regime.cash_target:.0%} cash.")

    return Plan(today, regime, phase, week, equity, cash, invested, max_invested,
                candidates, positions, orders, notes)
