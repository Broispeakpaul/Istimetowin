"""Sections 4-5 and the 3-month plan: regime filter, sizing, competition phase."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

import pandas as pd

from .config import StrategyConfig
from .indicators import sma


@dataclass
class Regime:
    symbol: str
    price: float
    ma: float
    risk_on: bool
    cash_target: float
    ma_window: int = 200

    @property
    def label(self) -> str:
        return "RISK-ON" if self.risk_on else "RISK-OFF"


def market_regime(df: pd.DataFrame, cfg: StrategyConfig) -> Regime:
    price = float(df["Close"].iloc[-1])
    ma = float(sma(df["Close"], cfg.regime_ma).iloc[-1]) if len(df) >= cfg.regime_ma else float("nan")
    # Without 200 days of history, be defensive rather than optimistic.
    risk_on = not math.isnan(ma) and price > ma
    cash = cfg.cash_reserve_risk_on if risk_on else cfg.cash_reserve_risk_off
    return Regime(cfg.regime_symbol, price, ma, risk_on, cash, cfg.regime_ma)


class Phase(str, Enum):
    SETUP = "SETUP"        # weeks 1-2: build screen, rank, no new orders
    DEPLOY = "DEPLOY"      # weeks 3-10: deploy in tranches, rebalance weekly
    HARVEST = "HARVEST"    # weeks 11-12: tighten stops, take profits
    FINISHED = "FINISHED"  # competition over
    OPEN = "OPEN"          # no competition dates configured: always deploy


def competition_phase(cfg: StrategyConfig, today: date) -> tuple[Phase, int | None]:
    if cfg.competition_start is None:
        return Phase.OPEN, None
    week = (today - cfg.competition_start).days // 7 + 1
    if week < 1:
        return Phase.SETUP, week
    if week > cfg.competition_weeks:
        return Phase.FINISHED, week
    if week < cfg.deploy_start_week:
        return Phase.SETUP, week
    if week >= cfg.harvest_start_week:
        return Phase.HARVEST, week
    return Phase.DEPLOY, week


@dataclass
class Size:
    shares: int
    risk_pct: float        # fraction of equity lost if the stop is hit
    position_pct: float    # fraction of equity in the position
    limited_by: str


def position_size(
    equity: float,
    entry: float,
    stop: float,
    conviction: int,
    cfg: StrategyConfig,
    budget: float,
) -> Size:
    """Risk-based shares: min(risk budget / per-share risk, 15% cap, cash budget)."""
    per_share_risk = entry - stop
    if equity <= 0 or entry <= 0 or per_share_risk <= 0:
        return Size(0, 0.0, 0.0, "invalid inputs")
    risk_pct = cfg.risk_pct(conviction)
    by_risk = math.floor(equity * risk_pct / per_share_risk)
    by_cap = math.floor(equity * cfg.max_position_pct / entry)
    by_cash = math.floor(max(budget, 0) / entry)
    shares, limited_by = min(
        (by_risk, "risk"), (by_cap, "position cap"), (by_cash, "cash budget"),
        key=lambda t: t[0],
    )
    shares = max(shares, 0)
    return Size(
        shares,
        shares * per_share_risk / equity,
        shares * entry / equity,
        limited_by,
    )
