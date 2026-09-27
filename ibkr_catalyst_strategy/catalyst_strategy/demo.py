"""Offline demo: synthetic prices that exercise every rule, no IB needed."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .catalysts import Catalyst, CatalystType
from .config import StrategyConfig
from .planner import Holding, Plan, build_plan


def make_bars(end: date, n: int = 300, start: float = 50.0, drift: float = 0.0005,
              vol: float = 0.015, volume: float = 1_000_000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    rets = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(rets))
    spread = np.abs(rng.normal(0, vol / 2, n)) * close
    df = pd.DataFrame({
        "Open": close * (1 + rng.normal(0, vol / 4, n)),
        "High": close + spread,
        "Low": close - spread,
        "Close": close,
        "Volume": volume * rng.uniform(0.7, 1.3, n),
    }, index=idx)
    df["High"] = df[["Open", "High", "Close"]].max(axis=1)
    df["Low"] = df[["Open", "Low", "Close"]].min(axis=1)
    return df


def with_breakout(df: pd.DataFrame, jump: float = 0.06) -> pd.DataFrame:
    df = df.copy()
    prior_high = df["High"].iloc[-21:-1].max()
    last = prior_high * (1 + jump)
    df.iloc[-1, df.columns.get_loc("Close")] = last
    df.iloc[-1, df.columns.get_loc("High")] = last * 1.005
    df.iloc[-1, df.columns.get_loc("Volume")] = df["Volume"].iloc[-21:-1].mean() * 2.5
    return df


def with_pullback(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ma20 = df["Close"].rolling(20).mean().iloc[-1]
    df.iloc[-1, df.columns.get_loc("Low")] = ma20 * 0.995
    df.iloc[-1, df.columns.get_loc("Close")] = ma20 * 1.01
    df.iloc[-1, df.columns.get_loc("High")] = max(df["High"].iloc[-1], ma20 * 1.02)
    return df


def with_fade(df: pd.DataFrame, drop: float = 0.12) -> pd.DataFrame:
    df = df.copy()
    ma50 = df["Close"].rolling(50).mean().iloc[-1]
    df.iloc[-1, df.columns.get_loc("Close")] = ma50 * (1 - drop / 3)
    df.iloc[-1, df.columns.get_loc("Low")] = min(df["Low"].iloc[-1], ma50 * (1 - drop / 3) * 0.99)
    return df


def demo_inputs(today: date | None = None):
    today = today or date.today()
    ago = lambda d: today - timedelta(days=d)  # noqa: E731

    bars: dict[str, pd.DataFrame] = {}
    # Broad liquid universe for relative-strength ranking.
    for i in range(40):
        drift = np.linspace(-0.0015, 0.0020, 40)[i]
        bars[f"U{i:02d}"] = make_bars(today, start=20 + i, drift=drift, volume=600_000, seed=100 + i)

    bars["BRKO"] = with_breakout(make_bars(today, start=30, drift=0.0032, volume=900_000, seed=12))
    bars["PULL"] = with_pullback(make_bars(today, start=25, drift=0.0032, volume=1_200_000, seed=27))
    bars["ACTV"] = with_breakout(make_bars(today, start=40, drift=0.0026, volume=700_000, seed=3))
    bars["SPIN"] = make_bars(today, start=35, drift=0.0027, volume=800_000, seed=4)
    bars["DEAL"] = make_bars(today, start=46, drift=0.0, vol=0.002, volume=600_000, seed=5)
    bars["EARN"] = with_breakout(make_bars(today, start=60, drift=0.0025, volume=500_000, seed=6))
    bars["TINY"] = with_breakout(make_bars(today, start=3, drift=0.003, volume=400_000, seed=7))
    bars["WINR"] = make_bars(today, start=30, drift=0.0030, volume=900_000, seed=8)
    bars["FADE"] = with_fade(make_bars(today, start=50, drift=0.0012, volume=900_000, seed=9))

    regime = make_bars(today, start=450, drift=0.0006, vol=0.009, volume=80e6, seed=50)
    bench = make_bars(today, start=200, drift=0.0002, vol=0.012, volume=30e6, seed=51)

    deal_last = float(bars["DEAL"]["Close"].iloc[-1])
    C = CatalystType
    catalysts = {c.symbol: c for c in [
        Catalyst("BRKO", C.PEAD, ago(6), 3, earnings_surprise_pct=18, guidance_raised=True,
                 revisions_up=True, market_cap=1.8e9, short_interest=0.14, notes="beat + raise"),
        Catalyst("PULL", C.PEAD, ago(12), 2, earnings_surprise_pct=9, guidance_raised=True,
                 market_cap=2.4e9),
        Catalyst("ACTV", C.ACTIVIST, ago(9), 2, activist="Elliott", market_cap=4.1e9),
        Catalyst("SPIN", C.CORP_ACTION, ago(20), 2, market_cap=6.0e9, notes="spin-off announced"),
        Catalyst("DEAL", C.CASH_MA, ago(15), 2, offer_price=round(deal_last * 1.045, 2),
                 expected_close=today + timedelta(days=50), market_cap=1.2e9),
        Catalyst("EARN", C.PEAD, ago(40), 3, event_date=today + timedelta(days=6), market_cap=3.3e9,
                 use_options=True, notes="FDA decision next week"),
        Catalyst("TINY", C.CORP_ACTION, ago(5), 1, market_cap=0.2e9),
        Catalyst("WINR", C.ACTIVIST, ago(30), 2, activist="Starboard", market_cap=2.9e9),
        Catalyst("FADE", C.PEAD, ago(35), 2, market_cap=5.5e9),
    ]}

    winr_last = float(bars["WINR"]["Close"].iloc[-1])
    fade_last = float(bars["FADE"]["Close"].iloc[-1])
    holdings = {
        # A winner whose stop has been raised: eligible for its second tranche.
        "WINR": Holding("WINR", 70, round(winr_last * 0.85, 2), round(winr_last * 0.88, 2), 70),
        "FADE": Holding("FADE", 120, round(fade_last * 1.05, 2), round(fade_last * 0.8, 2), 120),
    }
    return dict(bars=bars, regime_bars=regime, benchmark_bars=bench, catalysts=catalysts,
                holdings=holdings, equity=100_000.0, cash=78_000.0)


def run_demo(cfg: StrategyConfig | None = None, today: date | None = None) -> Plan:
    today = today or date.today()
    cfg = cfg or StrategyConfig(options_enabled=True)
    d = demo_inputs(today)
    return build_plan(today, cfg, d["equity"], d["cash"], d["holdings"], d["bars"],
                      d["regime_bars"], d["benchmark_bars"], d["catalysts"])
