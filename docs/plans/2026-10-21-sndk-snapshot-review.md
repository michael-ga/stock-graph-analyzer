# SNDK snapshot review → what the bots missed, and one setup to add

*2026-10-21 · source: `daychart` pictures of SNDK Oct 12–20, `daytrade_backtest.py`
replays of the same days, and a 60-day / 20-ticker check of the pattern.*

> Read with `algolab/LEARNINGS.md`. Nothing here is shipped yet. Everything below is
> either a measurement fix or a **paper bot that collects evidence** — no gate,
> weight or threshold of the existing bots is changed on this data.

---

## 0. What the pictures show

| Day | Prev close | 9:30 open | Real gap | Bot-measured gap | Open→close | Day low / high time |
|---|--:|--:|--:|--:|--:|---|
| Mon 10-12 | 1581.82 | 1537.15 | −2.8% | −1.0% | −3.2% | high 9:30 |
| Tue 10-13 | 1487.33 | 1405.75 | **−5.5%** | −3.4% | **+5.4%** | low 9:30, high 15:55 |
| Wed 10-14 | 1482.33 | 1427.38 | −3.7% | −2.5% | −1.7% | low 15:55 |
| Thu 10-15 | 1403.00 | 1312.97 | **−6.4%** | −4.5% | **+6.3%** | low 9:35, high 15:55 |
| Fri 10-16 | 1395.61 | 1348.82 | −3.4% | −1.1% | −3.7% | low 15:45 |
| Mon 10-19 | 1298.82 | 1358.50 | +4.6% | +3.4% | +0.9% | high 9:50, then faded |
| Tue 10-20 | 1371.20 | 1336.96 | −2.5% | −2.1% | +2.1% | low 9:30 |

The damage is done **overnight** (5 of 6 opens were gap-downs). Intraday, SNDK either
bottomed in the first 5 minutes and recovered most of the gap (13th, 15th, 20th) or
bled all day to the low (14th, 16th).

---

## 1. Bot limitations checked against the snapshot

### ✅ Real problems (fix these)

**L1 — The gap is measured from the pre-market open, not the 9:30 open.**
Already flagged as an "open observation" on 2026-07-26 as a *boundary* risk. The
snapshot shows it is systematic: the bot's gap was off on **all 6 days**, by
0.4–2.3 points, always toward zero (see table). Consequences:
- Gap-and-Go's `gap ≥ +2%` gate reads the wrong number.
- The reversal bots' "gap-down ≤ −2% is a knife, stand aside" guard reads the
  wrong number. On Fri 10-16 (real −3.4%, read as −1.1%) the knife guard would
  **not** trigger, on a day that closed at its low.
- Fix: measure the gap from the first **regular-session** bar (09:30 ET). At 09:45,
  when the ORB/reversal decisions start, that bar is already known, so there's no
  lookahead. Shared by `orb.opening_range`, `reversal_signal` and the replay, so
  fix it once in the shared as-of path.
- This is an algorithm change: own LEARNINGS entry, and ORB/reversal stats start a
  new baseline from the fix date.

**L2 — Live day-trades are held overnight, but the backtest/replay closes them at
end of day.**
`replay.judge_intraday` scores a day-trade at the 15:55 close (`"eod"`). The live
book has no Mon–Thu end-of-day flatten: `horizon_days=1` expires after ~1.5 days,
and only `bot-gap-mgd` flattens, only on Fridays. On SNDK this week the overnight
gap *was* the move.
- Example, Mon 10-19: Gap-and-Go fired at 09:45 (OR-high break 1392.90, stop ≈1356.28,
  2R target 1466). It closed at 1371.20: **−0.6R in the backtest**. Live, the trade
  stays open; Tue opened at 1336.96, below the stop, so it's a full stop-out.
- **L2b:** a stop is filled *at the stop price* even when the market opens through
  it, so that stop-out is recorded as −1R when the real fill was ≈−1.5R. Live
  results look better than reality on exactly the days that hurt most.
- Fix: flatten every day-trade (`bot-gap-*`, `rev-*`) into the 15:55 close Mon–Thu,
  like the Friday rule. Then backtest, replay and live agree. Separately, fill a
  gap-through stop at `min(stop, open)`.

### ⚠️ Worth watching (do NOT change on this data)

**L3 — Gap-and-Go has no daily-trend filter.** It fired on Oct 19 into a −20% month
on a gap-up that peaked at 09:50 and faded. Over 60 days SNDK's gap-ups split both
ways: big runners (Jul 30 +12.7%, Sep 4 +9.7%) and big fades (Jul 31 −12.1%, Aug 7
−7.2%, Aug 19 −6.8%). With n≈1 for "ORB in a downtrend", log it as a hypothesis,
not a gate.

**L4 — Long-only.** The cleanest repeating SNDK shape is the **gap-up fade** (high in
the first 5 min, close −3 to −12% below the open: Jul 31, Aug 7, Aug 19, Aug 25,
Aug 27, Sep 21). The bots can't short. Gap-and-Go correctly stayed out of these (no
OR-high break). Adding shorts is a large change; record only.

### ❌ Not a bot error

- **Swing bots never fired.** Every day the plan was `breakout_wait`, and the
  `Reward ≥ 1.5× risk` check failed. Volatility-aware stops on a 5%-range stock can't
  reach 1.5R to resistance in a downtrend. Correct to stay out.
- **Reversal bots — not verified for these 7 days.** By design, `with_trend` stands
  aside on a down trend / gap-down. But the 60-day `daytrade_backtest.py --variants`
  sweep on SNDK shows fires (`rev-trend-*` 1 fire +8.5R; `rev-strong-touch` 4 fires,
  2W/2L), and the sweep doesn't print dates. Whether any of those fell on Oct 12–20
  is open. (Note: `--variants` ignores `--date` and always sweeps ~60 days.)

---

## 2. Patterns seen

| # | Pattern | Seen on | Bot can take it? |
|---|---|---|---|
| P1 | **Gap-down → early low → reclaim → gap fill** | 13th, 15th, 20th | No: reversal bots stand aside on gap-downs, ORB needs a gap-up |
| P2 | Gap-down → bleed to close | 14th, 16th | Short only |
| P3 | Gap-up → fade from the open | 19th; often over 60d | Short only |

P1 is the only long-side pattern the current bots structurally cannot take, so
that's the one to test.

---

## 3. Evidence for P1 (pre-registered, then tested)

Rule written **before** looking beyond SNDK. `gap ≤ −2%` (real 9:30 open). From
09:45 to 11:30, enter on the first 5-min close above **both** the opening-range high
and VWAP. Stop just under the session low so far. Target = previous close (full
gap fill). Exit at 15:55 if neither is hit. Skip if target < 1R away.

**60 days × 20 liquid tickers** (SNDK MU AMD INTC NVDA SMCI MSFT TSLA AVGO WDC STX
PLTR COIN META AAPL AMZN GOOGL NFLX ARM MRVL):

| | Trades | Wins | Total R | Avg R |
|---|--:|--:|--:|--:|
| All | 26 | 20 | +8.0 | +0.31 |
| Excluding Oct 13 & Oct 15 | 19 | 13 | **+0.7** | +0.04 |

Read this honestly:
- **26 trades ≈ 15 independent days.** Sep 14 alone is 8 tickers (semis gapped
  together, net −1.1R); Oct 13/15 supply +7.3R of the +8.0R. It's the snapshot we
  started from, so it can't also be the proof.
- **Only 2 of 26 reached the gap-fill target.** Most exits were the 15:55 close for
  small gains. The full-fill target may be too far.
- Losers were few but full-sized (AMD 08-05 −1R, ARM 09-14 −1R).
- No spread/slippage modeled (stops are wide, so $0.02 is small, but non-zero).

**Verdict:** a real, recurring shape with a weak, unproven edge. That's exactly what
the H5/H6 playbook is for: a **paper bot that collects evidence**, not a live change.

---

## 4. Proposal — `rev-gapfill` paper bots (H7)

### Rules (pure as-of function, same pattern as `orb.py` / `reversal.py`)

| Rule | Value | Why |
|---|---|---|
| Gap | real 9:30 open ≤ prev close −2% | needs L1 fix first |
| Window | 09:45–11:30 ET | reuse the ORB freeze + window from `session.py` |
| Trigger | 5-min **close** > OR-high **and** > VWAP | reclaim confirmed, not a first-tick guess |
| Stop | session low so far × (1 − 0.1%) | the flush low is the invalidation |
| Target | variant A: prev close (full fill) · variant B: 50% of the gap | pre-registered A/B (§3 says full fill is rarely reached) |
| Geometry | skip if target < 1R away | same discipline as the other bots |
| Exit | flatten at 15:55, every day | never carries a day-trade overnight (L2) |
| Blocks | earnings day/next day; `crash_block` | an earnings gap is a different animal |
| Limit | one fire per ticker per day | |
| Size | $1,000 paper, $0.02/share spread | same as all bots |

Two bots, `gapfill-full` and `gapfill-half`, each with its own `cohort_id` (different
targets = different ideas).

### Build order (small, reversible steps)

1. **PR 1 — L1 fix.** Measure the gap from the 09:30 bar in the shared as-of path;
   tests pinning the AMD 07-22 and SNDK 10-13 cases. LEARNINGS entry: "ORB/reversal
   baseline resets here."
2. **PR 2 — L2 fix.** Mon–Thu 15:55 flatten for day-trade bots (extend `manage` /
   `mark_trades`); gap-through stops fill at `min(stop, open)`. LEARNINGS entry.
3. **PR 3 — H7 bots.**
   - `stockanalyzer/analysis/gapfill.py`: `gapfill_signal(intraday_df, prev_close,
     asof_ts, cfg)`, no I/O.
   - `GAPFILL_VARIANTS` config list; `app.py` `_run_gapfill_bots` beside
     `_run_reversal_bots`; a radar line "↩ Gap-fill reclaim".
   - Add to `daytrade_backtest.py --variants` and `replay.precompute_day` (same
     function, no drift; extend the import-identity test).
   - Tests: fires on the SNDK 10-15 fixture; doesn't fire on a 10-16 fixture (no
     reclaim); no-lookahead stepped-vs-truncated test; flatten at 15:55.
   - `BOT_RULES.md` section + LEARNINGS **H7** row.

### How we'll know (decided now, before data)

- Judge by **distinct days**, not rows: correlated sector gaps (memory, semis) count
  once per day. Same idea as `cohort_id`.
- **Keep** if avg R > 0 after spread at ≥ 50 distinct days, **and** still > 0 with the
  single best day removed.
- **Kill** if avg R ≤ 0 at 50 distinct days.
- A/B: `half` vs `full`: does the nearer target win more R, or just more often?
- Don't touch thresholds (−2%, 11:30, VWAP) before then.

---

## 5. SNDQ: the short side without short code

SNDQ = **Tradr 2X Short SNDK Daily ETF** (−2× SNDK's *daily* move, ~$150M assets,
~27M shares/day, trading since 2026-07-28). Close-to-close it tracked −2× SNDK within
0.30 pp on every one of 59 days (mean error +0.01 pp).

It mirrors the patterns. SNDK's gap-down **bleed** days (P2) are SNDQ **gap-up and
go** days, which the existing long-only Gap-and-Go bot can already take. SNDK's
gap-down **reclaim** days (P1) are SNDQ gap-up **fades**, and the OR-high-break rule
correctly stays out of those:

| Day | SNDQ gap | Open→close | ORB long (gap ≥2%, close > OR-high 9:45–11:30, stop OR-low, 2R, EOD exit) |
|---|--:|--:|---|
| 10-12 | +5.4% | +6.2% | 09:50 → −0.17R |
| 10-13 | +10.9% | −9.1% | no break (correctly out) |
| 10-14 | +7.3% | +3.0% | 11:05 → +0.52R |
| 10-15 | +12.8% | −10.3% | no break (correctly out) |
| 10-16 | +6.6% | +6.8% | 09:50 → +1.14R |
| 10-19 | −9.5% | −1.7% | n/a (gap-down) |
| 10-20 | +5.0% | −4.1% | no break (correctly out) |

Snapshot week: 3 trades, +1.49R. **Full 60 days: 8 trades, 4 wins, +0.77R**, so
the week flatters it again. Simplified check: no RVOL > 1.5 filter, real 9:30 open
(not the bot's pre-market reading, see L1).

**Proposal:** add SNDQ to the radar watchlist. That needs no code: the existing bots
simply see it.
- **L2 is a must-fix before this.** A 2× daily-reset ETF held overnight is the
  worst case for the "day-trade carried overnight" bug: volatility decay plus a
  doubled gap.
- **Independence:** an SNDK trade and an SNDQ trade on the same day are one bet on
  the same tape. Count them as one day in any evaluation (same rule as §4).
- **Spread:** $0.02 on a ~$16 share is ~0.12% per side. Fine, but larger in % than
  on SNDK.
- Only ~60 days of history exist, so treat backtests on it as anecdotes.

---

### Not proposed (and why)

- **Short gap-up fade (P3) via short-selling code:** adding shorts touches the whole
  book (`assess_position` mirrors, P&L sign, stats). For SNDK specifically, §5
  (trading SNDQ long) covers most of it without new code.
- **A trend filter on Gap-and-Go (L3):** n≈1. Watch `bot-gap-*` results split by
  daily trend first.
