# 2026-11-11 — CoreWeave beat: a gap above yesterday's high that never looked back

**Type:** live watch from 09:11 ET (premarket plan, then the full session). **No trades taken; no ACTION entry
signal all day.** **Ledger rows:** `murphy_ledger.csv` and `behavior_ledger.csv`, session
`2026-11-11-coreweave-gap-trend-day`.

Watchlist: SNDK (SNXX / SNDQ), AMD (AMDL / DAMD), INTC (INTW); CRWV (CRWU / CRWG / CORD) added at 11:47.
NBIS and the 11-10 earnings movers (AAON, HIMS, NOVT, GFL) were on the scanner from 09:33 to 09:36 only.

## Context

- **Catalyst (after 11-10's close):** CoreWeave Q3 revenue $3.66B (+168%), backlog $132.5B, 2026 guide raised to
  $13.0–13.3B. Premarket: CRWV +13–15%, NBIS +11%, SNDK +8%, AMD +5.6%, INTC +5.9%, NVDA +3–5% despite a Baird
  downgrade (target cut ~60%).
- **Missed on 11-10:** 11-10's selloff also followed a hotter-than-expected CPI print. The 11-10 note blamed only the
  Nvidia probe.
- **During the day:** lawmakers urged Huang to resign (10:28), Congress set to subpoena him (11:03), a Senate
  Republican called for resignation (11:48), CRN reported unnerved customers (12:15). The rally ignored all of it.
  Bond market closed (Veterans Day); Goldman shifted its Fed view after CPI.
- **Market:** SPY +1.3% but still under its 50-day (down regime); VIX 18.3 → 17.4; Nasdaq 100 +2.8%; KOSPI +2.6%.

## The day

Every name opened **above yesterday's high** and held it all day. Lows were set by 09:45; highs at 11:50–13:45.

| | Prev close | Open | Low (time) | High (time) | Close | Close in range | ETF day |
|---|---|---|---|---|---|---|---|
| SNDK | 859.99 | 928.24 | 920.18 (09:35) | 1011.05 (11:50) | 977.90 (+13.7%) | 64% | SNXX +27.2% |
| AMD | 515.21 | 543.06 | 522.65 (09:45) | 567.65 (13:45) | 552.96 (+7.3%) | 67% | AMDL +14.5% |
| INTC | 58.13 | 61.78 | 60.96 (09:40) | 64.06 (11:55) | 62.51 (+7.5%) | 50% | INTW +15.1% |
| CRWV | 48.73 | 55.97 | 53.81 (09:35) | 62.03 (13:25) | 60.84 (+24.9%) | 86% | CRWU +48.5% |
| NBIS | 134.00 | 149.72 | 147.36 (09:35) | 160.21 (13:20) | 155.18 (+15.8%) | 61% | NBIZ −31.4% |

## Plan vs what happened

| Plan (09:12) | What happened |
|---|---|
| 🅰 Failed gap-up → inverse ETF, checked 10:30–11:30 (the only 🟡 setup) | Every first hour closed **above** the open; no trigger. A short would have lost 7–14% on the underlying |
| Early warning: falls back under yesterday's high | AMD did (09:32, 533 vs 539.25), then reclaimed by 10:26 |
| 🅱 Gap holds → long ETF on a pullback to VWAP / 20-EMA after 10:00 | The only pullback to VWAP came at **09:50** (SNDK low 940.00 vs VWAP ~941, 09:55 close 961.78), inside the no-entry window. After 10:00 no name touched VWAP; the 11:12 and 14:18 dips stopped above it |
| No entries 09:30–10:00 | Kept. It cost the one clean pullback |

**Hindsight:** a plain 10:00 entry at $300 would have closed at about SNXX +$8 (4.24 → 4.39), AMDL +$18
(52.4 → 56.10), INTW +$4, CRWU +$30. The playbook has **no rule for a trend day after a gap above the prior high**:
every rule fades the move or waits for a pullback that never came.

## Scanner and tooling findings

1. **VWAP-reclaim blind spot.** From 10:00 the scanner skipped every reclaim with a negative R:R (−0.1 to −1.2):
   its target is the opening-range high, which price was already above. On trend days the rule can never fire.
2. **Paper trades died with the scanner.** Both automatic `xfade-dbl-bottom` paper trades (INTW 13:30, SNXX 14:50)
   started, then vanished at the next 30-minute Monitor restart without a result. Fixed: `common.detached()` starts
   them in their own process group. Results rebuilt from 1-minute bars: INTW stop at 14:03 (−$7.74 on $300),
   SNXX stop at 15:11 (−$5.94). Both consistent with the setup's −$1.28 expectancy.
3. **Repeated EXIT lines.** "EXIT SNDQ/DAMD if held" fired at every restart (the name had been below VWAP early).
   Filtered from the Monitor; still in the day log.
4. **New today:** every tagged line now goes to `.local/live/alerts-YYYY-MM-DD.log`; the Trade Radar shows
   "Today's alerts"; every ACTION entry starts a $300 paper trade automatically.

## Lessons

- A gap **above** yesterday's high (11-11) behaved very differently from a gap that opened **inside** yesterday's
  range (11-03, faded). One pair, n=1 each: worth a test, not a rule.
- The 09:30–10:00 no-entry rule cost the only VWAP pullback today. It protected on 11-03. Keep it; test a
  "first pullback after 09:45" variant.
- Political headlines (resignation calls, subpoena) did not dent the rally. The earnings news outweighed them.
- Bearish 5-min RSI divergences: SNDK 12:05 (high 1011.05) did mark the day high; CRWV 12:10 / 13:00 didn't (new high
  62.03 at 13:25).

## Plan for 11-12 (end-of-day scan, 15:46)

Island reversal bottoms on INTC, CRWV, NBIS (gap down 11-05, gap up 11-11): **one sector event**, weak, not
backtested, against the 200-day.

- INTC → INTW: only above 64.06 after 10:00.
- CRWV → CRWU: only above 62.03 after 10:00.
- NBIS reports before the open: no plan until the reaction; nothing before 10:00.
- AMD is back above its 50- and 200-day averages: the first clean uptrend read.
- The scan's stops (beyond the island) are 10–26% away on the 2× ETFs: trade intraday with a 4% stop, $300, one name.

## To do

- `[ALGO]` (own PR): wire the Gap & Go rule (`bot-gap-go`, replay −7.2R → +5.4R) into the live scanner, and give the
  VWAP-reclaim rule a target above the day high on trend days. Backtest both with `setup-lab` first.
- Confirm on the next live day that detached paper trades survive a Monitor restart.
