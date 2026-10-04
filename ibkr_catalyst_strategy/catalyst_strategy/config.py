"""All tunable parameters for the event-driven momentum strategy.

Every rule from the strategy write-up maps to a field here, so you can
tighten or loosen the playbook without touching the logic.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path


@dataclass
class StrategyConfig:
    # ---- 1. Universe & screening -------------------------------------
    min_market_cap: float = 300e6
    max_market_cap: float = 10e9
    min_avg_dollar_volume: float = 5e6      # 20-day average of close * volume
    min_price: float = 5.0
    rs_min_percentile: float = 0.70         # top 30% of the scanned universe
    rs_lookbacks: tuple[int, int] = (63, 126)  # ~3 and ~6 months
    rs_benchmark: str = "IWM"               # must also beat small caps outright
    squeeze_short_interest: float = 0.10    # >10% SI earns a small score bonus
    require_short_interest: bool = False    # set True to make SI > 10% mandatory

    # How long a catalyst stays "fresh" (calendar days since announcement)
    catalyst_max_age_days: dict[str, int] = field(default_factory=lambda: {
        "PEAD": 45, "ACTIVIST": 45, "CASH_MA": 120, "CORP_ACTION": 60,
    })

    # ---- 3. Entry & exit ---------------------------------------------
    breakout_lookback: int = 20             # close above prior 20-day high
    breakout_volume_mult: float = 1.2       # on above-average volume
    pullback_ma: int = 20                   # or a pullback to the 20-day MA
    pullback_tolerance: float = 0.02        # low within 2% of the 20-day MA
    stop_pct_min: float = 0.10              # hard stop 10% ...
    stop_pct_max: float = 0.145             # ... to 14.5% below entry
    stop_atr_mult: float = 3.0              # ATR-based stop, clamped to the band above
    atr_period: int = 14
    use_trailing_stop: bool = False         # True = TRAIL order instead of fixed STP
    trailing_pct: float = 0.12
    exit_ma: int = 50                       # momentum fade: close below 50-day MA
    binary_event_buffer_days: int = 3       # exit this many days before a binary event
    binary_entry_blackout_days: int = 14    # no new stock entries this close to a binary event
    binary_hold_max_position_pct: float = 0.05  # only conviction-3 names this small may hold through

    # ---- 4. Position sizing ------------------------------------------
    min_positions: int = 8
    max_positions: int = 12
    max_position_pct: float = 0.15
    risk_pct_by_conviction: dict[int, float] = field(default_factory=lambda: {
        1: 0.010, 2: 0.015, 3: 0.025,
    })
    max_risk_pct: float = 0.03              # "never risk ruin" hard ceiling
    tranches: int = 2                       # deploy each position in N tranches
    limit_offset_pct: float = 0.005         # buy limit = last * (1 + offset)

    # ---- 5. Market regime --------------------------------------------
    regime_symbol: str = "SPY"
    regime_ma: int = 200
    cash_reserve_risk_on: float = 0.15      # 10-20% cash when SPY > 200DMA
    cash_reserve_risk_off: float = 0.40     # 30-50% cash when SPY < 200DMA

    # ---- 6. Options (long calls only) ---------------------------------
    options_enabled: bool = False
    option_budget_pct: float = 0.015        # premium per trade, 1-2% of equity
    option_max_budget_pct: float = 0.02
    option_otm_pct: float = 0.05            # strike ~5% above spot
    option_days_after_event: int = 14       # expiry at least 2 weeks past the catalyst

    # ---- Cash M&A --------------------------------------------------------
    ma_min_spread: float = 0.02             # need >= 2% to the offer to enter
    ma_exit_spread: float = 0.005           # take it when < 0.5% is left

    # ---- 3-month competition calendar -----------------------------------
    competition_start: date | None = None
    competition_weeks: int = 12
    deploy_start_week: int = 3              # weeks 1-2: screen and rank only
    harvest_start_week: int = 11            # weeks 11-12: tighten, take profits
    harvest_stop_pct: float = 0.07          # tightened stop distance from last price
    harvest_imminent_days: int = 5          # new trades in harvest only if catalyst this close

    def __post_init__(self) -> None:
        if not 0 < self.stop_pct_min <= self.stop_pct_max:
            raise ValueError("stop_pct_min must be > 0 and <= stop_pct_max")
        if self.max_position_pct > 0.15:
            raise ValueError("max_position_pct above 15% breaks the sizing rules")
        if max(self.risk_pct_by_conviction.values()) > self.max_risk_pct:
            raise ValueError("a conviction risk level exceeds max_risk_pct")
        if self.option_budget_pct > self.option_max_budget_pct:
            raise ValueError("option_budget_pct exceeds option_max_budget_pct")
        if self.min_positions > self.max_positions:
            raise ValueError("min_positions must be <= max_positions")
        if isinstance(self.competition_start, str):
            self.competition_start = date.fromisoformat(self.competition_start)

    @property
    def competition_end(self) -> date | None:
        if self.competition_start is None:
            return None
        return self.competition_start + timedelta(weeks=self.competition_weeks)

    def risk_pct(self, conviction: int) -> float:
        conviction = min(max(int(conviction), 1), 3)
        return min(self.risk_pct_by_conviction[conviction], self.max_risk_pct)

    @classmethod
    def from_json(cls, path: str | Path) -> "StrategyConfig":
        """Load overrides from a JSON file; unknown keys raise so typos surface."""
        raw = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        if "rs_lookbacks" in raw:
            raw["rs_lookbacks"] = tuple(raw["rs_lookbacks"])
        if "risk_pct_by_conviction" in raw:
            raw["risk_pct_by_conviction"] = {int(k): v for k, v in raw["risk_pct_by_conviction"].items()}
        return cls(**raw)

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.competition_start:
            d["competition_start"] = self.competition_start.isoformat()
        return d
