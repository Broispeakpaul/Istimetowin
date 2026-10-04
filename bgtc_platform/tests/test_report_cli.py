import json

import pandas as pd

from screener.cli import build_parser
from screener.engine import run_day
from screener.report import write_report
from tests.synthetic import D1, amc, ledger, manual, market, stock


def test_report_files_and_reasons(cfg, members):
    px = {"DDD US Equity": stock(0.12, 3), "CCC US Equity": stock(0.03, 3)}
    md = market(cfg, members, px, earnings=[amc("DDD US Equity"), amc("CCC US Equity")])
    res = run_day(md.as_of(D1), "us", ledger([]), manual(), cfg)
    paths = write_report(res, cfg)
    assert paths["html"].name == "2025-10-30_us.html"
    html = paths["html"].read_text(encoding="utf-8")
    assert "SYNTHETIC DATA" in html and "Decision support only" in html and "Hong Kong time" in html
    sig = pd.read_csv(paths["signals_csv"])
    assert set(sig["bbg_ticker"]) == {"DDD US Equity", "CCC US Equity"}
    assert sig["reasons"].notna().all() and sig["status"].notna().all()
    meta = json.loads((paths["bundle"] / "meta.json").read_text(encoding="utf-8"))
    assert meta["session"] == "us" and meta["regime"]["derisk"] is False
    assert (paths["bundle"] / "candidates.csv").exists()


def test_empty_day_still_writes_report(cfg, members):
    md = market(cfg, members, {"DDD US Equity": stock(0, 1)})
    res = run_day(md.as_of(D1), "asia", ledger([]), manual(), cfg)
    paths = write_report(res, cfg)
    assert "No members had their day one today" in paths["html"].read_text(encoding="utf-8")
    assert pd.read_csv(paths["signals_csv"]).empty


def test_cli_arguments():
    p = build_parser()
    a = p.parse_args(["run", "--session", "us", "--date", "2025-10-30"])
    assert a.session == "us" and a.date == "2025-10-30"
    a = p.parse_args(["backtest", "contest", "--start", "2025-10-06", "--rolling", "--end", "2025-11-03"])
    assert a.bt_cmd == "contest" and a.rolling
