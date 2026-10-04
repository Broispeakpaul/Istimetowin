import pandas as pd
import pytest

from screener.engine import ACTION_BUY, ACTION_BUY_IF_C1, run_day
from tests.synthetic import D1, amc, flat_bench, flat_vix, ledger, manual, market, stock

A, B, C, D, E = "AAA US Equity", "BBB US Equity", "CCC US Equity", "DDD US Equity", "EEE US Equity"
ALL_C1 = [{"bbg_ticker": t, "report_date": "2025-10-29", "check1_revenue_guidance": "PASS"} for t in (A, B, C, D, E)]


def day(cfg, members, px, events, rows=(), man=None, as_of=D1, **kw):
    md = market(cfg, members, px, earnings=events, man=man, **{k: v for k, v in kw.items() if k in ("bench", "vix")})
    return run_day(md.as_of(as_of), kw.get("session", "us"), ledger(list(rows)), man or manual(), cfg,
                   now_utc=kw.get("now_utc"), mode=kw.get("mode", "live"))


def test_candidates_ranked_by_relative_return_and_sized(cfg, members):
    px = {C: stock(0.09, 3), D: stock(0.15, 3), A: stock(0.11, 3, range_pct=0.05)}
    res = day(cfg, members, px, [amc(C), amc(D), amc(A)], man=manual(checks=ALL_C1))
    cand = res.candidates
    assert list(cand["bbg_ticker"]) == [D, A, C]
    assert list(cand["rank"]) == [1, 2, 3]
    assert (cand["action"] == ACTION_BUY).all()
    r = cand.set_index("bbg_ticker")
    assert r.at[D, "tranche1_weight"] == pytest.approx(r.at[D, "target_weight"] / 2)
    assert r.at[D, "shares_tranche1"] == int(r.at[D, "tranche1_weight"] * res.portfolio.nav_usd // r.at[D, "close_usd"])
    assert r.at[A, "target_weight"] < r.at[D, "target_weight"]  # wider range -> higher ATR -> smaller size
    assert res.signals["reasons"].str.len().gt(0).all()


def test_failed_names_still_reported_with_reasons(cfg, members):
    res = day(cfg, members, {C: stock(0.05, 3)}, [amc(C)])
    assert res.candidates.empty
    row = res.signals.iloc[0]
    assert row["status"] == "REJECTED" and "FAIL rel_return" in row["reasons"]


def test_theme_cap_with_existing_holdings(cfg, members):
    rows = [{"date": "2025-10-01", "bbg_ticker": A, "side": "BUY", "quantity": 1000, "price_local": 100},
            {"date": "2025-10-01", "bbg_ticker": B, "side": "BUY", "quantity": 1000, "price_local": 100}]
    px = {A: stock(0, 1), B: stock(0, 1), E: stock(0.12, 3)}
    res = day(cfg, members, px, [amc(E)], rows=rows, man=manual(checks=ALL_C1))
    s = res.signals.set_index("bbg_ticker").loc[E]
    assert s["status"] == "REJECTED (THEME CAP)" and "max 2" in s["reasons"]


def test_funding_needed_when_cash_short(cfg, members):
    rows = [{"date": "2025-10-01", "bbg_ticker": C, "side": "BUY", "quantity": 9800, "price_local": 100, "role": "staging"}]
    res = day(cfg, members, {C: stock(0, 1), D: stock(0.12, 3)}, [amc(D)], rows=rows, man=manual(checks=ALL_C1))
    note = res.candidates.iloc[0]["funding_note"]
    assert note.startswith("FUNDING NEEDED") and C in note and "No leverage" in note


def test_check1_pending_changes_action(cfg, members):
    res = day(cfg, members, {D: stock(0.12, 3)}, [amc(D)])
    assert res.candidates.iloc[0]["action"] == ACTION_BUY_IF_C1


def test_derisk_blocks_entries(cfg, members):
    b = flat_bench()
    b.loc["2025-10-01":, ["open", "high", "low", "close", "adj_close"]] *= 0.9
    res = day(cfg, members, {D: stock(0.12, 3)}, [amc(D)], bench=b, vix=flat_vix(30), man=manual(checks=ALL_C1))
    assert res.regime.derisk is True
    assert res.candidates.empty and res.signals.iloc[0]["status"] == "BLOCKED (DE-RISK)"


def test_second_tranche_on_day_three(cfg, members):
    d3 = pd.Timestamp("2025-11-03")
    rows = [{"date": "2025-10-30", "bbg_ticker": D, "side": "BUY", "quantity": 500, "price_local": 112,
             "tranche": 1, "day_one_date": "2025-10-30"}]
    man = manual(checks=[{"bbg_ticker": D, "report_date": "2025-10-29", "check1_revenue_guidance": "PASS",
                          "check5_eps_revision": "PASS"}])
    res = day(cfg, members, {D: stock(0.12, 3)}, [amc(D)], rows=rows, man=man, as_of=d3)
    t2 = res.tranche2.iloc[0]
    assert t2["action"] == "BUY" and t2["day_one"] == D1 and t2["shares"] > 0
    # not on day two
    res2 = day(cfg, members, {D: stock(0.12, 3)}, [amc(D)], rows=rows, man=man, as_of=pd.Timestamp("2025-10-31"))
    assert res2.tranche2.empty


def test_second_tranche_skipped_below_day_one_low(cfg, members):
    d3 = pd.Timestamp("2025-11-03")
    rows = [{"date": "2025-10-30", "bbg_ticker": D, "side": "BUY", "quantity": 500, "price_local": 112,
             "tranche": 1, "day_one_date": "2025-10-30"}]
    px = {D: stock(0.12, 3, extra_shocks={d3: (-0.08, 1.0)})}
    res = day(cfg, members, px, [amc(D)], rows=rows, as_of=d3)
    assert res.tranche2.iloc[0]["action"] == "SKIP"


def test_no_look_ahead(cfg, members):
    """Changing every bar after day one must not change day one's output."""
    base = stock(0.12, 3)
    future = base.copy()
    future.loc[future.index > D1, ["open", "high", "low", "close", "adj_close"]] *= 0.5
    future.loc[future.index > D1, "volume"] *= 50
    b1, b2 = flat_bench(), flat_bench()
    b2.loc[b2.index > D1, "adj_close"] *= 3
    r1 = day(cfg, members, {D: base}, [amc(D)], bench=b1)
    r2 = day(cfg, members, {D: future}, [amc(D)], bench=b2)
    cols = ["rel_return", "volume_ratio", "adv_usd", "atr_pct", "target_weight", "shares_tranche1", "status"]
    pd.testing.assert_frame_equal(r1.signals[cols], r2.signals[cols])


def test_preliminary_when_run_before_close(cfg, members):
    before_close = pd.Timestamp("2025-10-30 19:00", tz="UTC")  # 15:00 New York
    res = day(cfg, members, {D: stock(0.12, 3)}, [amc(D)], now_utc=before_close)
    assert "PRELIMINARY" in res.candidates.iloc[0]["action"]
    assert "OPEN" in res.session_closes.iloc[0]["status"]


def test_session_filter(cfg, members):
    t = "7203 JT Equity"
    from tests.synthetic import sessions
    fx = {"JPY": pd.Series(0.0067, index=sessions("XTKS"))}
    px = {t: stock(0.12, 3, price=3000, volume=5e6, code="XTKS"), D: stock(0.12, 3)}
    md = market(cfg, members, px, earnings=[amc(t), amc(D)], fx=fx)
    us = run_day(md.as_of(D1), "us", ledger([]), manual(), cfg)
    asia = run_day(md.as_of(D1), "asia", ledger([]), manual(), cfg)
    assert set(us.signals["bbg_ticker"]) == {D}
    assert set(asia.signals["bbg_ticker"]) == {t}
    both = run_day(md.as_of(D1), "all", ledger([]), manual(), cfg)
    assert set(both.signals["bbg_ticker"]) == {t, D}
