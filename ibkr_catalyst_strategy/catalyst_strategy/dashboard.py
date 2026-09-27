"""Terminal dashboard for a Plan, plus JSON/CSV export."""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .planner import Plan


def _pct(x: float | None, digits: int = 1) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{digits}%}"


def _money(x: float | None) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"${x:,.2f}"


def _signed(x: float) -> Text:
    return Text(_pct(x), style="green" if x >= 0 else "red")


def header(plan: Plan) -> Panel:
    r = plan.regime
    phase = plan.phase.value + (f" · week {plan.week}" if plan.week is not None else "")
    regime_style = "bold green" if r.risk_on else "bold red"
    lines = Text()
    lines.append(f"{plan.today}   ", style="bold")
    lines.append(f"Phase: {phase}   ")
    lines.append(f"{r.symbol} {r.price:,.2f} vs {r.ma_window}DMA {r.ma:,.2f} ")
    lines.append(r.label, style=regime_style)
    lines.append("\n")
    invested_pct = plan.invested / plan.equity if plan.equity else 0
    lines.append(f"Equity {_money(plan.equity)}   Cash {_money(plan.cash)}   "
                 f"Invested {_pct(invested_pct)} (cap {_pct(plan.max_invested / plan.equity if plan.equity else 0)}, "
                 f"cash target {_pct(r.cash_target, 0)})")
    return Panel(lines, title="Event-Driven Momentum · IBKR", border_style="cyan")


def positions_table(plan: Plan) -> Table:
    t = Table(title="Open positions", box=box.SIMPLE_HEAVY, expand=True)
    for col in ("Symbol", "Shares", "Avg cost", "Last", "P&L", "Weight", "Stop", "Action", "Why"):
        t.add_column(col, justify="right" if col not in ("Symbol", "Action", "Why") else "left")
    for p in plan.positions:
        style = "bold red" if p.action == "EXIT" else ("yellow" if p.action != "HOLD" else "")
        t.add_row(p.symbol, f"{p.shares:g}", _money(p.avg_cost), _money(p.last), _signed(p.pnl_pct),
                  _pct(p.position_pct), _money(p.stop), Text(p.action, style=style),
                  "; ".join(p.reasons) or "trend + catalyst intact")
    if not plan.positions:
        t.add_row("-", "", "", "", "", "", "", "", "no stock positions")
    return t


CANDIDATE_COLUMNS = (  # (header, justify, shown on narrow terminals)
    ("#", "right", False), ("Symbol", "left", True), ("Catalyst", "left", True), ("Conv", "right", False),
    ("Score", "right", True), ("Last", "right", False), ("RS pct", "right", True), ("ADV", "right", False),
    ("Trigger", "left", False), ("Entry", "right", True), ("Stop", "right", True), ("Shares", "right", True),
    ("Risk", "right", False), ("Weight", "right", False), ("Status / detail", "left", True),
)


def candidates_table(plan: Plan, limit: int = 25, compact: bool = False) -> Table:
    t = Table(title="Catalyst candidates (ranked)", box=box.SIMPLE_HEAVY, expand=True)
    shown = [c for c in CANDIDATE_COLUMNS if c[2] or not compact]
    for col, just, _ in shown:
        t.add_column(col, justify=just, overflow="fold")
    for i, c in enumerate(plan.candidates[:limit], 1):
        status_style = "bold green" if c.status.startswith("BUY") else (
            "dim" if c.status.startswith(("SCREENED", "SKIP", "NO ")) else "yellow")
        detail = c.status + (f" — {c.detail}" if c.detail else "")
        cells = {
            "#": str(i), "Symbol": c.symbol, "Catalyst": c.catalyst, "Conv": str(c.conviction),
            "Score": f"{c.score:.2f}", "Last": _money(c.last), "RS pct": _pct(c.rs_pct, 0),
            "ADV": f"${c.adv_usd / 1e6:,.0f}M", "Trigger": c.trigger or "-", "Entry": _money(c.entry),
            "Stop": _money(c.stop), "Shares": str(c.shares or "-"), "Risk": _pct(c.risk_pct, 2),
            "Weight": _pct(c.position_pct), "Status / detail": Text(detail, style=status_style),
        }
        t.add_row(*(cells[col] for col, _, _ in shown))
    return t


def orders_table(plan: Plan) -> Table:
    t = Table(title="Proposed orders", box=box.SIMPLE_HEAVY, expand=True)
    for col in ("Symbol", "Side", "Qty", "Type", "Limit", "Stop / trail", "Protective child", "Reason"):
        t.add_column(col)
    for o in plan.orders:
        side = Text(o.action, style="green" if o.action == "BUY" else "red")
        if o.sec_type == "OPT":
            qty = f"calls ≤{_money(o.option.budget)}"
            stop = f"K≈{o.option.target_strike} exp≥{o.option.min_expiry}"
        else:
            qty = f"{o.quantity:g}"
            stop = _money(o.stop_price) if o.stop_price else (_pct(o.trail_pct) + " trail" if o.trail_pct else "-")
        child = (f"STP {_money(o.attach_stop)}" if o.attach_stop
                 else f"TRAIL {_pct(o.attach_trail_pct)}" if o.attach_trail_pct else "-")
        t.add_row(o.symbol, side, qty, o.order_type, _money(o.limit_price) if o.limit_price else "-",
                  stop, child, o.reason)
    if not plan.orders:
        t.add_row("-", "", "", "", "", "", "", "nothing to do today")
    return t


def render(plan: Plan, console: Console | None = None, extra_notes: list[str] | None = None,
           exec_log: list[str] | None = None) -> None:
    console = console or Console()
    compact = console.width < 140
    parts = [header(plan), positions_table(plan), candidates_table(plan, compact=compact), orders_table(plan)]
    notes = list(plan.notes) + list(extra_notes or [])
    if notes:
        parts.append(Panel("\n".join(f"• {n}" for n in notes), title="Notes", border_style="yellow"))
    if exec_log:
        parts.append(Panel("\n".join(exec_log), title="IB execution", border_style="magenta"))
    console.print(Group(*parts))


def export(plan: Plan, out_dir: str | Path) -> Path:
    """Write plan JSON plus orders/candidates CSVs; returns the JSON path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = plan.today.isoformat()
    data = asdict(plan)
    json_path = out / f"plan_{stamp}.json"
    json_path.write_text(json.dumps(data, indent=2, default=str))
    for name, rows in (("orders", data["orders"]), ("candidates", data["candidates"])):
        if rows:
            with open(out / f"{name}_{stamp}.csv", "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows({k: (json.dumps(v, default=str) if isinstance(v, dict) else v)
                             for k, v in r.items()} for r in rows)
    return json_path
