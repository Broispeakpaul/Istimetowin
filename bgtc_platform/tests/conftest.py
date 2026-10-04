"""Shared fixtures. Tests never touch the network: only FixtureSource / fake objects are used."""
from __future__ import annotations

import socket
from pathlib import Path

import pandas as pd
import pytest

from screener.config import PROJECT_ROOT, load_config

MEMBERS_CSV = """bbg_ticker,yahoo_symbol,name,country,exchange,sector,theme,wls_weight,currency,lot_size
AAA US Equity,AAA,Alpha Corp,US,US,Technology,software,0.040,,
BBB US Equity,BBB,Beta Semis,US,US,Technology,semiconductors,0.030,,
CCC US Equity,CCC,Gamma Retail,US,US,Consumer,retail,0.010,,
DDD US Equity,DDD,Delta Health,US,US,Health Care,healthcare,0.008,,
EEE US Equity,EEE,Epsilon Soft,US,US,Technology,software,0.006,,
7203 JT Equity,7203.T,Toyota Motor,JP,JT,Consumer,autos,0.005,,
700 HK Equity,0700.HK,Tencent,CN,HK,Communication,internet,0.007,,100
VOD LN Equity,VOD.L,Vodafone,GB,LN,Communication,telecom,0.001,,
"""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def guard(*a, **k):
        raise RuntimeError("Network access is not allowed in tests")
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "wls_members.csv").write_text(MEMBERS_CSV, encoding="utf-8")
    return tmp_path


@pytest.fixture
def cfg(workdir):
    """The real config.yaml, with every path pointed at a temp folder."""
    return load_config(PROJECT_ROOT / "config.yaml", root=workdir)


@pytest.fixture
def members(cfg):
    from screener.universe import load_members
    return load_members(cfg)
