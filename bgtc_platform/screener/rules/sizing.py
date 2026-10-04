"""Position sizing: ATR risk parity, earnings-hold cap, tranches and board lots."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from ..config import Config
from ..inputs import FAIL, PASS, PENDING


@dataclass
class SizeResult:
    target: Optional[float]                 # total target weight (both tranches)
    raw_atr_weight: Optional[float] = None
    earnings_cap: Optional[float] = None
    notes: list[str] = field(default_factory=list)

    def tranche(self, i: int, cfg: Config) -> Optional[float]:
        return None if self.target is None else self.target * cfg.sizing.tranche_split[i]


def atr_weight(atr_pct: Optional[float], cfg: Config) -> tuple[Optional[float], Optional[float], str]:
    """weight = risk_budget / ATR%, clamped to [min_weight, max_weight]. Returns (clamped, raw, note)."""
    if atr_pct is None or atr_pct <= 0:
        return None, None, "MISSING ATR"
    s = cfg.sizing
    raw = s.risk_budget / atr_pct
    w = min(max(raw, s.min_weight), s.max_weight)
    note = f"{s.risk_budget:.2%} / ATR{s.atr_window} {atr_pct:.2%} = {raw:.1%}"
    if w != raw:
        note += f" -> clamped to {w:.1%}"
    return w, raw, note


def earnings_hold_cap(implied_move: Optional[float], cfg: Config) -> Optional[float]:
    """Max weight when holding through the position's own report: 1% / (1.5 x implied move)."""
    if implied_move is None or implied_move <= 0:
        return None
    return cfg.sizing.earnings_hold_risk / (cfg.sizing.implied_move_multiplier * implied_move)


def size_position(atr_pct: Optional[float], cfg: Config, holds_through_report: bool = False,
                  implied_move: Optional[float] = None, implied_source: str = "manual") -> SizeResult:
    w, raw, note = atr_weight(atr_pct, cfg)
    res = SizeResult(target=w, raw_atr_weight=raw, notes=[note])
    if w is None:
        return res
    if holds_through_report:
        cap = earnings_hold_cap(implied_move, cfg)
        if cap is None:
            res.notes.append("reports within hold window: CONFIRM MANUALLY implied move (earnings-hold cap not applied)")
        else:
            res.earnings_cap = cap
            res.notes.append(f"earnings-hold cap {cfg.sizing.earnings_hold_risk:.0%} / ({cfg.sizing.implied_move_multiplier}"
                             f" x {implied_move:.1%} {implied_source}) = {cap:.1%}")
            if cap < w:
                res.target = cap
    res.target = min(res.target, cfg.competition.max_position_weight)
    return res


def lot_int(lot) -> int:
    """Board lot as an int; missing (None / NaN / pd.NA) means 1."""
    try:
        return 1 if lot is None or pd.isna(lot) or int(lot) < 1 else int(lot)
    except (TypeError, ValueError):
        return 1


def shares_for(weight: Optional[float], nav_usd: float, price_usd: Optional[float], lot: Optional[int]) -> Optional[int]:
    """Shares for a weight, rounded DOWN to the board lot (never over-allocates)."""
    if weight is None or price_usd is None or pd.isna(price_usd) or price_usd <= 0 or nav_usd <= 0:
        return None
    lot = lot_int(lot)
    n = math.floor(weight * nav_usd / price_usd / lot) * lot
    return max(n, 0)


def second_tranche_decision(close_d3: Optional[float], low_d1: Optional[float], check5: str,
                            derisk: Optional[bool], cfg: Config) -> tuple[str, str]:
    """Day-three add: close above the day-one low and check 5 not failed."""
    if close_d3 is None or low_d1 is None:
        return "DATA MISSING", "MISSING day-three close or day-one low"
    if close_d3 <= low_d1:
        return "SKIP", f"close {close_d3:,.2f} is not above the day-one low {low_d1:,.2f}"
    if derisk is True and cfg.sizing.block_second_tranche_in_derisk:
        return "BLOCKED (DE-RISK)", "market de-risk active: no adds"
    if check5 == FAIL:
        return "SKIP", "check 5 FAILED: consensus EPS not revised up"
    base = f"close {close_d3:,.2f} > day-one low {low_d1:,.2f}"
    if check5 == PASS:
        return "BUY", base + "; check 5 confirmed"
    return "BUY ONLY IF CHECK 5 CONFIRMED", base + "; CONFIRM MANUALLY: consensus EPS revised up within 3 sessions"
