"""Offline rehearsal of the acceptance dry run for 30 October 2025.

Report dates/timings below follow the public Q3-2025 schedule as an input pattern only; every PRICE here is
synthetic. The real acceptance test is `python -m screener run --session all --date 2025-10-30` with live data
(see README). This test pins the logic that run depends on: day-one dates per exchange calendar, USD returns,
ranking, theme caps, sizes and reasons.
"""
import pandas as pd
import pytest

from screener.config import PROJECT_ROOT
from screener.engine import run_day
from screener.universe import load_members
from tests.synthetic import ledger, manual, market, sessions, stock

D1 = pd.Timestamp("2025-10-30")


def ev(t, report, timing):
    return {"bbg_ticker": t, "report_date": pd.Timestamp(report), "timing": timing, "report_time_local": "", "source": "test"}


@pytest.fixture
def world(cfg):
    members = load_members(cfg, PROJECT_ROOT / "data" / "wls_members.csv")
    px = {
        "MSFT US Equity": stock(0.10, 3.0, d1=D1),                       # AMC 29 Oct -> day one 30 Oct
        "GOOGL US Equity": stock(0.03, 2.5, d1=D1),                      # AMC 29 Oct, too small
        "LLY US Equity": stock(0.09, 2.5, d1=D1),                        # BMO 30 Oct -> same day
        "AAPL US Equity": stock(0.00, 1.0, d1=None),                     # AMC 30 Oct -> day one 31 Oct (not today)
        "005930 KS Equity": stock(0.11, 4.0, price=100_000, volume=20e6, code="XKRX", d1=D1),   # BMO 30 Oct
        "8035 JT Equity": stock(0.085, 2.2, price=30_000, volume=3e6, code="XTKS", d1=D1),      # AMC 29 Oct
        "SHEL LN Equity": stock(0.02, 1.5, price=2_700, volume=8e6, code="XLON", d1=D1),        # BMO 30 Oct
    }
    fx = {"KRW": pd.Series(0.0007, index=sessions("XKRX")), "JPY": pd.Series(0.0066, index=sessions("XTKS")),
          "GBP": pd.Series(1.32, index=sessions("XLON"))}
    events = [ev("MSFT US Equity", "2025-10-29", "AMC"), ev("GOOGL US Equity", "2025-10-29", "AMC"),
              ev("LLY US Equity", "2025-10-30", "BMO"), ev("AAPL US Equity", "2025-10-30", "AMC"),
              ev("005930 KS Equity", "2025-10-30", "BMO"), ev("8035 JT Equity", "2025-10-29", "AMC"),
              ev("SHEL LN Equity", "2025-10-30", "BMO")]
    md = market(cfg, members, px, earnings=events, fx=fx)
    return run_day(md.as_of(D1), "all", ledger([]), manual(), cfg)


def test_lists_qualifying_reactions_with_day_one_dates(world):
    sig = world.signals.set_index("bbg_ticker")
    assert "AAPL US Equity" not in sig.index                       # its day one is 31 Oct
    assert (pd.to_datetime(sig["day_one"]) == D1).all()
    assert sig.at["MSFT US Equity", "report_date"] == pd.Timestamp("2025-10-29")
    assert sig.at["LLY US Equity", "timing_used"] == "BMO"
    assert list(world.candidates["bbg_ticker"]) == ["005930 KS Equity", "MSFT US Equity", "LLY US Equity"]


def test_theme_cap_and_rejections_have_reasons(world):
    sig = world.signals.set_index("bbg_ticker")
    assert sig.at["8035 JT Equity", "status"] == "REJECTED (THEME CAP)"   # third semis/software name
    assert "max 2" in sig.at["8035 JT Equity", "reasons"]
    assert sig.at["GOOGL US Equity", "status"] == "REJECTED" and "FAIL rel_return" in sig.at["GOOGL US Equity", "reasons"]
    assert sig.at["SHEL LN Equity", "status"] == "REJECTED"
    assert sig["reasons"].str.len().gt(20).all()


def test_sizes_follow_atr_rule_and_lots(world, cfg):
    nav = world.portfolio.nav_usd
    for _, r in world.candidates.iterrows():
        raw = cfg.sizing.risk_budget / r["atr_pct"]
        assert r["target_weight"] == pytest.approx(min(max(raw, 0.04), 0.15))
        assert r["tranche1_weight"] == pytest.approx(r["target_weight"] / 2)
        assert r["shares_tranche1"] * r["close_usd"] <= r["tranche1_weight"] * nav + 1e-6
        assert "check 1 PENDING" in r["reasons"]                        # manual check still to confirm
    tokyo = world.session_closes.set_index("calendar").at["XTKS", "close_hkt"]
    assert tokyo == "2025-10-30 14:30 HKT"
