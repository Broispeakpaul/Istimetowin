"""Event-driven momentum strategy for small/mid caps on Interactive Brokers.

Educational tooling, not financial advice. Test on a paper account first.
"""
from .catalysts import Catalyst, CatalystType, load_catalysts
from .config import StrategyConfig
from .planner import Holding, OrderIntent, Plan, build_plan

__all__ = [
    "Catalyst", "CatalystType", "load_catalysts", "StrategyConfig",
    "Holding", "OrderIntent", "Plan", "build_plan",
]
