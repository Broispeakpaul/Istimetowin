"""Catalyst watchlist: the events that give each stock a reason to re-rate.

The IB API does not publish 13D filings, earnings surprises, or deal terms,
so catalysts live in a CSV you maintain from your own research (EDGAR,
earnings releases, deal press releases). See catalysts_example.csv.

CSV columns (only symbol, type and announced are required):
    symbol, type, announced, conviction, event_date, offer_price,
    expected_close, earnings_surprise_pct, guidance_raised, revisions_up,
    short_interest, market_cap, activist, use_options, completed, notes
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path


class CatalystType(str, Enum):
    PEAD = "PEAD"                # earnings beat + raised guidance
    ACTIVIST = "ACTIVIST"        # 13D from a credible activist
    CASH_MA = "CASH_MA"          # announced all-cash acquisition target
    CORP_ACTION = "CORP_ACTION"  # spin-off, asset sale, buyback, major contract win


# How strong each bucket is on its own before conviction and freshness.
BASE_STRENGTH = {
    CatalystType.PEAD: 0.55,
    CatalystType.ACTIVIST: 0.60,
    CatalystType.CASH_MA: 0.50,
    CatalystType.CORP_ACTION: 0.50,
}
CONVICTION_MULT = {1: 0.8, 2: 1.0, 3: 1.2}


@dataclass
class Catalyst:
    symbol: str
    type: CatalystType
    announced: date
    conviction: int = 2
    event_date: date | None = None        # upcoming BINARY event (earnings, vote, FDA...)
    offer_price: float | None = None      # CASH_MA only
    expected_close: date | None = None    # CASH_MA only
    earnings_surprise_pct: float | None = None
    guidance_raised: bool = False
    revisions_up: bool = False
    short_interest: float | None = None   # fraction of float, e.g. 0.14
    market_cap: float | None = None       # USD; used when IB fundamentals are unavailable
    activist: str = ""
    use_options: bool = False
    completed: bool = False
    notes: str = ""

    def age_days(self, today: date) -> int:
        return (today - self.announced).days

    def is_fresh(self, today: date, max_age: dict[str, int]) -> bool:
        age = self.age_days(today)
        return 0 <= age <= max_age.get(self.type.value, 45) and not self.completed

    def days_to_event(self, today: date) -> int | None:
        return None if self.event_date is None else (self.event_date - today).days

    def is_imminent(self, today: date, days: int) -> bool:
        for d in (self.event_date, self.expected_close):
            if d is not None and 0 <= (d - today).days <= days:
                return True
        return False

    def ma_spread(self, price: float) -> float | None:
        if self.type is not CatalystType.CASH_MA or not self.offer_price or price <= 0:
            return None
        return self.offer_price / price - 1

    def strength(self, today: date, max_age: dict[str, int]) -> float:
        """0-1ish score: bucket base, catalyst quality, conviction, freshness decay."""
        s = BASE_STRENGTH[self.type]
        if self.type is CatalystType.PEAD:
            if self.guidance_raised:
                s += 0.15
            if self.revisions_up:
                s += 0.10
            if self.earnings_surprise_pct:
                s += min(max(self.earnings_surprise_pct, 0) / 100, 0.15)
        elif self.type is CatalystType.ACTIVIST and self.activist:
            s += 0.10
        s *= CONVICTION_MULT.get(self.conviction, 1.0)
        if self.short_interest and self.short_interest >= 0.10:
            s += 0.05
        limit = max_age.get(self.type.value, 45)
        freshness = 1 - 0.5 * min(max(self.age_days(today), 0) / limit, 1)
        return round(s * freshness, 4)


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def _parse_float(value: str) -> float | None:
    value = (value or "").strip().replace(",", "").rstrip("%")
    return float(value) if value else None


def _parse_bool(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}


def load_catalysts(path: str | Path) -> dict[str, Catalyst]:
    """Read the catalyst CSV. If a symbol appears twice, the most recent wins."""
    out: dict[str, Catalyst] = {}
    with open(path, newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if not row.get("symbol") or row["symbol"].startswith("#"):
                continue
            try:
                cat = Catalyst(
                    symbol=row["symbol"].upper(),
                    type=CatalystType(row["type"].upper()),
                    announced=_parse_date(row["announced"]),
                    conviction=int(row.get("conviction") or 2),
                    event_date=_parse_date(row.get("event_date", "")),
                    offer_price=_parse_float(row.get("offer_price", "")),
                    expected_close=_parse_date(row.get("expected_close", "")),
                    earnings_surprise_pct=_parse_float(row.get("earnings_surprise_pct", "")),
                    guidance_raised=_parse_bool(row.get("guidance_raised", "")),
                    revisions_up=_parse_bool(row.get("revisions_up", "")),
                    short_interest=_parse_float(row.get("short_interest", "")),
                    market_cap=_parse_float(row.get("market_cap", "")),
                    activist=row.get("activist", ""),
                    use_options=_parse_bool(row.get("use_options", "")),
                    completed=_parse_bool(row.get("completed", "")),
                    notes=row.get("notes", ""),
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(f"{path}: bad catalyst row {i}: {exc}") from exc
            if cat.announced is None:
                raise ValueError(f"{path}: row {i} is missing 'announced'")
            if cat.type is CatalystType.CASH_MA and not cat.offer_price:
                raise ValueError(f"{path}: row {i} CASH_MA needs offer_price")
            prev = out.get(cat.symbol)
            if prev is None or cat.announced >= prev.announced:
                out[cat.symbol] = cat
    return out
