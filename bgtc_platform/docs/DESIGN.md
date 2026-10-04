# Design notes: questions asked before writing code

This file records the questions I asked before building the platform and the
answer I chose for each one. Every answer that is a judgement call is a
setting in `config.yaml`. If the playbook says something different, change
that setting. You should not need to edit the code.

> **The playbook was not available.** `Earnings_Catalyst_Playbook_v2.docx`
> was not attached to the session. The rules below come from the build
> prompt, which restates the playbook's v2 rules. Anything marked
> **[PLAYBOOK?]** is a point where the docx should decide. Check those first.

---

## 1. Rules as implemented

| # | Rule | Default | Config key |
|---|------|---------|-----------|
| 1 | Day one is the first full session after the report. A before-open report counts the same day; after-close and during-session reports count the next session. Each exchange uses its own calendar. | | `earnings.*`, `exchanges.*.calendar` |
| 2a | Day-one USD return minus the WLS return over the same window | at least +8% | `entry.min_day_one_rel_return` |
| 2b | Day-one volume divided by the 50-session average volume before day one | at least 2.0x | `entry.min_volume_ratio`, `entry.volume_avg_window` |
| 2c | 50-session average daily value traded, in USD, before day one | at least $50m | `entry.min_adv_usd` |
| 2d | WLS member | required | `entry.require_wls_member` |
| 2e | Theme cap (semis and software merged into one theme) | max 2 names, max 35% | `themes.*` |
| 3 | Check 1 (revenue beat, guidance at or above consensus) and check 5 (EPS revised up within 3 sessions) | stay PENDING until confirmed | `data/manual_checks.csv` |
| 4 | Rank by day-one relative return | largest first | |
| 5 | Weight = 0.5% / ATR14%, clamped to 4–15%. Half on day 1, half on day 3. | | `sizing.*` |
| 5b | When holding through the position's own report: weight = min(ATR weight, 1% / (1.5 × implied move)) | | `sizing.earnings_hold_*` |
| 6 | Relative stop when the position is 8% behind WLS. Trim above 18% back to 15%. Exit on thesis break. No time stop. | | `exits.*` |
| 6b | Core reverse check: halve the name if it falls at least 4% below WLS on at least 2x volume on its post-report day one | | `exits.core_reverse_*` |
| 7 | De-risk when WLS is below its 50-day average AND VIX is above 25: block new entries and move beta toward 1.0 | | `regime.*` |
| 8 | Staging: list heavyweights reporting in the next 5 sessions. Never recommend holding cash. | | `staging.*` |

Competition hard limits are always enforced: long only, no leverage, no
ETFs, WLS members only, and no position above 20%.

---

## 2. Questions I asked myself, and my answers

### A. Data and timing

**Q1. The docx is missing. What is the source of truth?**
The rules in the prompt. All numbers live in `config.yaml`. The backtest
leaves the playbook's Tables 1, 4 and 5 blank in
`screener/backtest/playbook_reference.yaml`. Copy the numbers from the docx
into that file, and the backtest prints our results next to them with the
differences. I did not guess those numbers.

**Q2. What does "available at the close of date t" mean for a global universe?**
Each listing is evaluated on its own exchange-local session date. The
engine only sees bars dated on or before `t`, through `MarketView.as_of(t)`,
and the live run and the backtest use the same view. On a live run before
the benchmark has closed for date `t` (for example, the Asia session in
the afternoon in Hong Kong), the relative return is marked **PROVISIONAL**.
It is never computed silently against a stale benchmark.

**Q3. How are stock and benchmark returns aligned when exchanges close at different times?**
The benchmark return is measured over the same calendar window as the
stock's return: from the WLS close on or before the stock's previous
session to the WLS close on or before day one. For Asian listings, the WLS
close for date `t` comes after the Asian close. This is a known small
timing mismatch, and I chose to accept it. The alternative, the previous
day's WLS return, measures the wrong day.

**Q4. Is the relative return a difference or a ratio?**
A difference (`r_stock - r_wls`), because the prompt says "minus". The
same method applies to the relative stop. Setting:
`entry.relative_return_method: difference | ratio`. **[PLAYBOOK?]**

**Q5. Do the 50-day volume and value-traded averages include day one?**
No. Both use the 50 sessions *before* day one. Including day one would
inflate the baseline with the event itself. A name with fewer than 50
sessions of history gets **MISSING**, not a pass.

**Q6. Does ATR14 include day one?**
Yes, by default. It is the ATR at the moment of entry (the day-one close),
so it has no look-ahead. The gap makes it larger, which gives a smaller
size. Setting: `sizing.atr_include_day_one`. Wilder smoothing.
**[PLAYBOOK?]**

**Q7. What happens when report timing is unknown?**
Yahoo often has no time of day. Overrides in `data/earnings_overrides.csv`
always win. With no override, `earnings.unknown_timing: both` evaluates
both possible day ones (the report day and the next session). Each is
flagged `TIMING UNCONFIRMED`, so a real BMO move is not lost and a wrong
guess is never hidden.

**Q8. Which currency?**
All comparisons are in USD. The adapter returns local prices plus a
currency code. A shared layer converts them with daily FX (USD per unit of
currency). Minor units are handled: LSE prices are in pence (GBp),
Johannesburg prices in cents (ZAc), and Tel Aviv prices in agorot (ILA).

**Q9. Adjusted or raw prices?**
Returns and ATR use dividend- and split-adjusted prices. Value traded uses
split-adjusted close × split-adjusted volume, which equals the raw value
traded.

**Q10. Benchmark?**
`WLS Index` from Bloomberg. With Yahoo, the VT ETF stands in, and every
report shows a red banner saying so. VIX comes from `VIX Index` or `^VIX`.

**Q11. Should we run the strategy on today's membership in the backtest?**
There is no other free option, so yes, with **survivorship bias**. The
backtest output says so.

### B. Entry, sizing and portfolio

**Q12. "Buy near the close on day one". When does the team see the signal?**
There are two run modes:
* **Final** runs after the session close, at the times in the Windows Task
  Scheduler section. These match the backtest exactly.
* **Preliminary** runs shortly before the close. The last bar is partial, and
  every row is marked `PRELIMINARY`. Partial volume is used as-is. It is never
  extrapolated, so the volume test only gets harder. This is the run to
  use for "near the close" orders in TMSG.

**Q13. Which date is "day three"?**
The third session counting day one as session 1, so day one + 2 sessions,
on that exchange's calendar. Setting: `sizing.second_tranche_session: 3`.
**[PLAYBOOK?]**

**Q14. Check 5 is "required for the second tranche", but the second tranche also says "if check 5 has not failed". Which one?**
Both readings are kept in the output. PASS → `BUY`. PENDING →
`BUY ONLY IF CHECK 5 CONFIRMED`. FAIL → `SKIP`. Check 1 works the same way
on day one: PENDING → `CANDIDATE – CONFIRM CHECK 1`, and FAIL → rejected.

**Q15. Does de-risk mode block the second tranche?**
Yes, by default (`sizing.block_second_tranche_in_derisk`). Adding to a
position is a new entry.

**Q16. Does the theme cap count core and staging holdings, or only catalyst names?**
All holdings by default (`themes.scope: all`), because that is the safer
choice. If the team holds NVDA and MSFT as core names, no new catalyst can
enter the merged semis-and-software theme. Switch to `catalyst_only` if the
playbook means only catalyst positions. **[PLAYBOOK?]**

**Q17. When the theme cap binds, shrink the position or reject it?**
Shrink it to the remaining room if that is still at least the 4% minimum,
otherwise reject it (`themes.cap_mode: shrink | reject`). The reason column
shows the size before and after. The cap counts the full target weight,
both tranches.

**Q18. There is no leverage. Where does the money for a new entry come from?**
The report shows how much cash is available. If the first tranche needs
more, it adds a `FUNDING NEEDED` line and lists the positions tagged
`staging` as the first sources to sell. It never suggests margin.

**Q19. How do we hold the portfolio?**
`data/positions.csv` is a fills ledger: date, ticker, side, qty, price,
role, tranche, day_one_date, thesis_break. Holdings, average cost, entry
date and benchmark level at entry are all derived from it.
`data/account.csv` (optional) holds the cash and NAV figures from the
competition screen. Without it, cash = `backtest.initial_capital` − buys +
sells.

**Q20. How are share counts set?**
Floor of (weight × NAV) / (USD price), rounded down to the board lot. Japan
trades in lots of 100. HK lots vary by stock, so use the `lot_size` column
in `wls_members.csv`. Missing HK lot → `CONFIRM BOARD LOT`.

**Q21. What does "8% behind WLS since entry" mean with two tranches?**
Position return = USD price / fill-weighted average USD cost − 1. WLS
return = current WLS / fill-weighted average WLS level at the fill dates
− 1. Distance to stop = relative return − (−8%), in percentage points.

**Q22. Earnings-hold cap: which positions, and when?**
Every holding (any role) whose next report falls within
`sizing.earnings_hold_lookahead_sessions` (default 5). If its weight is
above the cap, the report says `TRIM BEFORE REPORT`. A missing implied
move shows `CONFIRM MANUALLY: implied move`, never a made-up number.

**Q23. What counts as a "heavyweight" for staging?**
The top `staging.heavyweight_top_n` (20) WLS members by `wls_weight`. If
weights are missing, the list says `MISSING wls_weight`.

### C. Backtest

**Q24. How do we avoid duplicating rule logic?**
The simulator calls the same `engine.run_day()` as the CLI, with a
simulated ledger. It fills at that session's closing price.

**Q25. Manual checks and implied moves have no history. What does the backtest do?**
It treats manual checks as PASS (`backtest.manual_checks_assumption`), and
every result is labelled *quant-only*. The implied move comes from a proxy:
the mean absolute day-one move over the last 8 reports, using past data
only. It is labelled `PROXY`. The live engine never uses this proxy.

**Q26. In the simulator, where does idle money go when no ETFs are allowed?**
It earns the benchmark return as a stand-in for a heavyweight tilt
(`backtest.idle_cash: benchmark_proxy`), consistent with "never recommend
cash staging". `cash` is available as a setting.

**Q27. Costs and starting capital?**
`backtest.cost_bps: 10` and `initial_capital: 1,000,000`. **[Check the
competition rules for the real figures.]**

### D. Operations

**Q28. Hong Kong time and daylight saving?**
Times are stored in UTC and shown in `Asia/Hong_Kong`. Session closes come
from `exchange_calendars` and the tz database. Tests pin the 2026 changes:
the US close moves from 04:00 to 05:00 HKT after 1 Nov, and the London
close moves from 23:30 to 00:30 HKT after 25 Oct.

**Q29. Windows?**
Paths use `pathlib` only, nothing POSIX-specific, and file names contain no
colons. `.bat` launchers are in `scripts/`.

**Q30. Is Bloomberg data allowed in scripts?**
Check the university terminal policy first. Use `BloombergAdapter` only on
a machine with a logged-in Terminal, and keep cached Bloomberg data off
shared drives. Field names marked *verify with FLDS* in `config.yaml`
should be checked on the Terminal before first use.

---

## 3. Open questions for the team (answer by editing `config.yaml`)

1. Relative return: difference or ratio (Q4)?
2. Does ATR include day one (Q6)?
3. Is day three day one + 2 sessions (Q13)?
4. Theme cap scope: all holdings or catalyst only (Q16)?
5. Real starting capital and commission in the 2026 challenge (Q27)?
6. Which of our holdings are *core*? Tag them `role=core` in `positions.csv`.
7. Is `heavyweight_top_n = 20` the playbook's definition of "heavyweight"?
8. What exactly do Tables 1, 4 and 5 contain? Paste the numbers into
   `screener/backtest/playbook_reference.yaml`.

---

## 4. Architecture

```
DataSource (ABC) ── YahooAdapter / BloombergAdapter / FixtureSource
      │                (raw local-currency data only, no rules)
      ▼
CachedSource ── parquet in data/cache/  (reruns do not refetch)
      ▼
MarketData ── USD conversion, minor-unit fix, benchmark, VIX
      │   .as_of(t) → MarketView  (everything dated > t is removed)
      ▼
engine.run_day(view, session, portfolio, inputs, cfg)
      ├─ rules.events   → day-one resolution per exchange calendar
      ├─ rules.metrics  → returns, ATR, ADV, volume ratio (pure functions)
      ├─ rules.entry    → quant + manual checks, each with a reason
      ├─ rules.sizing   → ATR weight, earnings-hold cap, tranches, lots
      ├─ rules.themes   → theme caps in rank order
      ├─ rules.exits    → relative stop, trim, thesis, core reverse
      ├─ rules.regime   → de-risk switch
      └─ rules.staging  → heavyweights reporting soon
      ▼
DayResult ──► report.py (HTML + CSV + data bundle) ──► dashboard (Streamlit)
          └─► backtest.simulator (same engine, simulated ledger)
```
