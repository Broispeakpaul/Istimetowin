import shutil

import pytest

from screener.config import PROJECT_ROOT, load_config
from screener.engine import run_day
from screener.report import write_report
from tests.synthetic import D1, amc, ledger, manual, market, stock


@pytest.mark.filterwarnings("ignore")
def test_dashboard_renders_every_tab(workdir, members, monkeypatch):
    shutil.copy(PROJECT_ROOT / "config.yaml", workdir / "config.yaml")
    cfg = load_config(workdir / "config.yaml")
    px = {"AAA US Equity": stock(0, 1), "BBB US Equity": stock(0, 1), "DDD US Equity": stock(0.12, 3)}
    rows = [{"date": "2025-10-01", "bbg_ticker": "AAA US Equity", "side": "BUY", "quantity": 1000, "price_local": 100},
            {"date": "2025-10-01", "bbg_ticker": "BBB US Equity", "side": "BUY", "quantity": 800, "price_local": 100,
             "role": "core"}]
    md = market(cfg, members, px, earnings=[amc("DDD US Equity")])
    write_report(run_day(md.as_of(D1), "us", ledger(rows), manual(), cfg), cfg)

    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("BGTC_CONFIG", str(workdir / "config.yaml"))
    at = AppTest.from_file(str(PROJECT_ROOT / "screener" / "dashboard" / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs] == ["Today's candidates", "Earnings calendar", "Positions and alerts",
                                          "Portfolio risk", "Market regime"]
    assert "2025-10-30" in at.title[0].value
    assert len(at.dataframe) >= 4  # candidates, alerts, holdings, betas ...
