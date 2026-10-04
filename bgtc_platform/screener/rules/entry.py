"""Entry checks for a day-one event. One function serves the live engine and the event study."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import pandas as pd

from ..config import Config
from ..inputs import FAIL, MISSING, PASS, PENDING, ManualInputs
from . import metrics as m
from .events import DayOneEvent

ST_CANDIDATE = "CANDIDATE"
ST_CONFIRM_C1 = "CANDIDATE - CONFIRM CHECK 1"
ST_REJECTED = "REJECTED"
ST_MISSING = "DATA MISSING"
ST_BLOCKED = "BLOCKED (DE-RISK)"
ST_CONFIRM_REGIME = "CANDIDATE - CONFIRM REGIME"
CANDIDATE_STATES = {ST_CANDIDATE, ST_CONFIRM_C1, ST_CONFIRM_REGIME}


@dataclass
class Check:
    name: str
    status: str
    value: Optional[float] = None
    threshold: Optional[float] = None
    reason: str = ""


@dataclass
class EventSignal:
    bbg_ticker: str
    name: str
    exchange: str
    session: str
    theme: str
    theme_group: str
    report_date: pd.Timestamp
    timing: str
    timing_used: str
    timing_flag: str
    day_one: pd.Timestamp
    source: str
    day_one_return: Optional[float] = None
    bench_return: Optional[float] = None
    rel_return: Optional[float] = None
    volume_ratio: Optional[float] = None
    adv_usd: Optional[float] = None
    atr_pct: Optional[float] = None
    day_one_low: Optional[float] = None
    close_local: Optional[float] = None
    close_usd: Optional[float] = None
    currency: str = ""
    check5: str = PENDING
    provisional: bool = False
    preliminary: bool = False
    checks: list[Check] = field(default_factory=list)
    status: str = ""
    reasons: str = ""

    def check(self, name: str) -> Optional[Check]:
        return next((c for c in self.checks if c.name == name), None)

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("checks")
        for c in self.checks:
            d[f"chk_{c.name}"] = c.status
        return d


def _fmt_pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:+.1%}"


def evaluate_event(view, member: pd.Series, ev: DayOneEvent, cfg: Config, manual: ManualInputs,
                   preliminary: bool = False) -> EventSignal:
    """Run the quantitative and manual entry checks for one day-one event (no sizing, no caps)."""
    t = member.name
    df = view.prices(t)
    d = ev.day_one
    sig = EventSignal(bbg_ticker=t, name=member["name"], exchange=member["exchange"], session=member["session"],
                      theme=member["theme"], theme_group=member["theme_group"], report_date=ev.report_date,
                      timing=ev.timing, timing_used=ev.timing_used, timing_flag=ev.flag, day_one=d,
                      source=ev.source, currency=member["currency"], preliminary=preliminary)
    checks: list[Check] = []

    # WLS membership (and a guard against ETFs slipping into the universe file)
    if member.get("looks_like_etf", False):
        checks.append(Check("wls_member", FAIL, reason="name looks like an ETF; ETFs are not allowed"))
    else:
        checks.append(Check("wls_member", PASS, reason="in data/wls_members.csv"))

    # Day-one relative return
    r, prev = m.day_one_return(df, d, member["calendar"])
    if r.ok:
        rb = m.benchmark_window_return(view.benchmark(), prev, d, cfg.benchmark.calendar)
        sig.day_one_return = r.value
        if rb.ok:
            sig.bench_return = rb.value
            sig.rel_return = m.relative_return(r.value, rb.value, cfg.entry.relative_return_method)
            sig.provisional = rb.status == m.PROVISIONAL
            thr = cfg.entry.min_day_one_rel_return
            ok = sig.rel_return >= thr
            note = f"day-one {_fmt_pct(r.value)} vs {view.benchmark_name} {_fmt_pct(rb.value)} = {_fmt_pct(sig.rel_return)} (need >= {thr:+.0%})"
            if sig.provisional:
                note += "; PROVISIONAL: " + rb.note
            if r.note:
                note += "; " + r.note
            checks.append(Check("rel_return", PASS if ok else FAIL, sig.rel_return, thr, note))
        else:
            checks.append(Check("rel_return", MISSING, reason=rb.note))
    else:
        checks.append(Check("rel_return", MISSING, reason=r.note))

    # Volume vs 50-day average
    vr = m.volume_ratio(df, d, cfg.entry.volume_avg_window)
    thr = cfg.entry.min_volume_ratio
    if vr.ok:
        sig.volume_ratio = vr.value
        note = f"volume {vr.value:.1f}x the {cfg.entry.volume_avg_window}d average (need >= {thr:.1f}x)"
        if preliminary:
            note += "; PRELIMINARY: partial-session volume"
        checks.append(Check("volume_ratio", PASS if vr.value >= thr else FAIL, vr.value, thr, note))
    else:
        checks.append(Check("volume_ratio", MISSING, reason=vr.note))

    # Liquidity
    adv = m.adv_usd(df, d, cfg.entry.adv_window)
    thr = cfg.entry.min_adv_usd
    if adv.ok:
        sig.adv_usd = adv.value
        checks.append(Check("adv_usd", PASS if adv.value >= thr else FAIL, adv.value, thr,
                            f"{cfg.entry.adv_window}d ADV ${adv.value / 1e6:,.0f}m (need >= ${thr / 1e6:,.0f}m)"))
    else:
        checks.append(Check("adv_usd", MISSING, reason=adv.note))

    # Manual check 1: revenue beat and guidance at or above consensus
    c1 = manual.check_status(t, ev.report_date, "check1")
    checks.append(Check("check1_rev_guidance", c1, reason={
        PASS: "confirmed in manual_checks.csv", FAIL: "marked FAIL in manual_checks.csv",
        PENDING: "CONFIRM MANUALLY: revenue beat and guidance >= consensus"}[c1]))
    sig.check5 = manual.check_status(t, ev.report_date, "check5")

    # Facts needed for sizing and the second tranche
    if df is not None and d in df.index:
        bar = df.loc[d]
        sig.day_one_low = float(bar["low"])
        sig.close_local = float(bar["close"])
        sig.close_usd = None if pd.isna(bar["close_usd"]) else float(bar["close_usd"])
    atr = m.atr_pct(df, d, cfg.sizing.atr_window, cfg.sizing.atr_method, cfg.sizing.atr_include_day_one)
    sig.atr_pct = atr.value if atr.ok else None

    sig.checks = checks
    sig.status, sig.reasons = decide_status(sig)
    return sig


QUANT_CHECKS = ("wls_member", "rel_return", "volume_ratio", "adv_usd")


def decide_status(sig: EventSignal) -> tuple[str, str]:
    quant = [c for c in sig.checks if c.name in QUANT_CHECKS]
    failed = [c for c in quant if c.status == FAIL]
    miss = [c for c in quant if c.status == MISSING]
    c1 = sig.check("check1_rev_guidance")
    parts = []
    if failed:
        parts = [f"FAIL {c.name}: {c.reason}" for c in failed]
        status = ST_REJECTED
    elif miss:
        parts = [f"MISSING {c.name}: {c.reason}" for c in miss]
        status = ST_MISSING
    elif c1 is not None and c1.status == FAIL:
        parts = [f"FAIL check 1: {c1.reason}"]
        status = ST_REJECTED
    else:
        parts = [f"PASS {c.name}: {c.reason}" for c in quant if c.name != "wls_member"]
        status = ST_CONFIRM_C1 if (c1 is not None and c1.status == PENDING) else ST_CANDIDATE
        if c1 is not None:
            parts.append(f"check 1 {c1.status}: {c1.reason}")
        if sig.atr_pct is None:
            parts.append("MISSING ATR: cannot size")
            status = ST_MISSING
    if sig.timing_flag:
        parts.append(f"{sig.timing_flag}: assumed {sig.timing_used}; confirm report timing")
    return status, " | ".join(parts)


def apply_regime(sig: EventSignal, derisk: Optional[bool]) -> None:
    if sig.status not in CANDIDATE_STATES:
        return
    if derisk is True:
        sig.status = ST_BLOCKED
        sig.reasons += " | BLOCKED: market de-risk (WLS < 50d average and VIX > threshold)"
    elif derisk is None:
        sig.status = ST_CONFIRM_REGIME
        sig.reasons += " | CONFIRM MANUALLY: regime data missing (WLS 50d average / VIX)"
