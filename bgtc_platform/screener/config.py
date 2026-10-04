"""Typed configuration loaded from config.yaml. Every rule threshold lives here."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

Session = Literal["us", "asia", "europe"]
SESSIONS: tuple[str, ...] = ("asia", "europe", "us")  # chronological order within a HK day


class Paths(BaseModel):
    members: Path = Path("data/wls_members.csv")
    positions: Path = Path("data/positions.csv")
    earnings_overrides: Path = Path("data/earnings_overrides.csv")
    manual_checks: Path = Path("data/manual_checks.csv")
    account: Path = Path("data/account.csv")
    cache_dir: Path = Path("data/cache")
    reports_dir: Path = Path("reports")

    def resolve(self, root: Path) -> "Paths":
        return Paths(**{k: (v if v.is_absolute() else root / v) for k, v in self.__dict__.items()})


class Benchmark(BaseModel):
    bloomberg_ticker: str = "WLS Index"
    fallback_yahoo: str = "VT"
    vix_bloomberg: str = "VIX Index"
    vix_yahoo: str = "^VIX"
    calendar: str = "XNYS"


class History(BaseModel):
    lookback_calendar_days: int = 900


class Entry(BaseModel):
    min_day_one_rel_return: float = 0.08
    relative_return_method: Literal["difference", "ratio"] = "difference"
    min_volume_ratio: float = 2.0
    volume_avg_window: int = 50
    min_adv_usd: float = 50_000_000
    adv_window: int = 50
    require_wls_member: bool = True


class Themes(BaseModel):
    max_names_per_theme: int = 2
    max_weight_per_theme: float = 0.35
    groups: dict[str, str] = Field(default_factory=lambda: {
        "semiconductors": "semis_software", "software": "semis_software"})
    scope: Literal["all", "catalyst_only"] = "all"
    cap_mode: Literal["shrink", "reject"] = "shrink"

    def group(self, theme: Optional[str]) -> str:
        t = (theme or "").strip().lower() or "unclassified"
        return self.groups.get(t, t)


class Sizing(BaseModel):
    risk_budget: float = 0.005
    atr_window: int = 14
    atr_method: Literal["wilder", "simple"] = "wilder"
    atr_include_day_one: bool = True
    min_weight: float = 0.04
    max_weight: float = 0.15
    tranche_split: tuple[float, float] = (0.5, 0.5)
    second_tranche_session: int = 3
    block_second_tranche_in_derisk: bool = True
    earnings_hold_risk: float = 0.01
    implied_move_multiplier: float = 1.5
    earnings_hold_lookahead_sessions: int = 5

    @field_validator("tranche_split")
    @classmethod
    def _sum_to_one(cls, v):
        if abs(sum(v) - 1.0) > 1e-9:
            raise ValueError("tranche_split must sum to 1")
        return v


class Competition(BaseModel):
    max_position_weight: float = 0.20
    long_only: bool = True
    allow_leverage: bool = False


class Exits(BaseModel):
    relative_stop: float = -0.08
    trim_trigger: float = 0.18
    trim_target: float = 0.15
    time_stop_sessions: Optional[int] = None
    core_reverse_rel_return: float = -0.04
    core_reverse_volume_ratio: float = 2.0
    core_reverse_sell_fraction: float = 0.5


class Regime(BaseModel):
    sma_window: int = 50
    vix_threshold: float = 25.0
    target_beta: float = 1.0


class Staging(BaseModel):
    horizon_sessions: int = 5
    heavyweight_top_n: int = 20
    recommend_cash_staging: bool = False


class CalendarView(BaseModel):
    horizon_sessions: int = 10


class Beta(BaseModel):
    years: int = 2
    frequency: str = "W-FRI"
    raw_weight: float = 0.67
    prior_weight: float = 0.33
    min_observations: int = 52


class Earnings(BaseModel):
    cache_ttl_hours: float = 12
    unknown_timing: Literal["both", "bmo", "amc"] = "both"
    history_limit: int = 40


class ExchangeInfo(BaseModel):
    calendar: str
    session: Session
    currency: str
    yahoo_suffix: str = ""
    lot_size: Optional[int] = None


class BloombergFields(BaseModel):
    price_fields: dict[str, str] = Field(default_factory=lambda: {
        "open": "PX_OPEN", "high": "PX_HIGH", "low": "PX_LOW", "close": "PX_LAST", "volume": "PX_VOLUME"})
    currency_field: str = "CRNCY"
    fx_ticker_template: str = "{ccy}USD Curncy"
    members_field: str = "INDX_MWEIGHT"
    earnings_history_field: str = "EARN_ANN_DT_TIME_HIST_WITH_EPS"
    expected_report_date_field: str = "EXPECTED_REPORT_DT"
    expected_report_time_field: str = "EXPECTED_REPORT_TIME"
    implied_move_field: Optional[str] = None


class Backtest(BaseModel):
    initial_capital: float = 1_000_000
    contest_weeks: int = 5
    cost_bps: float = 10
    idle_cash: Literal["benchmark_proxy", "cash"] = "benchmark_proxy"
    manual_checks_assumption: Literal["pass", "pending"] = "pass"
    implied_move_proxy_reports: int = 8
    forward_horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20, 25])
    reaction_buckets: list[float] = Field(default_factory=lambda: [-1.0, -0.08, -0.04, 0.0, 0.04, 0.08, 0.12, 10.0])


class Config(BaseModel):
    data_source: Literal["yahoo", "bloomberg", "fixture"] = "yahoo"
    timezone: str = "Asia/Hong_Kong"
    paths: Paths = Field(default_factory=Paths)
    benchmark: Benchmark = Field(default_factory=Benchmark)
    history: History = Field(default_factory=History)
    entry: Entry = Field(default_factory=Entry)
    themes: Themes = Field(default_factory=Themes)
    sizing: Sizing = Field(default_factory=Sizing)
    competition: Competition = Field(default_factory=Competition)
    exits: Exits = Field(default_factory=Exits)
    regime: Regime = Field(default_factory=Regime)
    staging: Staging = Field(default_factory=Staging)
    calendar_view: CalendarView = Field(default_factory=CalendarView)
    beta: Beta = Field(default_factory=Beta)
    earnings: Earnings = Field(default_factory=Earnings)
    exchanges: dict[str, ExchangeInfo] = Field(default_factory=lambda: {
        "US": ExchangeInfo(calendar="XNYS", session="us", currency="USD")})
    bloomberg: BloombergFields = Field(default_factory=BloombergFields)
    backtest: Backtest = Field(default_factory=Backtest)

    @model_validator(mode="after")
    def _check_limits(self):
        if self.sizing.max_weight > self.competition.max_position_weight:
            raise ValueError("sizing.max_weight exceeds the competition's max position weight")
        if self.exits.trim_target > self.exits.trim_trigger:
            raise ValueError("exits.trim_target must be <= exits.trim_trigger")
        if self.exits.trim_trigger > self.competition.max_position_weight:
            raise ValueError("exits.trim_trigger exceeds the competition's max position weight")
        if not self.competition.long_only or self.competition.allow_leverage:
            raise ValueError("Competition rules: long only, no leverage. These cannot be switched off.")
        return self

    def exchange(self, code: str) -> ExchangeInfo:
        try:
            return self.exchanges[code]
        except KeyError as exc:
            raise KeyError(f"Exchange code '{code}' is not in config.yaml 'exchanges'") from exc


def load_config(path: Path | str | None = None, root: Path | None = None) -> Config:
    """Load config.yaml. Relative paths in the file resolve against `root` (default: the config's folder)."""
    path = Path(path) if path else PROJECT_ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    raw["exchanges"] = {str(k): v for k, v in (raw.get("exchanges") or {}).items()}
    cfg = Config(**raw)
    cfg.paths = cfg.paths.resolve(root or path.parent)
    return cfg
