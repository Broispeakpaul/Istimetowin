"""Implied earnings move lookup: manual input first, then the data source, then (backtest only) a proxy."""
from __future__ import annotations

from typing import Callable, Optional

from ..inputs import ManualInputs

ProxyFn = Callable[[str, object], Optional[float]]


class ImpliedMoveBook:
    def __init__(self, manual: ManualInputs, source_moves: dict[str, float] | None = None,
                 proxy_fn: ProxyFn | None = None) -> None:
        self.manual = manual
        self.source_moves = source_moves or {}
        self.proxy_fn = proxy_fn  # only the backtest sets this; live runs never estimate

    def get(self, ticker: str, report_date) -> tuple[Optional[float], str]:
        im = self.manual.implied_move(ticker, report_date)
        if im is not None:
            return im, "manual"
        if ticker in self.source_moves:
            return self.source_moves[ticker], "Bloomberg"
        if self.proxy_fn is not None:
            p = self.proxy_fn(ticker, report_date)
            if p is not None:
                return p, "PROXY"
        return None, "MISSING"
