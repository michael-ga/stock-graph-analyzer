# 2026-11-03 — semis relief gap after the Oct 30 crash (SNXX, AMDL)

**Ledger rows:** `murphy_ledger.csv` and `behavior_ledger.csv`, session
`2026-11-03-semis-relief-gap`. **Status:** written at 14:10 ET; the close section is
filled in after 16:00.

## Context

- **Oct 30:** INTC fell 28.6% on 455M shares (3.7× normal) after Q4 guidance of
  $15–16B vs about $17.1B expected (PC slump, rising memory costs). SNDK fell 13.5%
  and AMD 10.9% the same day, and each gapped below its Oct 29 low (downside
  breakaway gaps: INTC 68.77–90.84, SNDK 1239–1322, AMD 520.4–560.6).
- **Nov 2:** SNDK, INTC and AMD each made a lower low and **closed at the low**, on
  lower volume than Oct 30. There was no selling climax.
- **Premarket Nov 3:** the underlyings were up 4–5%, so SNXX was +9.5% and INTW +8%.
  Every opening price was **inside Monday's range**. Market: QQQ +0.7% and below its
  EMA20/50, SPY below its 50-day, VIX 17.5 → 20.4 over 5 days.
- **Event risk:** AMD reports Q3 after the close (options priced ±7.9% for Nov 4).

## The plan (written before the open)

- Don't buy the first 15–30 minutes. Enter 09:45–10:30 on a break of the
  opening-range high, or on a pullback that holds VWAP or the reclaimed level.
- Key levels came from the underlying and were converted to the 2× ETF with
  `ETF ≈ prev_close × (1 + 2 × underlying % move)`. Old ETF prices don't map after
  a 2× daily reset.
- Role-reversal level = the Oct 30 low: SNDK 1115 (SNXX 6.33), AMD 492.2
  (AMDL 48.0), INTC 63.2 (INTW 6.91).
- Ranking: SNXX first (SNDK was already above 1115 premarket), AMDL second, INTW
  last. Pick one; they are the same bet.

## Timeline (ET)

| Time | Event |
|---|---|
| 09:30 | Gaps faded at the open. AMD opened at 480 (low 473.5); SNDK at 1106, briefly **below 1115** (low 1100.5). |
| ~09:40 | **Day high for SNDK (1147–1153), AMD (495.9–497.4), INTC (62.58) and MU.** None of them was retaken. |
| (early) | AMDL bought at **45.38** ($150), near the low and before the AMD 492.2 trigger. |
| ~10:40 | SNXX bought at **6.58** ($200), after a run from 6.17 to 6.74, under SNDK resistance 1153–1159. |
| 10:49–11:10 | SNDK crossed VWAP four times. The 10:50 and 10:55 candles closed above it; the **11:05 candle closed 1125.05, below VWAP, on the heaviest 5-min volume of the half hour (100k)**. Exit signal. |
| ~11:12 | The exit wasn't placed in time. SNXX traded at 6.38, through the 6.40 **stop-limit, which didn't fill**. |
| ~11:17 | SNXX sold at **6.41**. |
| 11:30 | SNDK broke **1115** (role reversal failed), heading toward 1100. |
| 11:35 | The AMD 11:30 candle closed 486.42, below VWAP: the AMDL exit rule. AMDL sold at **47.15**, into a small uptick. |
| 12:00 / 13:50 | SNDK rejected at **1120–1122** twice from below (closes 1117.85 and 1114.01). |
| afternoon | SNDK held a 1108–1118 range on thin volume. SNXX traded 6.24–6.38, below the exit. AMDL bounced to about 47.6 after the exit. |

## Trades

| Trade | Entry → exit | Net after $3 fees | Grade |
|---|---|---|---|
| AMDL, 3.305 sh ($150) | 45.38 → 47.15 | **+$2.85** | B+: good price near the low, but entered before the trigger |
| SNXX, 30.4 sh ($200) | 6.58 → 6.41 | **−$8.17** | C: chased the extension under resistance; the exit followed the rule |
| **Day** | | **−$5.32** | Flat into AMD earnings |

AMDL's best open profit was about +$8.6 net at 48.88 (09:40–11:00 area). Most of it
was given back before the exit rule fired.

## What the session says about the rules

See `murphy_ledger.csv` for the row-level verdicts. In short:

- **Validated today:** a gap that opens inside the prior range is a bounce, not a
  reversal; downside breakaway gaps cap the bounce; no selling climax means no
  bottom; the reclaimed-then-lost role-reversal level (SNDK 1115) turned into
  resistance; lower highs under resistance preceded the break.
- **Challenged by data the same day:** "losing VWAP predicts the turn" worked today
  but has **no edge** over 59 sessions (p=0.99, `LEARNINGS.md` *Ruled out*). A late-day
  measured-move target on a big gap day (DD to 157) reached +1.6% in only 18% of 17
  similar cases.
- **Open:** the relief gap topping early (H12 in `LEARNINGS.md`; today is its first
  out-of-sample day, with 4 tickers forming one sector event).

## Lessons (execution)

1. **Fees set the minimum trade size.** $1.50 + $1.50 on $150–200 is a 1.5–2% hurdle.
   Don't scale out of a small position (each sell is another $1.50), and skip
   setups whose realistic target is under about 2–3%.
2. **Use stop-market orders on 2× ETFs.** A stop-limit was skipped when price
   gapped through it.
3. **Pre-place the exit order** when an exit signal is one candle away, so acting on
   it is one click.
4. **Buy the pullback, not the extension** (Murphy's ⅓–½ retracement). The one
   trade bought near the low won; the one bought after a +6% run lost.
5. **The day high comes early.** On 51% of ticker-days the high is set by 10:15
   (59-session study). On a relief-gap day, take profit into the first push rather
   than waiting for a trailing exit.
6. **After a loss, the urge to trade the opposite side (SNDQ) right away is a
   known trap.** Wait for a full setup and check the clock and fees.

## Close (fill in after 16:00; follow-ups due by 2026-11-05)

Closing prices (16:00 ET, 5-min data):

| | Close | Day | Range | VWAP | Close position | Volume vs Nov 2 |
|---|---|---|---|---|---|---|
| SNDK | 1126.40 | +4.8% | 1100.53–1153.19 | 1124.90 | 0.49 | 7.6M vs 10.6M |
| SNXX | 6.44 | +9.3% | 6.17–6.74 | 6.45 | 0.48 | |
| AMD | 484.47 | +3.8% | 473.54–497.40 | 488.22 | 0.46 | 23.9M vs 32.1M |
| AMDL | 46.47 | +7.4% | 44.55–48.88 | 47.14 | 0.44 | |
| INTC | 60.50 | +0.9% | 59.87–62.59 | 61.07 | 0.23 | 129M vs 231M |
| SMH | 518.30 | +2.0% | 515.50–524.70 | 520.04 | 0.30 | |
| NBIS | 151.59 | +7.2% | 143.71–152.67 | 150.06 | 0.88 | |
| DD | 153.56 | +8.7% | 143.36–155.61 | 152.43 | 0.83 | 2.5M vs 1.7M |
| TBBB | 63.10 | +10.8% | 57.69–63.10 | 61.38 | 1.00 | 2.7M vs 1.0M |
| NBIX | 164.90 | +13.5% | 157.91–169.53 | 165.88 | 0.60 | 3.5M vs 2.0M |

- **SNDK: no Murphy reversal day.** No new low (1100.5 vs Monday's 1067), a mid-range
  close, and lighter volume than Monday. The 15:00 bounce lifted it back just above
  VWAP into the close. `no-climax-no-bottom` stays inconclusive.
- **SNXX exit (6.41):** it closed 6.44, so holding to the close would have been $0.91
  better. Intraday the exit was ahead by up to $4.26 (SNXX low 6.27 at ~15:00). The
  process was right; the outcome was about neutral, and the late bounce erased most of
  the gap.
- **AMDL exit (47.15):** it closed 46.47, so the exit beat holding to the close by $2.25.
- **SMH closed below its 200-day** (518.30 vs 520.7), below VWAP, at 0.30 of its range.
  At the final close the SOXS conditions **passed again**, though they failed at the
  15:45 check during the 15:00 bounce. Lesson: the close test must use the final
  minutes, and the result can flip in the last 15.
- **DD** poked a new high (155.61, +0.4% above the 154.98 flag high) and closed 153.56,
  matching the gap-flag study (new high 82%, median further +0.70%, flat close). The
  157 target was not reached.
- AMD earnings reaction (next morning) → after hours 468.31–530.38, last 508.20 (+5.0%),
  about +7% overnight. The Q4 guide of $17.0B vs $16.2B drove it. The study of whether
  this was predictable is in `2026-11-04-amd-earnings.md`.
- DD / TBBB / NBIX breakaway gaps (filled or continued?) → see the follow-up below.

### Follow-up (written 2026-11-05 after the close, daily + 5-min yfinance data)

11-05 was a chip-sector crash (SPY −4.4%, QQQ −6.5%, SMH −11.2%) after weak Qualcomm
and Arm reports on the evening of 11-04. Read every 11-05 move with that in mind.

| | 11-03 close | 11-04 O / H / L / C | 11-05 O / H / L / C | Gap test |
|---|---|---|---|---|
| DD | 153.46 | 155.24 / 155.96 / 150.73 / 150.73 | 150.27 / 152.79 / 146.01 / 144.93 | ❓ held 11-04, broke 148.5 on 11-05 (market crash) |
| TBBB | 62.97 | 63.74 / 65.14 / 62.57 / 65.07 | 65.02 / 65.91 / 63.44 / 64.21 | ✅ held well above 60.00 |
| NBIX | 164.85 | 166.36 / 169.02 / 162.02 / 166.05 | 167.68 / 167.68 / 159.53 / 159.76 | ✅ held, but the 11-05 low was only 0.5 above the 159.00 gap open |
| SNDK | 1126.52 | 1079.91 / 1120.22 / 1014.00 / 1037.40 | 962.25 / 967.99 / 803.97 / 806.43 | no reversal day |
| SNXX | 6.45 | 5.88 / 6.37 / 5.16 / 5.43 | 4.63 / 4.70 / 3.06 / 3.09 | |
| AMD | 483.90 | 485.25 / 514.61 / 476.50 / 488.78 | 452.50 / 456.20 / 402.10 / 404.40 | no island |
| AMDL | 46.49 | 46.65 / 52.25 / 44.95 / 47.27 | 40.25 / 40.85 / 30.54 / 30.79 | |
| SMH | 518.52 | 511.17 / 522.20 / 497.33 / 504.27 | 482.14 / 483.89 / 445.59 / 447.73 | |
| SOXS | 53.64 | 56.05 / 61.81 / 52.24 / 58.34 | 67.21 / 81.45 / 66.32 / 81.22 | |

- **DD** jumped on 11-03 because of its Q3 report: adjusted EPS $2.09 vs $1.87 expected,
  sales $1.9B vs $1.84B, the full-year EPS guide raised to about $7.50 at the midpoint,
  and a $250M Q4 buyback
  ([Motley Fool](https://www.fool.com/investing/2026/11/03/why-dupont-stock-rocked-the-market-today/),
  [MarketBeat](https://www.marketbeat.com/instant-alerts/price-dupont-de-nemours-nyse-dd-shares-gap-up-on-earnings-beat-2026-11-03/)).
  So it was an earnings gap, not a pure technical breakout. It held on 11-04, though it
  closed at the low (150.73), then broke 148.5 at about 11:10 on 11-05 and closed 144.93.
  The gap is partly filled (low 146.01; 141.30 not reached). DD fell 3.8% vs SPY's 4.4%
  that day, so the failure is mostly the market, and the ledger row is **inconclusive**.
- **DD swing plan (buy a 151–152.5 pullback that holds, stop below 148.5):** triggered
  at about 152.3 at 10:20 on 11-04 (low 152.26), ran to 155.79 (+0.9R), then was
  stopped at about 148.4 on 11-05: **about −1R** (−2.6%). The stop saved about 3.5 points
  versus the 11-05 close.
- **TBBB and NBIX** held their gaps even through the crash: **validated** (one
  observation; NBIX was borderline).
- **SNDK:** no Murphy reversal day. 11-04 made a new low (1014.00) but closed in the
  lower quarter, below the prior close. 11-05 made another new low and closed **at** the
  low on 29.4M shares (about 2.5× 11-04), which is heavy volume without a reversal, so
  it isn't a climax. `no-climax-no-bottom` is **validated**: the bounce failed and the
  low wasn't in (the 11-04 lower low came before the crash news). A new pending row
  checks whether 11-05 was the low.
- **SNXX exit at 6.41:** SNXX closed 6.45 / 5.43 / 3.09. The exit looked neutral at
  the 11-03 close (−$0.91) but was **+$100 better than holding the $200 position to
  the 11-05 close** (30.4 sh × (6.41 − 3.09)). Exiting by rule didn't matter that
  day; it mattered two days later.
- **SOXS close-test preview:** SMH opened 511.17 (−1.4%) and closed 504.27 (−2.7%)
  on 11-04. SOXS opened +4.5% (base rate median +1.5%) and closed +8.8% (base rate
  mean −2.8%). The overnight-to-open leg worked; the "fade by the close" part of the
  base rate didn't, because semis kept falling into the 11-05 crash.
- **Stayed flat into AMD earnings:** the after-hours +5% was gone by the 11-04 open
  (+0.3%). $150 of AMDL held overnight would have been worth about +$0.50 at the 11-04
  open, not the +$14 seen after hours, and about −$51 by the 11-05 close.

One session is n=1. None of this changes a bot or the score.
