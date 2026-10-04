"""Market de-risk switch: WLS below its 50-day average AND VIX above 25."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..config import Config
from .metrics import sma_status


@dataclass
class RegimeState:
    bench_name: str
    bench_date: Optional[pd.Timestamp]
    bench_close: Optional[float]
    bench_sma: Optional[float]
    vix_date: Optional[pd.Timestamp]
    vix: Optional[float]
    below_sma: Optional[bool]
    vix_high: Optional[bool]
    derisk: Optional[bool]       # None = data missing, confirm manually
    summary: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def evaluate_regime(view, cfg: Config) -> RegimeState:
    r = cfg.regime
    bench = view.benchmark()
    close, sma = sma_status(bench["adj_close"], r.sma_window) if not bench.empty else (None, None)
    bdate = bench.index.max() if not bench.empty else None
    vix = view.vix().dropna()
    v = float(vix.iloc[-1]) if len(vix) else None
    vdate = vix.index.max() if len(vix) else None
    below = None if (close is None or sma is None) else close < sma
    high = None if v is None else v > r.vix_threshold
    if below is True and high is True:
        derisk, summary = True, "DE-RISK: block new entries; move portfolio beta toward %.1f" % r.target_beta
    elif below is False or high is False:
        derisk, summary = False, "Normal: entries allowed"
    else:
        derisk, summary = None, "MISSING regime data: confirm WLS vs 50d average and VIX manually"
    parts = []
    if close is not None and sma is not None:
        parts.append(f"{view.benchmark_name} {close:,.2f} vs {r.sma_window}d avg {sma:,.2f} ({'below' if below else 'above'})")
    else:
        parts.append(f"{view.benchmark_name} {r.sma_window}d average MISSING")
    parts.append(f"VIX {v:.1f} (threshold {r.vix_threshold:g})" if v is not None else "VIX MISSING")
    return RegimeState(view.benchmark_name, bdate, close, sma, vdate, v, below, high, derisk,
                       summary + " | " + "; ".join(parts))


def regime_history(view, cfg: Config, sessions: int = 260) -> pd.DataFrame:
    """Benchmark, its moving average and VIX for the regime chart (data up to the as-of date only)."""
    b = view.benchmark()["adj_close"].dropna()
    if b.empty:
        return pd.DataFrame(columns=["date", "benchmark", "sma", "vix"])
    sma = b.rolling(cfg.regime.sma_window).mean()
    vix = view.vix().dropna()
    df = pd.DataFrame({"benchmark": b, "sma": sma})
    df["vix"] = vix.reindex(df.index.union(vix.index)).ffill().reindex(df.index) if len(vix) else float("nan")
    return df.tail(sessions).rename_axis("date").reset_index()
