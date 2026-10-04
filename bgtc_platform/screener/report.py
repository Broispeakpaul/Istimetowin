"""Daily report: a self-contained HTML page, a CSV of every signal with its reason, and a data
bundle the dashboard reads."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from . import calendars as cal
from .config import Config
from .engine import DayResult

PCT_COLS = {"day_one_return", "bench_return", "rel_return", "atr_pct", "target_weight", "tranche1_weight",
            "tranche2_weight", "current_weight", "weight", "pos_return", "bench_return", "distance_to_stop",
            "target_weight", "wls_weight", "our_weight", "active_weight", "implied_move", "earnings_hold_cap",
            "cap_weight", "cash_weight"}
MONEY_COLS = {"adv_usd", "market_value_usd", "nav_usd", "cash_usd"}
NUM2_COLS = {"volume_ratio", "beta_adj", "beta_raw", "close_local", "close_usd", "day_one_low", "price_local",
             "price_usd", "avg_cost_usd"}


def stem(res: DayResult) -> str:
    return f"{res.as_of.strftime('%Y-%m-%d')}_{res.session}"


def _fmt(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        if c in PCT_COLS:
            out[c] = out[c].map(lambda v: "" if v is None or pd.isna(v) else f"{v:.1%}")
        elif c in MONEY_COLS:
            out[c] = out[c].map(lambda v: "MISSING" if v is None or pd.isna(v) else f"${v / 1e6:,.1f}m")
        elif c in NUM2_COLS:
            out[c] = out[c].map(lambda v: "" if v is None or pd.isna(v) else f"{v:,.2f}")
        elif pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
        else:
            out[c] = out[c].map(lambda v: v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else v)
    return out.fillna("")


def _table(df: pd.DataFrame, cols: list[str] | None = None, empty: str = "None today.") -> str:
    if df is None or df.empty:
        return f"<p class='muted'>{html.escape(empty)}</p>"
    d = df[[c for c in (cols or df.columns) if c in df.columns]]
    return _fmt(d).to_html(index=False, classes="t", border=0, escape=True)


CANDIDATE_COLS = ["rank", "bbg_ticker", "name", "exchange", "theme_group", "report_date", "timing_used", "day_one",
                  "day_one_return", "bench_return", "rel_return", "volume_ratio", "adv_usd", "atr_pct",
                  "target_weight", "tranche1_weight", "shares_tranche1", "close_local", "currency", "action"]
SIGNAL_COLS = ["status", "bbg_ticker", "name", "exchange", "report_date", "timing", "timing_used", "timing_flag",
               "day_one", "day_one_return", "bench_return", "rel_return", "volume_ratio", "adv_usd", "atr_pct",
               "chk_wls_member", "chk_rel_return", "chk_volume_ratio", "chk_adv_usd", "chk_check1_rev_guidance",
               "check5", "target_weight", "provisional", "preliminary", "source", "reasons"]
HOLDING_COLS = ["bbg_ticker", "name", "role", "theme_group", "quantity", "price_local", "currency", "weight",
                "pos_return", "bench_return", "rel_return", "distance_to_stop", "entry_date", "day_one_date",
                "tranches", "stale_price"]

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--head:#f3f4f6;--warn:#fff4e5;--warnb:#f0a020;--bad:#fdecea;--badb:#d93025;--ok:#e6f4ea;--okb:#188038}
@media (prefers-color-scheme: dark){:root{--bg:#121212;--fg:#e8e8e8;--muted:#9a9a9a;--line:#333;--head:#1e1e1e;--warn:#3a2a0f;--bad:#3b1414;--ok:#11301b}}
body{font-family:Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--fg);margin:16px;max-width:1500px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:26px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.muted{color:var(--muted)} .box{padding:10px 14px;border-radius:6px;margin:8px 0;border-left:5px solid}
.warn{background:var(--warn);border-color:var(--warnb)} .bad{background:var(--bad);border-color:var(--badb)} .ok{background:var(--ok);border-color:var(--okb)}
.wrap{overflow-x:auto} table.t{border-collapse:collapse;font-size:12.5px;width:100%}
table.t th{background:var(--head);text-align:left;position:sticky;top:0} table.t td,table.t th{border-bottom:1px solid var(--line);padding:4px 6px;vertical-align:top}
.kpi{display:inline-block;margin-right:28px} .kpi b{font-size:18px;display:block}
li{margin:3px 0}
table.t td:last-child{min-width:360px} .why{font-size:12.5px} .why li{margin:6px 0}
"""


def _action_list(res: DayResult) -> str:
    items = []
    for _, r in res.candidates.iterrows():
        sh = "" if pd.isna(r["shares_tranche1"]) or r["shares_tranche1"] is None else f"{int(r['shares_tranche1']):,} sh"
        items.append(f"<li><b>{html.escape(r['action'])}</b>: {html.escape(r['bbg_ticker'])} "
                     f"{r['tranche1_weight']:.1%} of NAV ({sh}); target {r['target_weight']:.1%}"
                     f"{' | ' + html.escape(r['funding_note']) if r['funding_note'] else ''}</li>")
    for _, r in res.tranche2.iterrows():
        w = "" if r["tranche2_weight"] is None or pd.isna(r["tranche2_weight"]) else f"{r['tranche2_weight']:.1%}"
        items.append(f"<li><b>Tranche 2 {html.escape(r['action'])}</b>: {html.escape(r['bbg_ticker'])} {w} "
                     f"({int(r['shares'] or 0):,} sh) - {html.escape(r['reason'])}</li>")
    for _, a in res.alerts[res.alerts["severity"].isin(["ACTION", "BREACH"])].iterrows():
        sh = "" if a["shares_to_sell"] is None or pd.isna(a["shares_to_sell"]) else f" (sell {int(a['shares_to_sell']):,} sh)"
        items.append(f"<li><b>{html.escape(a['action'])}</b>: {html.escape(a['bbg_ticker'])}{sh} - "
                     f"{html.escape(a['rule'])}: {html.escape(a['detail'])}</li>")
    for _, a in res.alerts[res.alerts["severity"] == "CONFIRM"].iterrows():
        items.append(f"<li>Confirm: {html.escape(a['bbg_ticker'])} - {html.escape(a['action'])} ({html.escape(a['detail'])})</li>")
    if not items:
        return "<p class='muted'>No orders or alerts for this session.</p>"
    return "<ol>" + "".join(items) + "</ol>"


def _why(res: DayResult) -> str:
    if res.candidates.empty:
        return ""
    items = []
    for _, r in res.candidates.iterrows():
        parts = [r["reasons"], "Sizing: " + r["sizing_note"], "Theme: " + r["theme_note"]]
        if r["funding_note"]:
            parts.append(r["funding_note"])
        items.append(f"<li><b>#{int(r['rank'])} {html.escape(r['bbg_ticker'])}</b>: " + html.escape(" | ".join(parts)) + "</li>")
    return "<ul class='why'>" + "".join(items) + "</ul>"


def render_html(res: DayResult, cfg: Config) -> str:
    p, an = res.portfolio, res.analytics
    title = f"Earnings catalyst screen {res.as_of:%Y-%m-%d} ({res.session})"
    warn_html = ""
    for w in res.warnings:
        cls = "bad" if ("SYNTHETIC" in w or "FALLBACK" in w) else "warn"
        warn_html += f"<div class='box {cls}'>{html.escape(w)}</div>"
    rcls = "bad" if res.regime.derisk else ("warn" if res.regime.derisk is None else "ok")
    beta = "MISSING" if not an or an.portfolio_beta is None else f"{an.portfolio_beta:.2f}"
    kpis = (f"<div class='kpi'><span class='muted'>NAV</span><b>${p.nav_usd:,.0f}</b></div>"
            f"<div class='kpi'><span class='muted'>Cash</span><b>${p.cash_usd:,.0f} ({p.cash_usd / p.nav_usd:.1%})</b></div>"
            f"<div class='kpi'><span class='muted'>Positions</span><b>{len(p.holdings)}</b></div>"
            f"<div class='kpi'><span class='muted'>Adj. beta vs {html.escape(res.benchmark_name)}</span><b>{beta}</b></div>")
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in res.notes + (an.notes if an else []))
    gen = cal.fmt_hkt(res.generated_at_utc)
    body = f"""
<h1>{html.escape(title)}</h1>
<div class='muted'>Generated {gen} | data: {html.escape(res.data_source)} | benchmark: {html.escape(res.benchmark_name)} |
cash basis: {html.escape(p.cash_source)} | mode: {html.escape(res.mode)}</div>
<div class='box warn'><b>Decision support only.</b> Nothing is sent to a broker. Place every order by hand in Bloomberg TMSG
after checking it. Long only, no leverage, no ETFs, WLS members only, max {cfg.competition.max_position_weight:.0%} per position (playbook cap {cfg.sizing.max_weight:.0%}).</div>
{warn_html}
<h2>Session closes (Hong Kong time)</h2><div class='wrap'>{_table(res.session_closes)}</div>
<h2>Market regime</h2><div class='box {rcls}'>{html.escape(res.regime.summary)}</div>
<h2>Today's action list</h2>{_action_list(res)}
<h2>New candidates (ranked by day-one relative return)</h2><div class='wrap'>{_table(res.candidates, CANDIDATE_COLS, "No names passed today.")}</div>{_why(res)}
<h2>Second tranche (day three)</h2><div class='wrap'>{_table(res.tranche2, empty="No positions are on day three today.")}</div>
<h2>Positions and alerts</h2><div class='wrap'>{_table(res.alerts, empty="No alerts.")}</div>
<h2>Portfolio</h2>{kpis}
<div class='wrap'>{_table(p.holdings, HOLDING_COLS, "No holdings in positions.csv.")}</div>
<h3>Beta (weekly, {cfg.beta.years}y, adjusted = {cfg.beta.raw_weight} x raw + {cfg.beta.prior_weight})</h3><div class='wrap'>{_table(an.betas if an else None, empty="n/a")}</div>
<h3>Theme exposure</h3><div class='wrap'>{_table(an.theme_exposure if an else None, empty="n/a")}</div>
<h3>Active weights vs WLS top 10</h3><div class='wrap'>{_table(an.active_weights if an else None, empty="n/a")}</div>
<h2>Staging: heavyweights reporting in the next sessions</h2><div class='wrap'>{_table(res.staging, empty="No heavyweights report in the staging window.")}</div>
<h2>Earnings calendar</h2><div class='wrap'>{_table(res.calendar, empty="No reports scheduled in the window.")}</div>
<h2>All signals with reasons</h2><div class='wrap'>{_table(res.signals, SIGNAL_COLS, "No members had their day one today.")}</div>
<h2>Notes</h2><ul>{notes}</ul>
"""
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Catalyst screen {res.as_of:%Y-%m-%d}</title><style>{CSS}</style></head><body>{body}</body></html>")


def write_report(res: DayResult, cfg: Config, reports_dir: Path | None = None) -> dict[str, Path]:
    reports_dir = Path(reports_dir or cfg.paths.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    s = stem(res)
    paths = {"html": reports_dir / f"{s}.html", "signals_csv": reports_dir / f"{s}_signals.csv",
             "bundle": reports_dir / f"{s}_data"}
    paths["html"].write_text(render_html(res, cfg), encoding="utf-8")
    sig = res.signals.copy()
    if sig.empty:
        sig = pd.DataFrame(columns=SIGNAL_COLS)
    sig.to_csv(paths["signals_csv"], index=False)
    b = paths["bundle"]
    b.mkdir(exist_ok=True)
    tables = {"signals": res.signals, "candidates": res.candidates, "tranche2": res.tranche2, "alerts": res.alerts,
              "holdings": res.portfolio.holdings, "staging": res.staging, "calendar": res.calendar,
              "session_closes": res.session_closes, "regime_history": res.regime_history}
    if res.analytics:
        tables.update({"betas": res.analytics.betas, "themes": res.analytics.theme_exposure,
                       "active_weights": res.analytics.active_weights})
    for name, df in tables.items():
        (df if df is not None else pd.DataFrame()).to_csv(b / f"{name}.csv", index=False)
    meta = {"as_of": res.as_of, "session": res.session, "mode": res.mode, "generated_hkt": cal.fmt_hkt(res.generated_at_utc),
            "data_source": res.data_source, "benchmark": res.benchmark_name, "warnings": res.warnings,
            "notes": res.notes + (res.analytics.notes if res.analytics else []), "regime": res.regime.to_dict(),
            "summary": res.summary}
    (b / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    return paths
