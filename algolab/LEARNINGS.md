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

---

## Open hypotheses (test as data accumulates)

| # | Hypothesis | Evidence so far | Status | How to confirm/kill |
|---|---|---|---|---|
| H1 | **Daily MACD against the entry predicts losers.** | Losers avg macd_hist −0.124 vs winners +0.021; "MACD above signal" split W50%/L0% on 6M & 1Y. Directionally clean, not yet significant (p≈0.11). | **Acted (refined):** shipped as a GO veto 2026-06-18. ⚠️ First cut (raw MACD<signal) over-suppressed healthy buy-the-dips — a normal pullback rolls MACD under signal even in an uptrend. **Refined** to: veto only when the daily MACD *histogram* is on the wrong side **AND still deteriorating** (last bar worse than prior). A stabilizing dip passes; a falling knife is blocked. | Compare GO win-rate before/after; confirm it still lets with-trend "Support test"/"Pullback" GOs through (H3). |
| H2 | **The high-conviction layer is mis-calibrated (inverted).** 80+ score, high R:R, "engine bias agrees", "A-grade confluence" clustered on losers; snap_rr anti-predictive (winners R:R 1.38 vs losers 1.72; the 1.5–2.0 R:R band was worst). | Consistent across cuts, none significant. | **Watch only.** Do NOT re-weight yet. | When n≥100 ideas, split win-rate by score band and by R:R band; re-weight only if the inversion holds. |
| H3 | **"Support test (uptrend)" is the real edge; with-trend > counter-trend.** | Support-test 6/6; Breakout(volume) 6/8; counter-trend Pullback/Momentum poor. | Watch only (n too small). | Track idea-level win-rate per setup over time. |
| H4 | **The throwback/breakout model doesn't beat immediate entries.** | breakout_wait 9/15 (60%) vs immediate 8/13 (62%), but immediate captured ~2× profit/trade. | Watch only. | Compare idea-level avg_pnl breakout_wait vs immediate at larger n. |

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
