import pandas as pd
import pytest

from screener.rules.entry import (ST_BLOCKED, ST_CANDIDATE, ST_CONFIRM_C1, ST_CONFIRM_REGIME, ST_MISSING,
                                  ST_REJECTED, apply_regime, evaluate_event)
from screener.rules.events import day_one_events
from tests.synthetic import D1, REPORT, amc, flat_bench, manual, market, stock

T = "AAA US Equity"


def run(cfg, members, px, man=None, bench=None, events=None, fx=None):
    md = market(cfg, members, px, earnings=events or [amc(T)], bench=bench, man=man, fx=fx)
    v = md.as_of(D1)
    evs = day_one_events(v.earnings(), members, D1, ["us", "asia", "europe"])
    return [evaluate_event(v, members.loc[e.bbg_ticker], e, cfg, man or manual()) for e in evs]


def test_clean_pass_waits_for_check_1(cfg, members):
    [s] = run(cfg, members, {T: stock(0.10, 3.0)})
    assert s.day_one == D1 and s.report_date == REPORT
    assert s.rel_return == pytest.approx(0.10)
    assert s.status == ST_CONFIRM_C1
    assert "CONFIRM MANUALLY" in s.reasons


def test_check_1_confirmed_gives_candidate_and_fail_rejects(cfg, members):
    ok = manual(checks=[{"bbg_ticker": T, "report_date": "2025-10-29", "check1_revenue_guidance": "PASS"}])
    assert run(cfg, members, {T: stock()}, man=ok)[0].status == ST_CANDIDATE
    bad = manual(checks=[{"bbg_ticker": T, "report_date": "2025-10-29", "check1_revenue_guidance": "FAIL"}])
    assert run(cfg, members, {T: stock()}, man=bad)[0].status == ST_REJECTED


def test_threshold_is_inclusive(cfg, members):
    assert run(cfg, members, {T: stock(0.08, 2.0)})[0].status == ST_CONFIRM_C1


@pytest.mark.parametrize("ret,vol,price,volume,failing", [
    (0.07, 3.0, 100, 1e6, "rel_return"),
    (0.10, 1.9, 100, 1e6, "volume_ratio"),
    (0.10, 3.0, 40, 1e6, "adv_usd"),       # ADV $40m < $50m
])
def test_each_quant_rule_rejects_with_reason(cfg, members, ret, vol, price, volume, failing):
    [s] = run(cfg, members, {T: stock(ret, vol, price=price, volume=volume)})
    assert s.status == ST_REJECTED
    assert s.check(failing).status == "FAIL"
    assert f"FAIL {failing}" in s.reasons


def test_return_is_relative_to_benchmark(cfg, members):
    b = flat_bench()
    b.loc[D1:, ["open", "high", "low", "close", "adj_close"]] *= 1.03
    [s] = run(cfg, members, {T: stock(0.10)}, bench=b)
    assert s.rel_return == pytest.approx(0.07)
    assert s.status == ST_REJECTED


def test_missing_fx_is_data_missing_not_a_guess(cfg, members):
    t = "7203 JT Equity"
    px = {t: stock(0.10, 3.0, code="XTKS")}
    [s] = run(cfg, members, px, events=[amc(t)])
    assert s.status == ST_MISSING and "FX" in s.reasons


def test_non_usd_listing_compared_in_usd(cfg, members):
    from tests.synthetic import sessions
    t = "7203 JT Equity"
    s_ = sessions("XTKS")
    fx = pd.Series(0.0067, index=s_)
    fx.loc[D1:] = 0.0067 * 0.97  # yen falls 3% on day one
    px = {t: stock(0.10, 3.0, price=3000, volume=5e6, code="XTKS")}
    [s] = run(cfg, members, px, events=[amc(t)], fx={"JPY": fx})
    assert s.day_one_return == pytest.approx(1.10 * 0.97 - 1)
    # +10% in yen but only +6.7% in USD: the USD figure decides, so it fails the +8% rule
    assert s.status == ST_REJECTED and "FAIL rel_return" in s.reasons


def test_regime_blocks_or_asks_for_confirmation(cfg, members):
    [s] = run(cfg, members, {T: stock()})
    apply_regime(s, True)
    assert s.status == ST_BLOCKED
    [s] = run(cfg, members, {T: stock()})
    apply_regime(s, None)
    assert s.status == ST_CONFIRM_REGIME


def test_etf_in_universe_is_rejected(cfg, members):
    members = members.copy()
    members.loc[T, "looks_like_etf"] = True
    [s] = run(cfg, members, {T: stock()})
    assert s.status == ST_REJECTED and s.check("wls_member").status == "FAIL"


def test_unknown_timing_evaluates_both_day_ones_flagged(cfg, members):
    md = market(cfg, members, {T: stock()}, earnings=[amc(T, report=D1, timing="UNKNOWN")])
    v = md.as_of(D1)
    [e] = day_one_events(v.earnings(), members, D1, ["us"])
    assert e.flag == "TIMING UNCONFIRMED" and e.timing_used == "BMO"
    v2 = md.as_of(pd.Timestamp("2025-10-31"))
    [e2] = day_one_events(v2.earnings(), members, pd.Timestamp("2025-10-31"), ["us"])
    assert e2.timing_used == "AMC"
    s = evaluate_event(v, members.loc[T], e, cfg, manual())
    assert "TIMING UNCONFIRMED" in s.reasons
