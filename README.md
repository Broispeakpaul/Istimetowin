# Istimetowin
This is a real money making ISTIMETOWIN Project! GUYS LOCK IN PLEASE!!
a/algo_trading_signal\tests\test_strategy.py → b/algo_trading_signal\tests\test_strategy.py
@@ -0,0 +1,48 @@
+import pandas as pd
+
+from trading_signal.strategy import StrategyConfig, generate_signal
+
+
+def _uptrend_frame(rows=260, pullback=True):
+    base = 100.0
+    closes = []
+    for i in range(rows):
+        closes.append(base + i * 0.20)
+    if pullback:
+        # Create a controlled pullback near the 20 EMA, then bullish recovery.
+        closes[-6:] = [151.0, 149.5, 148.8, 148.5, 149.0, 151.2]
+    highs = [c + 1.0 for c in closes]
+    lows = [c - 1.0 for c in closes]
+    opens = [c - 0.2 for c in closes]
+    volume = [2_000_000] * rows
+    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volume})
+
+
+def test_generate_signal_marks_quality_pullback_as_buy_with_rr_above_minimum():
+    df = _uptrend_frame()
+    spy = _uptrend_frame(pullback=False)
+    cfg = StrategyConfig(min_price=5, min_avg_dollar_volume=1_000_000, min_reward_risk=2.01)
+
+    signal = generate_signal("TEST", df, spy, cfg)
+
+    assert signal.action == "BUY"
+    assert signal.entry > signal.stop_loss
+    assert signal.target > signal.entry
+    assert signal.reward_risk >= 2.01
+    assert signal.score >= cfg.min_score
+    assert not signal.risk_flags
+
+
+def test_generate_signal_rejects_low_price_high_risk_stock():
+    df = _uptrend_frame()
+    df["Close"] = 0.45
+    df["Open"] = 0.44
+    df["High"] = 0.48
+    df["Low"] = 0.42
+    spy = _uptrend_frame(pullback=False)
+    cfg = StrategyConfig(min_price=5, min_avg_dollar_volume=1_000_000)
+
+    signal = generate_signal("PENNY", df, spy, cfg)
+
+    assert signal.action == "AVOID"
+    assert any("price below" in flag.lower() for flag in signal.risk_flags)
+    a/algo_trading_signal\tests\test_backtest.py → b/algo_trading_signal\tests\test_backtest.py
@@ -0,0 +1,26 @@
+import pandas as pd
+
+from trading_signal.backtest import BacktestConfig, backtest_symbol
+
+
+def _breakout_dataset(rows=320):
+    # Long warm-up uptrend, one pullback/recovery signal, then a clean target hit.
+    closes = [100 + i * 0.15 for i in range(rows)]
+    closes[255:262] = [139, 138, 137, 136.5, 137.0, 138.5, 140.0]
+    closes[263:268] = [143.0, 146.0, 149.0, 152.0, 155.0]
+    highs = [c + 1.0 for c in closes]
+    lows = [c - 1.0 for c in closes]
+    opens = [c - 0.2 for c in closes]
+    volume = [2_000_000] * rows
+    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volume})
+
+
+def test_backtest_records_trades_and_summary_metrics():
+    df = _breakout_dataset()
+    spy = _breakout_dataset()
+    result = backtest_symbol("TEST", df, spy, BacktestConfig(initial_cash=10_000, risk_per_trade=0.01))
+
+    assert result.summary["trades"] >= 1
+    assert result.summary["win_rate"] >= 0
+    assert "expectancy_r" in result.summary
+    assert set(result.trades.columns) >= {"ticker", "entry_date", "exit_date", "entry", "stop_loss", "target", "exit_reason", "r_multiple"}
+    a/algo_trading_signal\requirements.txt → b/algo_trading_signal\requirements.txt
a/algo_trading_signal\requirements.txt → b/algo_trading_signal\requirements.txt
@@ -0,0 +1,4 @@
+pandas>=2.0
+numpy>=1.24
+yfinance>=0.2.40
+pytest>=8.0
a/algo_trading_signal\README.md → b/algo_trading_signal\README.md
@@ -0,0 +1,5 @@
+# NYSE Trend Pullback Signal Engine
+
+Educational Python scanner/backtester for NYSE-style swing trading signals. It focuses on liquid, non-penny stocks with trend, momentum, support, ATR-based stops, and >=2.01R target logic.
+
+This is not financial advice and does not guarantee profit.
a/algo_trading_signal\trading_signal\__init__.py → b/algo_trading_signal\trading_signal\__init__.py
@@ -0,0 +1,17 @@
+"""NYSE-style trend pullback signal engine.
+
+Educational tooling only. It produces scanner/backtest outputs but does not
+promise future profitability.
+"""
+
+from .strategy import Signal, StrategyConfig, generate_signal
+from .backtest import BacktestConfig, BacktestResult, backtest_symbol
+
+__all__ = [
+    "Signal",
+    "StrategyConfig",
+    "generate_signal",
+    "BacktestConfig",
+    "BacktestResult",
+    "backtest_symbol",

+]a/algo_trading_signal\trading_signal\indicators.py → b/algo_trading_signal\trading_signal\indicators.py
@@ -0,0 +1,127 @@
+from __future__ import annotations
+
+import pandas as pd
+
+REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
+
+
+def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
+    """Return a clean OHLCV frame with standard column names.
+
+    Accepts ordinary dataframes and the column shapes returned by yfinance.
+    """
+    if df is None or df.empty:
+        raise ValueError("OHLCV dataframe is empty")
+
+    cleaned = df.copy()
+
+    if isinstance(cleaned.columns, pd.MultiIndex):
+        # yfinance can return either (Price, Ticker) or (Ticker, Price). Pick the
+        # first level that contains the required price names.
+        levels = list(range(cleaned.columns.nlevels))
+        selected_level = None
+        for level in levels:
+            names = {str(v).lower() for v in cleaned.columns.get_level_values(level)}
+            if {c.lower() for c in REQUIRED_COLUMNS}.issubset(names):
+                selected_level = level
+                break
+        if selected_level is not None:
+            cleaned.columns = cleaned.columns.get_level_values(selected_level)
+        else:
+            cleaned.columns = ["_".join(str(part) for part in col if part) for col in cleaned.columns]
+
+    rename = {}
+    for column in cleaned.columns:
+        key = str(column).strip().lower().replace(" ", "_")
+        if key in {"open", "1._open"}:
+            rename[column] = "Open"
+        elif key in {"high", "2._high"}:
+            rename[column] = "High"
+        elif key in {"low", "3._low"}:
+            rename[column] = "Low"
+        elif key in {"close", "4._close", "adj_close", "adjclose", "adjusted_close"}:
+            rename[column] = "Close"
+        elif key in {"volume", "5._volume"}:
+            rename[column] = "Volume"
+    cleaned = cleaned.rename(columns=rename)
+
+    missing = [column for column in REQUIRED_COLUMNS if column not in cleaned.columns]
+    if missing:
+        raise ValueError(f"Missing OHLCV columns: {missing}")
+
+    cleaned = cleaned.loc[:, list(REQUIRED_COLUMNS)]
+    for column in REQUIRED_COLUMNS:
+        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
+    cleaned = cleaned.dropna(subset=list(REQUIRED_COLUMNS)).sort_index()
+    return cleaned
+
+
+def sma(series: pd.Series, window: int) -> pd.Series:
+    return series.rolling(window=window, min_periods=window).mean()
+
+
+def ema(series: pd.Series, span: int) -> pd.Series:
+    return series.ewm(span=span, adjust=False, min_periods=span).mean()
+
+
+def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
+    high = df["High"]
+    low = df["Low"]
+    close = df["Close"]
+    prev_close = close.shift(1)
+    true_range = pd.concat(
+        [
+            high - low,
+            (high - prev_close).abs(),
+            (low - prev_close).abs(),
+        ],
+        axis=1,
… omitted 49 diff line(s) across 1 additional file(s)/section(s)
a/algo_trading_signal\trading_signal\strategy.py → b/algo_trading_signal\trading_signal\strategy.py
@@ -0,0 +1,347 @@
+from __future__ import annotations
+
+from dataclasses import asdict, dataclass, field
+from typing import Any
+
+import pandas as pd
+
+from .indicators import add_indicators, normalize_ohlcv
+
+
+@dataclass(frozen=True)
+class StrategyConfig:
+    """Configurable safety and signal parameters.
+
+    Defaults are intentionally conservative for beginner paper trading.
+    """
+
+    min_price: float = 5.0
+    min_avg_dollar_volume: float = 20_000_000.0
+    max_atr_pct: float = 0.08
+    max_gap_pct: float = 0.12
+    min_reward_risk: float = 2.01
+    min_score: int = 70
+    min_history: int = 220
+    atr_stop_multiplier: float = 1.5
+    swing_lookback: int = 10
+    support_tolerance_atr: float = 1.25
+    avoid_earnings_days: int = 2
+
+
+@dataclass
+class Signal:
+    ticker: str
+    action: str
+    date: Any
+    close: float | None
+    entry: float | None
+    stop_loss: float | None
+    target: float | None
+    reward_risk: float
+    score: int
+    setup: str
+    reason: str
+    risk_flags: list[str] = field(default_factory=list)
+    trend_score: int = 0
+    momentum_score: int = 0
+    support_score: int = 0
+    volume_score: int = 0
+    regime_score: int = 0
+    risk_score: int = 0
+    indicators: dict[str, float | None] = field(default_factory=dict)
+
+    def to_dict(self) -> dict[str, Any]:
+        data = asdict(self)
+        data["risk_flags"] = "; ".join(self.risk_flags)
+        data.update({f"ind_{key}": value for key, value in self.indicators.items()})
+        data.pop("indicators", None)
+        return data
+
+
+def _round_price(value: float | None) -> float | None:
+    if value is None:
+        return None
+    if value >= 10:
+        return round(value, 2)
+    return round(value, 4)
+
+
+def _safe_float(value: Any) -> float | None:
+    if pd.isna(value):
+        return None
+    return float(value)
+
+
+def _blank_signal(ticker: str, action: str, date: Any = None, reason: str = "", risk_flags: list[str] | None = None) -> Signal:
+    return Signal(
+        ticker=ticker,
+        action=action,
… omitted 269 diff line(s) across 1 additional file(s)/section(s)
a/algo_trading_signal\trading_signal\backtest.py → b/algo_trading_signal\trading_signal\backtest.py
@@ -0,0 +1,220 @@
+from __future__ import annotations
+
+from dataclasses import dataclass, field
+from typing import Any
+
+import pandas as pd
+
+from .indicators import normalize_ohlcv
+from .strategy import StrategyConfig, generate_signal
+
+
+@dataclass(frozen=True)
+class BacktestConfig:
+    initial_cash: float = 10_000.0
+    risk_per_trade: float = 0.005
+    commission_per_trade: float = 0.0
+    slippage_bps: float = 5.0
+    max_hold_days: int = 30
+    strategy: StrategyConfig = field(default_factory=StrategyConfig)
+
+
+@dataclass
+class BacktestResult:
+    ticker: str
+    summary: dict[str, Any]
+    trades: pd.DataFrame
+    equity_curve: pd.DataFrame
+
+
+def _max_drawdown(equity: pd.Series) -> float:
+    if equity.empty:
+        return 0.0
+    running_max = equity.cummax()
+    drawdown = equity / running_max - 1
+    return float(drawdown.min())
+
+
+def _summarize(ticker: str, initial_cash: float, cash: float, trades: pd.DataFrame, equity_curve: pd.DataFrame) -> dict[str, Any]:
+    if trades.empty:
+        return {
+            "ticker": ticker,
+            "trades": 0,
+            "wins": 0,
+            "losses": 0,
+            "win_rate": 0.0,
+            "avg_win_r": 0.0,
+            "avg_loss_r": 0.0,
+            "realized_reward_risk": 0.0,
+            "expectancy_r": 0.0,
+            "profit_factor": 0.0,
+            "total_return_pct": round((cash / initial_cash - 1) * 100, 2),
+            "max_drawdown_pct": round(_max_drawdown(equity_curve.get("equity", pd.Series(dtype=float))) * 100, 2),
+        }
+
+    wins = trades[trades["r_multiple"] > 0]
+    losses = trades[trades["r_multiple"] <= 0]
+    gross_win = float(wins["pnl"].sum()) if not wins.empty else 0.0
+    gross_loss = abs(float(losses["pnl"].sum())) if not losses.empty else 0.0
+    avg_win_r = float(wins["r_multiple"].mean()) if not wins.empty else 0.0
+    avg_loss_r = abs(float(losses["r_multiple"].mean())) if not losses.empty else 0.0
+    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
+    win_rate = len(wins) / len(trades) if len(trades) else 0.0
+    expectancy_r = float(trades["r_multiple"].mean()) if len(trades) else 0.0
+
+    return {
+        "ticker": ticker,
+        "trades": int(len(trades)),
+        "wins": int(len(wins)),
+        "losses": int(len(losses)),
+        "win_rate": round(win_rate, 4),
+        "avg_win_r": round(avg_win_r, 4),
+        "avg_loss_r": round(avg_loss_r, 4),
+        "realized_reward_risk": round(avg_win_r / avg_loss_r, 4) if avg_loss_r > 0 else round(avg_win_r, 4),
+        "expectancy_r": round(expectancy_r, 4),
+        "profit_factor": round(profit_factor, 4),
+        "total_return_pct": round((cash / initial_cash - 1) * 100, 2),
+        "max_drawdown_pct": round(_max_drawdown(equity_curve["equity"]) * 100, 2) if not equity_curve.empty else 0.0,
+    }
… omitted 142 diff line(s) across 1 additional file(s)/section(s)
a/algo_trading_signal\trading_signal\data.py → b/algo_trading_signal\trading_signal\data.py
@@ -0,0 +1,76 @@
+from __future__ import annotations
+
+from pathlib import Path
+
+import pandas as pd
+
+DEFAULT_NYSE_WATCHLIST = [
+    "JPM",
+    "BAC",
+    "GS",
+    "MS",
+    "XOM",
+    "CVX",
+    "COP",
+    "CAT",
+    "DE",
+    "GE",
+    "UNH",
+    "JNJ",
+    "PFE",
+    "KO",
+    "MCD",
+    "WMT",
+    "HD",
+    "DIS",
+    "BA",
+    "IBM",
+]
+
+
+def read_tickers_file(path: str | Path) -> list[str]:
+    text = Path(path).read_text(encoding="utf-8")
+    tickers: list[str] = []
+    for raw in text.replace(",", "\n").splitlines():
+        symbol = raw.strip().upper()
+        if not symbol or symbol.startswith("#"):
+            continue
+        tickers.append(symbol)
+    return sorted(set(tickers))
+
+
+def fetch_history(ticker: str, period: str = "3y", interval: str = "1d") -> pd.DataFrame:
+    try:
+        import yfinance as yf
+    except ImportError as exc:
+        raise RuntimeError("Install yfinance first: python -m pip install -r requirements.txt") from exc
+
+    df = yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False, threads=False)
+    if df.empty:
+        raise RuntimeError(f"No data returned for {ticker}")
+    return df
+
+
+def fetch_many(tickers: list[str], period: str = "3y", interval: str = "1d") -> dict[str, pd.DataFrame]:
+    data: dict[str, pd.DataFrame] = {}
+    for ticker in tickers:
+        try:
+            data[ticker] = fetch_history(ticker, period=period, interval=interval)
+        except Exception as exc:  # keep scanning remaining tickers
+            print(f"WARN: {ticker}: {exc}")
+    return data
+
+
+def make_demo_ohlcv(rows: int = 320, start: str = "2024-01-01", price: float = 100.0) -> pd.DataFrame:
+    """Deterministic offline dataset with an uptrend, pullback, and recovery."""
+    dates = pd.bdate_range(start=start, periods=rows)
+    closes = [price + i * 0.18 for i in range(rows)]
+    if rows > 270:
+        closes[250:262] = [price + 45, price + 44, price + 43, price + 42.5, price + 42, price + 41.8, price + 42.2, price + 42.9, price + 43.8, price + 45.2, price + 47, price + 49]
+        for i in range(262, rows):
+            closes[i] = closes[i - 1] + 0.45
+    opens = [c - 0.25 for c in closes]
+    highs = [c + 1.0 for c in closes]
+    lows = [c - 1.0 for c in closes]
+    volume = [2_000_000 + (i % 7) * 50_000 for i in range(rows)]
+    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volume}, index=dates)
+    a/algo_trading_signal\trading_signal\report.py → b/algo_trading_signal\trading_signal\report.py
@@ -0,0 +1,85 @@
+from __future__ import annotations
+
+from pathlib import Path
+from typing import Iterable
+
+import pandas as pd
+
+from .backtest import BacktestConfig, backtest_symbol
+from .strategy import StrategyConfig, generate_signal
+
+
+def scan_tickers(
+    data_by_ticker: dict[str, pd.DataFrame],
+    benchmark_df: pd.DataFrame | None,
+    config: StrategyConfig | None = None,
+) -> pd.DataFrame:
+    cfg = config or StrategyConfig()
+    rows = []
+    for ticker, df in data_by_ticker.items():
+        signal = generate_signal(ticker, df, benchmark_df, cfg)
+        rows.append(signal.to_dict())
+    if not rows:
+        return pd.DataFrame()
+    out = pd.DataFrame(rows)
+    action_rank = {"BUY": 0, "WATCH": 1, "SELL": 2, "AVOID": 3}
+    out["action_rank"] = out["action"].map(action_rank).fillna(9)
+    return out.sort_values(["action_rank", "score", "reward_risk"], ascending=[True, False, False]).drop(columns=["action_rank"])
+
+
+def scan_with_backtests(
+    data_by_ticker: dict[str, pd.DataFrame],
+    benchmark_df: pd.DataFrame | None,
+    strategy_config: StrategyConfig | None = None,
+    backtest_config: BacktestConfig | None = None,
+) -> tuple[pd.DataFrame, pd.DataFrame]:
+    strategy_cfg = strategy_config or StrategyConfig()
+    backtest_cfg = backtest_config or BacktestConfig(strategy=strategy_cfg)
+    scan = scan_tickers(data_by_ticker, benchmark_df, strategy_cfg)
+    summaries = []
+    for ticker, df in data_by_ticker.items():
+        result = backtest_symbol(ticker, df, benchmark_df, backtest_cfg)
+        summaries.append(result.summary)
+    summary_df = pd.DataFrame(summaries)
+    if not scan.empty and not summary_df.empty:
+        scan = scan.merge(summary_df, on="ticker", how="left", suffixes=("", "_bt"))
+        scan["backtest_pass"] = (
+            (scan["win_rate"].fillna(0) >= 0.35)
+            & (scan["realized_reward_risk"].fillna(0) >= strategy_cfg.min_reward_risk)
+            & (scan["expectancy_r"].fillna(0) > 0)
+            & (scan["trades"].fillna(0) >= 3)
+        )
+    return scan, summary_df
+
+
+def write_reports(scan: pd.DataFrame, output_dir: str | Path, name: str = "morning_watchlist") -> dict[str, Path]:
+    outdir = Path(output_dir)
+    outdir.mkdir(parents=True, exist_ok=True)
+    csv_path = outdir / f"{name}.csv"
+    html_path = outdir / f"{name}.html"
+    scan.to_csv(csv_path, index=False)
+    title = "NYSE Trend/Momentum/Support Watchlist"
+    html = f"""
+<!doctype html>
+<html>
+<head>
+  <meta charset="utf-8">
+  <title>{title}</title>
+  <style>
+    body {{ font-family: Arial, sans-serif; margin: 24px; }}
+    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
+    th, td {{ border: 1px solid #ddd; padding: 6px; }}
+    th {{ background: #111827; color: white; position: sticky; top: 0; }}
+    tr:nth-child(even) {{ background: #f8fafc; }}
+    .note {{ color: #555; margin-bottom: 16px; }}
+  </style>
+</head>
+<body>
+  <h1>{title}</h1>
… omitted 7 diff line(s) across 1 additional file(s)/section(s)
a/algo_trading_signal\trading_signal\cli.py → b/algo_trading_signal\trading_signal\cli.py
@@ -0,0 +1,118 @@
+from __future__ import annotations
+
+import argparse
+from datetime import datetime
+from pathlib import Path
+
+import pandas as pd
+
+from .backtest import BacktestConfig
+from .data import DEFAULT_NYSE_WATCHLIST, fetch_history, fetch_many, make_demo_ohlcv, read_tickers_file
+from .report import scan_tickers, scan_with_backtests, write_reports
+from .strategy import StrategyConfig
+
+
+def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
+    parser = argparse.ArgumentParser(description="NYSE trend/momentum/support signal scanner")
+    parser.add_argument("--tickers", nargs="*", help="Ticker symbols to scan. Default: built-in NYSE watchlist.")
+    parser.add_argument("--tickers-file", help="Text/CSV file with one ticker per line or comma separated.")
+    parser.add_argument("--period", default="3y", help="yfinance period, e.g. 1y, 3y, 5y, 10y")
+    parser.add_argument("--interval", default="1d", help="yfinance interval. This strategy is designed for 1d.")
+    parser.add_argument("--benchmark", default="SPY", help="Market regime benchmark ticker")
+    parser.add_argument("--output-dir", default="reports", help="Directory for CSV/HTML reports")
+    parser.add_argument("--top", type=int, default=30, help="Number of rows to print")
+    parser.add_argument("--demo", action="store_true", help="Run deterministic offline demo data instead of downloading live data")
+    parser.add_argument("--backtest", action="store_true", help="Also run simple historical backtests per ticker")
+
+    parser.add_argument("--min-price", type=float, default=5.0)
+    parser.add_argument("--min-dollar-volume", type=float, default=20_000_000.0)
+    parser.add_argument("--max-atr-pct", type=float, default=0.08)
+    parser.add_argument("--min-rr", type=float, default=2.01)
+    parser.add_argument("--min-score", type=int, default=70)
+    parser.add_argument("--risk-per-trade", type=float, default=0.005, help="Backtest risk per trade, e.g. 0.005 = 0.5%")
+    parser.add_argument("--commission", type=float, default=0.0, help="Commission per trade side")
+    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Slippage in basis points per entry/exit")
+    return parser.parse_args(argv)
+
+
+def _resolve_tickers(args: argparse.Namespace) -> list[str]:
+    tickers: list[str] = []
+    if args.tickers_file:
+        tickers.extend(read_tickers_file(args.tickers_file))
+    if args.tickers:
+        tickers.extend(symbol.upper() for symbol in args.tickers)
+    if not tickers:
+        tickers = DEFAULT_NYSE_WATCHLIST.copy()
+    return sorted(set(tickers))
+
+
+def main(argv: list[str] | None = None) -> int:
+    args = _parse_args(argv)
+    strategy_cfg = StrategyConfig(
+        min_price=args.min_price,
+        min_avg_dollar_volume=args.min_dollar_volume,
+        max_atr_pct=args.max_atr_pct,
+        min_reward_risk=args.min_rr,
+        min_score=args.min_score,
+    )
+    tickers = _resolve_tickers(args)
+
+    if args.demo:
+        benchmark_df = make_demo_ohlcv(price=400.0)
+        data_by_ticker = {ticker: make_demo_ohlcv(price=80.0 + index * 7) for index, ticker in enumerate(tickers[:5])}
+    else:
+        print(f"Downloading benchmark {args.benchmark}...")
+        benchmark_df = fetch_history(args.benchmark, period=args.period, interval=args.interval)
+        print(f"Downloading {len(tickers)} tickers...")
+        data_by_ticker = fetch_many(tickers, period=args.period, interval=args.interval)
+
+    if not data_by_ticker:
+        raise SystemExit("No ticker data available. Check symbols, internet connection, or use --demo.")
+
+    if args.backtest:
+        backtest_cfg = BacktestConfig(
+            risk_per_trade=args.risk_per_trade,
+            commission_per_trade=args.commission,
+            slippage_bps=args.slippage_bps,
+            strategy=strategy_cfg,
+        )
… omitted 40 diff line(s) across 1 additional file(s)/section(s)
a/algo_trading_signal\run_scanner.py → b/algo_trading_signal\run_scanner.py
@@ -0,0 +1,4 @@
+from trading_signal.cli import main
+
+if __name__ == "__main__":
+    raise SystemExit(main())
+    a/algo_trading_signal\README.md → b/algo_trading_signal\README.md
@@ -2,4 +2,61 @@
 
 Educational Python scanner/backtester for NYSE-style swing trading signals. It focuses on liquid, non-penny stocks with trend, momentum, support, ATR-based stops, and >=2.01R target logic.
 
-This is not financial advice and does not guarantee profit.
+> This is not financial advice. It cannot guarantee profit, a future win rate, or positive EV. Use it for research and paper trading first.
+
+## Install
+
+```bash
+cd "C:/Users/Lam Wing Ming/algo_trading_signal"
+python -m pip install -r requirements.txt
+```
+
+## Offline demo
+
+```bash
+python run_scanner.py --demo --backtest
+```
+
+This creates:
+
+- `reports/morning_watchlist_YYYYMMDD_HHMM.csv`
+- `reports/morning_watchlist_YYYYMMDD_HHMM.html`
+- `reports/backtest_summary.csv`
+
+## Live free-data scan using yfinance
+
+```bash
+python run_scanner.py --tickers JPM XOM CVX CAT DE UNH KO WMT IBM --period 5y --backtest
+```
+
+## Main safety filters
+
+- Minimum price, default `$5`
+- Minimum 20-day average dollar volume, default `$20M`
+- Maximum ATR percentage, default `8%`
+- Broad-market filter using `SPY`
+- Trend: 50/200-day averages, rising 50-day average, price above EMA/SMA support
+- Momentum: 20/60-day return and relative strength versus SPY
+- Support: EMA20/SMA50/recent swing-low proximity
+- Stop: ATR/swing-low based
+- Target: at least `2.01R`
+
+## Output signal meanings
+
+- `BUY`: long setup candidate for paper trading/research.
+- `WATCH`: not enough confirmation yet.
+- `SELL`: exit/avoid long because trend support broke. It is not a short-selling recommendation.
+- `AVOID`: failed safety filters such as price, liquidity, or volatility.
+
+## Backtest pass logic
+
+A ticker's backtest is marked as passing only if:
+
+```text
+win_rate >= 35%
+realized_reward_risk >= 2.01
+expectancy_r > 0
+trades >= 3
+```
+
+Still, passing historical backtests do not guarantee future returns.
a/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\strategy.py → b/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\strategy.py
@@ -298,7 +298,7 @@
         support_score >= 10,
         regime_score >= 7,
         score >= cfg.min_score,
-        reward_risk >= cfg.min_reward_risk,
+        reward_risk + 1e-9 >= cfg.min_reward_risk,
     ]
 
     if all(buy_conditions):
     a/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\data.py → b/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\data.py
@@ -69,6 +69,10 @@
         closes[250:262] = [price + 45, price + 44, price + 43, price + 42.5, price + 42, price + 41.8, price + 42.2, price + 42.9, price + 43.8, price + 45.2, price + 47, price + 49]
         for i in range(262, rows):
             closes[i] = closes[i - 1] + 0.45
+        # End with a fresh pullback/recovery so the scanner demo shows how a BUY
+        # candidate looks today instead of only historical trades.
+        anchor = closes[-7]
+        closes[-6:] = [anchor + 0.2, anchor - 1.3, anchor - 2.0, anchor - 2.3, anchor - 1.6, anchor + 0.6]
     opens = [c - 0.25 for c in closes]
     highs = [c + 1.0 for c in closes]
     lows = [c - 1.0 for c in closes]
     a/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\data.py → b/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\data.py
@@ -72,7 +72,7 @@
         # End with a fresh pullback/recovery so the scanner demo shows how a BUY
         # candidate looks today instead of only historical trades.
         anchor = closes[-7]
-        closes[-6:] = [anchor + 0.2, anchor - 1.3, anchor - 2.0, anchor - 2.3, anchor - 1.6, anchor + 0.6]
+        closes[-6:] = [anchor - 0.2, anchor - 1.7, anchor - 2.4, anchor - 2.7, anchor - 2.0, anchor - 1.2]
     opens = [c - 0.25 for c in closes]
     highs = [c + 1.0 for c in closes]
     lows = [c - 1.0 for c in closes]
     a/algo_trading_signal\tests\test_report.py → b/algo_trading_signal\tests\test_report.py
@@ -0,0 +1,19 @@
+from trading_signal.data import make_demo_ohlcv
+from trading_signal.report import scan_with_backtests
+from trading_signal.strategy import StrategyConfig
+
+
+def test_scan_with_backtests_demotes_unconfirmed_buy_signals_to_watch():
+    data = {"DEMO": make_demo_ohlcv()}
+    benchmark = make_demo_ohlcv(price=400)
+    scan, _ = scan_with_backtests(
+        data,
+        benchmark,
+        StrategyConfig(min_avg_dollar_volume=1_000_000),
+    )
+
+    row = scan.iloc[0]
+    assert row["raw_action"] == "BUY"
+    assert row["backtest_pass"] is False or row["backtest_pass"] == False
+    assert row["action"] == "WATCH"
+    assert "backtest not confirmed" in row["risk_flags"].lower()
+    a/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\report.py → b/C:\Users\Lam Wing Ming\algo_trading_signal\trading_signal\report.py
@@ -49,6 +49,18 @@
             & (scan["expectancy_r"].fillna(0) > 0)
             & (scan["trades"].fillna(0) >= 3)
         )
+        scan["raw_action"] = scan["action"]
+        unconfirmed_buy = (scan["raw_action"] == "BUY") & (~scan["backtest_pass"])
+        if unconfirmed_buy.any():
+            existing_flags = scan.loc[unconfirmed_buy, "risk_flags"].fillna("").astype(str)
+            separator = existing_flags.map(lambda text: "; " if text.strip() else "")
+            scan.loc[unconfirmed_buy, "risk_flags"] = existing_flags + separator + "backtest not confirmed"
+            scan.loc[unconfirmed_buy, "reason"] = scan.loc[unconfirmed_buy, "reason"].astype(str) + " Backtest confirmation failed; demoted to WATCH."
+            scan.loc[unconfirmed_buy, "setup"] = "watchlist-backtest-not-confirmed"
+            scan.loc[unconfirmed_buy, "action"] = "WATCH"
+        action_rank = {"BUY": 0, "WATCH": 1, "SELL": 2, "AVOID": 3}
+        scan["action_rank"] = scan["action"].map(action_rank).fillna(9)
+        scan = scan.sort_values(["action_rank", "score", "reward_risk"], ascending=[True, False, False]).drop(columns=["action_rank"])
     return scan, summary_df
     a/C:\Users\Lam Wing Ming\algo_trading_signal\README.md → b/C:\Users\Lam Wing Ming\algo_trading_signal\README.md
@@ -43,8 +43,8 @@
 
 ## Output signal meanings
 
-- `BUY`: long setup candidate for paper trading/research.
-- `WATCH`: not enough confirmation yet.
+- `BUY`: confirmed long setup candidate when no backtest is requested, or when `--backtest` confirms the historical filters.
+- `WATCH`: not enough confirmation yet; with `--backtest`, raw buy signals are also demoted to WATCH when the backtest does not pass.
 - `SELL`: exit/avoid long because trend support broke. It is not a short-selling recommendation.
 - `AVOID`: failed safety filters such as price, liquidity, or volatility.
 - cd "C:/Users/Lam Wing Ming/algo_trading_signal"
python run_scanner.py --tickers JPM XOM CVX CAT DE UNH KO WMT IBM --period 5y --backtest
