"""WLS universe: loads data/wls_members.csv and attaches calendar / currency / session info."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .config import Config

MEMBER_COLUMNS = ["bbg_ticker", "yahoo_symbol", "name", "country", "exchange", "sector", "theme", "wls_weight"]
OPTIONAL_COLUMNS = ["currency", "lot_size"]

ETF_HINTS = (" ETF", " UCITS", "ISHARES", "SPDR", "VANGUARD TOTAL", "INVESCO QQQ")


def exchange_from_bbg(bbg_ticker: str) -> str:
    """'7203 JT Equity' -> 'JT'."""
    parts = str(bbg_ticker).split()
    return parts[1].upper() if len(parts) >= 3 else (parts[-1].upper() if len(parts) == 2 else "")


def yahoo_from_bbg(bbg_ticker: str, cfg: Config) -> str:
    """Best-effort Bloomberg -> Yahoo symbol mapping. Always check unusual listings by hand."""
    parts = str(bbg_ticker).split()
    if len(parts) < 2:
        return ""
    code, exch = parts[0], parts[1].upper()
    info = cfg.exchanges.get(exch)
    if info is None:
        return ""
    if exch in ("US", "UN", "UW"):
        return code.replace("/", "-").replace(".", "-")
    if exch == "HK":
        return f"{int(code):04d}.HK" if code.isdigit() else f"{code}.HK"
    if exch in ("CH", "C1", "C2"):
        suffix = ".SS" if code.startswith("6") else ".SZ"
        return f"{code}{suffix}"
    if exch in ("LN",):
        code = code.rstrip("/").replace("/", ".")
    if exch in ("CN", "CT"):
        code = code.replace("/", "-")
    return f"{code}{info.yahoo_suffix}"


def _clean_weight(v):
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except (TypeError, ValueError):
        return None


def load_members(cfg: Config, path: Path | None = None) -> pd.DataFrame:
    """Return the universe indexed by bbg_ticker, with calendar/session/currency columns added.

    wls_weight is a fraction (0.045 = 4.5%). Values above 1 are read as percentages.
    Missing weights stay NaN and are reported as MISSING downstream; nothing is invented.
    """
    path = Path(path or cfg.paths.members)
    if not path.exists():
        raise FileNotFoundError(f"Universe file not found: {path}")
    df = pd.read_csv(path, dtype=str, comment="#").fillna("")
    missing = [c for c in MEMBER_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")
    for c in OPTIONAL_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df["bbg_ticker"] = df["bbg_ticker"].str.strip()
    df = df[df["bbg_ticker"] != ""].drop_duplicates("bbg_ticker").copy()
    df["exchange"] = [e.strip().upper() or exchange_from_bbg(t) for e, t in zip(df["exchange"], df["bbg_ticker"])]
    df["yahoo_symbol"] = [y.strip() or yahoo_from_bbg(t, cfg) for y, t in zip(df["yahoo_symbol"], df["bbg_ticker"])]

    weights = df["wls_weight"].map(_clean_weight)
    if weights.dropna().gt(1).any():
        weights = weights / 100.0
    df["wls_weight"] = weights.astype(float)

    unknown = sorted(set(df["exchange"]) - set(cfg.exchanges))
    if unknown:
        raise ValueError(f"Exchange codes not configured in config.yaml: {unknown}")
    df["calendar"] = df["exchange"].map(lambda e: cfg.exchange(e).calendar)
    df["session"] = df["exchange"].map(lambda e: cfg.exchange(e).session)
    df["currency"] = [c.strip() or cfg.exchange(e).currency for c, e in zip(df["currency"], df["exchange"])]
    lots = []
    for lot, e in zip(df["lot_size"], df["exchange"]):
        lot = str(lot).strip()
        lots.append(int(float(lot)) if lot else (cfg.exchange(e).lot_size or None))
    df["lot_size"] = pd.array(lots, dtype="Int64")
    df["theme"] = df["theme"].str.strip().str.lower().replace("", "unclassified")
    df["theme_group"] = df["theme"].map(cfg.themes.group)
    df["looks_like_etf"] = df["name"].str.upper().map(lambda n: any(h in n for h in ETF_HINTS))
    return df.set_index("bbg_ticker", drop=False)


def heavyweights(members: pd.DataFrame, top_n: int) -> pd.DataFrame:
    w = members.dropna(subset=["wls_weight"])
    return w.sort_values("wls_weight", ascending=False).head(top_n)
