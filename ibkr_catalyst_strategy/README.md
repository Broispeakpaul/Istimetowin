# Event-Driven Momentum on Interactive Brokers

A long-only, concentrated strategy for small/mid caps, built for a 3-month competition. It connects to TWS or IB Gateway through the IB API (`ib_async`), and each run does the following:

1. It scans IB for liquid $300M–$20B stocks and ranks them by 3–6 month relative strength.
2. It combines those ranks with **your catalyst list** (earnings beats, 13D activists, cash deals, spin-offs and buybacks).
3. It checks the SPY 200-day regime filter and which competition week you are in.
4. It shows a dashboard of positions, ranked candidates and proposed orders.
5. It can optionally send the orders to IB as parent orders with attached protective stops.

> Educational tooling, not financial advice. **Start on a paper account (port 7497)** and use `--mode stage` so every order waits in TWS for you to click *Transmit*.

## How the rules map to code

| Rule | Where | Default |
|---|---|---|
| Market cap $300M–$10B, ADV > $5M, price > $5 | `screener.py` | `min_market_cap`, `min_avg_dollar_volume`, `min_price` |
| 3–6 month RS in the top 30% and beating IWM | `screener.py`, `indicators.rs_raw` | `rs_min_percentile=0.70` |
| Fresh catalyst required (PEAD / ACTIVIST / CASH_MA / CORP_ACTION) | `catalysts.py` | `catalyst_max_age_days` |
| Short interest > 10% (optional) | score bonus, or hard filter | `require_short_interest=False` |
| Entry: breakout above the 20-day high on volume, or a pullback to the 20DMA after the catalyst | `signals.entry_signal` | `breakout_lookback=20` |
| Cash M&A: buy if the spread is ≥ 2% and the deal closes inside the window; exit when < 0.5% is left | `signals.py` | `ma_min_spread`, `ma_exit_spread` |
| Hard stop 10–14.5% below entry (3×ATR, clamped), or a trailing stop | `signals.stop_distance` | `--trailing` |
| Exit: close below the 50DMA, the event completes, or a binary event is ahead | `signals.exit_reasons` | `exit_ma=50` |
| Don't hold through binary events (unless conviction is 3 **and** the position is ≤ 5%) | `signals.exit_reasons` | `binary_hold_max_position_pct` |
| 8–12 positions, max 15% each | `planner.py`, `risk.position_size` | `max_positions`, `max_position_pct` |
| Risk 1% / 1.5% / 2.5% of equity by conviction 1/2/3, hard cap 3% | `risk.position_size` | `risk_pct_by_conviction` |
| Deploy in tranches and add only to winners | `planner.py` | `tranches=2` |
| SPY > 200DMA → 15% cash; below → 40% cash, trimming the weakest names | `risk.market_regime`, `planner.py` | `cash_reserve_risk_on/off` |
| Options: long OTM calls expiring after the event, 1.5% premium each, only for binary events | `planner.py`, `ib_gateway.resolve_call` | `--options`, `use_options` in CSV |
| Weeks 1–2 screen only; weeks 3–10 deploy; weeks 11–12 tighten stops to 7% and take no new trades unless imminent + conviction 3 | `risk.competition_phase` | `--start-date` |

## Setup

```bash
cd ibkr_catalyst_strategy
pip install -r requirements.txt
python -m pytest            # offline tests
python -m catalyst_strategy demo   # see the dashboard with synthetic data
```

In TWS, go to **Global Configuration → API → Settings**:
- tick *Enable ActiveX and Socket Clients*;
- note the socket port (paper **7497**, live 7496; IB Gateway paper 4002, live 4001);
- untick *Read-Only API* if you want `stage` or `live` modes.

Market data: you need US equity data for the scanner and history. Use `--delayed` if you don't subscribe. Options need an OPRA subscription for quotes.

## Weekly workflow

```bash
# Week 1: find strong names, then research which have real catalysts
python -m catalyst_strategy scan --port 7497

# Record them in your catalyst file
cp catalysts_example.csv catalysts.csv   # then edit

# Any day: display the plan (read-only, nothing sent)
python -m catalyst_strategy --start-date 2026-10-05 plan --catalysts catalysts.csv

# Rebalance day: put the orders into TWS without transmitting them, review, click Transmit
python -m catalyst_strategy --start-date 2026-10-05 plan --catalysts catalysts.csv --mode stage

# Or transmit directly (asks you to type YES)
python -m catalyst_strategy --start-date 2026-10-05 plan --catalysts catalysts.csv --mode live
```

Global flags go **before** the sub-command: `--config my.json`, `--start-date`, `--options`, `--trailing`, `--export reports/`.
IB flags go after it: `--host`, `--port`, `--client-id`, `--account`, `--delayed`, `--rows`, `--no-scan`, `--volume-multiplier`.

Run `plan` daily to catch exits (50DMA breaks, binary events, completed deals, regime flips). Run it with `--mode stage` weekly for new entries.

## Catalyst CSV

The IB API does not provide 13D filings, earnings surprises or deal terms, so you keep these in a CSV (see `catalysts_example.csv`). Only `symbol,type,announced` are required.

| column | meaning |
|---|---|
| `type` | `PEAD`, `ACTIVIST`, `CASH_MA`, `CORP_ACTION` (spin-off, asset sale, buyback, major contract win) |
| `announced` | date of the event (YYYY-MM-DD); drives freshness and the "pullback after catalyst" check |
| `conviction` | 1–3, which sets the risk per trade (1% / 1.5% / 2.5%) |
| `event_date` | an upcoming **binary** event (earnings, vote, FDA). The planner exits before it, or buys calls instead if `use_options=yes` and `--options` is on |
| `offer_price`, `expected_close` | required for `CASH_MA` |
| `earnings_surprise_pct`, `guidance_raised`, `revisions_up` | raise the PEAD score |
| `short_interest` | fraction of float (0.14 = 14%) |
| `market_cap` | USD. Used when IB fundamentals aren't available and the symbol didn't come from the scanner |
| `completed` | `yes` forces an exit (the deal closed, the spin-off is done) |

## Orders sent to IB

- **Entry**: a DAY `LMT` buy at last price + 0.5%, with a child GTC `STP` sell at the stop (or `TRAIL` with `--trailing`). The parent/child pair is sent together.
- **Exit**: a `MKT` sell. In `live` mode, the symbol's existing protective orders are cancelled first. In `stage` mode the dashboard tells you to cancel them yourself.
- **Stop changes** (missing stop, harvest-phase tightening): the old protective orders are replaced.
- **Calls**: a `LMT` buy at the ask. The expiry is the first one at least 14 days after `event_date`, the strike is the first one ≥ spot + 5%, and the quantity is sized to 1.5% of equity.
- Every order carries `orderRef = catalyst-momo:<reason>`, so you can filter for it in TWS. BUY orders for a symbol that already has a working strategy order are skipped.

## Configuration

Every threshold is a field on `StrategyConfig` (`catalyst_strategy/config.py`). Override any of them with a JSON file:

```bash
python -m catalyst_strategy --config config_example.json plan --catalysts catalysts.csv
```

## Known limitations

- **Relative strength** is ranked against the symbols the IB scanners return (most active, hot by volume, 26-week highs, top gainers and losers). That is a proxy for "the market", not an index-wide ranking. Use `--rows 50` (the maximum) for a bigger reference set.
- **Volume units**: some TWS versions report daily US stock volume in round lots. If the ADV figures look 100× too small, add `--volume-multiplier 100`.
- **Fill prices**: the planner sizes from the last daily close. Check limit prices before transmitting in fast markets.
- **Options**: the planner only opens call positions. Close them yourself after the event.
- **No backtest**: this is a decision and execution tool. Validate the rules on paper before using real money.
