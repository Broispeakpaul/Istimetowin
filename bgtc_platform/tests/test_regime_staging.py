import numpy as np
import pandas as pd
import pytest

from screener.rules.implied import ImpliedMoveBook
from screener.rules.regime import evaluate_regime
from screener.rules.staging import earnings_calendar, staging_list
from tests.synthetic import D1, amc, flat_bench, flat_vix, manual, market, sessions, stock


def _falling_bench():
    b = flat_bench()
    b.loc["2025-10-01":, ["open", "high", "low", "close", "adj_close"]] *= 0.90
    return b


@pytest.mark.parametrize("falling,vix,derisk", [(True, 30, True), (True, 20, False), (False, 30, False)])
def test_derisk_needs_both_conditions(cfg, members, falling, vix, derisk):
    md = market(cfg, members, {"AAA US Equity": stock(0, 1)}, bench=_falling_bench() if falling else None,
                vix=flat_vix(vix))
    r = evaluate_regime(md.as_of(D1), cfg)
    assert r.derisk is derisk


def test_missing_vix_means_confirm_manually(cfg, members):
    md = market(cfg, members, {"AAA US Equity": stock(0, 1)}, bench=_falling_bench(), vix=pd.Series(dtype=float))
    r = evaluate_regime(md.as_of(D1), cfg)
    assert r.derisk is None and "MISSING" in r.summary


def test_staging_lists_heavyweights_reporting_within_5_sessions(cfg, members):
    ev = [amc("AAA US Equity", report=pd.Timestamp("2025-11-04")),     # 3 sessions ahead: in
          amc("BBB US Equity", report=pd.Timestamp("2025-11-20")),     # too far: out
          amc("CCC US Equity", report=pd.Timestamp("2025-10-30"))]     # AMC today: still ahead at the close
    cfg.staging.heavyweight_top_n = 3
    px = {t: stock(0, 1) for t in ("AAA US Equity", "BBB US Equity", "CCC US Equity")}
    md = market(cfg, members, px, earnings=ev)
    man = manual(checks=[{"bbg_ticker": "AAA US Equity", "report_date": "2025-11-04", "implied_move": "0.05"}])
    st, notes = staging_list(md.as_of(D1), members, pd.Series(dtype=float), cfg, ImpliedMoveBook(man))
    assert set(st["bbg_ticker"]) == {"AAA US Equity", "CCC US Equity"}
    a = st.set_index("bbg_ticker").loc["AAA US Equity"]
    assert a["earnings_hold_cap"] == pytest.approx(0.01 / 0.075)
    assert any("not recommended" in n for n in notes)


def test_staging_without_weights_reports_missing(cfg, members):
    members = members.copy()
    members["wls_weight"] = np.nan
    md = market(cfg, members, {"AAA US Equity": stock(0, 1)})
    st, notes = staging_list(md.as_of(D1), members, pd.Series(dtype=float), cfg, ImpliedMoveBook(manual()))
    assert st.empty and "MISSING wls_weight" in notes[0]


def test_calendar_uses_each_listing_calendar(cfg, members):
    ev = [amc("7203 JT Equity", report=pd.Timestamp("2025-11-13")), amc("AAA US Equity", report=pd.Timestamp("2025-11-13"))]
    px = {"AAA US Equity": stock(0, 1), "7203 JT Equity": stock(0, 1, code="XTKS")}
    md = market(cfg, members, px, earnings=ev)
    cal = earnings_calendar(md.as_of(D1), members, pd.Series(dtype=float), cfg)
    # 13 Nov is the 10th US session after 30 Oct but the 9th Tokyo session (3 Nov holiday) -> both in range
    assert set(cal["bbg_ticker"]) == {"7203 JT Equity", "AAA US Equity"}
    cal14 = earnings_calendar(md.as_of(pd.Timestamp("2025-10-29")), members, pd.Series(dtype=float), cfg)
    assert set(cal14["bbg_ticker"]) == {"7203 JT Equity"}
