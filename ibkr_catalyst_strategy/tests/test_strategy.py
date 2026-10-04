from datetime import date, timedelta
from itertools import count
from pathlib import Path

import pytest
from rich.console import Console

from catalyst_strategy.catalysts import Catalyst, CatalystType, load_catalysts
from catalyst_strategy.config import StrategyConfig
from catalyst_strategy.dashboard import export, render
from catalyst_strategy.demo import demo_inputs, make_bars, run_demo, with_breakout, with_fade, with_pullback
from catalyst_strategy.ib_gateway import build_orders
from catalyst_strategy.planner import OrderIntent, build_plan
from catalyst_strategy.risk import Phase, competition_phase, market_regime, position_size
from catalyst_strategy.signals import entry_signal, exit_reasons, stop_distance

TODAY = date(2026, 9, 28)
ROOT = Path(__file__).resolve().parents[1]


def _plan(cfg=None, **overrides):
    d = demo_inputs(TODAY)
    d.update(overrides)
    return build_plan(TODAY, cfg or StrategyConfig(options_enabled=True), d["equity"], d["cash"],
                      d["holdings"], d["bars"], d["regime_bars"], d["benchmark_bars"], d["catalysts"])


# ---- config / sizing / regime -------------------------------------------

def test_config_rejects_risk_above_ceiling():
    with pytest.raises(ValueError):
        StrategyConfig(risk_pct_by_conviction={1: 0.01, 2: 0.02, 3: 0.05})
    with pytest.raises(ValueError):
        StrategyConfig(max_position_pct=0.25)


def test_stop_distance_is_clamped_to_10_to_14_5_pct():
    cfg = StrategyConfig()
    assert stop_distance(100, 0.5, cfg) == pytest.approx(0.10)
    assert stop_distance(100, 20, cfg) == pytest.approx(0.145)
    assert stop_distance(100, 4, cfg) == pytest.approx(0.12)


def test_position_size_risk_and_cap():
    cfg = StrategyConfig()
    s = position_size(100_000, 100, 88, conviction=2, cfg=cfg, budget=1e9)
    assert s.shares == 125 and s.limited_by == "risk"          # 1.5% / 12% = 12.5% weight
    assert s.risk_pct == pytest.approx(0.015)
    s = position_size(100_000, 100, 90, conviction=3, cfg=cfg, budget=1e9)
    assert s.shares == 150 and s.limited_by == "position cap"  # 2.5% / 10% = 25% -> capped 15%
    s = position_size(100_000, 100, 90, conviction=2, cfg=cfg, budget=5_000)
    assert s.shares == 50 and s.limited_by == "cash budget"


def test_regime_filter():
    cfg = StrategyConfig()
    up = make_bars(TODAY, drift=0.002, vol=0.005, seed=1)
    down = make_bars(TODAY, drift=-0.002, vol=0.005, seed=1)
    assert market_regime(up, cfg).risk_on and market_regime(up, cfg).cash_target == 0.15
    assert not market_regime(down, cfg).risk_on and market_regime(down, cfg).cash_target == 0.40


def test_competition_phases():
    cfg = StrategyConfig(competition_start=date(2026, 10, 5))
    assert competition_phase(cfg, date(2026, 10, 7))[0] is Phase.SETUP        # week 1
    assert competition_phase(cfg, date(2026, 10, 19))[0] is Phase.DEPLOY       # week 3
    assert competition_phase(cfg, date(2026, 12, 14)) == (Phase.HARVEST, 11)
    assert competition_phase(cfg, date(2027, 1, 5))[0] is Phase.FINISHED
    assert competition_phase(StrategyConfig(), TODAY)[0] is Phase.OPEN


# ---- catalysts ------------------------------------------------------------

def test_example_csv_loads():
    cats = load_catalysts(ROOT / "catalysts_example.csv")
    assert set(cats) == {"XMPLA", "XMPLB", "XMPLC", "XMPLD", "XMPLE"}
    assert cats["XMPLC"].type is CatalystType.CASH_MA and cats["XMPLC"].offer_price == 52.5
    assert cats["XMPLE"].use_options and cats["XMPLE"].event_date == date(2026, 10, 15)


def test_cash_deal_row_requires_offer_price(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("symbol,type,announced\nABC,CASH_MA,2026-09-01\n")
    with pytest.raises(ValueError, match="offer_price"):
        load_catalysts(p)


# ---- signals ----------------------------------------------------------------

def test_entry_triggers():
    cfg = StrategyConfig()
    cat = Catalyst("X", CatalystType.PEAD, TODAY - timedelta(days=10))
    brk = with_breakout(make_bars(TODAY, drift=0.002, seed=3))
    assert entry_signal(brk, cat, cfg, TODAY).trigger == "BREAKOUT"
    pull = with_pullback(make_bars(TODAY, start=25, drift=0.0032, seed=27))
    assert entry_signal(pull, cat, cfg, TODAY).trigger == "PULLBACK_20DMA"

    flat = make_bars(TODAY, start=48, drift=0, vol=0.001, seed=4)
    last = float(flat["Close"].iloc[-1])
    deal = Catalyst("D", CatalystType.CASH_MA, TODAY, offer_price=last * 1.05)
    assert entry_signal(flat, deal, cfg, TODAY).trigger == "DEAL_SPREAD"
    thin = Catalyst("D", CatalystType.CASH_MA, TODAY, offer_price=last * 1.01)
    assert entry_signal(flat, thin, cfg, TODAY).trigger is None


def test_exit_rules():
    cfg = StrategyConfig()
    fade = with_fade(make_bars(TODAY, drift=0.001, seed=9))
    cat = Catalyst("F", CatalystType.PEAD, TODAY - timedelta(days=20))
    assert any("MOMENTUM_FADE" in r for r in exit_reasons(fade, cat, cfg, TODAY, 0.1, None))

    strong = make_bars(TODAY, drift=0.003, vol=0.005, seed=2)
    binary = Catalyst("B", CatalystType.PEAD, TODAY - timedelta(days=20), conviction=2,
                      event_date=TODAY + timedelta(days=2))
    assert any("BINARY_EVENT" in r for r in exit_reasons(strong, binary, cfg, TODAY, 0.10, None))
    binary.conviction = 3  # high conviction AND small size may hold through
    assert exit_reasons(strong, binary, cfg, TODAY, 0.04, None) == []

    last = float(strong["Close"].iloc[-1])
    done = Catalyst("D", CatalystType.CASH_MA, TODAY, offer_price=last * 1.002)
    assert any("EVENT_COMPLETE" in r for r in exit_reasons(strong, done, cfg, TODAY, 0.1, None))


# ---- planner ----------------------------------------------------------------

def test_plan_respects_risk_and_exposure_limits():
    cfg = StrategyConfig(options_enabled=True)
    plan = _plan(cfg)
    orders = {o.symbol: o for o in plan.orders}

    assert orders["FADE"].action == "SELL" and orders["FADE"].order_type == "MKT"
    assert orders["WINR"].reason == "add tranche to winner"
    assert orders["EARN"].sec_type == "OPT"                   # binary event -> calls only
    assert "TINY" not in orders                                # fails price/liquidity/cap screen

    buys = [o for o in plan.orders if o.action == "BUY" and o.sec_type == "STK"]
    assert buys and all(o.attach_stop and o.attach_stop < o.limit_price for o in buys)
    for c in plan.candidates:
        assert c.risk_pct <= cfg.max_risk_pct + 1e-9
        assert c.position_pct <= cfg.max_position_pct + 1e-9
        if c.stop:
            assert cfg.stop_pct_min - 1e-3 <= 1 - c.stop / c.entry <= cfg.stop_pct_max + 1e-3

    spend = sum(o.quantity * o.limit_price for o in buys) + sum(o.option.budget for o in plan.orders if o.option)
    kept_value = sum(p.shares * p.last for p in plan.positions if p.action != "EXIT")
    assert kept_value + spend <= plan.max_invested + 1
    assert spend <= 78_000


def test_risk_off_trims_and_raises_cash():
    down = make_bars(TODAY, start=450, drift=-0.002, vol=0.006, seed=5)
    cfg = StrategyConfig(cash_reserve_risk_off=0.95)
    plan = _plan(cfg, regime_bars=down)
    assert not plan.regime.risk_on and plan.max_invested == pytest.approx(5_000)
    assert any("REGIME_TRIM" in o.reason for o in plan.orders)
    spend = sum(o.quantity * (o.limit_price or 0) for o in plan.orders if o.action == "BUY")
    spend += sum(o.option.budget for o in plan.orders if o.option)
    assert spend <= plan.max_invested


def test_setup_phase_places_no_new_buys():
    cfg = StrategyConfig(competition_start=TODAY - timedelta(days=3))
    plan = _plan(cfg)
    assert plan.phase is Phase.SETUP
    assert not any(o.action == "BUY" for o in plan.orders)
    assert any(c.status.startswith("READY") for c in plan.candidates)


def test_harvest_phase_tightens_stops_and_blocks_entries():
    cfg = StrategyConfig(competition_start=TODAY - timedelta(weeks=10, days=1))
    plan = _plan(cfg)
    assert plan.phase is Phase.HARVEST
    winr = next(o for o in plan.orders if o.symbol == "WINR")
    assert winr.order_type == "STP" and winr.modify_stops
    assert not any(o.action == "BUY" for o in plan.orders)


def test_trailing_stop_mode():
    cfg = StrategyConfig(use_trailing_stop=True)
    plan = _plan(cfg)
    assert any(o.order_type == "TRAIL" and o.symbol == "WINR" for o in plan.orders)
    assert all(o.attach_trail_pct for o in plan.orders if o.action == "BUY" and o.sec_type == "STK"
               and o.reason != "add tranche to winner")


# ---- IB order construction ------------------------------------------------------

def test_bracket_order_transmits_on_child_only():
    ids = count(100)
    intent = OrderIntent("ABC", "BUY", 50, "LMT", "BREAKOUT", limit_price=20.1, attach_stop=17.7)
    parent, child = build_orders(intent, lambda: next(ids), transmit=True)
    assert (parent.orderType, parent.lmtPrice, parent.transmit) == ("LMT", 20.1, False)
    assert (child.orderType, child.auxPrice, child.parentId, child.tif, child.transmit) == \
        ("STP", 17.7, parent.orderId, "GTC", True)

    parent, child = build_orders(intent, lambda: next(ids), transmit=False)  # stage mode
    assert not parent.transmit and not child.transmit


def test_exit_and_trail_orders():
    ids = count(1)
    [mkt] = build_orders(OrderIntent("ABC", "SELL", 10, "MKT", "fade"), lambda: next(ids), True)
    assert (mkt.orderType, mkt.action, mkt.totalQuantity) == ("MKT", "SELL", 10)
    [trail] = build_orders(OrderIntent("ABC", "SELL", 10, "TRAIL", "t", trail_pct=0.12), lambda: next(ids), True)
    assert trail.orderType == "TRAIL" and trail.trailingPercent == 12.0


# ---- dashboard ----------------------------------------------------------------

def test_dashboard_renders_and_exports(tmp_path):
    plan = run_demo(today=TODAY)
    for width in (90, 180):
        console = Console(record=True, width=width)
        render(plan, console, extra_notes=["test"], exec_log=["STAGED BUY 1 X"])
        text = console.export_text()
        assert "Proposed orders" in text and "RISK-ON" in text
    path = export(plan, tmp_path)
    assert path.exists() and (tmp_path / f"orders_{TODAY}.csv").exists()


# ---- IB execution path against a fake client ------------------------------------

class _FakeIB:
    def __init__(self, open_trades=()):
        from types import SimpleNamespace
        self._ids = count(1)
        self.client = SimpleNamespace(getReqId=lambda: next(self._ids))
        self.placed, self.cancelled, self._open = [], [], list(open_trades)

    def openTrades(self):
        return self._open

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))

    def cancelOrder(self, order):
        self.cancelled.append(order)

    def qualifyContracts(self, *cs):
        return list(cs)

    def sleep(self, _):
        pass


def _gateway(fake):
    from catalyst_strategy.ib_gateway import IBGateway
    gw = IBGateway()
    gw.ib = fake
    return gw


def _trade(symbol, action, order_type, ref="catalyst-momo:x"):
    from types import SimpleNamespace
    from ib_async import Order, Stock
    return SimpleNamespace(contract=Stock(symbol, "SMART", "USD"),
                           order=Order(action=action, orderType=order_type, orderRef=ref))


def test_execute_stage_mode_never_transmits_or_cancels():
    fake = _FakeIB([_trade("OLD", "SELL", "STP")])
    intents = [OrderIntent("NEW", "BUY", 10, "LMT", "b", limit_price=10, attach_stop=8.8),
               OrderIntent("OLD", "SELL", 5, "MKT", "fade")]
    log = _gateway(fake).execute(intents, "stage")
    assert len(fake.placed) == 3 and not any(o.transmit for _, o in fake.placed)
    assert fake.cancelled == [] and any("cancel 1 existing stop" in line for line in log)


def test_execute_live_cancels_old_stops_and_skips_duplicates():
    fake = _FakeIB([_trade("OLD", "SELL", "STP"), _trade("DUP", "BUY", "LMT")])
    intents = [OrderIntent("OLD", "SELL", 5, "MKT", "fade"),
               OrderIntent("DUP", "BUY", 10, "LMT", "b", limit_price=10, attach_stop=8.8)]
    log = _gateway(fake).execute(intents, "live")
    assert len(fake.cancelled) == 1
    assert [o.orderType for _, o in fake.placed] == ["MKT"] and fake.placed[0][1].transmit
    assert any(line.startswith("SKIP BUY DUP") for line in log)
    assert _gateway(_FakeIB()).execute(intents, "preview") == ["preview mode: no orders sent"]
