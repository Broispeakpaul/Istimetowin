"""Command line entry point.

    python -m catalyst_strategy demo                       # offline, synthetic data
    python -m catalyst_strategy scan                       # week 1: find momentum names to research
    python -m catalyst_strategy plan --catalysts my.csv    # weekly rebalance / daily check
    python -m catalyst_strategy plan --catalysts my.csv --mode stage   # orders into TWS, untransmitted
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from rich.console import Console
from rich.table import Table

from .catalysts import load_catalysts
from .config import StrategyConfig
from .dashboard import export, render

console = Console()


def _config(args) -> StrategyConfig:
    cfg = StrategyConfig.from_json(args.config) if args.config else StrategyConfig()
    if getattr(args, "start_date", None):
        cfg.competition_start = date.fromisoformat(args.start_date)
    if getattr(args, "options", False):
        cfg.options_enabled = True
    if getattr(args, "trailing", False):
        cfg.use_trailing_stop = True
    return cfg


def _gateway(args):
    from .ib_gateway import IBGateway

    gw = IBGateway(args.host, args.port, args.client_id, args.account,
                   market_data_type=3 if args.delayed else 1,
                   volume_multiplier=args.volume_multiplier)
    readonly = getattr(args, "mode", "preview") == "preview"
    try:
        gw.connect(readonly=readonly)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Could not connect to TWS/IB Gateway at {args.host}:{args.port}: {exc}[/red]\n"
                      "Is TWS running with API enabled (Global Configuration > API > Settings)?")
        sys.exit(2)
    return gw


def cmd_demo(args) -> None:
    from .demo import run_demo

    cfg = _config(args)
    cfg.options_enabled = True
    plan = run_demo(cfg)
    render(plan, console, extra_notes=["DEMO: synthetic prices, not market data."])
    if args.export:
        console.print(f"Exported to {export(plan, args.export)}")


def cmd_scan(args) -> None:
    from .screener import screen_universe

    cfg = _config(args)
    catalysts = load_catalysts(args.catalysts) if args.catalysts else {}
    with _gateway(args) as gw:
        universe = gw.scan_universe(cfg, rows=args.rows)
        console.print(f"IB scanners returned {len(universe)} symbols; downloading daily bars...")
        bars = gw.many_bars(universe + list(catalysts), cache_dir=args.cache)
        bench = gw.daily_bars(cfg.rs_benchmark)
    results = screen_universe(bars, bench, catalysts, cfg, date.today(), cap_in_range=set(universe))

    t = Table(title="Momentum names that pass size / liquidity / RS — research their catalysts")
    for col in ("Symbol", "Last", "RS pct", "3m", "6m", "ADV", "Catalyst on file", "Remaining fails"):
        t.add_column(col)
    rows = [r for r in results if all(f in ("no catalyst on file",) or f.startswith("catalyst") for f in r.fails)]
    rows.sort(key=lambda r: r.metrics.get("rs_pct", 0), reverse=True)
    for r in rows[: args.top]:
        m = r.metrics
        t.add_row(r.symbol, f"{m['last']:.2f}", f"{m['rs_pct']:.0%}", f"{m['ret_3m']:.1%}", f"{m['ret_6m']:.1%}",
                  f"${m['adv_usd'] / 1e6:,.0f}M", r.catalyst.type.value if r.catalyst else "-",
                  "; ".join(r.fails) or "PASS")
    console.print(t)
    console.print("Next: add the ones with real catalysts (13D, beat+raise, cash deal, spin-off) to your "
                  "catalysts CSV, then run `plan`.")


def cmd_plan(args) -> None:
    from .planner import build_plan

    cfg = _config(args)
    catalysts = load_catalysts(args.catalysts)
    today = date.today()
    with _gateway(args) as gw:
        acct = gw.account_state()
        universe = [] if args.no_scan else gw.scan_universe(cfg, rows=args.rows)
        symbols = universe + list(catalysts) + list(acct.holdings)
        console.print(f"Account {acct.account}: equity ${acct.equity:,.0f}. "
                      f"Downloading bars for {len(set(symbols))} symbols...")
        bars = gw.many_bars(symbols, cache_dir=args.cache)
        regime_bars = gw.daily_bars(cfg.regime_symbol)
        bench = gw.daily_bars(cfg.rs_benchmark)
        caps = {s: c for s in catalysts if not catalysts[s].market_cap and s not in universe
                for c in [gw.market_cap(s)] if c}

        plan = build_plan(today, cfg, acct.equity, acct.cash, acct.holdings, bars, regime_bars, bench,
                          catalysts, caps, cap_in_range=set(universe))
        notes = [f"Non-stock / short positions not managed: {', '.join(acct.other_positions)}"] \
            if acct.other_positions else []
        if len(universe) < 30 and not args.no_scan:
            notes.append("Small RS universe: percentile ranks are rough. Increase --rows.")

        exec_log = None
        if args.mode != "preview" and plan.orders:
            if args.mode == "live" and not args.yes:
                where = "a LIVE trading port" if gw.is_live_port else "a paper port"
                render(plan, console, notes)
                answer = console.input(f"[bold red]Transmit {len(plan.orders)} orders to {acct.account} "
                                       f"on {where}? Type YES: [/bold red]")
                if answer.strip() != "YES":
                    console.print("Aborted, nothing sent.")
                    return
            exec_log = gw.execute(plan.orders, args.mode)
        render(plan, console, notes, exec_log)
    if args.export:
        console.print(f"Exported to {export(plan, args.export)}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="catalyst_strategy", description="Event-driven momentum on IBKR")
    p.add_argument("--config", help="JSON file overriding StrategyConfig fields")
    p.add_argument("--start-date", help="competition start date YYYY-MM-DD (enables week phases)")
    p.add_argument("--options", action="store_true", help="allow long calls on catalysts with use_options")
    p.add_argument("--trailing", action="store_true", help="use trailing stops instead of fixed stops")
    p.add_argument("--export", metavar="DIR", help="write plan JSON/CSV to DIR")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def ib_args(sp):
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=7497, help="7497 TWS paper, 7496 TWS live, 4002/4001 Gateway")
        sp.add_argument("--client-id", type=int, default=17)
        sp.add_argument("--account", default="", help="IB account id (default: first managed account)")
        sp.add_argument("--delayed", action="store_true", help="use delayed market data")
        sp.add_argument("--volume-multiplier", type=float, default=1.0,
                        help="set to 100 if your TWS reports daily volume in round lots")
        sp.add_argument("--rows", type=int, default=50, help="rows per IB scanner (max 50)")
        sp.add_argument("--cache", default=".bar_cache", help="daily bar cache directory ('' disables)")

    sp = sub.add_parser("demo", help="render a plan from synthetic data (no IB connection)")
    sp.set_defaults(func=cmd_demo)

    sp = sub.add_parser("scan", help="scan IB for momentum names that need catalyst research")
    ib_args(sp)
    sp.add_argument("--catalysts", help="catalyst CSV (optional)")
    sp.add_argument("--top", type=int, default=40)
    sp.set_defaults(func=cmd_scan, mode="preview")

    sp = sub.add_parser("plan", help="build today's plan and optionally send orders to IB")
    ib_args(sp)
    sp.add_argument("--catalysts", required=True, help="catalyst CSV")
    sp.add_argument("--mode", choices=["preview", "stage", "live"], default="preview",
                    help="preview: display only; stage: orders in TWS untransmitted; live: transmit")
    sp.add_argument("--yes", action="store_true", help="skip the live-mode confirmation prompt")
    sp.add_argument("--no-scan", action="store_true", help="skip IB scanners; rank only catalyst names")
    sp.set_defaults(func=cmd_plan)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
