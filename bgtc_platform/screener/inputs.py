"""Hand-maintained inputs: positions ledger, earnings overrides, manual checks, account snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import Config

POSITION_COLUMNS = ["date", "bbg_ticker", "side", "quantity", "price_local", "fx_to_usd", "role",
                    "tranche", "day_one_date", "thesis_break", "notes"]
OVERRIDE_COLUMNS = ["bbg_ticker", "report_date", "timing", "report_time_local", "source", "notes"]
MANUAL_COLUMNS = ["bbg_ticker", "report_date", "check1_revenue_guidance", "check5_eps_revision",
                  "implied_move", "confirmed_by", "notes"]
ACCOUNT_COLUMNS = ["date", "cash_usd", "nav_usd"]

PASS, FAIL, PENDING, MISSING = "PASS", "FAIL", "PENDING", "MISSING"
ROLES = {"catalyst", "core", "staging"}


def _read(path: Path, columns: list[str]) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(path, dtype=str, comment="#").fillna("")
    for c in columns:
        if c not in df.columns:
            df[c] = ""
    for c in df.columns:
        df[c] = df[c].astype(str).str.strip()
    return df


def normalise_check(value) -> str:
    v = str(value or "").strip().upper()
    if v in ("PASS", "Y", "YES", "TRUE", "1", "CONFIRMED"):
        return PASS
    if v in ("FAIL", "N", "NO", "FALSE", "0", "FAILED"):
        return FAIL
    return PENDING


def _to_float(v) -> Optional[float]:
    try:
        f = float(str(v).replace("%", ""))
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f / 100.0 if "%" in str(v) or f > 1.0 else f


def _yes(v) -> bool:
    return str(v or "").strip().upper() in ("Y", "YES", "TRUE", "1")


@dataclass
class ManualInputs:
    overrides: pd.DataFrame
    checks: pd.DataFrame
    tolerance_days: int = 4
    assume: Optional[str] = None  # backtest: force a status for checks with no row

    def override_events(self) -> pd.DataFrame:
        df = self.overrides.copy()
        if df.empty:
            return pd.DataFrame(columns=["bbg_ticker", "report_date", "timing", "report_time_local", "source"])
        df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
        df = df.dropna(subset=["report_date"])
        df["timing"] = df["timing"].str.upper().replace("", "UNKNOWN")
        df["source"] = df["source"].replace("", "manual override")
        return df[["bbg_ticker", "report_date", "timing", "report_time_local", "source"]]

    def _row(self, ticker: str, report_date) -> Optional[pd.Series]:
        df = self.checks
        if df.empty:
            return None
        rows = df[df["bbg_ticker"] == ticker]
        if rows.empty:
            return None
        rd = pd.to_datetime(rows["report_date"], errors="coerce")
        target = pd.Timestamp(report_date)
        delta = (rd - target).abs().dt.days
        ok = delta <= self.tolerance_days
        if not ok.any():
            return None
        return rows.loc[delta[ok].idxmin()]

    def check_status(self, ticker: str, report_date, which: str) -> str:
        col = {"check1": "check1_revenue_guidance", "check5": "check5_eps_revision"}[which]
        row = self._row(ticker, report_date)
        if row is None or normalise_check(row[col]) == PENDING:
            return self.assume.upper() if self.assume else PENDING
        return normalise_check(row[col])

    def implied_move(self, ticker: str, report_date) -> Optional[float]:
        row = self._row(ticker, report_date)
        return None if row is None else _to_float(row["implied_move"])


def load_manual_inputs(cfg: Config) -> ManualInputs:
    return ManualInputs(overrides=_read(cfg.paths.earnings_overrides, OVERRIDE_COLUMNS),
                        checks=_read(cfg.paths.manual_checks, MANUAL_COLUMNS))


@dataclass
class Ledger:
    """Fills ledger. Holdings, cost and entry dates are derived from it."""
    fills: pd.DataFrame
    account: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=ACCOUNT_COLUMNS))

    @classmethod
    def from_frame(cls, fills: pd.DataFrame, account: Optional[pd.DataFrame] = None) -> "Ledger":
        df = fills.copy()
        for c in POSITION_COLUMNS:
            if c not in df.columns:
                df[c] = ""
        if df.empty:
            df = pd.DataFrame(columns=POSITION_COLUMNS)
        for c in ("side", "role", "thesis_break", "notes", "bbg_ticker"):
            df[c] = df[c].astype(object).where(df[c].notna(), "")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["side"] = df["side"].astype(str).str.upper().str.strip()
        bad = set(df["side"]) - {"BUY", "SELL"}
        if bad:
            raise ValueError(f"positions.csv: side must be BUY or SELL, got {sorted(bad)}")
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0).abs()
        df["price_local"] = pd.to_numeric(df["price_local"], errors="coerce")
        df["fx_to_usd"] = pd.to_numeric(df["fx_to_usd"], errors="coerce")
        df["role"] = df["role"].astype(str).str.lower().str.strip().replace("", "catalyst")
        bad_roles = set(df["role"]) - ROLES
        if bad_roles:
            raise ValueError(f"positions.csv: role must be one of {sorted(ROLES)}, got {sorted(bad_roles)}")
        df["tranche"] = pd.to_numeric(df["tranche"], errors="coerce")
        df["day_one_date"] = pd.to_datetime(df["day_one_date"], errors="coerce")
        df["thesis_break"] = df["thesis_break"].map(_yes)
        acct = account if account is not None else pd.DataFrame(columns=ACCOUNT_COLUMNS)
        if not acct.empty:
            acct = acct.copy()
            acct["date"] = pd.to_datetime(acct["date"], errors="coerce")
            acct["cash_usd"] = pd.to_numeric(acct["cash_usd"], errors="coerce")
            acct["nav_usd"] = pd.to_numeric(acct.get("nav_usd"), errors="coerce")
        return cls(fills=df.sort_values("date", kind="stable").reset_index(drop=True), account=acct)

    def as_of(self, d) -> "Ledger":
        d = pd.Timestamp(d)
        f = self.fills[self.fills["date"] <= d]
        a = self.account[self.account["date"] <= d] if not self.account.empty else self.account
        return Ledger(fills=f.reset_index(drop=True), account=a)

    def add_fill(self, **row) -> "Ledger":
        new = pd.DataFrame([{c: row.get(c, "") for c in POSITION_COLUMNS}])
        return Ledger.from_frame(pd.concat([self.fills.astype(object), new], ignore_index=True), self.account)


def load_ledger(cfg: Config) -> Ledger:
    fills = _read(cfg.paths.positions, POSITION_COLUMNS)
    acct = _read(cfg.paths.account, ACCOUNT_COLUMNS)
    return Ledger.from_frame(fills, acct)
