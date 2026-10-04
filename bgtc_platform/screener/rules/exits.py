"""Exit rules on current holdings: relative stop, trim, thesis break, core reverse, earnings-hold cap."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

import pandas as pd

from .. import calendars as cal
from ..config import Config
from . import metrics as m
from .events import day_one_events, next_report
from .implied import ImpliedMoveBook
from .sizing import atr_weight, earnings_hold_cap, lot_int

SEV_ACTION, SEV_CONFIRM, SEV_INFO, SEV_BREACH = "ACTION", "CONFIRM", "INFO", "BREACH"


@dataclass
class Alert:
    bbg_ticker: str
    rule: str
    action: str
    severity: str
    current_weight: Optional[float]
    target_weight: Optional[float]
    shares_to_sell: Optional[int]
    detail: str

    def to_row(self) -> dict:
        return asdict(self)


def _sell_shares(qty: float, current_w: float, target_w: float, lot) -> Optional[int]:
    if current_w is None or not current_w or pd.isna(current_w):
        return None
    lot = lot_int(lot)
    raw = qty * (1 - target_w / current_w)
    n = math.ceil(raw / lot) * lot  # round UP when selling so the limit is actually met
    return int(min(n, qty))


def evaluate_exits(view, holdings: pd.DataFrame, members: pd.DataFrame, cfg: Config,
                   implied: ImpliedMoveBook) -> list[Alert]:
    as_of = view.as_of
    e = cfg.exits
    alerts: list[Alert] = []
    for _, h in holdings.iterrows():
        t, w, q = h["bbg_ticker"], h["weight"], h["quantity"]
        w_txt = f"{w:.1%}" if w is not None and not pd.isna(w) else "MISSING"

        if h["thesis_break"]:
            alerts.append(Alert(t, "thesis_break", "EXIT", SEV_ACTION, w, 0.0, int(q), "thesis break flagged in positions.csv"))

        if h["rel_return"] is not None and not pd.isna(h["rel_return"]):
            if h["rel_return"] <= e.relative_stop:
                alerts.append(Alert(t, "relative_stop", "EXIT", SEV_ACTION, w, 0.0, int(q),
                                    f"{h['rel_return']:+.1%} vs WLS since entry {h['entry_date'].date()} "
                                    f"(stop {e.relative_stop:+.0%}); position {h['pos_return']:+.1%}, WLS {h['bench_return']:+.1%}"))
        else:
            alerts.append(Alert(t, "relative_stop", "CONFIRM MANUALLY", SEV_CONFIRM, w, None, None,
                                "relative return since entry MISSING (price, FX or benchmark at fill dates)"))

        if w is not None and not pd.isna(w):
            if w > cfg.competition.max_position_weight:
                alerts.append(Alert(t, "competition_limit", f"TRIM TO {e.trim_target:.0%} NOW", SEV_BREACH, w, e.trim_target,
                                    _sell_shares(q, w, e.trim_target, h["lot_size"]),
                                    f"{w_txt} breaches the competition's {cfg.competition.max_position_weight:.0%} limit"))
            elif w > e.trim_trigger:
                alerts.append(Alert(t, "trim", f"TRIM TO {e.trim_target:.0%}", SEV_ACTION, w, e.trim_target,
                                    _sell_shares(q, w, e.trim_target, h["lot_size"]),
                                    f"{w_txt} > {e.trim_trigger:.0%} trigger"))

        if e.time_stop_sessions and h["entry_date"] is not None:
            held = cal.session_count_between(h["calendar"], h["entry_date"], as_of)
            if held >= e.time_stop_sessions:
                alerts.append(Alert(t, "time_stop", "EXIT", SEV_ACTION, w, 0.0, int(q), f"held {held} sessions"))

        # Core reverse checklist: post-report day one >= 4% below WLS on >= 2x volume -> halve
        if h["role"] == "core":
            for ev in day_one_events(view.earnings(), members.loc[[t]], as_of, [h["session"]], cfg.earnings.unknown_timing):
                frame = view.prices(t)
                r, prev = m.day_one_return(frame, as_of, h["calendar"])
                vr = m.volume_ratio(frame, as_of, cfg.entry.volume_avg_window)
                if not (r.ok and vr.ok):
                    alerts.append(Alert(t, "core_reverse", "CONFIRM MANUALLY", SEV_CONFIRM, w, None, None,
                                        f"post-report day one: data MISSING ({r.note or vr.note})"))
                    continue
                rb = m.benchmark_window_return(view.benchmark(), prev, as_of, cfg.benchmark.calendar)
                if not rb.ok:
                    alerts.append(Alert(t, "core_reverse", "CONFIRM MANUALLY", SEV_CONFIRM, w, None, None, rb.note))
                    continue
                rel = m.relative_return(r.value, rb.value, cfg.entry.relative_return_method)
                hit = rel <= e.core_reverse_rel_return and vr.value >= e.core_reverse_volume_ratio
                detail = (f"report {ev.report_date.date()} ({ev.timing_used}{', ' + ev.flag if ev.flag else ''}): "
                          f"{rel:+.1%} vs WLS on {vr.value:.1f}x volume")
                if hit:
                    tgt = (w or 0) * (1 - e.core_reverse_sell_fraction)
                    alerts.append(Alert(t, "core_reverse", "HALVE", SEV_ACTION, w, tgt,
                                        int(q * e.core_reverse_sell_fraction), detail))
                else:
                    alerts.append(Alert(t, "core_reverse", "HOLD", SEV_INFO, w, w, 0, detail + " (no trigger)"))

        # Holding through its own report: cap = min(ATR weight, 1% / (1.5 x implied move))
        nr = next_report(view.earnings(), t, as_of)
        if nr is not None:
            upto = cal.next_n_sessions(h["calendar"], as_of, cfg.sizing.earnings_hold_lookahead_sessions)[-1]
            if nr["report_date"] <= upto:
                im, src = implied.get(t, nr["report_date"])
                frame = view.prices(t)
                atr = m.atr_pct(frame, frame.index[-1], cfg.sizing.atr_window, cfg.sizing.atr_method, True) if frame is not None and len(frame) else m.missing("no prices")
                aw, _, _ = atr_weight(atr.value if atr.ok else None, cfg)
                cap = earnings_hold_cap(im, cfg)
                when = f"reports {nr['report_date'].date()} {nr['timing']}"
                if cap is None:
                    alerts.append(Alert(t, "earnings_hold", "CONFIRM MANUALLY: implied move", SEV_CONFIRM, w, None, None,
                                        f"{when}; implied move MISSING, so the earnings-hold cap cannot be computed"))
                else:
                    limit = min(cap, aw) if aw is not None else cap
                    if w is not None and not pd.isna(w) and w > limit + 1e-9:
                        alerts.append(Alert(t, "earnings_hold", f"TRIM BEFORE REPORT TO {limit:.1%}", SEV_ACTION, w, limit,
                                            _sell_shares(q, w, limit, h["lot_size"]),
                                            f"{when}; cap min(ATR weight {aw if aw is None else f'{aw:.1%}'}, "
                                            f"1%/(1.5 x {im:.1%} {src}) = {cap:.1%})"))
                    else:
                        alerts.append(Alert(t, "earnings_hold", "OK TO HOLD THROUGH REPORT", SEV_INFO, w, limit, 0,
                                            f"{when}; weight {w_txt} <= cap {limit:.1%}"))
    return alerts
