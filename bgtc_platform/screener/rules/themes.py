"""Theme concentration caps, applied in rank order across holdings and new candidates."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import Config


@dataclass
class ThemeDecision:
    weight: float
    ok: bool
    reason: str


class ThemeBook:
    """Tracks names and weight per theme group while candidates are accepted one by one."""

    def __init__(self, holdings: pd.DataFrame, cfg: Config) -> None:
        self.cfg = cfg
        h = holdings
        if not h.empty and cfg.themes.scope == "catalyst_only":
            h = h[h["role"] == "catalyst"]
        self.names: dict[str, set[str]] = {}
        self.weight: dict[str, float] = {}
        for _, r in h.iterrows():
            g = r["theme_group"]
            self.names.setdefault(g, set()).add(r["bbg_ticker"])
            self.weight[g] = self.weight.get(g, 0.0) + float(r.get("weight") or 0.0)

    def propose(self, ticker: str, group: str, target: float, current_weight: float = 0.0) -> ThemeDecision:
        t = self.cfg.themes
        names = self.names.get(group, set())
        is_new = ticker not in names
        if is_new and len(names) >= t.max_names_per_theme:
            return ThemeDecision(0.0, False, f"theme '{group}' already has {len(names)} names "
                                             f"({', '.join(sorted(names))}); max {t.max_names_per_theme}")
        add = max(target - current_weight, 0.0)
        room = t.max_weight_per_theme - self.weight.get(group, 0.0)
        if add <= room + 1e-12:
            return ThemeDecision(target, True, f"theme '{group}' {self.weight.get(group, 0.0):.1%} + {add:.1%} "
                                               f"<= {t.max_weight_per_theme:.0%}")
        if t.cap_mode == "shrink" and current_weight + room >= self.cfg.sizing.min_weight and room > 0:
            new = current_weight + room
            return ThemeDecision(new, True, f"SIZE REDUCED by theme cap: '{group}' at {self.weight.get(group, 0.0):.1%}, "
                                            f"target {target:.1%} -> {new:.1%}")
        return ThemeDecision(0.0, False, f"theme '{group}' at {self.weight.get(group, 0.0):.1%}; adding {add:.1%} "
                                         f"breaches {t.max_weight_per_theme:.0%}")

    def accept(self, ticker: str, group: str, target: float, current_weight: float = 0.0) -> None:
        self.names.setdefault(group, set()).add(ticker)
        self.weight[group] = self.weight.get(group, 0.0) + max(target - current_weight, 0.0)
