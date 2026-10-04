import pandas as pd
import pytest

from screener.portfolio import build_portfolio, portfolio_analytics
from screener.rules.exits import evaluate_exits
from screener.rules.implied import ImpliedMoveBook
from tests.synthetic import D1, amc, flat_bench, ledger, manual, market, sessions, stock

A, C = "AAA US Equity", "CCC US Equity"
ENTRY = "2025-10-01"


def fill(t, qty, price, date=ENTRY, side="BUY", role="catalyst", **kw):
    return {"date": date, "bbg_ticker": t, "side": side, "quantity": qty, "price_local": price, "role": role, **kw}


def alerts_for(cfg, members, px, rows, events=None, man=None, as_of=D1):
    md = market(cfg, members, px, earnings=events or [], man=man)
    v = md.as_of(as_of)
    st = build_portfolio(ledger(rows), v, members, cfg)
    return st, evaluate_exits(v, st.holdings, members, cfg, ImpliedMoveBook(man or manual(), md.implied_moves))


def test_holdings_cost_cash_and_weights(cfg, members):
    px = {A: stock(0.0, 1.0, price=100.0)}
    rows = [fill(A, 1000, 100.0), fill(A, 1000, 110.0, date="2025-10-02"), fill(A, 500, 120.0, date="2025-10-03", side="SELL")]
    md = market(cfg, members, px)
    st = build_portfolio(ledger(rows), md.as_of(D1), members, cfg)
    h = st.holdings.set_index("bbg_ticker").loc[A]
    assert h["quantity"] == 1500 and h["avg_cost_usd"] == pytest.approx(105.0)
    assert st.cash_usd == pytest.approx(1_000_000 - 210_000 + 60_000)
    assert st.nav_usd == pytest.approx(st.cash_usd + 1500 * 100.0)
    assert h["weight"] == pytest.approx(150_000 / st.nav_usd)


def test_account_snapshot_overrides_cash(cfg, members):
    px = {A: stock(0.0, 1.0)}
    md = market(cfg, members, px)
    led = ledger([fill(A, 1000, 100.0)], account=[{"date": "2025-10-15", "cash_usd": 500_000, "nav_usd": ""}])
    st = build_portfolio(led, md.as_of(D1), members, cfg)
    assert st.cash_usd == pytest.approx(500_000) and "account.csv" in st.cash_source


def test_pence_listing_ledger_fx_is_per_pound(cfg, members):
    t = "VOD LN Equity"
    px = {t: stock(0.0, 1.0, price=80.0, code="XLON")}  # 80p
    fx = {"GBP": pd.Series(1.25, index=sessions("XLON"))}
    md = market(cfg, members, px, fx=fx)
    st = build_portfolio(ledger([fill(t, 10_000, 80.0, fx_to_usd=1.25)]), md.as_of(D1), members, cfg)
    h = st.holdings.iloc[0]
    assert h["avg_cost_usd"] == pytest.approx(1.0) and h["price_usd"] == pytest.approx(1.0)


def test_relative_stop_triggers_at_minus_8(cfg, members):
    # bought at 100 on 1 Oct, falls 9% on 20 Oct while benchmark is flat
    px = {A: stock(0.0, 1.0, extra_shocks={pd.Timestamp("2025-10-20"): (-0.09, 1.0)})}
    st, al = alerts_for(cfg, members, px, [fill(A, 1000, 100.0)])
    stop = [a for a in al if a.rule == "relative_stop"]
    assert stop and stop[0].action == "EXIT" and stop[0].shares_to_sell == 1000
    assert st.holdings.iloc[0]["distance_to_stop"] == pytest.approx(-0.01)


def test_no_stop_when_benchmark_fell_too(cfg, members):
    b = flat_bench()
    b.loc["2025-10-20":, "adj_close"] *= 0.93
    px = {A: stock(0.0, 1.0, extra_shocks={pd.Timestamp("2025-10-20"): (-0.09, 1.0)})}
    md = market(cfg, members, px, bench=b)
    v = md.as_of(D1)
    st = build_portfolio(ledger([fill(A, 1000, 100.0)]), v, members, cfg)
    al = evaluate_exits(v, st.holdings, members, cfg, ImpliedMoveBook(manual()))
    assert not [a for a in al if a.rule == "relative_stop" and a.action == "EXIT"]
    assert st.holdings.iloc[0]["rel_return"] == pytest.approx(-0.09 + 0.07)


def test_trim_above_18_percent_and_breach_above_20(cfg, members):
    px = {A: stock(0.0, 1.0)}
    _, al = alerts_for(cfg, members, px, [fill(A, 1900, 100.0)])  # 190k / 1m = 19%
    trim = [a for a in al if a.rule == "trim"][0]
    assert trim.action == "TRIM TO 15%" and trim.shares_to_sell == 400
    _, al = alerts_for(cfg, members, px, [fill(A, 2100, 100.0)])
    assert [a for a in al if a.rule == "competition_limit"][0].severity == "BREACH"


def test_thesis_break_flag(cfg, members):
    _, al = alerts_for(cfg, members, {A: stock(0.0, 1.0)}, [fill(A, 100, 100.0, thesis_break="Y")])
    assert any(a.rule == "thesis_break" and a.action == "EXIT" for a in al)


def test_no_time_stop_by_default(cfg, members):
    _, al = alerts_for(cfg, members, {A: stock(0.0, 1.0)}, [fill(A, 100, 100.0, date="2024-01-02")])
    assert not any(a.rule == "time_stop" for a in al)


def test_core_reverse_halves_on_bad_report(cfg, members):
    px = {C: stock(-0.06, 2.5)}
    _, al = alerts_for(cfg, members, px, [fill(C, 1000, 100.0, role="core")], events=[amc(C)])
    cr = [a for a in al if a.rule == "core_reverse"][0]
    assert cr.action == "HALVE" and cr.shares_to_sell == 500
    _, al = alerts_for(cfg, members, {C: stock(-0.03, 2.5)}, [fill(C, 1000, 100.0, role="core")], events=[amc(C)])
    assert [a for a in al if a.rule == "core_reverse"][0].action == "HOLD"
    _, al = alerts_for(cfg, members, {C: stock(-0.06, 1.5)}, [fill(C, 1000, 100.0, role="core")], events=[amc(C)])
    assert [a for a in al if a.rule == "core_reverse"][0].action == "HOLD"


def test_earnings_hold_cap_trims_or_asks_for_implied_move(cfg, members):
    px = {A: stock(0.0, 1.0)}
    ev = [amc(A, report=pd.Timestamp("2025-11-04"))]
    rows = [fill(A, 1200, 100.0)]  # 12% weight
    _, al = alerts_for(cfg, members, px, rows, events=ev)
    assert [a for a in al if a.rule == "earnings_hold"][0].action == "CONFIRM MANUALLY: implied move"
    man = manual(checks=[{"bbg_ticker": A, "report_date": "2025-11-04", "implied_move": "8%"}])
    _, al = alerts_for(cfg, members, px, rows, events=ev, man=man)
    eh = [a for a in al if a.rule == "earnings_hold"][0]
    assert eh.action.startswith("TRIM BEFORE REPORT") and eh.target_weight == pytest.approx(0.01 / (1.5 * 0.08))


def test_analytics_theme_exposure_and_active_weights(cfg, members):
    px = {A: stock(0.0, 1.0), "BBB US Equity": stock(0.0, 1.0)}
    md = market(cfg, members, px)
    v = md.as_of(D1)
    st = build_portfolio(ledger([fill(A, 1000, 100.0), fill("BBB US Equity", 500, 100.0)]), v, members, cfg)
    an = portfolio_analytics(st, v, members, cfg)
    th = an.theme_exposure.set_index("theme_group")
    assert th.at["semis_software", "names"] == 2
    assert th.at["semis_software", "weight"] == pytest.approx(0.15)
    act = an.active_weights.set_index("bbg_ticker")
    assert act.at[A, "active_weight"] == pytest.approx(0.10 - 0.04)
    assert len(an.active_weights) == len(members)  # fixture has 8 members (< 10)
