import pandas as pd
import pytest

from screener.universe import heavyweights, load_members, yahoo_from_bbg


def test_members_get_calendar_session_currency(members):
    assert members.at["7203 JT Equity", "calendar"] == "XTKS"
    assert members.at["7203 JT Equity", "session"] == "asia"
    assert members.at["7203 JT Equity", "lot_size"] == 100
    assert members.at["VOD LN Equity", "currency"] == "GBp"
    assert members.at["AAA US Equity", "theme_group"] == members.at["BBB US Equity", "theme_group"]
    assert members["wls_weight"].max() == pytest.approx(0.04)


def test_yahoo_mapping(cfg):
    assert yahoo_from_bbg("700 HK Equity", cfg) == "0700.HK"
    assert yahoo_from_bbg("BRK/B US Equity", cfg) == "BRK-B"
    assert yahoo_from_bbg("SAP GY Equity", cfg) == "SAP.DE"
    assert yahoo_from_bbg("600519 C1 Equity", cfg) == "600519.SS"
    assert yahoo_from_bbg("2330 TT Equity", cfg) == "2330.TW"


def test_missing_weights_stay_missing(cfg, workdir):
    p = workdir / "data" / "m.csv"
    p.write_text("bbg_ticker,yahoo_symbol,name,country,exchange,sector,theme,wls_weight\n"
                 "AAA US Equity,,A,US,,Tech,software,\nBBB US Equity,,B,US,,Tech,software,4.5\n", encoding="utf-8")
    m = load_members(cfg, p)
    assert pd.isna(m.at["AAA US Equity", "wls_weight"])
    assert m.at["BBB US Equity", "wls_weight"] == pytest.approx(0.045)  # percentages converted
    assert m.at["AAA US Equity", "yahoo_symbol"] == "AAA"
    assert list(heavyweights(m, 5).index) == ["BBB US Equity"]


def test_unknown_exchange_is_an_error(cfg, workdir):
    p = workdir / "data" / "m.csv"
    p.write_text("bbg_ticker,yahoo_symbol,name,country,exchange,sector,theme,wls_weight\nX ZZ Equity,,X,,,,,\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_members(cfg, p)
