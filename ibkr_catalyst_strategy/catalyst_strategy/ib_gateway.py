"""Interactive Brokers connection via ib_async (TWS or IB Gateway).

Order modes:
    preview  - nothing is sent to IB; the plan is only displayed.
    stage    - orders are sent with transmit=False. They appear in TWS's
               Orders panel for you to review and click "Transmit".
    live     - orders are transmitted immediately.

Ports: TWS paper 7497, TWS live 7496, IB Gateway paper 4002, live 4001.
Enable API access in TWS: Global Configuration > API > Settings >
"Enable ActiveX and Socket Clients" and untick "Read-Only API" for stage/live.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .config import StrategyConfig
from .planner import Holding, OrderIntent

log = logging.getLogger(__name__)

ORDER_REF = "catalyst-momo"
PROTECTIVE_TYPES = {"STP", "STP LMT", "TRAIL", "TRAIL LIMIT"}
LIVE_PORTS = {7496, 4001}


def build_orders(intent: OrderIntent, next_id: Callable[[], int], transmit: bool, account: str = "") -> list:
    """Translate an OrderIntent into ib_async Order objects (parent first).

    Pure apart from the id generator, so it can be unit-tested offline.
    """
    from ib_async import LimitOrder, MarketOrder, Order, StopOrder

    ref = f"{ORDER_REF}:{intent.reason[:40]}"
    common = dict(account=account, orderRef=ref)
    qty = intent.quantity

    if intent.action == "BUY":
        has_child = intent.attach_stop is not None or intent.attach_trail_pct is not None
        parent = LimitOrder("BUY", qty, intent.limit_price, orderId=next_id(), tif="DAY",
                            transmit=transmit and not has_child, **common)
        orders = [parent]
        if intent.attach_stop is not None:
            orders.append(StopOrder("SELL", qty, intent.attach_stop, orderId=next_id(),
                                    parentId=parent.orderId, tif="GTC", transmit=transmit, **common))
        elif intent.attach_trail_pct is not None:
            orders.append(Order(action="SELL", orderType="TRAIL", totalQuantity=qty,
                                trailingPercent=round(intent.attach_trail_pct * 100, 2),
                                orderId=next_id(), parentId=parent.orderId, tif="GTC",
                                transmit=transmit, **common))
        return orders

    if intent.order_type == "MKT":
        return [MarketOrder("SELL", qty, orderId=next_id(), tif="DAY", transmit=transmit, **common)]
    if intent.order_type == "STP":
        return [StopOrder("SELL", qty, intent.stop_price, orderId=next_id(), tif="GTC",
                          transmit=transmit, **common)]
    if intent.order_type == "TRAIL":
        return [Order(action="SELL", orderType="TRAIL", totalQuantity=qty,
                      trailingPercent=round(intent.trail_pct * 100, 2), orderId=next_id(),
                      tif="GTC", transmit=transmit, **common)]
    raise ValueError(f"Unsupported order: {intent}")


@dataclass
class AccountState:
    account: str
    equity: float
    cash: float
    holdings: dict[str, Holding]
    other_positions: list[str]


class IBGateway:
    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 17,
                 account: str = "", market_data_type: int = 1, volume_multiplier: float = 1.0):
        from ib_async import IB

        self.ib = IB()
        self.host, self.port, self.client_id = host, port, client_id
        self.account = account
        self.market_data_type = market_data_type  # 1 live, 3 delayed
        self.volume_multiplier = volume_multiplier
        self._contracts: dict[str, object] = {}

    # ---- connection -------------------------------------------------------
    def connect(self, readonly: bool = False) -> "IBGateway":
        self.ib.connect(self.host, self.port, clientId=self.client_id, readonly=readonly, timeout=15)
        self.ib.reqMarketDataType(self.market_data_type)
        if not self.account:
            accounts = self.ib.managedAccounts()
            self.account = accounts[0] if accounts else ""
        log.info("Connected to IB %s:%s account %s", self.host, self.port, self.account)
        return self

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.disconnect()

    @property
    def is_live_port(self) -> bool:
        return self.port in LIVE_PORTS

    # ---- contracts & data -------------------------------------------------
    def stock(self, symbol: str):
        from ib_async import Stock

        if symbol not in self._contracts:
            contract = Stock(symbol, "SMART", "USD")
            qualified = self.ib.qualifyContracts(contract)
            if not qualified:
                raise LookupError(f"IB could not qualify {symbol}")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    def daily_bars(self, symbol: str, duration: str = "1 Y") -> pd.DataFrame | None:
        from ib_async import util

        try:
            bars = self.ib.reqHistoricalData(
                self.stock(symbol), endDateTime="", durationStr=duration,
                barSizeSetting="1 day", whatToShow="TRADES", useRTH=True, formatDate=1,
            )
        except Exception as exc:  # noqa: BLE001 - IB raises many error types
            log.warning("%s: history request failed: %s", symbol, exc)
            return None
        df = util.df(bars)
        if df is None or df.empty:
            log.warning("%s: no historical data returned", symbol)
            return None
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                "close": "Close", "volume": "Volume"})
        df.index = pd.to_datetime(df["date"])
        df["Volume"] = df["Volume"].astype(float) * self.volume_multiplier
        return df[["Open", "High", "Low", "Close", "Volume"]]

    def many_bars(self, symbols: Iterable[str], duration: str = "1 Y",
                  cache_dir: str | None = None) -> dict[str, pd.DataFrame]:
        """Daily bars for many symbols, cached per day so re-runs are fast."""
        folder = Path(cache_dir) / date.today().isoformat() if cache_dir else None
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
        out = {}
        for sym in dict.fromkeys(symbols):
            path = folder / f"{sym}.csv" if folder else None
            if path and path.exists():
                out[sym] = pd.read_csv(path, index_col=0, parse_dates=True)
                continue
            df = self.daily_bars(sym, duration)
            if df is not None:
                out[sym] = df
                if path:
                    df.to_csv(path)
            self.ib.sleep(0.05)  # stay well inside IB's historical-data pacing limits
        return out

    def scan_universe(self, cfg: StrategyConfig, rows: int = 50,
                      scan_codes: Iterable[str] = ("MOST_ACTIVE_USD", "HOT_BY_VOLUME", "HIGH_VS_26W_HL",
                                                   "TOP_PERC_GAIN", "TOP_PERC_LOSE")) -> list[str]:
        """Run IB market scanners pre-filtered to the size/price universe.

        The mix of most-active, gainers and losers gives the relative-strength
        ranking a reference set that is not only this week's winners.
        """
        from ib_async import ScannerSubscription, TagValue

        filters = [
            TagValue("priceAbove", str(cfg.min_price)),
            TagValue("marketCapAbove1e6", str(int(cfg.min_market_cap / 1e6))),
            TagValue("marketCapBelow1e6", str(int(cfg.max_market_cap / 1e6))),
            TagValue("avgVolumeAbove", "200000"),
        ]
        symbols: list[str] = []
        for code in scan_codes:
            sub = ScannerSubscription(instrument="STK", locationCode="STK.US.MAJOR",
                                      scanCode=code, numberOfRows=rows, stockTypeFilter="CORP")
            try:
                data = self.ib.reqScannerData(sub, [], filters)
            except Exception as exc:  # noqa: BLE001
                log.warning("scanner %s failed: %s", code, exc)
                continue
            symbols += [d.contractDetails.contract.symbol for d in data]
        return list(dict.fromkeys(symbols))

    def market_cap(self, symbol: str) -> float | None:
        """Market cap from IB fundamentals (needs a fundamentals subscription)."""
        try:
            xml = self.ib.reqFundamentalData(self.stock(symbol), "ReportSnapshot")
        except Exception:  # noqa: BLE001
            return None
        m = re.search(r'FieldName="MKTCAP"[^>]*>([\d.]+)<', xml or "")
        return float(m.group(1)) * 1e6 if m else None

    # ---- account ----------------------------------------------------------
    def account_state(self) -> AccountState:
        values = {(v.tag, v.currency): v.value for v in self.ib.accountSummary(self.account)}

        def tag(name: str) -> float:
            for cur in ("USD", "BASE", ""):
                if (name, cur) in values:
                    return float(values[(name, cur)])
            return 0.0

        equity, cash = tag("NetLiquidation"), tag("TotalCashValue")

        holdings: dict[str, Holding] = {}
        other: list[str] = []
        for p in self.ib.positions(self.account):
            if p.position == 0:
                continue
            c = p.contract
            if c.secType == "STK" and p.position > 0:
                holdings[c.symbol] = Holding(c.symbol, float(p.position), float(p.avgCost))
            else:
                other.append(f"{c.localSymbol or c.symbol} {c.secType} x{p.position:g}")

        self.ib.reqAllOpenOrders()
        for trade in self.ib.openTrades():
            o, c = trade.order, trade.contract
            h = holdings.get(c.symbol)
            if h is None or c.secType != "STK" or o.action != "SELL" or o.orderType not in PROTECTIVE_TYPES:
                continue
            h.stop_qty += float(trade.remaining())
            if o.orderType.startswith("TRAIL"):
                h.trailing = True
                pct = o.trailingPercent
                h.trail_pct = pct / 100 if pct and pct < 1e300 else None  # IB uses UNSET_DOUBLE
                stop = o.trailStopPrice
            else:
                stop = o.auxPrice
            if stop and stop < 1e300:
                h.stop_price = max(h.stop_price or 0, float(stop))
        return AccountState(self.account, equity, cash, holdings, other)

    def _protective_trades(self, symbol: str) -> list:
        return [t for t in self.ib.openTrades()
                if t.contract.symbol == symbol and t.contract.secType == "STK"
                and t.order.action == "SELL" and t.order.orderType in PROTECTIVE_TYPES]

    # ---- options ----------------------------------------------------------
    def resolve_call(self, intent: OrderIntent):
        """Pick the first expiry after the catalyst and the nearest OTM strike."""
        from ib_async import Option

        spec = intent.option
        stk = self.stock(intent.symbol)
        chains = self.ib.reqSecDefOptParams(stk.symbol, "", stk.secType, stk.conId)
        chain = next((c for c in chains if c.exchange == "SMART"), chains[0] if chains else None)
        if chain is None:
            return None, "no option chain"
        expiries = sorted(e for e in chain.expirations
                          if datetime.strptime(e, "%Y%m%d").date() >= spec.min_expiry)
        strikes = sorted(s for s in chain.strikes if s >= spec.target_strike)
        if not expiries or not strikes:
            return None, "no expiry/strike after catalyst"
        opt = Option(intent.symbol, expiries[0], strikes[0], "C", "SMART",
                     tradingClass=chain.tradingClass, multiplier=chain.multiplier)
        if not self.ib.qualifyContracts(opt):
            return None, "option did not qualify"
        [ticker] = self.ib.reqTickers(opt)
        price = ticker.ask if ticker.ask and ticker.ask > 0 else ticker.marketPrice()
        if not price or math.isnan(price) or price <= 0:
            return None, "no option quote (check market data subscription)"
        mult = float(opt.multiplier or 100)
        qty = math.floor(spec.budget / (price * mult))
        if qty < 1:
            return None, f"premium ${price * mult:,.0f} exceeds budget ${spec.budget:,.0f}"
        return (opt, qty, round(price, 2)), f"{opt.localSymbol} x{qty} @ {price:.2f}"

    # ---- execution --------------------------------------------------------
    def execute(self, intents: list[OrderIntent], mode: str) -> list[str]:
        """Send the plan's orders to IB. Returns a human-readable log."""
        from ib_async import LimitOrder

        if mode == "preview":
            return ["preview mode: no orders sent"]
        transmit = mode == "live"
        next_id = self.ib.client.getReqId
        results = []
        working = {(t.contract.symbol, t.order.action) for t in self.ib.openTrades()
                   if (t.order.orderRef or "").startswith(ORDER_REF) and t.order.orderType in ("LMT", "MKT")}
        for intent in intents:
            if intent.order_type in ("LMT", "MKT") and (intent.symbol, intent.action) in working:
                results.append(f"SKIP {intent.action} {intent.symbol}: an order from this strategy is already working")
                continue
            try:
                if intent.sec_type == "OPT":
                    resolved, msg = self.resolve_call(intent)
                    if resolved is None:
                        results.append(f"SKIP {intent.symbol} calls: {msg}")
                        continue
                    opt, qty, px = resolved
                    order = LimitOrder("BUY", qty, px, tif="DAY", transmit=transmit,
                                       account=self.account, orderRef=f"{ORDER_REF}:calls")
                    self.ib.placeOrder(opt, order)
                    results.append(f"{'SENT' if transmit else 'STAGED'} BUY {msg}")
                    continue

                contract = self.stock(intent.symbol)
                replaces_protection = intent.action == "SELL" and (intent.order_type == "MKT" or intent.modify_stops)
                if replaces_protection:
                    old = self._protective_trades(intent.symbol)
                    if transmit:
                        for t in old:
                            self.ib.cancelOrder(t.order)
                    elif old:
                        results.append(f"NOTE {intent.symbol}: cancel {len(old)} existing stop order(s) "
                                       "in TWS when you transmit")
                for order in build_orders(intent, next_id, transmit, self.account):
                    self.ib.placeOrder(contract, order)
                verb = "SENT" if transmit else "STAGED"
                px = intent.limit_price or intent.stop_price or ""
                results.append(f"{verb} {intent.action} {intent.quantity:g} {intent.symbol} "
                               f"{intent.order_type} {px} ({intent.reason})")
            except Exception as exc:  # noqa: BLE001
                results.append(f"ERROR {intent.symbol}: {exc}")
        self.ib.sleep(1)
        return results
