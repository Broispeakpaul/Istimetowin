"""Section 1: universe screen (size, liquidity, price, relative strength, catalyst)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .catalysts import Catalyst, CatalystType
from .config import StrategyConfig
from .indicators import percentile_ranks, rs_raw, snapshot


@dataclass
class ScreenResult:
    symbol: str
    passed: bool
    metrics: dict
    catalyst: Catalyst | None = None
    fails: list[str] = field(default_factory=list)


def screen_universe(
    bars: dict[str, pd.DataFrame],
    benchmark_bars: pd.DataFrame | None,
    catalysts: dict[str, Catalyst],
    cfg: StrategyConfig,
    today: date,
    market_caps: dict[str, float] | None = None,
    cap_in_range: set[str] | None = None,
) -> list[ScreenResult]:
    """Apply every screen rule and return one result per symbol.

    Relative strength is ranked against every symbol in `bars`, so feed it
    the whole scanned universe, not just your catalyst names.
    `cap_in_range` lists symbols already filtered by market cap (IB scanner).
    """
    market_caps = market_caps or {}
    cap_in_range = cap_in_range or set()
    min_bars = cfg.rs_lookbacks[0] + 1
    usable = {s: df for s, df in bars.items() if df is not None and len(df) >= min_bars}
    rs_pct = percentile_ranks({s: rs_raw(df, cfg.rs_lookbacks) for s, df in usable.items()})
    bench_rs = rs_raw(benchmark_bars, cfg.rs_lookbacks) if benchmark_bars is not None else None

    results = []
    for symbol, df in bars.items():
        cat = catalysts.get(symbol)
        if symbol not in usable:
            results.append(ScreenResult(symbol, False, {}, cat, ["not enough price history"]))
            continue
        m = snapshot(df, cfg)
        m["rs_pct"] = rs_pct.get(symbol, float("nan"))
        mcap = market_caps.get(symbol) or (cat.market_cap if cat else None)
        m["market_cap"] = mcap
        fails = []

        if m["last"] < cfg.min_price:
            fails.append(f"price ${m['last']:.2f} < ${cfg.min_price:.0f}")
        if m["adv_usd"] < cfg.min_avg_dollar_volume:
            fails.append(f"ADV ${m['adv_usd'] / 1e6:.1f}M < ${cfg.min_avg_dollar_volume / 1e6:.0f}M")
        if mcap is None and symbol in cap_in_range:
            pass  # IB scanner already applied the market-cap filter
        elif mcap is None:
            fails.append("market cap unknown (add market_cap to catalysts CSV)")
        elif not cfg.min_market_cap <= mcap <= cfg.max_market_cap:
            fails.append(f"market cap ${mcap / 1e9:.2f}B outside range")

        # Cash-deal targets trade on the spread, not momentum, so skip RS for them.
        is_deal = cat is not None and cat.type is CatalystType.CASH_MA
        if not is_deal:
            if math.isnan(m["rs_pct"]) or m["rs_pct"] < cfg.rs_min_percentile:
                fails.append(f"RS pct {m['rs_pct']:.0%} < {cfg.rs_min_percentile:.0%}")
            if bench_rs is not None and not math.isnan(bench_rs) and m["rs_raw"] <= bench_rs:
                fails.append(f"not beating {cfg.rs_benchmark}")

        if cat is None:
            fails.append("no catalyst on file")
        elif not cat.is_fresh(today, cfg.catalyst_max_age_days):
            fails.append(f"catalyst stale or completed ({cat.age_days(today)}d old)")

        si = cat.short_interest if cat else None
        if cfg.require_short_interest and (si is None or si < cfg.squeeze_short_interest):
            fails.append("short interest below squeeze threshold")

        results.append(ScreenResult(symbol, not fails, m, cat, fails))
    return results
