# algolab/live — what live trading sessions teach us

Backtests answer "does this rule have an edge over many days?". A live session
answers different questions: did the textbook rule show up on the tape today, did
we follow our own plan, and what did fees, fills and nerves cost? This folder
keeps those answers as a second data source next to the replay/backtest numbers
in [`../LEARNINGS.md`](../LEARNINGS.md).

**One live session is n=1.** Nothing here can change a bot or the score on its
own. A rule moves into `LEARNINGS.md` (Ruled out / Open hypotheses) only once the
ledgers show a pattern across **several distinct dates**, or a backtest confirms
it. See the guardrails at the top of `LEARNINGS.md`.

## Files

| File | What it holds |
|---|---|
| `YYYY-MM-DD-<slug>.md` | One note per live session: context, the plan, what happened (timeline), trades, verdicts, lessons. |
| `murphy_ledger.csv` | One row per **observation of a textbook rule** (John J. Murphy, *Technical Analysis of the Financial Markets*, `docs/`), or any other named rule, with a verdict. |
| `behavior_ledger.csv` | One row per **execution or behavior event**: chasing, a missed fill, a hesitation, following the plan. Includes the dollar cost when it can be measured. |
| `summarize.py` | Tallies both ledgers: per rule, validated / challenged / inconclusive across distinct dates; per behavior tag, count and total cost. |

```
python algolab/live/summarize.py
```

## Ledger columns

`murphy_ledger.csv`

| Column | Meaning |
|---|---|
| `date` | Session date (ET). |
| `ticker` | Underlying the rule was read on (SNDK, not SNXX). |
| `rule_id` | Stable short id, reused across sessions (e.g. `breakaway-gap-resists`). Add new ids freely; never rename old ones. |
| `source` | Where the rule comes from: `murphy ch4 p94` (book page), `study 2026-11-03`, `own`. |
| `expectation` | What the rule predicted, in one line, written **before** the outcome if possible. |
| `outcome` | What actually happened, with prices and times. |
| `verdict` | `validated`, `challenged` or `inconclusive`. |
| `backtest_ref` | A `LEARNINGS.md` hypothesis id or study that measured this rule over many days, if any. A live `validated` against a backtest `ruled out` is noted, not acted on. |
| `session` | The session note file name. |

`behavior_ledger.csv`

| Column | Meaning |
|---|---|
| `date`, `time_et`, `ticker` | When and on what. |
| `tag` | Stable short id (e.g. `chased-extension`, `stop-limit-no-fill`, `followed-exit-rule`). |
| `kind` | `mistake`, `good` or `neutral`. |
| `what_happened` | One line. |
| `cost_usd` | Measured cost (negative) or benefit (positive) vs the plan; empty if unknowable. |
| `rule_next_time` | The concrete rule that would prevent or repeat it. |
| `session` | The session note file name. |

## Verdict rules (keep them honest)

- **validated**: the rule's expectation happened, for the reason the rule gives.
- **challenged**: the expectation failed, or data from a backtest contradicts it.
- **inconclusive**: the move happened, but for another obvious reason (news, the
  whole market), or the outcome isn't known yet. Prefer this when unsure.
- Write the expectation before the outcome when you can. A rule "validated" with
  hindsight is weak evidence.
- A rule observed on several tickers in one sector on one day is **one** event,
  not several. Log one row with all tickers, or mark the extra rows `same-event`
  in `outcome`.
