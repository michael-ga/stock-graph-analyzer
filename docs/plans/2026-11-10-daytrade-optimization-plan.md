# Day-trade setups, bots and the live watcher: review and optimization plan

Date: 2026-11-10. Status: **implemented in the working tree, not committed** (section 9).
Scripts that produced every number here: `algolab/screens/daytrade_review_2026_11_10/`
(see "Reproduce" at the end).

## What was reviewed

- **Bots and setups:** `setups/orb.py`, `setups/reversal.py`, `setups/silver_bullet.py`,
  `setups/double_bottom.py`, `manage.py`, `radar_worker.py`, `analysis/daycard.py`,
  `analysis/indicators.py`, `algolab/BOT_RULES.md`, `algolab/LEARNINGS.md`.
- **Live bot:** `.claude/skills/live-trade-watch/` (`SKILL.md`, `watch_setups.py`,
  `common.py`, `track_position.py`) and the `algolab/live/` ledgers.
- **Backtest data, reused, nothing refetched:** the cached replay A/B of 2026-11-02
  (`.local/replay-ab/cache`, 12 tickers, every day, 708 ticker-days, 396 trades). The
  cached rows for the four ETF-pair tickers are identical under the current tree's code
  hash (`a7e22d25…`). Window: 59 sessions, 2026-08-10 → 2026-10-30. **"Last 30" =
  2026-09-21 → 2026-10-30.**
- **New here:** the live watcher's coded rules were backtested for the first time, on the
  same cached 5-minute bars (9 underlyings, each mapped to a ±2x ETF as `common.mapped`
  does).

Read every number with the guardrails in `LEARNINGS.md`. Two extra cautions:

1. The reversal variants were pruned and the two setups were picked **on this same
   window**, so their rows are in-sample. Only new sessions count as evidence.
2. Bot rows are **gross** (the book charges fixed-exit bots no spread). Setup rows are net
   of $0.02/share. They are not comparable until finding F3 is fixed.

## Headline

1. **Two bot inputs don't do what the rule says.** The ORB volume gate passes 96% of all
   bars at 09:45 and 1–3% after 10:30, because pre-market bars carry zero volume. The gap
   is measured from the 04:00 pre-market open, so the ORB misses 47% of real gap-ups and
   the knife guard misses 42% of real gap-downs.
2. **The last 30 sessions earned 18% of the 59-session result** (+12.1 R of +66.6 R).
   With the best day removed, every bot and setup except the double bottom is at or below
   zero in that window.
3. **No bot or setup clears the real cost of a $300 trade.** $3 round trip is 1% of the
   stake. The best average gross move per trade is 0.44% (`sb-am-bias`). Break-even stake
   ranges from about $680 to $2,030.
4. **The live watcher is silent or negative as coded.** Of 8,752 scanner candidates in 59
   sessions, 15 passed the R:R gate. The 14 failed-bounce shorts it would have taken won
   14% of the time and lost $8.15 each.
5. **AI traders are not a measurable price factor yet.** Adoption is real but small, and
   no study measures crowding from retail AI on intraday prices. The plan logs crowding
   proxies in shadow and tests them later; it does not put them in the score.

## 1. Scoreboard (cached replay, 12 tickers)

R per trade. Bots gross, setups net of $0.02/share. "No best day" drops the best date.

| Bot / setup | Last 30: trades / days | Win % | Avg R | Net R | No best day | Prior 29: Avg R | Net R | All 59: Net R | Day t |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| `bot-gap-fixed` | 14 / 9 | 57 | +0.04 | +0.60 | −0.04 | +0.05 | +0.74 | +1.34 | 0.33 |
| `bot-gap-mgd` | 14 / 9 | 50 | −0.05 | −0.70 | −0.17 | +0.15 | +2.19 | +1.49 | 0.46 |
| `rev-strong-touch` | 23 / 16 | 17 | +0.20 | +4.66 | −0.19 | +0.82 | +11.41 | +16.07 | 0.91 |
| `rev-trend-confirm` | 5 / 5 | 0 | −1.00 | −5.00 | −1.00 | +2.38 | +16.63 | +11.63 | 0.99 |
| `sb-am-bias` | 29 / 21 | 34 | +0.10 | +2.88 | −0.06 | +1.12 | +20.09 | +22.97 | 1.57 |
| `xfade-dbl-bottom` | 123 / 30 | 56 | +0.08 | +9.67 | +0.05 | +0.03 | +3.40 | +13.07 | 0.67 |
| **Whole book** | 208 | | | **+12.11** | | | **+54.46** | **+66.57** | |

"Day t" is the t-statistic of daily net R over all 59 sessions. Nothing reaches 2.

By instrument, all 59 sessions:

| Setup | 2x ETFs (SNXX, SNDQ, NBIZ) | Stocks (9) |
|---|---|---|
| `xfade-dbl-bottom` | 61 trades / 44 days, 66% win, **+0.24 R, t 2.54** | 181 trades, 51% win, −0.01 R |
| `sb-am-bias` | 9 trades, +1.06 R | 38 trades, +0.35 R |
| `rev-trend-confirm` | 7 trades, **0 wins**, −1.00 R | 5 trades, 3 wins, +3.73 R |
| `rev-strong-touch` | 24 trades, 17% win, +0.35 R | 13 trades, 23% win, +0.58 R |
| `bot-gap-fixed` | 17 trades, +0.15 R | 12 trades, −0.10 R |

The double bottom was picked on the ETFs, and the nine stocks it was never tuned on show
no edge. Either the edge needs 2x-ETF volatility or it is selection. H8's new-session test
decides.

## 2. Findings

### F1. The ORB volume gate is a clock, not a volume filter

`relative_volume` is the last bar's volume over the mean of the last 20 bars. The intraday
frame is fetched with `prepost=True`, and Yahoo's extended-hours bars come back with
`volume = 0` (100% of pre-market bars in the cache; `providers.py` says so in a comment).
At 09:45 the 20-bar window holds 17 zero-volume bars and 3 real ones.

| Bar | 09:45 | 09:50 | 09:55 | 10:00 | 10:15 | 10:30 | 11:00 | 11:30 |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| Share of all bars passing `rvol > 1.5` today | 96% | 85% | 61% | 53% | 12% | 4% | 1% | 3% |
| Same test, time-of-day RVOL (same clock bar, prior 10 sessions) | 12% | 12% | 13% | 12% | 12% | 11% | 13% | 12% |

Result: 22 of 29 ORB entries fired at 09:45 or 09:50. The rule as written ("volume
confirms the break") has never been tested. The same function gates the swing engine's
breakout (`rvol > 1.5`) and pullback (`rvol < 1.0`) checks on intraday frames. Live adds a
second distortion: the last bar is still forming, so its volume is partial.

### F2. Gap, session low and session high are read from pre-market bars

`daycard._session_bars` returns every bar of the last calendar day, 04:00 onwards.

| Measure (720 ticker-days) | From the 04:00 open (today) | From the 09:30 open | Agree |
|---|--:|--:|--:|
| Gap-up ≥ 2% (ORB gate) | 102 | 151 | 80 |
| Gap-down ≤ −2% (knife guard, both reversal variants) | 117 | 162 | 94 |

- The ORB skips 71 of 151 real gap-up days and trades 22 days that did not gap at the open.
- The knife guard misses 68 of 162 real gap-downs and stands aside on 23 non-gap days.
- The day card's session low is a pre-market print on 37% of ticker-days, the session high
  on 36%. The reversal bots use that low as support and for the "lower 35% of the range"
  test.

The gap issue has been an open observation in `LEARNINGS.md` since 2026-07-26. The session
low/high and knife-guard consequences are new.

### F3. Bots are not charged costs, and on cheap ETFs the cost is most of the edge

Reversal stops sit a median 0.41% from entry: $0.08 per share on instruments under $60
(35 of 49 reversal trades).

| Cost model, round trip | `rev-strong-touch` | `rev-trend-confirm` |
|---|--:|--:|
| Book as is (none) | +16.1 R | +11.6 R |
| $0.02/share (what the harness charges setups) | +7.9 R | +10.2 R |
| 10 bps of price | +6.1 R | +9.1 R |

On the under-$60 trades the $0.02 cost is 9.3 R of 13.2 R gross. The flat $0.02 is also too
cheap for SNDK near $1,000 (0.2 bps). No model includes stop slippage.

### F4. ORB geometry: the 2R target is decorative

- Median risk is 5.3% of price (stop under the 15-minute low after a gap). 2R is +10.5%
  from a 09:45 entry.
- Reached before the stop: +0.5R 14/29, +1R 7/29, +1.5R 2/29, **+2R 1/29**.
- 24 of 29 fixed exits were the close. The bot is "buy at 09:45, hold to the close".
- **H5 has its ~30 pairs (in replay): managed − fixed = +0.005 R, t = 0.05** (29 pairs;
  managed better on 17, worse on 11). Breakeven (+1R) and the trail (+1.5R) rarely arm;
  the rule that fires is the EMA8 "test" tighten, which trades a smaller average win
  (0.40 R vs 0.63 R) for a smaller average loss (−0.37 R vs −0.58 R).

### F5. Reversal stops are inside one bar of noise

| Setup | Median stop distance | Median 5-min bar range | Stop in bars |
|---|--:|--:|--:|
| `rev-strong-touch` | 0.39% | 0.80% | 0.6 |
| `rev-trend-confirm` | 0.48% | 0.81% | 0.7 |
| `sb-am-bias` | 0.58% | 0.31% | 1.8 |
| `xfade-dbl-bottom` | 0.81% | 0.42% | 2.2 |
| `bot-gap-fixed` | 5.27% | 0.80% | 8.7 |

After a `rev-strong-touch` stop-out (30 cases): price was back above the entry within six
bars 53% of the time, later reached +1R 80% of the time and **later reached the full 3%
target 37% of the time**. The median overshoot below the stop was 3.2 R (about 1.3%).
That fits the 2026-11-02 result that a fixed 0.5% or 1.0% stop loses more: the overshoot is
bigger than both. Direction is often right; entry and stop are too early and too tight.
All of `rev-strong-touch`'s profit came from entries before 11:00 (18 trades, +23.4 R; 19
later trades, −7.3 R). That split is post-hoc, so it is a hypothesis, not a rule.

### F6. Account view: the $3 fee is larger than every measured edge at $300

| Setup (all 59) | Median risk | Avg gross per trade | Net per trade at $300 | at $500 | at $1,000 | Break-even stake | Fee in R at $300 |
|---|--:|--:|--:|--:|--:|--:|--:|
| `sb-am-bias` | 0.58% | +0.44% | −$1.68 | −$0.81 | +$1.38 | $684 | 1.7 R |
| `bot-gap-fixed` | 5.27% | +0.34% | −$1.99 | −$1.32 | +$0.36 | $894 | 0.2 R |
| `rev-trend-confirm` | 0.48% | +0.33% | −$2.01 | −$1.35 | +$0.30 | $909 | 2.1 R |
| `rev-strong-touch` | 0.39% | +0.18% | −$2.47 | −$2.11 | −$1.23 | $1,695 | 2.6 R |
| `xfade-dbl-bottom` | 0.81% | +0.15% | −$2.56 | −$2.26 | −$1.53 | $2,034 | 1.2 R |
| `xfade-dbl-bottom`, 2x ETFs only | 1.58% | +0.57% | −$1.28 | −$0.14 | +$2.72 | $524 | 0.6 R |

In the last 30 sessions every row is negative at every size. This is system arithmetic,
not advice on how much to trade: a setup must average more than +1% gross per trade to
break even at $300, and none does. R-based scoreboards hide this.

### F7. The live watcher's gate rejects 99.8% of what the scanner finds

`common.plan` forces a 4% minimum stop and needs R:R ≥ 1.5 after the $3 fee at $300, so
the target must be at least +8.5% on the ETF (+4.25% on the underlying, after entry).

| Scanner rule | Candidates (59 sessions × 9 underlyings) | Passed the gate | Median R:R |
|---|--:|--:|--:|
| Failed bounce to VWAP (short) | 5,983 | 14 | 0.16 |
| Trend pullback, short / long | 1,184 / 908 | 0 / 0 | 0.03 |
| VWAP reclaim (long) | 675 | 0 | −0.02 |
| Oversold bounce | 2 | 1 | 1.42 |

- Each rejected candidate prints a `👀 WATCH … SKIP` line. That is about 16 lines per
  underlying per day that the relay has to suppress. The failed-bounce rule has no
  `sent` key, so it also repeats on consecutive candles.
- The gate only opens on crash days (day low ≥ 4.25% under VWAP at the bounce).

### F8. The trades the gate lets through lost

Failed-bounce shorts taken, $300, $3 fees, ETF spread ignored:

| Exits | Trades / days | Win % | Avg ETF move | Avg $ | Total $ | Day t |
|---|--:|--:|--:|--:|--:|--:|
| Plan only (stop, target, 14:55) | 14 / 8 | 14 | −1.72% | −$8.15 | −$114 | −2.82 |
| + breakeven at +1R, give-back exit | 15 / 8 | 20 | −1.72% | −$8.15 | −$122 | −3.34 |
| + tracker's VWAP-against exit | 17 / 8 | 18 | −0.75% | −$5.24 | −$89 | −1.82 |

Nine full stops against one target. Eight days is an anecdote, but there is no positive
evidence either: `SKILL.md` itself calls the rule "discretionary, not a backtested edge".
Mechanism: a stock that fell 4%+ and climbed all the way back to VWAP has shown strength;
the scanner shorts it there. The VWAP-against exit cut the average loss because the
entries were losers. That is not evidence the exit works, and it is the rule
`LEARNINGS.md` ruled out on 2026-11-03.

### F9. Live watcher: what the data supports

- **The 14:55 time stop for shorts.** Underlyings down ≥ 3% at 15:00 rose +0.48% from
  15:00 to the close (68 cases, 30 dates, t = 5.2), most of it by 15:30 (+0.27%, t = 3.9).
- **The R:R gate and the paper-trade ledger.** The gate kept the watcher out of 8,737
  low-payoff candidates. The two user-idea paper trades both went the bot's way.
- The first-15-minute freeze, stop-market orders, and one bet per underlying are unchanged.

### F10. Live watcher: bugs and inconsistencies

| Where | Problem | Effect |
|---|---|---|
| `common.py` `ET = timezone(-5h)` | Fixed offset | Every clock rule is an hour off from 2027-03-14 (DST) |
| `watch_setups.py` failed bounce | No `sent` key | Repeats on consecutive candles |
| `common.context` / `watch_setups` volume grade | Bar volume vs the session average | Midday bars always read "weak", morning bars "strong" (intraday U-shape); "$300 size" fires by clock |
| `watch_setups.py` `DOWN_MKT` | `iloc[-2]` assumes today's bar exists | One day stale when started before the open |
| `track_position.py` VWAP-against exit | Wired as an ACTION | Contradicts "Ruled out" 2026-11-03 |
| `SKILL.md` Murphy table, `murphy_ledger.csv` | VIX cited as ch. 17 (the skill), volume as ch. 3 (the ledger) | Contrary opinion is ch. 10, volume is ch. 7 |
| Skill vs app | The watcher trades none of the app's backtested setups; its own rules are in no harness | Neither side learns from the other |

### F11. Live day trades were held overnight (found while implementing)

`TradeRepository.mark` closed a bot position only at its stop, its target or after
`horizon_days × 1.5` days. A one-day Gap-and-Go or reversal trade opened at 10:00 was
therefore still open the next morning (the managed bot flattened only on Fridays). The
replay and every backtest above close day trades at the 15:55 bar, so every number in
this document assumes an exit the live book did not make. Fixed: a bot's one-day
position still open at 15:58 ET on its fill day is closed at the market (`eod`).

### F12. The watcher's "tested" gap setup does not survive its own definitions

Re-run of `gap_check` exactly as coded (20-name basket, ≥ 3 of 20, SPY below its 50-day
the day before), 2023-12-21 → 2026-11-09 hourly bars, 22 names, $300 on a 2x ETF after $3
(`algolab/screens/gap_setup_revalidation_2026_11_10/revalidate.py`):

| Coded rule (down market + breadth) | Trades / dates | Win % | $ per trade | Halves | Without the best date |
|---|--:|--:|--:|---|--:|
| Failed gap-up → inverse ETF | 155 / 28 | 52 | +$1.10 | +$1.78 / +$0.35 | −$0.03 |
| Reclaimed gap-down → long ETF | 136 / 17 | 45 | −$3.88 | −$3.64 / −$4.35 | −$5.35 |

The 2026-10-23 study (51 names, ≥ 6 of 51, its own definitions) reported a clear edge; the
coded approximation keeps a weak one on the short side and none on the long side. NBIS is
missing (Yahoo refuses its hourly range).

## 3. Cross-check with Murphy

Chapter numbers are from memory of the 1999 edition. The PDF in `docs/` is a scan and
could not be searched in this session, so page numbers are not given.

| Murphy | What the book asks | What the code does | Gap |
|---|---|---|---|
| Ch. 7 Volume | A breakout needs heavier volume | F1: the gate is a clock | Rule not implemented |
| Ch. 4 Trend, gaps | Measure the gap between sessions; trade with the trend | F2: gap from the 04:00 print. `rev-strong-touch` buys in any trend | Input wrong; counter-trend by design |
| Ch. 16 Money management | Reward at least 3× the risk; stops wide enough for the market's volatility | ORB realises 0.63 R wins vs 0.58 R losses (F4). Reversal stops are 0.6 of a bar (F5) | Both ends: no payoff, and a stop inside noise |
| Ch. 15 Systems (ADX) | Trend tools in trending markets, oscillators and fades in ranges | No bot reads intraday ADX. The watcher uses it only for size | See the ADX split below |
| Ch. 10 Oscillators | Oversold persists in a strong trend; divergence is the signal | Oversold bounce is flagged risky and gated | Consistent |
| Ch. 17–18 Intermarket, breadth | Confirm with related markets | Only the watcher's gap setup checks breadth | See the peers split below |

Two splits, each stated before it was run, both exploratory:

- **ADX regime.** `xfade-dbl-bottom` with 5-min ADX < 20 at entry: 73 trades / 42 days, 63%
  win, **+0.22 R**, positive in both halves (+0.25, +0.18). ADX 20–30: −0.01 R (88). ADX ≥
  30: −0.02 R (81). A fade earns only in a range, as chapter 15 says.
- **Peers.** Entry when the median peer is above its open, vs below: `sb-am-bias` +0.87 R
  (21) vs +0.18 R (26); `bot-gap-fixed` +0.14 R (17) vs −0.09 R (12). The double bottom
  shows no difference (+0.05 vs +0.06). Momentum entries want confirmation; fades don't.
  This matches the watcher's own two-year study (lone-stock versions lose).

## 4. Outside research worth relying on

- **Opening range breakout.** Zarattini, Barbon and Aziz (2024, SSRN 4729284) test a
  5-minute ORB on 7,000+ US stocks, 2016–2023. Per the authors' summary the edge is in
  "stocks in play" (the top 20 by opening relative volume each day) and holds after costs;
  secondary summaries give the rules as opening-range volume ≥ 100% of its 14-day average,
  a stop at 10% of the 14-day ATR and an exit at the close. The full PDF was not read here.
  The app's ORB shares only the name: a fixed watchlist, a 15-minute range, a stop of about
  5% of price, a 2R target and a volume gate that does not work.
- **Late-day flows.** Barbon et al. (2021) find leveraged-ETF rebalancing and option
  hedging produce end-of-day momentum and mean reversion that fade the next day. On this
  sample the 15:00 → close move after a ≥ 3% drop was a bounce (F9) and after a ≥ 3% rise
  was flat (+0.12%, t = 0.6). Rebalance-driven continuation is not visible here.
- **Intraday momentum.** Gao, Han, Li and Zhou (2018): the first half-hour predicts the
  last on SPY, weakly (replications find about 1–2% of variance). Not a setup by itself.

## 5. AI traders as a factor

**What is known.**
- Adoption is real and small. Robinhood reported 150,000+ agentic trading accounts out of
  27 million funded accounts by 2026-09-30, with about 30 million tool calls a day.
- Regulators worry about herding (Bank of England, June 2026: many agents reacting to the
  same news at once). In simulations AI agents sustain collusive profits without
  communicating (Dou, Goldstein, Ji, NBER 34054). In experimental markets LLM agents show
  a disposition effect and extrapolate recent prices (Ouyang, 2026).
- **No study was found that measures crowding from retail AI on intraday prices.** Nothing
  supports a score input today.

**Where it would show up in this system, if it does.**
1. **Shared levels.** Models trained on the same texts mark the same levels: opening-range
   high and low, VWAP, yesterday's high and low, round numbers. Stops then cluster just
   beyond them. Every setup here stops 0.1–0.2% past exactly such a level. F5's stop-run
   numbers are what crowded stops look like, though a stop of 0.6 bars is swept by plain
   noise too. The two can't be separated yet.
2. **Faster decay.** A crowded signal moves before the bar closes. A setup whose edge
   disappears when the entry is delayed one bar is fragile.
3. **Shared headlines.** Agents read the same news at the same second: sharper first
   spikes, more partial reversals.
4. **The instruments.** 2x single-stock ETFs are the retail product; their flows are
   mechanical and public.

**What to do: log in shadow, decide later (H14 below).** Per signal, store:
`levels_nearby` (count of textbook levels within 0.3% of the entry), `sweep_rate_20`
(share of that setup's last 20 sessions of stop-outs that closed back above the entry
within six bars), `delay1_r` (the trade's R with a one-bar-late entry) and `or_fail_20`
(share of opening-range breaks that closed back inside within three bars). An attention
proxy (ETF volume over its 20-day average) is free; social-mention counts need a new feed.

F1–F3 and F6 are measured and large. AI crowding is unmeasured and second-order until
they are fixed.

## 6. Plan

Order matters: measurement first, then inputs, then geometry. Each `[ALGO]` item is its own
PR with a replay A/B and needs the owner's approval, per `CLAUDE.md`.

### Phase 0: make the scoreboard honest (no decision changes)

| # | Change | Where | Done when |
|---|---|---|---|
| 0.1 | Report column "net of cost" for every bot: `max($0.02/share, 10 bps of price)` round trip | `scripts/algo_replay_ab.py`, `algolab/analyze.py` | Bots and setups share one cost basis in the A/B table |
| 0.2 | Account view per bot and setup: average gross % per trade, net $ at $300 / $500 / $1,000 after $3, break-even stake | same | Table F6 prints from `analyze.py` |
| 0.3 | Record H5's replay verdict and F1–F8 in `LEARNINGS.md`; add H14–H18 | `algolab/LEARNINGS.md` | Dated entry added |
| 0.4 | Shadow-log the crowding fields in trade snapshots | `explain/snapshot.py` | Fields present; `algo-gate` shows 0 decision flips |

### Phase 1: fix the two inputs (`[ALGO]`, one PR each)

| # | Change | Affects | Evidence to attach |
|---|---|---|---|
| 1.1 | RVOL on regular-session bars only, normalised by time of day (same clock bar, prior 10–14 sessions), completed bars only | ORB gate; swing breakout and pullback volume checks on intraday frames | `algo_gate.py --history full`; expect far fewer 09:45 entries |
| 1.2 | Gap, session open, session high and low from the 09:30 session | ORB gap gate; both knife guards; reversal support and range position | Same; list the 71 + 22 ORB days and 68 + 23 knife days that flip |

These bring the code to the written rule, so judge them as corrections: accept a flat net R,
investigate a clearly negative one. Both reset the ORB and reversal baselines; say so in the
entry.

### Phase 2: setup geometry (pre-registered variants, judged on new sessions only)

Each is a new variant next to the current one, screened on the latest 40 sessions with a
20-session holdout, then confirmed on sessions after the PR date.

| # | Variant | Spec | Keep / kill |
|---|---|---|---|
| 2.1 | `orb-sip` | 5-min range; opening RVOL ≥ 1.0 vs its 14-day average; stop 10% of the 14-day ATR; exit at the close; no target | Keep if net R > 0 at ≥ 30 dates and without the best date |
| 2.2 | ORB exit | Drop the 2R target (reached 1/29); close-only vs current managed | Paired by `cohort_id`, ≥ 30 live pairs |
| 2.3 | `rev-*-vol` | Stop ≥ 1.5× the instrument's median 5-min range, aim scaled to keep R:R, entries before 11:00 | Net of cost (0.1) > 0 at ≥ 30 dates. This is a different spec from the fixed 0.5% / 1.0% stops already ruled out |
| 2.4 | `xfade-chop` | Current rules plus 5-min ADX(14) < 20 at entry | H8's rule: > 0 at ≥ 100 dates and without the best date |
| 2.5 | `sb-peers` | Current rules plus median peer above its open | H9's rule at ≥ 50 dates |
| 2.6 | `rev-trend-confirm` on 2x ETFs | 0 wins in 7; observe only | Decide with H11 on new sessions; no filter yet |

### Phase 3: the live watcher

| # | Change | Why |
|---|---|---|
| 3.1 | `ET` from `zoneinfo("America/New_York")`; `sent` key for the failed bounce; `DOWN_MKT` by date; time-of-day volume grade; Murphy chapter fixes | F10 |
| 3.2 | One summary line per underlying per 15 minutes instead of a WATCH per rejected candle; show the target % the gate needs | F7 |
| 3.3 | Failed bounce: WATCH only, with "backtest negative, 14 trades" in the message, until a variant passes | F8 |
| 3.4 | Mark every setup in `SKILL.md` with an evidence grade: tested, backtested negative, untested | The reader should see the grade in the ACTION block |
| 3.5 | Move the watcher's rules into `stockanalyzer/setups/` (signal on the underlying, order on the ETF) | Puts them under the harness, the replay A/B and `algo-gate` |
| 3.6 | Paper-track `xfade-dbl-bottom` on the 2x ETFs from the watcher | The only row with t > 2 (in-sample); nets −$1.28 at $300, so paper first |
| 3.7 | Keep the 14:55 short time stop; relabel the tracker's VWAP-against exit as a plan level and log both outcomes | F9, F8 |
| 3.8 | Re-validate the "tested" gap setup on the cache plus SPY and the 20-name basket | Not covered here: the cache has no SPY or basket bars |

### Phase 4: crowding

Run H14 once Phase 0.4 has logged ≥ 100 independent ideas.

## 7. Hypotheses to add to `LEARNINGS.md`

| # | Hypothesis | Evidence so far | Confirm / kill |
|---|---|---|---|
| H14 | Signals at crowded levels (high `levels_nearby`, high `sweep_rate_20`) underperform | None; F5 is suggestive only | Top vs bottom tercile of the crowding fields at ≥ 100 ideas, pre-registered direction, p < 0.05 |
| H15 | The double bottom earns only in a range (5-min ADX < 20) | +0.22 R (73 / 42 days), both halves positive; else ≈ 0 | Variant 2.4 on new sessions |
| H16 | Momentum entries (ORB, Silver Bullet) need peers above their open | `sb` +0.87 vs +0.18; ORB +0.14 vs −0.09; small n | Variant 2.5; ORB at ≥ 30 dates per side |
| H17 | Names down ≥ 3% at 15:00 bounce into the close | +0.48%, t 5.2, 30 dates, one regime | Sessions after 2026-10-30 only; keep if > 0 with t ≥ 2 at ≥ 30 new dates. An exit rule, not an entry: +1% on a 2x ETF equals the fee |
| H18 | Reversal entries after 11:00 lose | −7.3 R (19) vs +23.4 R (18); post-hoc | Variant 2.3 |

H5 (managed vs fixed): record 29 replay pairs, Δ +0.005 R, t 0.05. Keep collecting live
pairs; the replay says management neither earns nor costs.

## 8. Not covered

- Swing bots (`bot-GO`, `bot-70`, `bot-BRK`).
- Sessions after 2026-10-30. Nothing was refetched, so the six November sessions stay
  untouched for H8, H9 and H11.
- The watcher's gap setup and the VIX conditions (no SPY, basket or VIX bars in the cache).
- ETF spreads and stop slippage in the watcher backtest; ETF prices are mapped from the
  underlying.
- Live timing: partial bars, 20-second polling and relay delay.

## Reproduce

From the repository root, with `PYTHONPATH` set to the repository root (PowerShell:
`$env:PYTHONPATH = "."`) and any existing empty folder as `OUT`:

```
.venv/Scripts/python.exe algolab/screens/daytrade_review_2026_11_10/window30.py OUT        # section 1
.venv/Scripts/python.exe algolab/screens/daytrade_review_2026_11_10/mechanisms.py OUT      # F1-F5, F9, peers
.venv/Scripts/python.exe algolab/screens/daytrade_review_2026_11_10/account_view.py OUT    # F6
.venv/Scripts/python.exe algolab/screens/daytrade_review_2026_11_10/adx_regime.py OUT      # ADX split
.venv/Scripts/python.exe algolab/screens/daytrade_review_2026_11_10/skill_backtest.py OUT  # F7, F8
```

Run them in this order: `window30.py` writes the trade table the others read, and
`account_view.py` writes the harness table `adx_regime.py` reads. `.local/daytrade-review`
(gitignored) works as `OUT`. They read only `.local/replay-ab/` and use no network.

## Sources

- Zarattini, Barbon, Aziz, "A Profitable Day Trading Strategy For The U.S. Equity Market":
  https://concretumgroup.com/a-profitable-day-trading-strategy-for-the-u-s-equity-market/ ,
  https://www.cxoadvisory.com/individual-investing/intraday-trading-of-overactive-stocks-via-opening-range-breakout/
- Barbon et al., "Liquidity Provision to Leveraged ETFs and Equity Options Rebalancing Flows":
  https://abarbon.com/papers/liquidity-provision-to-leveraged-etfs-and-equity-options-rebalancing-flows
- Gao, Han, Li, Zhou, "Market intraday momentum" (summary): https://eranraviv.com/market-intraday-momentum
- Dou, Goldstein, Ji, "AI-Powered Trading, Algorithmic Collusion, and Price Efficiency":
  https://papers.ssrn.com/abstract_id=4452704
- Ouyang, "Dissecting AI Trading: Behavioral Finance and Market Bubbles": https://arxiv.org/abs/2604.18373
- CoinDesk, 2026-09-30, Robinhood agentic trading:
  https://www.coindesk.com/markets/2026/09/30/robinhood-is-giving-customers-an-ai-agent-that-trades-for-them-plus-10x-crypto-bets

## 9. Implementation (2026-11-10, working tree, not committed)

| Item | Status | Where | Evidence |
|---|---|---|---|
| 0.1–0.2 cost basis, account view | done | `stockanalyzer/costs.py`, `scripts/algo_replay_ab.py`, `algolab/analyze.py` | Phase 0 A/B: no trade difference |
| 0.3 LEARNINGS | done | `algolab/LEARNINGS.md` (2026-11-10 entry, H14–H18, two ruled-out rules, H5 replay read) | |
| 0.4 crowding shadow log | done | `explain/crowding.py`, snapshots from the radar worker and the replay | no decision reads it |
| 1.1 time-of-day RVOL `[ALGO]` | done | `indicators.py`, `setups/orb.py`, `explain/swing.py`, `data/volume_baseline.py` | whole book +2.01 R, ORB 26 → 26 trades on different days |
| 1.2 09:30 session `[ALGO]` | done, **regression under the owner's rule** | `analysis/daycard.py` | whole book −1.46 R; ORB 26 → 45 trades, +0.02 R each. A correction of the input; owner decides |
| 2.1 `orb-sip` | **failed the screen, removed** | — | −0.17 R screen, −1.18 R second half |
| 2.2 ORB exit | replaced by the close-of-day flatten (F11) | `db/repositories/trades.py` | the fixed bot already exits at the close 24/29 |
| 2.3 `rev-*-vol` | done, replay only | `setups/reversal.py` `REVERSAL_CANDIDATES` | +6.4 / +2.6 R in-sample, collecting |
| 2.4 `xfade-chop` | done, backtest only | `setups/double_bottom.py` | +11.0 R (64 trades) in-sample, collecting |
| 2.5 `sb-mkt` (QQQ, not the peer median) | done, backtest only | `setups/silver_bullet.py`, `setups/base.py` | +18.3 R (16 trades) in-sample, collecting |
| 2.6 `rev-trend-confirm` on 2x ETFs | observe only | — | |
| 3.1 watcher fixes | done | `common.py`, `watch_setups.py` | smoke run starts and loops |
| 3.2 skip summary | done | `watch_setups.py` | |
| 3.3 failed bounce WATCH only | done | `vwap_rules.py` `WATCH_ONLY` | also the reclaimed gap-down (F12) |
| 3.4 evidence grades | done | `vwap_rules.EVIDENCE`, `SKILL.md`, `radar_gui.py` | |
| 3.5 rules into `setups/` | done as a shared module, not a `SETUPS` entry | `setups/vwap_rules.py` | backtest reproduces 14 trades / −$114 exactly |
| 3.6 xfade paper tracker | done | `watch_setups.paper_xfade` | |
| 3.7 VWAP exit as plan level | done | `track_position.py`, `algolab/live/vwap_exit_ledger.csv` | |
| 3.8 gap re-validation | done | `algolab/screens/gap_setup_revalidation_2026_11_10/` | F12 |

A/B reports (gitignored): `.local/daytrade-review/ab/*.md`. Gate: BLOCKED (intended), 0
swing / backtest changes on frozen data; it does not scan `db/repositories/trades.py`.
