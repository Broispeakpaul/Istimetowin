import numpy as np
import pandas as pd
import pytest

from screener.backtest.event_study import event_study, table_by_reaction, table_filter_steps
from screener.backtest.simulator import make_implied_proxy, simulate_contest
from screener.backtest.tables import compare
from screener.engine import run_day
from tests.synthetic import D1, amc, ledger, manual, market, sessions, stock

D, C = "DDD US Equity", "CCC US Equity"


def drifting(ret_d1, vol_mult, drift_after=0.0, crash_on=None):
    s = sessions()
    rets = np.zeros(len(s))
    i = s.get_loc(D1)
    rets[i + 1:] = drift_after
    shocks = {D1: (ret_d1, vol_mult)}
    if crash_on is not None:
        shocks[pd.Timestamp(crash_on)] = (-0.15, 1.0)
    from screener.data.fixture import make_ohlcv
    return make_ohlcv(s, 100.0, daily_ret=rets, volume=1e6, shocks=shocks)


def test_event_study_forward_returns_and_checks(cfg, members):
    px = {D: drifting(0.12, 3, drift_after=0.01), C: drifting(0.02, 1.2)}
    md = market(cfg, members, px, earnings=[amc(D), amc(C)])
    df = event_study(md, cfg, manual(assume="pass"), "2025-10-01", "2025-10-31", "us")
    r = df.set_index("bbg_ticker")
    assert r.at[D, "quant_pass"] and not r.at[C, "quant_pass"]
    assert r.at[D, "fwd_rel_5"] == pytest.approx(1.01 ** 5 - 1, rel=1e-6)
    assert r.at[D, "reaction_bucket"] == ">= +12%"
    tb = table_by_reaction(df, cfg)
    assert tb.set_index("reaction_bucket").at["ALL", "events"] == 2
    steps = table_filter_steps(df, cfg)
    assert list(steps["events"]) == [2, 1, 1, 1]


def test_event_study_infers_unknown_timing_from_volume(cfg, members):
    md = market(cfg, members, {D: drifting(0.12, 3)}, earnings=[amc(D, report=pd.Timestamp("2025-10-29"), timing="UNKNOWN")])
    df = event_study(md, cfg, manual(), "2025-10-01", "2025-10-31", "us")
    assert df.iloc[0]["day_one"] == D1 and "inferred" in df.iloc[0]["timing_flag"]


def test_contest_simulator_uses_live_engine_and_respects_cash(cfg, members):
    px = {D: drifting(0.12, 3, drift_after=0.002, crash_on="2025-11-10")}
    md = market(cfg, members, px, earnings=[amc(D)])
    man = manual(assume="pass")
    sim = simulate_contest(md, cfg, "2025-10-27", weeks=3, session="us", manual=man)
    buys = sim.trades[sim.trades["side"] == "BUY"]
    t1 = buys[buys["rule"] == "tranche1"].iloc[0]
    assert pd.Timestamp(t1["date"]) == D1
    # same shares the daily engine recommends on day one (no duplicated logic)
    live = run_day(md.as_of(D1), "us", ledger([]), man, cfg, mode="backtest")
    assert t1["shares"] == live.candidates.iloc[0]["shares_tranche1"]
    t2 = buys[buys["rule"] == "tranche2"].iloc[0]
    assert pd.Timestamp(t2["date"]) == pd.Timestamp("2025-11-03")  # day three
    sells = sim.trades[sim.trades["side"] == "SELL"]
    assert pd.Timestamp(sells.iloc[0]["date"]) == pd.Timestamp("2025-11-10") and sells.iloc[0]["rule"] == "relative_stop"
    assert (sim.nav["cash"] >= -1e-6).all()
    assert sim.nav["nav"].iloc[0] == pytest.approx(cfg.backtest.initial_capital, rel=0.01)
    assert set(sim.summary) >= {"return", "benchmark_return", "relative_return", "max_drawdown", "entries"}


def test_idle_cash_earns_benchmark_without_look_ahead(cfg, members):
    from tests.synthetic import flat_bench
    b = flat_bench()
    b.loc[pd.Timestamp("2025-10-29"):, ["open", "high", "low", "close", "adj_close"]] *= 1.10  # +10% on 29 Oct
    md = market(cfg, members, {C: stock(0, 1)}, bench=b)
    sim = simulate_contest(md, cfg, "2025-10-27", weeks=1, session="us", manual=manual())
    nav = sim.nav.set_index("date")["nav"]
    assert nav[pd.Timestamp("2025-10-28")] == pytest.approx(1_000_000)
    assert nav[pd.Timestamp("2025-10-29")] == pytest.approx(1_100_000)
    cfg.backtest.idle_cash = "cash"
    sim = simulate_contest(md, cfg, "2025-10-27", weeks=1, session="us", manual=manual())
    assert sim.nav["nav"].iloc[-1] == pytest.approx(1_000_000)


def test_implied_move_proxy_uses_past_reports_only(cfg, members):
    s = sessions()
    reports = [s[s.get_loc(pd.Timestamp(d))] for d in ("2025-01-28", "2025-04-29", "2025-07-29")]
    shocks = {pd.Timestamp("2025-01-29"): (0.04, 2), pd.Timestamp("2025-04-30"): (-0.06, 2),
              pd.Timestamp("2025-07-30"): (0.20, 2)}
    from screener.data.fixture import make_ohlcv
    px = {D: make_ohlcv(s, 100.0, volume=1e6, shocks=shocks)}
    md = market(cfg, members, px, earnings=[amc(D, report=r) for r in reports])
    proxy = make_implied_proxy(md, cfg)
    assert proxy(D, "2025-07-29") == pytest.approx(0.05)    # mean(|4%|, |-6%|), excludes the +20% report itself
    assert proxy(D, "2025-01-28") is None                   # not enough history


def test_playbook_comparison():
    ours = {"reaction_buckets": pd.DataFrame({"reaction_bucket": ["+8% to +12%"], "mean_rel_20d": [0.025]})}
    ref = {"table1": {"title": "T1", "compare_to": "reaction_buckets", "key_column": "reaction_bucket",
                      "rows": [{"key": "+8% to +12%", "metric": "mean_rel_20d", "value": 0.031}]},
           "table4": {"compare_to": "filter_steps", "rows": []}}
    comp, notes = compare(ours, ref)
    assert comp.iloc[0]["difference"] == pytest.approx(-0.006)
    assert "no playbook values" in notes[0]


def test_backtest_cli_runner_end_to_end(cfg, members, monkeypatch, capsys):
    from screener.backtest import runner
    from screener.cli import build_parser
    px = {D: drifting(0.12, 3, drift_after=0.002), C: drifting(0.02, 1.2)}
    md = market(cfg, members, px, earnings=[amc(D), amc(C)])
    monkeypatch.setattr(runner, "load", lambda *a, **k: md)
    out = cfg.paths.reports_dir / "bt"
    for argv in (["backtest", "events", "--start", "2025-10-01", "--end", "2025-10-31", "--session", "us", "--out", str(out)],
                 ["backtest", "contest", "--start", "2025-10-27", "--session", "us", "--out", str(out)],
                 ["backtest", "contest", "--start", "2025-10-06", "--end", "2025-10-20", "--rolling", "--session", "us",
                  "--out", str(out)]):
        assert runner.main(build_parser().parse_args(argv), cfg) == 0
    text = capsys.readouterr().out
    assert "What each entry filter adds" in text and "Survivorship bias" in text and "no playbook values" in text
    assert (out / "events_20251001_20251031.csv").exists()
    assert (out / "contest_trades_20251027.csv").exists()
