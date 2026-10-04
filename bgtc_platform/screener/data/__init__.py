"""Data layer: DataSource adapters, parquet cache and point-in-time market views."""
from __future__ import annotations

from ..config import Config
from .base import DataSource, DataUnavailable
from .cache import CachedSource


def make_source(cfg: Config, cached: bool = True) -> DataSource:
    """Build the adapter named by `data_source` in config.yaml, wrapped in the parquet cache."""
    if cfg.data_source == "yahoo":
        from .yahoo import YahooAdapter
        src: DataSource = YahooAdapter(cfg)
    elif cfg.data_source == "bloomberg":
        from .bloomberg import BloombergAdapter
        src = BloombergAdapter(cfg)
    elif cfg.data_source == "fixture":
        raise DataUnavailable("data_source 'fixture' is built in code (tests/demo); use `python -m screener demo`")
    else:  # pragma: no cover - pydantic restricts values
        raise ValueError(cfg.data_source)
    return CachedSource(src, cfg.paths.cache_dir, cfg.earnings.cache_ttl_hours) if cached else src
