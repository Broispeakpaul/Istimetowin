"""Section 3: entry triggers, stop placement and exit rules."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from .catalysts import Catalyst, CatalystType
from .config import StrategyConfig
from .indicators import sma


@dataclass
class EntrySignal:
    trigger: str | None   # "BREAKOUT", "PULLBACK_20DMA", "DEAL_SPREAD" or None
    detail: str


def entry_signal(df: pd.DataFrame, cat: Catalyst, cfg: StrategyConfig, today: date) -> EntrySignal:
    close = df["Close"]
    last = float(close.iloc[-1])

    if cat.type is CatalystType.CASH_MA:
        spread = cat.ma_spread(last)
        end = cfg.competition_end
        if cat.expected_close and end and cat.expected_close > end:
            return EntrySignal(None, f"deal closes {cat.expected_close} after competition ends")
        if spread is not None and spread >= cfg.ma_min_spread:
            return EntrySignal("DEAL_SPREAD", f"{spread:.1%} to ${cat.offer_price:.2f} offer")
        return EntrySignal(None, f"spread {spread or 0:.1%} < {cfg.ma_min_spread:.0%}")

    n = cfg.breakout_lookback
    if len(df) > n + 1:
        prior_high = float(df["High"].iloc[-n - 1:-1].max())
        avg_vol = float(df["Volume"].iloc[-n - 1:-1].mean())
        vol_ok = float(df["Volume"].iloc[-1]) >= cfg.breakout_volume_mult * avg_vol
        if last > prior_high and vol_ok:
            return EntrySignal("BREAKOUT", f"close {last:.2f} > {n}d high {prior_high:.2f} on volume")

    ma20 = sma(close, cfg.pullback_ma)
    ma50 = sma(close, cfg.exit_ma)
    m20, m50 = float(ma20.iloc[-1]), float(ma50.iloc[-1])
    if not (math.isnan(m20) or math.isnan(m50)):
        # Only count pullbacks that happened after the catalyst hit.
        since_cat = df[df.index >= pd.Timestamp(cat.announced)]
        low = float(df["Low"].iloc[-1])
        touched = low <= m20 * (1 + cfg.pullback_tolerance)
        held = last >= m20 and m20 > m50
        if len(since_cat) >= 2 and touched and held:
            return EntrySignal("PULLBACK_20DMA", f"held 20DMA {m20:.2f} after catalyst")
    return EntrySignal(None, "waiting for breakout or 20DMA pullback")


def stop_distance(entry: float, atr_value: float, cfg: StrategyConfig) -> float:
    """ATR-based stop distance as a fraction of entry, clamped to 10-14.5%."""
    if atr_value and not math.isnan(atr_value) and entry > 0:
        raw = cfg.stop_atr_mult * atr_value / entry
    else:
        raw = cfg.stop_pct_min
    return min(max(raw, cfg.stop_pct_min), cfg.stop_pct_max)


def exit_reasons(
    df: pd.DataFrame,
    cat: Catalyst | None,
    cfg: StrategyConfig,
    today: date,
    position_pct: float,
    stop_price: float | None,
) -> list[str]:
    """Reasons to close an open position now. Empty list = keep holding."""
    reasons = []
    last = float(df["Close"].iloc[-1])
    ma50 = float(sma(df["Close"], cfg.exit_ma).iloc[-1]) if len(df) >= cfg.exit_ma else float("nan")

    if stop_price and last <= stop_price:
        reasons.append(f"STOP hit ({last:.2f} <= {stop_price:.2f})")

    is_deal = cat is not None and cat.type is CatalystType.CASH_MA
    if is_deal:
        spread = cat.ma_spread(last)
        if spread is not None and spread <= cfg.ma_exit_spread:
            reasons.append(f"EVENT_COMPLETE spread {spread:.2%} left")
    elif not math.isnan(ma50) and last < ma50:
        reasons.append(f"MOMENTUM_FADE close {last:.2f} < 50DMA {ma50:.2f}")

    if cat is not None and cat.completed:
        reasons.append("EVENT_COMPLETE (marked completed)")

    days = cat.days_to_event(today) if cat else None
    if days is not None and 0 <= days <= cfg.binary_event_buffer_days:
        small_and_sure = cat.conviction >= 3 and position_pct <= cfg.binary_hold_max_position_pct
        if not small_and_sure:
            reasons.append(f"BINARY_EVENT in {days}d ({cat.event_date})")
    return reasons
