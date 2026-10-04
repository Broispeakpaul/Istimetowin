"""Streamlit dashboard. Start with:  python -m screener dashboard   (or streamlit run screener/dashboard/app.py)

Reads the data bundles written by `python -m screener run`, and can trigger a new run.
Recommendations only: nothing here places orders.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from screener.config import load_config  # noqa: E402
from screener.report import CANDIDATE_COLS, HOLDING_COLS, SIGNAL_COLS, _fmt  # noqa: E402

# Reference palette (dataviz skill): categorical slots 1-2, diverging blue/red, status colours.
BLUE, ORANGE, RED, GRAY = "#2a78d6", "#eb6834", "#e34948", "#8a8984"

st.set_page_config(page_title="BGTC catalyst screen", layout="wide")
cfg = load_config(os.environ.get("BGTC_CONFIG") or None)  # BGTC_CONFIG lets tests point elsewhere


def bundles(root: Path) -> list[Path]:
    found = [p for p in root.rglob("*_data") if (p / "meta.json").exists()]
    return sorted(found, key=lambda p: p.name, reverse=True)


def read(b: Path, name: str) -> pd.DataFrame:
    f = b / f"{name}.csv"
    if not f.exists() or f.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(f)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def show(df: pd.DataFrame, cols=None, empty="Nothing to show."):
    if df.empty:
        st.caption(empty)
        return
    d = df[[c for c in (cols or df.columns) if c in df.columns]]
    st.dataframe(_fmt(d), hide_index=True, width="stretch")


# ------------------------------------------------------------------ sidebar
st.sidebar.header("Report")
all_b = bundles(Path(cfg.paths.reports_dir))
with st.sidebar.expander("Run the screen now", expanded=not all_b):
    sess = st.selectbox("Session", ["us", "asia", "europe", "all"])
    date = st.text_input("Session date (YYYY-MM-DD, blank = latest closed)", "")
    src = st.selectbox("Data source", ["yahoo", "bloomberg"], index=0 if cfg.data_source != "bloomberg" else 1)
    if st.button("Run"):
        from screener import calendars as cal
        from screener.pipeline import default_date, run_session
        from screener.report import write_report
        cfg.data_source = src
        with st.spinner("Fetching data and applying the rules..."):
            try:
                d = cal.parse_date(date) if date.strip() else default_date(sess)
                res = run_session(cfg, d, sess)
                write_report(res, cfg)
                st.success(f"Report written for {d:%Y-%m-%d} ({sess})")
                all_b = bundles(Path(cfg.paths.reports_dir))
            except Exception as exc:  # show the problem instead of a stack trace
                st.error(f"Run failed: {exc}")

if not all_b:
    st.title("BGTC 2026 earnings catalyst screen")
    st.info("No reports yet. Run `python -m screener run --session us` (or `python -m screener demo` for synthetic data), "
            "or use 'Run the screen now' in the sidebar.")
    st.stop()

choice = st.sidebar.selectbox("Report to view", all_b, format_func=lambda p: str(p.relative_to(cfg.paths.reports_dir)))
meta = json.loads((choice / "meta.json").read_text(encoding="utf-8"))
st.sidebar.caption(f"Generated {meta['generated_hkt']}")

# ------------------------------------------------------------------ header
st.title(f"Earnings catalyst screen: {str(meta['as_of'])[:10]} ({meta['session']})")
st.caption(f"Data: {meta['data_source']} | benchmark: {meta['benchmark']} | mode: {meta['mode']} | "
           "decision support only: place orders by hand in Bloomberg TMSG")
for w in meta["warnings"]:
    (st.error if ("SYNTHETIC" in w or "FALLBACK" in w) else st.warning)(w)

cand, sig, t2 = read(choice, "candidates"), read(choice, "signals"), read(choice, "tranche2")
alerts, hold = read(choice, "alerts"), read(choice, "holdings")
cal_df, staging = read(choice, "calendar"), read(choice, "staging")
betas, themes, active = read(choice, "betas"), read(choice, "themes"), read(choice, "active_weights")
hist = read(choice, "regime_history")

tabs = st.tabs(["Today's candidates", "Earnings calendar", "Positions and alerts", "Portfolio risk", "Market regime"])

# ------------------------------------------------------------------ 1. candidates
with tabs[0]:
    reg = meta["regime"]
    box = st.error if reg["derisk"] is True else (st.warning if reg["derisk"] is None else st.success)
    box(reg["summary"])
    st.subheader("Ranked candidates")
    show(cand, CANDIDATE_COLS, "No names passed today.")
    for _, r in cand.iterrows():
        with st.expander(f"#{int(r['rank'])} {r['bbg_ticker']}: why it passed and how it was sized"):
            st.write(r["reasons"])
            st.write("Sizing: " + str(r["sizing_note"]))
            st.write("Theme: " + str(r["theme_note"]))
            if isinstance(r.get("funding_note"), str) and r["funding_note"]:
                st.warning(r["funding_note"])
    st.subheader("Second tranche (day three)")
    show(t2, empty="No positions are on day three today.")
    with st.expander(f"All {len(sig)} day-one events with pass/fail reasons"):
        show(sig, SIGNAL_COLS, "No members had their day one today.")

# ------------------------------------------------------------------ 2. calendar
with tabs[1]:
    st.subheader(f"Reports in the next {cfg.calendar_view.horizon_sessions} trading days")
    show(cal_df, empty="No reports scheduled in the window.")
    st.subheader(f"Staging: top {cfg.staging.heavyweight_top_n} heavyweights reporting in the next "
                 f"{cfg.staging.horizon_sessions} sessions")
    show(staging, empty="No heavyweights report in the staging window (or wls_weight is MISSING).")
    st.caption("Cash staging is never recommended by default; idle money tilts toward these names.")

# ------------------------------------------------------------------ 3. positions
with tabs[2]:
    st.subheader("Alerts")
    if not alerts.empty:
        order = {"BREACH": 0, "ACTION": 1, "CONFIRM": 2, "INFO": 3}
        alerts = alerts.sort_values("severity", key=lambda s: s.map(order))
    show(alerts, empty="No alerts.")
    st.subheader("Holdings")
    show(hold, HOLDING_COLS, "No holdings in data/positions.csv.")
    if not hold.empty and hold["distance_to_stop"].notna().any():
        d = hold.dropna(subset=["distance_to_stop"]).copy()
        d["distance_pp"] = d["distance_to_stop"] * 100
        chart = alt.Chart(d).mark_bar(cornerRadiusEnd=4, color=BLUE).encode(
            x=alt.X("distance_pp:Q", title="Distance to relative stop (percentage points)"),
            y=alt.Y("bbg_ticker:N", sort="x", title=None),
            tooltip=["bbg_ticker", alt.Tooltip("distance_pp:Q", format=".1f", title="pp to stop"),
                     alt.Tooltip("rel_return:Q", format=".1%", title="vs WLS since entry")])
        st.altair_chart(chart, width="stretch")
        st.caption("0 = at the stop (8% behind WLS since entry). Smaller bars are closer to an exit.")

# ------------------------------------------------------------------ 4. risk
with tabs[3]:
    s = meta["summary"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("NAV", f"${s['nav_usd']:,.0f}")
    cash_w = f" ({s['cash_weight']:.1%})" if s["cash_weight"] is not None else ""
    c2.metric("Cash", f"${s['cash_usd']:,.0f}{cash_w}")
    c3.metric("Positions", s["positions"])
    c4.metric(f"Adjusted beta vs {meta['benchmark']}", "MISSING" if s["portfolio_beta"] is None else f"{s['portfolio_beta']:.2f}")
    st.caption(s["cash_source"])
    left, right = st.columns(2)
    with left:
        st.subheader("Theme exposure")
        if themes.empty:
            st.caption("No holdings.")
        else:
            bars = alt.Chart(themes).mark_bar(cornerRadiusEnd=4, color=BLUE).encode(
                x=alt.X("weight:Q", axis=alt.Axis(format="%"), title="Weight"),
                y=alt.Y("theme_group:N", sort="-x", title=None),
                tooltip=["theme_group", alt.Tooltip("weight:Q", format=".1%"), "names", "tickers"])
            cap = alt.Chart(pd.DataFrame({"cap": [cfg.themes.max_weight_per_theme]})).mark_rule(
                color=GRAY, strokeDash=[4, 3]).encode(x="cap:Q")
            st.altair_chart(bars + cap, width="stretch")
            st.caption(f"Dashed line: {cfg.themes.max_weight_per_theme:.0%} theme cap "
                       f"(max {cfg.themes.max_names_per_theme} names; semis + software = one theme)")
    with right:
        st.subheader("Active weights vs WLS top 10")
        if active.empty:
            st.caption("MISSING wls_weight in data/wls_members.csv")
        else:
            active["sign"] = active["active_weight"].map(lambda v: "overweight" if v >= 0 else "underweight")
            ch = alt.Chart(active).mark_bar(cornerRadiusEnd=4).encode(
                x=alt.X("active_weight:Q", axis=alt.Axis(format="%"), title="Our weight minus WLS weight"),
                y=alt.Y("name:N", sort="-x", title=None),
                color=alt.Color("sign:N", scale=alt.Scale(domain=["overweight", "underweight"], range=[BLUE, RED]),
                                legend=alt.Legend(title=None, orient="bottom")),
                tooltip=["bbg_ticker", alt.Tooltip("our_weight:Q", format=".2%"),
                         alt.Tooltip("wls_weight:Q", format=".2%"), alt.Tooltip("active_weight:Q", format="+.2%")])
            st.altair_chart(ch, width="stretch")
    st.subheader("Position betas")
    show(betas, empty="No holdings.")
    for n in meta["notes"]:
        st.caption(n)

# ------------------------------------------------------------------ 5. regime
with tabs[4]:
    reg = meta["regime"]
    (st.error if reg["derisk"] is True else (st.warning if reg["derisk"] is None else st.success))(reg["summary"])
    if hist.empty:
        st.caption("No benchmark history in this report.")
    else:
        hist["date"] = pd.to_datetime(hist["date"])
        long = hist.melt("date", ["benchmark", "sma"], var_name="series", value_name="level").dropna()
        names = [meta["benchmark"], f"{cfg.regime.sma_window}-day average"]
        long["series"] = long["series"].map({"benchmark": names[0], "sma": names[1]})
        colors = alt.Scale(domain=names, range=[BLUE, ORANGE])  # colour follows the entity, not alphabetical order
        hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
        base = alt.Chart(long).encode(x=alt.X("date:T", title=None))
        lines = base.mark_line(strokeWidth=2).encode(
            y=alt.Y("level:Q", scale=alt.Scale(zero=False), title="Level"),
            color=alt.Color("series:N", scale=colors, legend=alt.Legend(title=None, orient="top")))
        pts = base.mark_point(size=60, filled=True).encode(
            y="level:Q", color=alt.Color("series:N", scale=colors, legend=None),
            opacity=alt.condition(hover, alt.value(1), alt.value(0)),
            tooltip=[alt.Tooltip("date:T"), "series", alt.Tooltip("level:Q", format=",.2f")]).add_params(hover)
        rule = base.mark_rule(color=GRAY).encode(opacity=alt.condition(hover, alt.value(0.6), alt.value(0))).transform_filter(hover)
        st.subheader("Benchmark vs moving average")
        st.altair_chart(lines + pts + rule, width="stretch")
        v = hist.dropna(subset=["vix"])
        if not v.empty:
            st.subheader("VIX")
            vline = alt.Chart(v).mark_line(strokeWidth=2, color=BLUE).encode(
                x=alt.X("date:T", title=None), y=alt.Y("vix:Q", title="VIX"),
                tooltip=[alt.Tooltip("date:T"), alt.Tooltip("vix:Q", format=".1f")])
            thr = alt.Chart(pd.DataFrame({"t": [cfg.regime.vix_threshold]})).mark_rule(color=RED, strokeDash=[4, 3]).encode(y="t:Q")
            st.altair_chart(vline + thr, width="stretch")
            st.caption(f"Dashed line: VIX {cfg.regime.vix_threshold:g}. De-risk needs BOTH the benchmark below its "
                       f"{cfg.regime.sma_window}-day average AND VIX above the line.")
