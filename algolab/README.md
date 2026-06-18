# algolab — the algorithm development lab

This directory is the **memory of how the trading algorithm is being improved**.
It exists so that each session builds on the last instead of re-litigating the
same dead ends. The algorithm itself lives in `stockanalyzer/` (mainly
`explain/swing.py`, `analysis/`, `verdict/`); this folder is the *learning loop*
around it.

## The one rule

> **Read [`LEARNINGS.md`](LEARNINGS.md) before you change the algorithm, and add a
> dated entry after.**

Most of what a single session's trade data "shows" is noise. `LEARNINGS.md`
records what has already been tested, ruled out, or is still an open hypothesis —
so we don't keep re-discovering that "score <60 wins more" is sample noise, or
re-blaming a setup whose losses were really one bad market day.

## Files

- **`LEARNINGS.md`** — the running journal: standing guardrails, a *ruled-out /
  do-not-repeat* list, an *open-hypotheses* table with status, and a dated
  session log. This is the file to read and to append to.
- **`analyze.py`** — the post-mortem. Run it to see the current state:
  ```
  python algolab/analyze.py
  ```
  It prints three views (see below). Point it at another DB with
  `STOCKANALYZER_DB=/path/to/x.db`.

## Two ways to read the book (this is the key idea)

The virtual book runs several bots over the **same** plan: `bot-GO` (strict GO),
`bot-70` (score ≥70, ignores the GO gate), `bot-BRK` (armed breakouts), and `me`
(manual). So one decision can become 3–4 rows — which *inflates the sample* and
makes a single idea look like a winning (or losing) streak.

- **Per-bot view** (`store.trade_stats`) answers *"which trading RULE earns its
  keep?"* — compare bot-GO vs bot-70 vs bot-BRK.
- **Algorithm view** (`store.algorithm_correctness`) answers *"were the engine's
  CALLS right?"* It collapses every bot that took one plan into a single **idea**
  via `cohort_id`, so each decision counts once. **This is the honest sample** for
  judging setups and score bands.

`cohort_id` = a stable hash of `(ticker, setup, kind, entry, stop, target, day)`,
assigned in `store._cohort_id` at insert time. Same idea across bots → same
cohort. Different day or different levels → different cohort.

## The development loop

1. **Look.** `python algolab/analyze.py`.
2. **Read.** `LEARNINGS.md` — is this pattern already ruled out or open?
3. **Hypothesize.** Write it down as an H# with how you'll confirm/kill it.
4. **Change at most 1–2 things.** Keep edits small and reversible. The algorithm
   is rule-based and tested — add a guard, re-weight a check, gate a setup; then
   run `pytest`.
5. **Record.** Add a dated entry to `LEARNINGS.md`: what changed, why, and the
   metric that will tell you if it worked. Challenge (don't silently overwrite)
   any prior entry the new data contradicts.
6. **Let it run.** Outcomes need real market days. Resist re-tuning on n=28.

## Guardrails (full list in `LEARNINGS.md`)

- ~100+ *independent ideas* before trusting a sub-group win-rate.
- Judge the algorithm on the **idea-level** view, not raw bot rows.
- Don't fish across all ~22 swing-checks; pre-register the few you test.
- `target_hit=100%` / `stop_hit=0%` is definitional, not predictive.
- Rule out same-ticker/same-day confounds before blaming a setup.

## Where the data lives

`trades.db` (SQLite, repo root). Schema and all analytics:
`stockanalyzer/data/store.py`. Decision context for every trade (signals,
indicators incl. ADX/DMI/Bollinger, verdict, swing-checks, cohort) is stored at
open time by `store.insert_trade`, so every closed trade can be re-examined.
