# BGTC 2026 Earnings Catalyst Platform

Decision support for our team in the Bloomberg Global Trading Challenge 2026. Each trading day it:

1. finds WLS members whose **day one** after an earnings report is today;
2. applies the playbook's v2 entry checks and ranks the names that pass;
3. sizes each candidate and splits it into two tranches;
4. checks the exit rules on our holdings;
5. checks the market regime;
6. writes an HTML report, a CSV with a reason for every pass or fail, and data for a Streamlit dashboard.

> **It never places orders and never connects to a broker.** Every trade is entered by hand in Bloomberg
> TMSG after a person has checked it. Competition rules: long only, no leverage, no ETFs, WLS members only,
> 20% max per position (the playbook caps at 15%).

**The playbook docx was not available when this was built.** The rules come from the build prompt. Every
judgement call is a setting in `config.yaml`, and each one is explained in [`docs/DESIGN.md`](docs/DESIGN.md),
together with the open questions for the team. Read that file first.

---

## 1. Setup (Windows)

```bat
cd bgtc_platform
scripts\setup_windows.bat
```

This creates `.venv` with Python 3.11, installs `requirements.txt`, runs the tests and runs the offline
demo. To do it by hand:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest              :: about 110 tests, no network needed
python -m screener demo       :: synthetic data -> reports\demo\2025-10-30_all.html
```

macOS and Linux work the same way, with `source .venv/bin/activate`.

## 2. Files you maintain by hand (`data/`)

| File | What goes in it |
|---|---|
| `wls_members.csv` | The universe: `bbg_ticker, yahoo_symbol, name, country, exchange, sector, theme, wls_weight` (plus optional `currency, lot_size`). The file in the repo is a **51-name sample with blank weights**. On the Terminal, rebuild it with `python -m screener universe --from-bloomberg`. Weights are never guessed: blank shows as MISSING. |
| `positions.csv` | Our fills ledger from TMSG: date, ticker, BUY/SELL, quantity, price, role (`catalyst`/`core`/`staging`), tranche, day_one_date, thesis_break. Holdings, cost and entry dates are worked out from it. |
| `earnings_overrides.csv` | Confirmed report dates and timing (BMO/AMC/DMH). These replace data-source dates within 7 days. |
| `manual_checks.csv` | Check 1 (revenue beat and guidance at or above consensus), check 5 (EPS revised up within 3 sessions), implied earnings move. A blank cell means PENDING. |
| `account.csv` | Optional cash and NAV snapshots from the competition screen. |

## 3. Daily use

```bat
python -m screener run --session asia                 :: latest closed session
python -m screener run --session us --date 2025-10-30 :: a specific session date (exchange-local)
python -m screener run --session europe --before-close 30   :: preliminary run 30 min before the close
python -m screener dashboard                          :: Streamlit dashboard
```

Each run writes:

* `reports/<date>_<session>.html`, the morning report: regime, action list, ranked candidates with share
  counts, the second tranche, exit alerts, portfolio risk, staging list and earnings calendar;
* `reports/<date>_<session>_signals.csv`, every day-one event with each check's PASS/FAIL/MISSING/PENDING and
  the reason;
* `reports/<date>_<session>_data/`, the tables the dashboard reads.

**Statuses you will see**

| Status / action | Meaning |
|---|---|
| `CANDIDATE` + `BUY TRANCHE 1 NEAR CLOSE` | All checks passed, check 1 included. |
| `CANDIDATE - CONFIRM CHECK 1` | The quantitative checks passed. Confirm the revenue beat and guidance before buying. |
| `REJECTED` / `REJECTED (THEME CAP)` | A rule failed. The reason column says which one and by how much. |
| `DATA MISSING` | A field was not available. Nothing is filled in by guesswork. |
| `BLOCKED (DE-RISK)` | WLS is below its 50-day average AND VIX is above 25. |
| `PRELIMINARY` | The run happened before the close, so the bar is partial. Partial volume is never extrapolated. |
| `PROVISIONAL` | The benchmark close for today was not out yet when the relative return was computed. |
| `TIMING UNCONFIRMED` | The report time is unknown. Both possible day ones are checked. Add the timing to `earnings_overrides.csv`. |

Tranche 2 on day three shows `BUY`, `BUY ONLY IF CHECK 5 CONFIRMED`, `SKIP` (close not above the day-one
low, or check 5 failed) or `BLOCKED (DE-RISK)`.

All times are shown in Hong Kong time. The exchange calendars handle the 2026 clock changes: Europe on
25 Oct (London close 23:30 → 00:30 HKT) and the US on 1 Nov (New York close 04:00 → 05:00 HKT).

## 4. Switching to Bloomberg

Check your university's terminal policy before pulling Bloomberg data into a script.

1. On the lab machine with a logged-in Terminal:
   ```bat
   pip install --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/ blpapi
   pip install xbbg
   ```
2. Check the field names under `bloomberg:` in `config.yaml` with `FLDS <GO>`, especially the earnings
   fields. The adapter was written without Terminal access and is tested only against a fake `blp` object.
3. Set `data_source: bloomberg` in `config.yaml`, or pass `--source bloomberg`.
4. Rebuild the universe: `python -m screener universe --from-bloomberg` (MEMB weights, GICS themes). Then
   check the `theme` column and the HK board lots.

Nothing in the rules changes. The adapter supplies WLS Index prices, member prices with split and dividend
adjustments, FX crosses, and report dates and times from EE/EVTS. Implied moves come from Bloomberg only if
you set `bloomberg.implied_move_field`. Cached Bloomberg data stays in `data/cache/bloomberg/` on that
machine.

With Yahoo, the benchmark is the **VT ETF**, and every report carries a red banner saying so.

## 5. Scheduling (Windows Task Scheduler)

```bat
scripts\register_tasks.bat
```

This creates the tasks below in the `BGTC` folder. Times are HKT, and the PC clock is assumed to be HKT.

| Task | When | Purpose |
|---|---|---|
| `asia_preclose` | Mon–Fri 15:00, waits until 30 min before the HK close | "Buy near the close" list for Asia |
| `asia_final` | Mon–Fri 18:20 | Final Asia report (after the India close) |
| `europe_preclose` | Mon–Fri 22:30, waits until 30 min before the London close | Pre-close list for Europe |
| `europe_final` | Tue–Sat 00:50 | Final Europe report |
| `us_preclose` | Tue–Sat 03:00, waits until 30 min before the NY close | Pre-close list for the US |
| `us_final` | Tue–Sat 06:00 | Final US report, ready for the morning |

The pre-close tasks start early and the CLI waits for the real close time, so the US and European clock
changes need no edits. Output is appended to `logs/<session>.log`. To remove the tasks:
`schtasks /Delete /TN "BGTC\*" /F`.

To run them by hand: `scripts\run_session.bat us`.

## 6. Backtest

```bat
python -m screener backtest events  --start 2024-01-01 --end 2025-10-31
python -m screener backtest contest --start 2025-10-06
python -m screener backtest contest --start 2024-01-08 --end 2025-09-29 --rolling
```

* **Event study:** every past report goes through the *same* entry-check function as the live engine. The
  output gives forward relative returns (1/5/10/20/25 sessions) by reaction bucket, and shows what each
  filter adds.
* **Contest simulator:** five weeks, calling `engine.run_day()` for each session in Asia → Europe → US order,
  with fills at the close. No leverage, `cost_bps` per trade, and idle cash earning the benchmark (no ETFs
  are allowed, so this stands in for a heavyweight tilt).
* **Playbook Tables 1, 4 and 5:** copy the published numbers into
  `screener/backtest/playbook_reference.yaml`. The backtest then prints playbook, ours and the difference.
  The file is empty until someone does, because nothing was estimated.

Caveats, which are printed with every result: results are quant-only (manual checks are assumed PASS),
there is survivorship bias (today's membership), Yahoo report timing is incomplete, and the Yahoo
benchmark is VT.

## 7. Acceptance test: 30 October 2025

```bat
python -m screener run --session all --date 2025-10-30
```

You need network access for this; it could not be run where the platform was built. It should list every
member whose day one was 30 Oct 2025: after-close reports from 29 Oct, plus before-open reports on 30 Oct
on each exchange's own calendar. Each name gets its day-one date, size and reasons. After-close reports
*on* 30 Oct (day one 31 Oct) must **not** appear.

`tests/test_acceptance_2025_10_30.py` rehearses the same run offline with synthetic prices. It covers day-one
dates across the US, Korea, Japan and London, ranking, the merged semis-and-software theme cap, ATR sizes,
board lots and reasons.

Before relying on the live run:

1. Put the real WLS universe in `data/wls_members.csv`.
2. Check Yahoo's report dates for 29 and 30 Oct against Bloomberg EVTS, and add overrides where they
   differ.
3. Compare the qualifying list with a manual check of a few names on the Terminal.

## 8. Where each rule lives

| Rule | Code | Config |
|---|---|---|
| Day one per exchange calendar | `screener/calendars.py`, `rules/events.py` | `earnings.unknown_timing` |
| +8% vs WLS, 2x volume, $50m ADV, WLS member | `rules/entry.py`, `rules/metrics.py` | `entry.*` |
| Checks 1 and 5 (manual) | `inputs.py`, `rules/entry.py`, `rules/sizing.py` | `data/manual_checks.csv` |
| Ranking, theme caps, cash | `engine.py`, `rules/themes.py` | `themes.*` |
| ATR sizing, tranches, earnings-hold cap | `rules/sizing.py` | `sizing.*` |
| Relative stop, trim, thesis, core reverse | `rules/exits.py` | `exits.*` |
| De-risk regime | `rules/regime.py` | `regime.*` |
| Staging list, calendar | `rules/staging.py` | `staging.*`, `calendar_view.*` |
| Beta, theme exposure, active weights | `portfolio.py` | `beta.*` |
| Data sources and cache | `data/` | `data_source`, `bloomberg.*` |

Tests: `tests/`, synthetic prices only, with sockets blocked during tests.
