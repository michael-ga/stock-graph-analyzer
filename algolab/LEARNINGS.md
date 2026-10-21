# Algorithm Learnings — running journal

> **READ THIS FILE BEFORE CHANGING THE ALGORITHM.** Its whole purpose is to stop
> us re-deriving the same wrong conclusions session after session. If you are
> about to "fix" a setup or re-weight a check, first check whether it's already
> in **Ruled out / do not repeat** or **Open hypotheses** below. After you change
> anything, add a dated entry.

How to use this file:
1. Run `python algolab/analyze.py` to get the current per-bot + idea-level report.
2. Read **Ruled out** and **Open hypotheses** here before forming a theory.
3. Make at most one or two changes; record them under a new dated entry with the
   reasoning and how you'll know if it worked.
4. Challenge a prior entry if new data contradicts it — don't silently overwrite;
   add a new entry that supersedes it and say so.

---

## Standing guardrails (the meta-lessons)

- **Sample size honesty.** As of 2026-06-18 the book had 28 closed trades. A
  60.7% win rate at n=28 is statistically indistinguishable from a coin flip
  (binomial p≈0.35). **Do not re-weight the score or kill a setup on a single
  session's data.** Rough target: ~100+ *independent* closed ideas before any
  sub-group claim (per-setup, per-band, per-check) is actionable.
- **Independence > raw count.** The book copies one plan across bot-GO / bot-70 /
  bot-BRK / me, so N rows ≠ N independent bets. Always judge the *algorithm* on
  the **idea-level** view (`algorithm_correctness`, deduped by `cohort_id`), and
  use the per-bot view only to ask "which *rule* earns its place."
- **Don't fish across all the checks.** There are ~22 swing-checks. If you test
  all of them for an "edge" after the fact, one will look significant by chance.
  Pre-register the 2–3 you actually want to test before looking.
- **Definitional ≠ predictive.** "target_hit = 100% win, stop_hit = 0% win" is
  true by construction (the exit sets the price). It proves the exit plumbing
  works; it says nothing about entry quality. The only outcome-bearing exit
  bucket is `expired`.
- **Confounds: same ticker, same day.** Before blaming a setup, check whether all
  its losses are one ticker on one tape (e.g. AMD/INTC stopping together on a
  down day). One correlated event is n≈1, not n=several.

---

## Ruled out / do not repeat

These looked like signal on 2026-06-18 and were checked — **do not act on them
again without materially more data:**

- ❌ **"Score <60 wins the most (73%)."** Noise. Fisher p≈0.44 vs the 60+ band;
  band boundaries are arbitrary; one flipped trade moves it ~9pts. There is *no*
  evidence low scores beat high scores — most likely the scorer is simply
  uncorrelated with outcome at this n.
- ❌ **"The 80+ score band is 0/2, so high scores lose."** It's **n=1**. Both rows
  were the *same NOK "Pullback to 20-EMA" idea* (identical entry/stop/target
  14.93/13.70/16.95), taken by bot-GO and me — one cohort. The idea-level view
  correctly shows 80+ as a single idea.
- ❌ **"Momentum/gap setup is broken (0/3)."** Date confound. All three (AMD ×2,
  INTC ×1) opened 2026-06-15 and stopped 2026-06-16; other trades *won* that same
  day. It's one down-tape event across two tickers, not three independent setup
  failures. Needs Momentum/gap trades on other days to actually test.
- ❌ **Per-check "edges" from 2026-06-18.** Computed on cells of 4–8 trades from
  only 13 swing-checked trades across 7 tickers. The lone nominally-significant
  one ("Engine bias agrees", p≈0.035, and *anti*-predictive) does not survive
  multiple-comparison correction. Treat all as hypotheses, not facts.
- ❌ **"Speed up the radar by fetching fewer timeframes."** Tempting every time
  the UI feels slow — and it is an *algorithm* change wearing a performance
  costume. `build_verdict` normalizes by the weight of the timeframes actually
  **present** (`aggregate.py`, `weighted_sum / weight_total`), so dropping 6M/YTD/
  1Y/5Y silently re-weights the investor score, which flows into
  `_radar_plan` → `context={"investor_pct": …}` → plan score → bot entries → the
  book. Any "perf fix" that changes which timeframes reach `build_verdict` must be
  treated as a scoring change and measured as one. (The long frames are cached on
  disk for 6–24h anyway, so they are rarely the actual cost — measure first.)

---

## Open hypotheses (test as data accumulates)

| # | Hypothesis | Evidence so far | Status | How to confirm/kill |
|---|---|---|---|---|
| H1 | **Daily MACD against the entry predicts losers.** | Losers avg macd_hist −0.124 vs winners +0.021; "MACD above signal" split W50%/L0% on 6M & 1Y. Directionally clean, not yet significant (p≈0.11). | **Acted (refined):** shipped as a GO veto 2026-06-18. ⚠️ First cut (raw MACD<signal) over-suppressed healthy buy-the-dips — a normal pullback rolls MACD under signal even in an uptrend. **Refined** to: veto only when the daily MACD *histogram* is on the wrong side **AND still deteriorating** (last bar worse than prior). A stabilizing dip passes; a falling knife is blocked. | Compare GO win-rate before/after; confirm it still lets with-trend "Support test"/"Pullback" GOs through (H3). |
| H2 | **The high-conviction layer is mis-calibrated (inverted).** 80+ score, high R:R, "engine bias agrees", "A-grade confluence" clustered on losers; snap_rr anti-predictive (winners R:R 1.38 vs losers 1.72; the 1.5–2.0 R:R band was worst). | Consistent across cuts, none significant. | **Watch only.** Do NOT re-weight yet. | When n≥100 ideas, split win-rate by score band and by R:R band; re-weight only if the inversion holds. |
| H3 | **"Support test (uptrend)" is the real edge; with-trend > counter-trend.** | Support-test 6/6; Breakout(volume) 6/8; counter-trend Pullback/Momentum poor. | Watch only (n too small). | Track idea-level win-rate per setup over time. |
| H4 | **The throwback/breakout model doesn't beat immediate entries.** | breakout_wait 9/15 (60%) vs immediate 8/13 (62%), but immediate captured ~2× profit/trade. | Watch only. | Compare idea-level avg_pnl breakout_wait vs immediate at larger n. |
| H5 | **Active exit management beats a fixed stop/target** on identical Gap-and-Go entries. | None yet — instrumentation shipped 2026-06-21. | **Collecting.** Do NOT conclude until pairs accumulate. | `store.managed_vs_fixed()`: pair `bot-gap-mgd` vs `bot-gap-fixed` by `cohort_id`; positive mean Δ at n≥~30 pairs = management earns its place. |

---

## Session log

### 2026-06-18 — first post-mortem + measurement overhaul

**What the book said (verified):** 28 closed, 17W/11L, 60.7%, avg +1.87%, +$449.62.
target_hit 13/13, stop_hit 0/9, expired 4/6. Best: Support-test 6/6. Worst:
Momentum/gap 0/3, the NOK-87 pullbacks. bot-GO (strict GO) was the *worst* bot at
40%. See **Ruled out** for why most sub-group splits don't survive scrutiny.

**Assumptions challenged this session:**
- *Assumed* a higher score should win more → **challenged**: at n=28 the score is
  ~uncorrelated with outcome; the "80+ loses" story was one duplicated idea.
- *Assumed* the breakout/throwback model improves results → **challenged**: same
  win-rate as immediate, less profit. Kept (H4) but flagged.
- *Assumed* per-check pass-rates reveal what to fix → **challenged**: too few
  checked trades, too many checks; fishing risk.

**Changes shipped (code):**
1. **Schema v4** (`stockanalyzer/data/store.py`):
   - `trade_indicators` now persists **adx, plus_di, minus_di, bb_upper,
     bb_lower** — the famous guards the score already used but never stored, so
     post-mortems were blind to them.
   - `trades.cohort_id` — deterministic id grouping the same idea across bots
     (`_cohort_id`). Backfilled for existing rows.
   - **Repaired** 6 NOK rows whose `closed_ts` was NULL / near-epoch (broke any
     holding-period analysis). Recovered from the human `closed` text.
2. **`algorithm_correctness()`** (store + `virtualbook` wrapper): idea-level,
   deduped report. Surfaced in the app's Algorithm-evidence panel and in
   `algolab/analyze.py`.
3. **Daily-MACD GO veto** (`stockanalyzer/explain/swing.py`, `_macd_against`): a
   long can't reach GO while the daily MACD histogram is **negative and still
   falling** (mirror for shorts). Implements H1. Reads a true daily frame only;
   no-ops when absent (controlled fixtures unaffected).
   - **Assumption challenged in review:** the naive "MACD < signal" veto was
     caught over-suppressing the textbook buy-the-dip (a healthy pullback rolls
     MACD under signal). Lesson logged so we don't reintroduce the raw-cross
     version: a momentum-crossover veto must require *deterioration*, not just
     the cross. Tests `test_macd_veto_lets_a_stabilizing_buy_the_dip_through`
     guard against regressing this.

**Bug caught + fixed in review:** `_repair_closed_ts` originally caught only
`(ValueError, TypeError)`, but `time.mktime` raises `OverflowError` on
out-of-range years — and since the repair runs inside the migration on *every*
connect, one bad `closed` string (e.g. from a hand-edited JSON import) would have
bricked the DB permanently. Now catches everything and falls back to `opened_ts`.
Test: `test_repair_closed_ts_survives_out_of_range_year`.

**Deliberately NOT done (and why):** did not re-weight any swing-check, did not
delete any setup, did not change the score formula — n is far too small; per the
guardrails that would be overfitting. Those wait for H2/H3 to accumulate data.

**Next session, start here:** run `analyze.py`; if idea count has grown
meaningfully (say ≥60), begin testing H2 (score/R:R calibration). Otherwise just
keep logging and confirm the MACD veto (H1) isn't over-suppressing GOs.

### 2026-06-21 — Gap-and-Go day-trade engine + adaptive radar + managed exits (H5)

**What changed (code), all additive — baseline preserved:**
1. **Adaptive radar CPU** (`app.py` `_radar_tier`/`_radar_panel`): the radar
   fragment ticks every 5s but each ticker recomputes only when its tier is due
   (HOT 5s · WATCH_CLOSE 10s · BUILDUP 25s · FAR 60s), so far-away buildups stop
   burning CPU. Far buildups now cost ~1/12 of a hot name. No trade-logic change.
2. **Gap-and-Go (Carter ORB) intraday rules:**
   - New `stockanalyzer/session.py` ET clock: opening-range freeze (first 15 min,
     **no entries**), the 9:45–11:30 entry window, the Friday flatten cutoff.
   - Radar escalates to WATCH_CLOSE/HOT toward the opening-range HIGH **only** for
     a real **gap-up** (open > prev close +2%) **inside the morning window**;
     non-gappers/afternoon fall back to swing tiering.
   - **Volume confirmation**: `swing.py` `go` now requires RVOL > **1.5** for
     *breakout* setups (scoped so it doesn't suppress quiet pullback/support buys
     — preserves the H1 buy-the-dip lesson). Gap-and-Go entry needs RVOL > 1.5 too.
3. **Managed exits, MEASURED (H5):** two new bots take the *same* ORB entry
   (hard stop just below the opening-range LOW, 2R target, shared `cohort_id`):
   - `bot-gap-fixed` — fixed stop/target control.
   - `bot-gap-mgd` — (a) cautious protective stop: spread-adjusted breakeven at
     +1R then trail the tighter of 3×ATR chandelier and 5-min EMA8, **ratchet-only**;
     (b) smart trend-flip: a flip (conf ≥0.6) or a 5-min close below EMA8 *tightens*
     the stop to test the move (never market-dumps a winner on one noisy bar);
     plus a Friday weekend-flat. Applied in `store.manage_trades` (managed rows
     only) — `mark_trades` and the existing bots are untouched.
4. **Risk realism:** `_SPREAD_PER_SHARE = 0.02` (flat $/share) — breakeven =
   entry + $0.02; managed closes and `_cost_basis_block` deduct the flat per-share
   spread (baseline `_close_row` default `spread=0.0`, byte-identical).
5. **Schema v5**: `init_stop, mfe_pct, mae_pct, stop_moves, managed, entry_rvol,
   hold_weekend` + `trade_events`. `algorithm_correctness` now filters `managed=0`
   so the idea-level baseline is unchanged. `store.managed_vs_fixed()` + an
   `analyze.py` section + an app panel surface the paired comparison.

**Assumption challenged:** the naive "plan.go needs RVOL>1.5" would have suppressed
textbook low-volume pullback buys (pullbacks trade quiet) — re-derives the H1
mistake in volume form. The volume gate is now **scoped by setup type**, not blanket:
breakout / gap-and-go / momentum-gap need a **surge** (rvol > 1.5); pullbacks need
**drying** volume (rvol < 1.0 = seller exhaustion, a *low*-volume pass, never blocked
for being quiet); all other setups get no volume veto. Guards:
`test_pullback_on_heavy_volume_is_not_go` + `test_clean_pullback_stays_go_with_strong_score`.

**Measurement honesty:** the shared ORB entry rules reset the historical baseline,
so pre/post comparisons across this change are invalid. The **only** clean read is
the paired `managed_vs_fixed` (same entry, two exits). At today's n that is **zero
pairs** — H5 is *collecting*, not concluded. No score re-weighting was done.

**Next session, start here:** run `analyze.py` view 4 (MANAGED vs FIXED). If pairs
≥~30 with a positive mean Δ, the management earns its place; if negative, inspect
which rule (over-tight trail? premature trend-test?) is bleeding the edge before
touching thresholds in `manage.py`.

---

### 2026-07-21 — live-mode responsiveness (no algorithm change)

**Nothing in this entry touches scoring, entries, exits, or the book.** It is
recorded here only because the first plan for it *would* have — see the new
"speed up the radar by fetching fewer timeframes" item under **Ruled out**.

**Problem:** live mode felt stuck and the chart froze. Cause was structural, not
algorithmic: a single `@st.fragment(run_every="1s")` rebuilt the *entire*
dashboard every second, including two full Plotly figures, while the 5s radar and
30s portfolio fragments competed for the same per-session script lock.

**Changed:**
1. Live mode split into three sibling fragments by how fast the content actually
   changes — FAST 1s (price header, HTML only), MID 3s (heartbeat, bots, flip
   detection, event feed), SLOW 15s (plan, orders guide, candlestick chart,
   chips). Shared engine/plan state moved to `ss.live_shared`, rebuilt only in the
   slow frame, so the fast frames never touch the network.
2. Chart viewport (`_stable_focus`) computed **once per (ticker, timeframe,
   interval)** instead of on every redraw. It was derived from the last 40% of
   bars, so it crept with each new bar and the plot drifted under the user.
3. Candle-size picker (5m / 15m / 30m) via local roll-up — `data/resample.py`,
   pure + unit-tested. Coarser candles are exact integer multiples of the fetched
   5m bars, so switching costs **no** network call and no rate-limit budget. The
   engine is re-run on the rolled-up frame (indicator columns are deliberately
   dropped — RSI on 5m ≠ RSI on 15m) and memoized per last-bar.
4. Radar: reuse the `AnalysisResult` already in hand instead of re-entering
   `st.cache_data` via `_quiet_price` purely to read one float (that path pays a
   full unpickle of a seven-timeframe result). Behaviour identical.
5. Added an opt-in frame-timing panel (sidebar → "⏱ Show frame timings").

**How we know it worked — measured, after hours, 2026-07-21:**

| fragment | p50 | p95 | budget |
|---|--:|--:|--:|
| fast · price header | 1 ms | 2 ms | 1000 ms |
| mid · pulse/bots/feed | 1 ms | 1 ms | 3000 ms |
| slow · chart/plan | 407 ms | 448 ms | 15000 ms |
| radar | 9 ms | **4386 ms** | 5000 ms |

The 407 ms chart rebuild used to run **every second** against a 1000 ms budget —
~40% duty cycle before the radar took its share, which is exactly the backlog that
made the UI feel frozen. It is now ~2.7%.

**Next session, start here:** radar p95 is **4386 ms against a 5000 ms budget** —
it only passes because most tickers are idle-tiered after hours, and it will blow
the budget during RTH. That is the next thing to fix, and the fix is *not* fewer
timeframes (see Ruled out): move `_run_quiet` + `virtualbook` marking onto a
background worker thread (the `RealtimeStream` pattern in `data/realtime.py`) and
let the fragment read a snapshot. Re-measure with the timing panel during market
hours before and after.

---

### 2026-07-22 — reversal-at-support day-trade family (H6, collecting)

**Why:** the swing setups aren't built for day trading — they target multi-day
walls on a volatility budget, so a quick intraday dip-buy at support never
surfaces. Added a *family* of fast reversal-at-support setups, built the same way
as the Gap-and-Go engine (H5): one pure as-of function the live radar and
`daytrade_backtest.py` share verbatim (no drift), driven by a config list.

**What changed (code), all additive — nothing re-weighted, no schema change:**
1. New `stockanalyzer/analysis/reversal.py` — `reversal_signal(card, intraday_df,
   price, higher_trend, prev_close, asof_ts, cfg)`. Guard order: at support in the
   lower 35% of the day's range → trend/strength gate → reversal print (confirmed
   mode) → non-degenerate geometry → the **~3% go/no-go aim** (capped just under
   the nearest resistance; skip if < 2.5%). Stop is hard, just below support. It
   consumes `build_day_card` (session low, mapped supports, **volume battle-zones**
   as support confidence) — no new data path.
2. `REVERSAL_VARIANTS` — the starter matrix over two risk axes: entry
   `touch` (aggressive, first tag) vs `confirmed` (bullish reclaim); trend
   `with_trend` (dip-buy only when the daily trend is up, stands aside on a
   gap-down ≤ −2%) vs `strong_support` (any trend, but the level needs ≥2
   confirmations). Four bots: `rev-trend-touch/-confirm`, `rev-strong-touch/-confirm`.
3. `app.py` `_run_reversal_bots` opens one bot per fired variant (`horizon_days=1`,
   own `cohort_id`) and surfaces a `🔄 Reversal @ support` line on the radar card.
   Shares the ORB opening-range freeze.
4. `daytrade_backtest.py --variants` now prints a reversal `fires/wins/WR/expR`
   sweep next to the ORB one, replaying the **same** `reversal_signal` bar-by-bar
   (as-of, no lookahead); per-day contexts are built once and reused across
   variants.

**Pre-registered hypotheses (test as data accumulates — do NOT cull on one
session):** (a) does `confirmed` entry beat `touch` on expectancy, or does the
worse fill eat the higher hit-rate? (b) does `strong_support` (battle-zone-backed)
justify taking counter-trend reversals, or is `with_trend` strictly better? Judge
at the **idea level** (`algorithm_correctness` by `cohort_id`), ~100+ independent
closed ideas before any per-variant claim — same guardrail as H5. Each variant is
a genuinely distinct idea (different entry/stop), so they get distinct cohorts, not
one plan copied across bots.

**How we'll know:** run `daytrade_backtest.py <tickers> --variants` for the
counterfactual fire/WR/expR ranking now; let the live bots accrue paired closed
ideas for the real read. No baseline was reset (all additive), so existing swing
and Gap-and-Go measurements are unaffected.

---

### 2026-07-27 — quote day-change fix when the daily feed skips a session (no algorithm change)

**Why:** INTC's price header printed a "day change" roughly double the real
one-day move. Root cause: `pipeline._fallback_quote` takes `prev_close` from
`daily.df["close"].iloc[-2]` and *assumes* that row is the immediately preceding
session. yfinance's daily bars occasionally drop a session entirely (here Friday
2026-07-24 was missing while Monday was present), so `iloc[-2]` silently spanned
two trading days.

**This entry changes NO algorithm behaviour.** No gate, weight, threshold or
`SCHEMA_VERSION` moved; only the displayed quote (`prev_close` / `regular_close`)
is corrected.

**What changed (code):**
1. `data/market_session.session_closes(df, n)` — the last `n` distinct trading days'
   REGULAR-session closes from an intraday frame, most recent first.
2. `_fallback_quote` cross-checks the daily frame against the 5D/1D intraday frame,
   which gets every session's bars as they trade: if intraday knows a newer session
   than the daily frame, both closes come from intraday; if the latest day matches
   but the session *before* it differs, `prev_close` comes from intraday.

**How we'll know it worked:** `tests/test_fallback_quote.py` reproduces the INTC
gap (Thu → Mon daily, Friday present intraday → `prev_close` is Friday's close,
day change −2.58%, not the 2-day move) and pins that the common no-gap case is
left untouched.

---

### 2026-10-21 — gap measured from the 09:30 open, not the pre-market open (ALGORITHM CHANGE)

**Supersedes the "open observation" in the 2026-07-26 entry.** That entry flagged
that `orb.opening_range` took the gap from the first bar of the day, which with
`prepost=True` data is the **04:00 pre-market open**, and judged it a near-boundary
risk. A replay of SNDK 2026-10-12…20 shows it is systematic: the bot's gap was off
on **all 6 days**, by 0.4–2.3 pp, always toward zero:

| Day | Bot's gap (pre-market) | Real gap (09:30) |
|---|--:|--:|
| 10-13 | −3.4% | −5.5% |
| 10-16 | −1.1% | −3.3% |
| 10-19 | +3.4% | +4.6% |

The reversal family had the same bug by another route: it backed `session_open`
out of `card.day_change_pct`, which `build_day_card` also measures from the first
(pre-market) bar. On 10-16 (real −3.3%) the `with_trend` "gap-down ≤ −2% is a
knife" guard read −1.1% and **would not have stood aside**, on a day that closed
at its low.

**What changed (code):**
1. `data/market_session.regular_open(df)`: open of the first REGULAR bar of the
   latest ET date; `None` until 09:30 exists.
2. `orb.opening_range` uses it (returns `None` if there is no regular bar yet).
3. `DayCard.session_open` (new, default `None`) is set from it in `build_day_card`;
   `reversal_signal` prefers it and only falls back to the old `day_change_pct`
   derivation when the card has no regular-session bars.

**Deliberately NOT changed:** `DayCard.day_change_pct`, session high/low,
range position and battle zones still span pre-market. They are separate
behaviours (display + the reversal "lower 35% of range" gate), and changing them
on the same PR would blur what this one measures.

**Measurement honesty:** this changes which days pass the ORB `gap_up` gate and the
reversal knife guard, so **ORB and reversal stats before and after 2026-10-21 are
not comparable**. The H5 paired read (`managed_vs_fixed`) is unaffected: both bots
share the entry. Swing bots are unaffected.

**How we'll know:** `test_gap_is_measured_from_the_regular_open_not_premarket`,
`test_premarket_gap_up_that_opens_flat_is_not_a_gap_up` and
`test_gap_uses_card_regular_open_over_premarket_derived_open` all fail on the old
code. `daytrade_backtest.py SNDK --date 2026-10-16` now prints `gap -3.3% (open
$1348.82 …)`. Next: count how many ORB fires the corrected gate added/removed once
a few weeks of live data exist. Plan with the related L2 overnight-hold issue:
`docs/plans/2026-10-21-sndk-snapshot-review.md` (on the daychart branch).

---

### 2026-10-22 — [ALGO] measured impact of the 09:30 gap reference + skipped-session prev close

**Why this entry:** the two entries above ship together in one [ALGO] PR into `main`.
This records what they do to real trades, and corrects two claims made earlier.

**Corrections to earlier entries (data, not opinion):**
1. The 2026-07-27 quote fix is labelled "no algorithm change". That is wrong for live
   trading: `Quote.prev_close` feeds `orb.opening_range` (the Gap-and-Go gap) and
   `reversal.reversal_signal` in both `app.py` and `radar_worker.py`. On a day the
   daily feed skips a session, it changes the gap and can change trades. It is an
   **indirect** algorithm change. Replay does not exercise it (replay takes
   `prev_close` from the daily frame), so the A/B below cannot show it.
2. The 2026-10-21 entry says the pre-market gap was off "always toward zero". Over
   6 tickers × 22 sessions it is off in **both** directions: INTC 2026-09-30 read
   +5.06% from the 04:00 open against a real +0.10%; TSLA 2026-10-16 +3.93% vs +1.82%.

**Measured (real-history replay A/B, every bot on):** INTC AMD NVDA TSLA PLTR SMCI,
2026-09-22 → 2026-10-21, 132 ticker-days, identical yfinance bars on both trees.
- Gap-ups ≥ +2%: 8 days by the old measure, 16 by the 09:30 open. Median gap error of
  the old measure: 0.88 points against a 2-point threshold.
- Swing (`bot-GO`, `bot-BRK`) and reversal bots: **identical** trades.
- Gap-and-Go (`bot-gap-fixed` + `bot-gap-mgd`): **0 → 8 trades** — exactly the days that
  newly pass the gate (AMD, INTC, SMCI 2026-10-02; TSLA 2026-10-21). 1 win, 3 losses
  per pair; net R for the whole book −2.35 → −4.81.
- 6 other days newly pass the gap gate but fail later ORB checks: no trade.

**Statistical discipline:** 4 Gap-and-Go ideas is not a sample. This PR fixes a
measurement bug; it does not claim Gap-and-Go is profitable. Judge the bot on
`store.algorithm_correctness` (idea level) after several weeks of live data, and do
not compare ORB/reversal stats across 2026-10-21.

**How we'll know it worked:** the unit tests in the two entries above; the gap shown
on the replay page matches the 09:30 open; and a re-run of the replay A/B on a later
month shows Gap-and-Go firing only on days whose 09:30 gap is ≥ +2%.
