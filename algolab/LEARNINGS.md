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
- ❌ **Failed-breakdown, second-chance retest and resistance→support longs on
  2x single-stock ETFs (5-min, as specced 2026-10-25).** Negative in the 40-session
  screen *and* the 20-session holdout, across opposite regimes: failed breakdown
  −0.40R net over 155 trades / 105 underlying-days, second chance −0.30R / 92,
  resistance→support −0.29R / 38. Re-test only with a materially different spec
  (e.g. 1-min bars), not by nudging thresholds on the same data.

---

## Open hypotheses (test as data accumulates)

| # | Hypothesis | Evidence so far | Status | How to confirm/kill |
|---|---|---|---|---|
| H1 | **Daily MACD against the entry predicts losers.** | Losers avg macd_hist −0.124 vs winners +0.021; "MACD above signal" split W50%/L0% on 6M & 1Y. Directionally clean, not yet significant (p≈0.11). | **Acted (refined):** shipped as a GO veto 2026-06-18. ⚠️ First cut (raw MACD<signal) over-suppressed healthy buy-the-dips — a normal pullback rolls MACD under signal even in an uptrend. **Refined** to: veto only when the daily MACD *histogram* is on the wrong side **AND still deteriorating** (last bar worse than prior). A stabilizing dip passes; a falling knife is blocked. | Compare GO win-rate before/after; confirm it still lets with-trend "Support test"/"Pullback" GOs through (H3). |
| H2 | **The high-conviction layer is mis-calibrated (inverted).** 80+ score, high R:R, "engine bias agrees", "A-grade confluence" clustered on losers; snap_rr anti-predictive (winners R:R 1.38 vs losers 1.72; the 1.5–2.0 R:R band was worst). | Consistent across cuts, none significant. | **Watch only.** Do NOT re-weight yet. | When n≥100 ideas, split win-rate by score band and by R:R band; re-weight only if the inversion holds. |
| H3 | **"Support test (uptrend)" is the real edge; with-trend > counter-trend.** | Support-test 6/6; Breakout(volume) 6/8; counter-trend Pullback/Momentum poor. | Watch only (n too small). | Track idea-level win-rate per setup over time. |
| H4 | **The throwback/breakout model doesn't beat immediate entries.** | breakout_wait 9/15 (60%) vs immediate 8/13 (62%), but immediate captured ~2× profit/trade. | Watch only. | Compare idea-level avg_pnl breakout_wait vs immediate at larger n. |
| H5 | **Active exit management beats a fixed stop/target** on identical Gap-and-Go entries. | None yet — instrumentation shipped 2026-06-21. | **Collecting.** Do NOT conclude until pairs accumulate. | `store.managed_vs_fixed()`: pair `bot-gap-mgd` vs `bot-gap-fixed` by `cohort_id`; positive mean Δ at n≥~30 pairs = management earns its place. |
| H8 | **Extension-fade double bottom (`xfade-dbl-bottom`) has a small edge on 2x single-stock ETFs** — and it pays on whichever leg of the pair is trending up. | 2026-10-25: 60 sessions, 82 trades / 48 dates, +0.19R net, positive in both windows and both halves, beats a random-entry null (p=0.005). Cluster t vs zero 1.7: not significant. Leg split is post-hoc. | **Collecting** (backtest only, not a bot). | Re-run the harness on *new* sessions only. Keep if avg net R > 0 at ≥ 100 distinct dates and still > 0 without the best date; kill if ≤ 0 there. A daily-bias filter is a new variant, pre-registered before it's run. |
| H9 | **ICT Silver Bullet, long mirror (`sb-am-bias`)** has an edge. | 2026-10-25: 12 trades in 60 sessions, +0.78R net, null p=0.045. n is an anecdote. | **Collecting** (backtest only). | Same keep/kill rule as H8 at ≥ 50 distinct dates. To accrue faster, add tickers, chosen before running. |

(H7 is reserved for the `rev-gapfill` proposal in `docs/plans/2026-10-21-sndk-snapshot-review.md`.)

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

### 2026-07-26 — interactive replay simulator (observation surface, no algorithm change)

**Why:** we could analyse *now* and backtest *offline*, but there was no way to sit
inside a past session and watch it develop. `daytrade_backtest.py` already replayed
a day bar-by-bar with strict no-lookahead — it just printed a table at the end. You
couldn't see the chart move, pause on a bar, or take the trade yourself. That makes
two things hard: judging a setup without hindsight bias, and *learning* the tape.

**This entry changes NO algorithm behaviour.** `orb.py`, `reversal.py`, `swing.py`,
`engine.py`, `daycard.py` and `store.py` are untouched, `SCHEMA_VERSION` stays at 5,
and no live gate, weight or threshold moved. It is a new way to *observe* the engine.

**What changed (code):**
1. **`stockanalyzer/analysis/asof.py`** — the as-of reconstruction (`asof_intraday`,
   `asof_daily`, `asof_reports`, `decision_rep`, `higher_trend`, …) **moved** out of
   `daytrade_backtest.py`. The CLI now imports it under its old private names, so its
   body is unchanged (verified: the `--variants` run reproduces the prior output).
   This extends the H5/H6 "one pure as-of function, no drift" rule to a third
   consumer. `tests/test_asof.py` pins it with an **import-identity test** — a future
   edit cannot silently re-copy the logic and let the two versions drift.
2. **`stockanalyzer/replay.py`** — headless engine. `precompute_day` walks 09:45 →
   15:55 and freezes each bar's complete decision (swing plan scalars + all four
   reversal variants + the ORB verdict + the blocking gate) into an immutable
   `ReplayBar`. `apply_bar` runs the book forward one bar: `manage` → `mark`
   (low → high → close, so an ambiguous bar resolves stop-first using the store's
   own fill logic) → auto-place *after* marking, so a position is never judged
   against the bar that opened it.
3. **`replay_page.py` + `ui_common.py`** — the Streamlit page and the shared
   presentation helpers extracted so `app.py` can import the page without a cycle.
   `explain/snapshot.py` likewise now holds the three snapshot builders, so the
   radar, the bots and the replay all store identically-shaped context (a divergent
   builder would silently degrade `trade_stats`' `by_setup` grouping to "?").

**Measured (AMD, 2026-07-22, this machine):** `analyze_timeframe` 29–39 ms ·
`asof_reports` (5 calls) **121 ms/bar** · full-day precompute (75 bars) **9.6 s** ·
`candlestick_figure` at 384 bars **200 ms build + 60 ms serialize**. The *chart* is
4× the per-bar *decision*, which is why the day is precomputed once and why faster
playback advances more bars per tick rather than ticking faster (the same lesson
live mode learned at `_LIVE_FAST_S`).

**Isolation.** Practice trades go to `.replay/<TICKER>-<YYYYMMDD>-<n>.db`, never
`trades.db`, so `trade_stats` / `algorithm_correctness` over the real book are
unaffected. The **subdirectory is load-bearing**: `store._ensure_schema` seeds a
brand-new DB from `<db_path>.parent/.virtualbook.json`, so a replay book at the repo
root would silently inherit ~100 real trades. Pinned by
`test_replay_db_does_not_absorb_legacy_json`, which also asserts the failure mode
still exists (a book placed beside the JSON *does* inherit it) so the test can't go
vacuous. Bots keep their **live trader names** — isolation is by file, and identical
names make a replay scorecard comparable row-for-row with the live one.

**The no-lookahead invariant** is the flagship test
(`test_bar_state_is_identical_whether_stepped_or_truncated`): the state at bar *i*
must be identical whether the engine walked there or whether every later bar was
physically deleted from the input. If anything in the stack peeked forward, deleting
those bars would change the answer.

**New open observation (do NOT "fix" without a deliberate decision):**
`orb.opening_range` measures the gap from `_session_bars(df)["open"].iloc[0]`, which
with `prepost=True` intraday data is the **pre-market open, not the 09:30 open**.
On AMD 2026-07-22 that is 533.24 vs a regular open of 526.875 — a ~1.2 pp difference
in the measured gap, i.e. it can flip the 2% `GAP_MIN_PCT` gate near the boundary.
The live radar and the backtest share this behaviour, so the replay reproduces it
faithfully rather than diverging. Whether the gap *should* be measured from the
regular open is a real question — but it is an algorithm change, needs its own
entry, and must be judged on ideas, not on one session.

**How we'll know it worked:** the replay is a measurement instrument, so the test is
usage — load a day, run the bots, and check that the fired/blocked verdicts match
what `daytrade_backtest.py <ticker> --verbose` prints for the same day. Any
disagreement means the shared as-of path has drifted, which is exactly what the
import-identity test exists to prevent.

---

### 2026-10-21 — replay multi-day scan + replay daily-window fidelity (no algorithm change)

**Why:** the replay simulator answers "what happened on this day", but most days
nothing happens — stepping through them one ten-second rebuild at a time wastes the
session. `stockanalyzer/replay_scan.py` runs the same headless replay over a ticker
list × a set of days (every bot on, a throwaway book per day) and summarizes each
ticker-day — signals, the bots' trades and R, the idea count, the modal blocking gate
— so the 🔎 Multi-day scan view on the Replay page can point at the days worth
replaying. Rows are cached in `.replay/scan-cache.db`, keyed by a hash of the
decision code (`analysis/`, `explain/`, `verdict/`, `replay.py`, `strategy.py`,
`virtualbook.py`, `data/store.py`), so any algorithm change re-scans instead of
serving the old algorithm's answer.

**No gate, weight, threshold or `SCHEMA_VERSION` moved.** Two *harness* changes do
shift replay numbers, and are recorded here so they aren't mistaken for an
algorithm effect:
1. The replay/backtest intraday window is now yfinance's 60-day maximum
   (`replay.INTRADAY_PERIOD`), up from 1 month — more days, same per-day logic.
2. `replay.daily_for_day` rebuilds the 1-month daily frame as the trailing month
   **before the replayed day** (from the 6-month frame). Previously it was the
   month fetched *today*, so a session three weeks back saw only ~1 week of prior
   daily bars — often below `MIN_BARS`, silently dropping the M1 report the live
   radar had on that morning. `daytrade_backtest.py` uses the same window, so the
   replay ↔ backtest agreement check below still holds. `prev_close` now comes from
   the 6-month frame (identical values for recent days; defined for older ones).

**Statistical discipline still applies.** A scan's 🟢 days are a *finding aid*, not
evidence: they are one ticker list over ≤60 days, and the per-day "ideas" column is
the number to read, not the trade count (several bots copy one plan). Don't tune a
gate from a scan grid — use it to pick days, then judge with `algorithm_correctness`.

**How we'll know it worked:** a 🟢 cell's trades must match what the single-day
replay shows for that day with all three bot toggles on and the day revealed —
`tests/test_replay_scan.py` pins that the summary is built from the same
`apply_bar → force_flat → scorecard` path.

---

### 2026-10-25 — `setups/` package, setup-lab skill, 12-idea screen on SNXX/SNDQ/NBIZ/NBIC (no algorithm change)

**No live bot changed.** `orb.py` and `reversal.py` moved verbatim from
`analysis/` to `stockanalyzer/setups/` (only their relative imports changed);
callers import from the new path. The two new setups are backtest-only.

**What changed (code):**
1. `stockanalyzer/setups/`: one file per setup. `orb.py`, `reversal.py` (moved);
   `silver_bullet.py`, `double_bottom.py` (new); `base.py`, the contract new setups
   follow (`signal(SetupContext, cfg) -> SetupDecision`; `fire()` refuses stop ≥
   entry and targets < 1R); `harness.py`, the bar-by-bar backtest
   (`python -m stockanalyzer.setups.harness TICKERS --days 40 [--offset N] [--null 200]`).
   Judging reuses `replay.judge_intraday` (stop-first, flat at the 15:55 close),
   minus $0.02/share round trip.
2. `replay_scan._ENGINE_SOURCES` now includes `setups`. Without it, an edit to
   `orb.py`/`reversal.py` after the move would have served stale scan rows.
3. `.claude/skills/setup-lab/`: the idea → spec card → module → tests →
   backtest → record loop, with a jargon → code glossary (FVG, CE, sweep, MSS…).

**The screen (rules and pass rule written before the first run).** 12 pasted
ideas (ICT Silver Bullet, prev-PM Judas swing, London raid, ORG PM reversion,
lunch raid, prev-AM draw, range break, second chance, extension double bottom,
failed breakout, ABC + EMA9, resistance → support) coded to common rules: 5-min
bars, long only (the inverse ETF is the short side), entry at the trigger bar's
close or a resting order's level, 0.1% stop buffer, skip < 1R, stop-first, flat
at 15:55, one trade per idea per ticker-day, $0.02/share. Tickers: SNXX (2x long
SNDK), SNDQ (2x short SNDK), NBIZ (2x short NBIS), NBIS standing in for **NBIC**
(Corgi 2x long NBIS: median 9 of 78 five-minute bars trade, ~8k shares/day,
untradeable intraday). Pick on the latest 40 sessions (2026-08-28..10-23),
confirm on the 20 before (07-31..08-27). Pass rule: ≥ 8 trades; avg net R > 0 in
both halves, with the best day removed, and on both underlyings.
The ideas the source states as shorts were coded as their long mirror. Not
modelled: SMT vs NQ/ES/YM, news pauses, the London session (stocks don't trade
it; the pre-market range stood in), 1-min triggers.

| Idea | 40d n | avg net R | holdout n | avg net R |
|---|--:|--:|--:|--:|
| I1 Silver Bullet (long mirror) | 9 | +0.50 | 3 | +1.44 |
| I2 Judas swing, prev PM range | 10 | +0.54 *(2nd half −0.25)* | 1 | −1.15 |
| I3 pre-market raid (London adapted) | 6 | −0.38 | 2 | −1.03 |
| I4 opening-range gap, PM reversion | 14 | −0.15 | 7 | −0.78 |
| I5 NY lunch raid | 45 | −0.13 | 22 | +0.31 |
| I6 prev AM draw (mirror) | 15 | −0.37 | 3 | −0.61 |
| I7 consolidation range break | 118 | +0.02 | 63 | −0.05 |
| I8 second chance (break & retest) | 57 | −0.23 | 35 | −0.42 |
| I9 extension fade, double bottom | 57 | +0.18 | 25 | +0.11 |
| I10 failed breakdown | 99 | −0.52 | 56 | −0.19 |
| I11 ABC + EMA9 trail | 7 | +0.00 | 9 | −0.38 |
| I12 resistance → support | 22 | −0.35 | 16 | −0.22 |

Passed: **I1** and **I9**, now `silver_bullet.py` and `double_bottom.py`. The
production harness reproduces the screen trade for trade (same 57 + 9 entries).
Harness numbers, NBIS costed at its own price:

| Setup | 40d (dates) | win % | avg net R | halves | best day removed | holdout 20d | 60d | null p |
|---|--:|--:|--:|---|--:|--:|--:|--:|
| `xfade-dbl-bottom` | 57 (34) | 63 | +0.21 | +0.22 / +0.20 | +0.18 | +0.13 (25) | +0.19 (82 / 48) | 0.005 |
| `sb-am-bias` | 9 (9) | 44 | +0.55 | +1.26 / +0.34 | +0.17 | +1.49 (3) | +0.78 (12) | 0.045 |

Null = every trade re-entered at a random bar of the same ticker-day with the
same risk % and R target (200 reps); its mean was −0.10R and −0.16R.

**Read this honestly:**
- The two windows had **opposite regimes**: Aug SNDK +16% / NBIS +16%;
  Sep–Oct SNDK −17% / NBIS −27% (SNXX −37%, NBIZ +48%). The double bottom was
  positive in both, each time mostly on the leg trending up (Aug: SNXX +0.20,
  NBIS +0.17; Sep–Oct: NBIZ +0.50, SNDQ +0.51; the down-trending legs ≈ 0). So it
  behaves like a with-trend dip buy. That split was found after the fact, so it is
  H8's next pre-registered variant, not a rule.
- The edge is small: +0.2R on a 1R target at ~61% wins; cluster t ≈ 1.7. One
  bad week erases a month.
- Silver Bullet, the best known of the 12, has the best average per trade, but
  12 trades in 60 sessions is an anecdote (H9).
- Judas swing (I2) looked good on its average and failed the rule: the second
  half and the best-day-removed checks were negative. The rule did its job.
- The source ideas assume 1-min triggers for several setups; all of this is
  5-min.

**Not done (on purpose):** no live wiring, no replay bot, no threshold sweep. A
bot for either setup is its own `[ALGO]` PR with the user's approval.

**Next session, start here:** after ~20 new sessions (late Nov 2026) run
`python -m stockanalyzer.setups.harness SNXX SNDQ NBIZ NBIS --days 20` on only the
sessions after 2026-10-23. That is the first true out-of-sample read for H8/H9.
