# Trading-bot rule spec — for comparison against canonical trading rules

> Purpose: a precise, self-contained description of **how each virtual bot decides
> to enter, manage, and exit** — written so an AI (or a human) can diff it against
> textbook trading rules (Murphy, Carter, O'Neil, Minervini, etc.) and flag gaps,
> contradictions, or over-fits. All bots are **paper** (virtual $1,000/trade); none
> place real orders. Source of truth = the code paths cited; this doc is a mirror.

Code map:
- Entry signals / GO gate: `stockanalyzer/explain/swing.py` (`build_swing_plan`, `_build_side`)
- Bot entry wiring: `app.py` `_BOTS`, `_run_bots`, `_run_gap_bots`
- Fixed exits: `stockanalyzer/data/store.py` `mark_trades`
- Managed exits: `stockanalyzer/manage.py` `assess_position` + `store.manage_trades`
- Session clock: `stockanalyzer/session.py`
- Measurement: `store.algorithm_correctness`, `store.trade_stats`, `store.managed_vs_fixed`

---

## 1. Bot roster

| Bot | Strategy class | Entry trigger | Exit policy | Horizon | Managed? |
|-----|----------------|---------------|-------------|---------|----------|
| `bot-GO` | Swing (1–3d) | `plan.go == True` (full GO gate) | fixed stop/target/expiry | 3d | no |
| `bot-70` | Swing | `score≥70 & kind=immediate & not go & actionable & not extended` | fixed | 3d | no |
| `bot-BRK` | Swing breakout | `kind=breakout_wait & score≥55` (armed, two-phase fill) | fixed | 3d | no |
| `bot-gap-fixed` | Gap-and-Go (intraday ORB) | ORB breakout (see §3) | **fixed** stop/target (control) | 1d | no |
| `bot-gap-mgd` | Gap-and-Go (intraday ORB) | **same entry as bot-gap-fixed** | **managed** (see §5) | 1d | yes |

`bot-gap-fixed` and `bot-gap-mgd` open the *identical* trade (same entry/stop/target/
day ⇒ same `cohort_id`); they differ **only** in exit policy, so the pair is a clean
A/B for "does active management beat a fixed exit?" (`managed_vs_fixed`).

A trade is a 3-state machine: `pending` (armed breakout) → `open` → `closed`.

---

## 2. Entry pipeline — gates every swing entry must clear (`plan.go`)

`plan.go = immediate AND setup_present AND tier_ok AND rr≥1.5 AND aim≥min_move AND
vol_ok AND NOT(strong_downtrend, earnings_block, crash_block, overext, adverse_trend,
macd_against)`

| Gate | Rule | Canonical basis |
|------|------|-----------------|
| `setup_present` | A named setup detected (pullback-to-EMA, support test, breakout, momentum/gap, oversold-at-support, trend-change) | Pattern/structure must exist |
| `tier_ok` | With-trend, OR (countertrend allowed only if investor%≥45 or fast pace) | "Trade with the trend" (Murphy) |
| `rr ≥ 1.5` | Reward:risk to target1 ≥ 1.5:1 (`_MIN_RR`) | Positive expectancy / min R:R |
| `aim ≥ min_move` | Target move clears the pace's minimum (≈3% fast) | Don't trade for noise |
| `vol_ok` | **Setup-scoped volume** (see §4) | Volume confirms breakouts; dries on pullbacks (O'Neil) |
| `not strong_downtrend` | Not below a falling structure | Don't catch a falling knife |
| `not earnings_block` | No earnings inside the horizon | Event risk avoidance |
| `not crash_block` | Not a V-shaped crash-snapback bet | Snapbacks don't fill reliably |
| `not overext` | Not a 2σ Bollinger blow-off | Don't chase extension (Minervini) |
| `not adverse_trend` | ADX≥25 not running against entry | Don't fight a strong opposing trend |
| `not macd_against` | Daily MACD histogram not negative-and-deteriorating | H1: blocks falling knives, allows stabilizing dips |
| `actionable` (separate) | GO, or breakout coiled within `max(1.5%,0.6×ATR)` of the trigger | Only arm orders that can realistically fill |
| `not extended/emerging` | Fresh IPO / stretched move vetoed | No chasing thin-history extension |

Bot entry conditions then layer on top of `plan` (see §1 roster column).

---

## 3. Gap-and-Go entry (`_run_gap_bots`) — Carter-style ORB

Fires only when **all** hold:
1. `session.market_phase() == "regular"` and **NOT** the opening-range freeze (no entries 9:30–9:45).
2. `session.is_orb_window()` — **9:45–11:30 ET only** (no afternoon Gap-and-Go).
3. `orange["gap_up"]` — today's open ≥ **prev close +2%** (`_GAP_MIN_PCT`).
4. `price ≥ orange["high"]` — breaks the **15-minute opening-range high**.
5. `rvol > 1.5` (`_MIN_BREAKOUT_RVOL`) — volume confirms the break.

Order built: `entry = breakout price`, `init_stop = OR-Low × (1 − 0.1%)` (hard stop just
under the opening-range low), `target = entry + 2R` (`_ORB_TARGET_R`). Both gap bots open it.

| Rule | Value | Canonical basis |
|------|-------|-----------------|
| Gap requirement | open > prev close +2% | Gap-and-Go only on a real gap |
| Opening range | first 15 min H/L | Carter opening-range breakout |
| Time window | 9:45–11:30 ET | Morning momentum window; avoid lunch/afternoon chop |
| Volume confirm | rvol > 1.5 | A breakout without volume is suspect |
| Hard stop | just below OR-Low | ORB invalidation level |
| Target | 2R | Fixed reward:risk objective |

---

## 4. Volume gate (`vol_ok`) — scoped by setup type

| Setup bucket | Rule | Rationale |
|--------------|------|-----------|
| Breakout / gap-and-go / momentum-gap | `rvol > 1.5` | Breakouts need a volume surge |
| Pullback | `rvol < 1.0` (drying) | Healthy pullback = sellers exhausting; never blocked for being *quiet* |
| All other setups | neutral (no volume veto) | Support tests etc. legitimately trade quiet |
| Missing volume history (`rvol is None`) | passes in every bucket | Visible honesty, not a silent veto |

---

## 5. Exit policies

### 5a. Fixed exit (`mark_trades`) — all non-managed bots
- **stop_hit**: price ≤ stop → close at stop (stop wins on ambiguity; conservative).
- **target_hit**: price ≥ target → close at target.
- **expired**: held > `horizon_days × 1.5` (calendar) → close at market.
- **Breakout two-phase fill** (pending): (1) confirm a real break = price ≥ trigger×1.002,
  then (2) fill only on the **throwback to entry**. Runs-away-without-retest → expires
  unfilled; collapses through stop before fill → cancelled (not a loss). No spread haircut.

### 5b. Managed exit (`manage.assess_position`, `store.manage_trades`) — `bot-gap-mgd` only
`R = entry − init_stop` (pinned at open so trailing never shrinks R). Priority order:

| Rule | Trigger | Action | Canonical basis |
|------|---------|--------|-----------------|
| **weekend_flat** | Friday ≥15:45 ET and `hold_weekend` not set | flatten 100% | Don't carry day-trades over the weekend gap |
| **trend_test_tighten** | trend flip BEAR conf ≥0.6 **or** 5-min close < EMA8 | tighten stop to `price×(1−0.6×ATR)`, **never dump** | "Test, don't dump" — let a real break, not noise, exit |
| **trail_stop** | open R ≥ 1.5 | stop = max(3×ATR chandelier, EMA8×0.999); **ratchet-only** | Let winners run behind a trailing stop |
| **move_stop_breakeven** | open R ≥ 1.0 | stop = `entry + $0.02` (spread-adjusted breakeven) | Remove risk once paying; lock in no-loss |

**Invariant: a stop is NEVER widened** — enforced in `assess_position` (emits nothing if
not tighter) and defensively in `manage_trades` (`new_stop = max(old, suggested)`). Managed
closes haircut the exit by the spread, so realized P&L is friction-honest. Tightened stops
are closed by the *next* `mark_trades` tick (manage runs before mark).

---

## 6. Risk / sizing / friction

| Parameter | Value | Notes |
|-----------|-------|-------|
| Stake | $1,000 virtual / trade | `shares = stake / entry` |
| Risk unit R | `entry − init_stop` | Pinned at open |
| Min reward:risk | 1.5:1 (swing), 2:1 target (gap) | |
| Spread / commission | $0.02 / share flat (`_SPREAD_PER_SHARE = 0.02`) | breakeven = entry + $0.02; managed P&L net of it |
| Swing horizon | 3 days (expiry ≈ 4.5d) | |
| Gap-and-Go horizon | 1 day (expiry ≈ 1.5d) + Friday-flat | |

---

## 7. How the bots are judged (so rule-changes are measurable)

- `trade_stats()` — per-bot win rate / avg P&L (which *rule* earns its keep).
- `algorithm_correctness()` — idea-level, deduped by `cohort_id`, **excludes managed** rows
  (baseline = the engine's entry calls, one vote per idea).
- `managed_vs_fixed()` — paired delta `bot-gap-mgd − bot-gap-fixed` on shared cohorts
  (isolates the *exit management*, not entry quality). This is the H5 test.

---

## 8. Known simplifications / things an AI should flag when comparing

- **Long-only.** No short bots; `assess_position` mirrors are not implemented.
- **Pullback volume rule is a hard boundary** (`rvol < 1.0`): a pullback on exactly
  average volume is withheld — intentional, but stricter than some texts (which allow
  ≤ average). Tunable via `_PULLBACK_MAX_RVOL`.
- **Spread modeled as a flat $0.02 / share** (`_SPREAD_PER_SHARE`) — realistic for a
  fixed commission/spread, but does not scale with price or size; tunable.
- **EMA8 / "5-min"** = the shortest intraday frame the data layer provides (≈5-min bars
  via the 1D timeframe), not a guaranteed true 5-minute series.
- **Chandelier uses highest *session* close**, not highest-high-since-entry — a slightly
  looser trail than the classic chandelier.
- **No partial profit-taking / scaling out** (all-in / all-out) and **no position-size
  scaling by conviction or volatility** — fixed $1,000 stake.
- **Trend-flip confidence (0.6)** and **trail start (+1.5R)** / **breakeven (+1R)** are
  un-tuned defaults; do not re-tune on small n (see LEARNINGS guardrails).
