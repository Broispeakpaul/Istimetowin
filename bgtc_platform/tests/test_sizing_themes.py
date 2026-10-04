import pandas as pd
import pytest

from screener.rules.sizing import (atr_weight, earnings_hold_cap, second_tranche_decision, shares_for,
                                   size_position)
from screener.rules.themes import ThemeBook


@pytest.mark.parametrize("atr,expected", [(0.025, 0.15), (0.05, 0.10), (0.0625, 0.08), (0.20, 0.04)])
def test_atr_weight_is_clamped(cfg, atr, expected):
    w, raw, _ = atr_weight(atr, cfg)
    assert w == pytest.approx(expected)
    assert raw == pytest.approx(0.005 / atr)


def test_earnings_hold_cap(cfg):
    assert earnings_hold_cap(0.05, cfg) == pytest.approx(0.01 / 0.075)
    assert earnings_hold_cap(None, cfg) is None
    r = size_position(0.03, cfg, holds_through_report=True, implied_move=0.10)  # ATR 16.7%->15%; cap 6.7%
    assert r.target == pytest.approx(0.01 / 0.15)
    r = size_position(0.05, cfg, holds_through_report=True, implied_move=0.04)  # ATR 10%; cap 16.7%
    assert r.target == pytest.approx(0.10)
    r = size_position(0.05, cfg, holds_through_report=True, implied_move=None)
    assert r.target == pytest.approx(0.10) and "CONFIRM MANUALLY" in r.notes[-1]


def test_tranches_split_half_half(cfg):
    r = size_position(0.05, cfg)
    assert r.tranche(0, cfg) == pytest.approx(0.05) and r.tranche(1, cfg) == pytest.approx(0.05)


def test_shares_round_down_to_lot():
    assert shares_for(0.05, 1_000_000, 33.0, 100) == 1500  # 1515 -> 1500
    assert shares_for(0.05, 1_000_000, 33.0, None) == 1515
    assert shares_for(0.05, 1_000_000, None, 1) is None


@pytest.mark.parametrize("close,low,c5,derisk,action", [
    (101, 100, "PASS", False, "BUY"),
    (101, 100, "PENDING", False, "BUY ONLY IF CHECK 5 CONFIRMED"),
    (101, 100, "FAIL", False, "SKIP"),
    (99, 100, "PASS", False, "SKIP"),
    (100, 100, "PASS", False, "SKIP"),            # must be strictly above the day-one low
    (101, 100, "PASS", True, "BLOCKED (DE-RISK)"),
    (None, 100, "PASS", False, "DATA MISSING"),
])
def test_second_tranche_rules(cfg, close, low, c5, derisk, action):
    assert second_tranche_decision(close, low, c5, derisk, cfg)[0] == action


def _holdings(rows):
    return pd.DataFrame(rows, columns=["bbg_ticker", "theme_group", "weight", "role"])


def test_theme_max_two_names_semis_and_software_merged(cfg):
    book = ThemeBook(_holdings([["AAA US Equity", "semis_software", 0.10, "catalyst"],
                                ["BBB US Equity", "semis_software", 0.08, "catalyst"]]), cfg)
    d = book.propose("EEE US Equity", "semis_software", 0.06)
    assert not d.ok and "max 2" in d.reason
    assert book.propose("AAA US Equity", "semis_software", 0.12, current_weight=0.10).ok  # add to existing name


def test_theme_weight_cap_shrinks_or_rejects(cfg):
    book = ThemeBook(_holdings([["AAA US Equity", "semis_software", 0.25, "catalyst"]]), cfg)
    d = book.propose("EEE US Equity", "semis_software", 0.15)
    assert d.ok and d.weight == pytest.approx(0.10) and "SIZE REDUCED" in d.reason
    book = ThemeBook(_holdings([["AAA US Equity", "semis_software", 0.32, "catalyst"]]), cfg)
    assert not book.propose("EEE US Equity", "semis_software", 0.10).ok  # 3% room < 4% minimum
    cfg.themes.cap_mode = "reject"
    book = ThemeBook(_holdings([["AAA US Equity", "semis_software", 0.25, "catalyst"]]), cfg)
    assert not book.propose("EEE US Equity", "semis_software", 0.15).ok


def test_theme_book_counts_accepted_candidates(cfg):
    book = ThemeBook(_holdings([]), cfg)
    for t in ("A", "B"):
        d = book.propose(t, "retail", 0.15)
        book.accept(t, "retail", d.weight)
    assert not book.propose("C", "retail", 0.05).ok


def test_theme_scope_catalyst_only_ignores_core(cfg):
    h = _holdings([["AAA US Equity", "semis_software", 0.20, "core"],
                   ["BBB US Equity", "semis_software", 0.15, "core"]])
    assert not ThemeBook(h, cfg).propose("EEE US Equity", "semis_software", 0.05).ok
    cfg.themes.scope = "catalyst_only"
    assert ThemeBook(h, cfg).propose("EEE US Equity", "semis_software", 0.05).ok
