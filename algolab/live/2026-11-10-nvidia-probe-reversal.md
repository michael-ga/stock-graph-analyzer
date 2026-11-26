# 2026-11-10 — Nvidia CEO probe: a failed chip bounce, then a late VWAP reclaim

**Type:** live watch, joined at 12:32 ET. **No trades taken.** **Ledger rows:** `murphy_ledger.csv` and
`behavior_ledger.csv`, session `2026-11-10-nvidia-probe-reversal`.

Watchlist: SNDK (SNXX / SNDQ), NBIS (NBIZ), AMD (AMDL / DAMD), INTC (INTW). From 15:13 a new
market-wide movers watcher (`watch_trending.py`) also ran. Sizing asked for: $500, and $500–600 for quick runs.

## Context

- **Backdrop:** the 11-05 chip crash (data-center regulation fears after the midterms, Nasdaq in
  correction), then a relief bounce on 11-06 and 11-09 (AMD +13% on the Google Cloud TPU 8tx / EPYC
  deal and an Oppenheimer upgrade to Outperform, $600 target; NBIS +9%; INTC +5.5%).
- **Today's driver:** Nvidia's board opened an investigation into CEO Jensen Huang's relationship
  with a woman US intelligence believes may be a Chinese spy. NVDA −6% on 3.3× volume; Microsoft passed
  it in market value; the board hired Wachtell Lipton (14:19–14:24 headlines, repeated all afternoon).
- **Other names:** SNDK on regulation and NAND-price worries (8-for-1 split on 11-19); NBIS reports
  Thursday 11-12; INTC still digesting Friday's −29% guidance miss.
- **Market:** SPY under its 50-day average (down regime), VIX 19.6–21.6. Europe closed flat to up;
  Nasdaq futures −1.1%, Bitcoin −2.4%, gold −1.9%.

## The day in one pattern

Every name pushed **above yesterday's high** in the first 45 minutes, then fell back under
yesterday's close and VWAP by 10:30 (AMD by 11:20). Then the lunch grind, then a bounce from about
15:00.

| | Prev close | Day high (time) | Day low | Close | Close vs VWAP | Close in day range |
|---|---|---|---|---|---|---|
| SNDK | 886.51 | 914.82 (10:00) | 826.67 | 859.70 (−3.0%) | **+0.1%, above** | 37% |
| NBIS | 141.45 | 146.23 (10:00) | 130.90 | 133.91 (−5.3%) | −1.9% | **20%** |
| AMD | 508.58 | 539.25 (10:15) | 500.25 | 515.49 (+1.4%) | −0.6% | 39% |
| INTC | 59.37 | 60.65 | 56.85 | 58.11 (−2.1%) | −0.2% | 33% |
| SMH | 487.41 | 492.74 | 474.03 | 483.75 (−0.8%) | +0.5% | 52% |

ETFs: NBIZ +10.4% (36.00 low → 44.24 high), SNDQ +5.8%, SNXX −6.0%, AMDL +2.6%, DAMD −2.5%, INTW −4.4%.

## Timeline (ET)

| Time | Event | Call |
|---|---|---|
| 10:00–10:15 | Day highs on every name, above yesterday's highs | (before the session) |
| 10:30 | NBIS 15-min close 138.72 under VWAP 141.22; NBIZ crosses above its VWAP | (before the session). NBIZ then ran 37.4 → 43.2 by 11:45 |
| 12:32 | Watchers and Trade Radar started | Starting picture: all four under VWAP, no setup |
| 12:33 | User asks about NBIZ (43.13, near its high) | **Don't chase**: 8% above its VWAP, no target that pays 1.5 R:R |
| 12:35 | User asks about SNXX | Wait: SNXX only on an SNDK VWAP reclaim (864) |
| 12:37 | User asks about DAMD | Not now: AMD 3.2% under VWAP, RSI 33, near its low. Watch the 20-EMA (~513) pullback |
| 12:47 | SNDK breaks 833 (low 826.67), recovers 836 by 13:02 | Watch only |
| 12:55 | INTC bullish RSI divergence (56.85 low, RSI 34.6 vs 27.2) | Watch |
| 13:17 | SNDK, NBIS failed bounces skipped by the scanner (R:R 0.27) | Correct skips |
| 14:02 | AMD closes back above its 20-EMA: the DAMD pullback setup failed | Off |
| 14:20 | NBIS bullish RSI divergence; NBIS makes a marginal new low 131.10 at 14:25, then rises | Watch |
| 14:25 | AMD bearish RSI divergence at 514.71 | Watch |
| 14:45 | AMD 5-min bar on 2.06× time-of-day volume, 512.6 → 516.3 | (user asked afterwards about an early AMDL entry at 48.40) |
| 14:55 | Time stop for shorts | |
| 15:00 | AMD 516.4 under VWAP 518.8: the AMDL reclaim did not trigger at 15:00 or 15:05 | No entry |
| 15:05 | SNDK bearish RSI divergence at 854.24 | Watch (it failed: SNDK closed 859.70) |
| 15:20–15:28 | SNDK 847.8 → 860.0 on the strongest SNXX volume of the afternoon | User wanted a quick SNXX long; given as an outside-the-playbook order, $300, conditional on a 5-min close above VWAP |
| 15:30 | The 15:25 bar closed 858.66 **under** VWAP 859.29 | **Skip**: trigger failed |
| 15:46 | Swing scans: no reversal evidence on SNDK, NBIS, AMD, INTC, SMH; weak candidates AAON (island reversal bottom), NOVT (mixed) | No overnight holds |
| 15:50–15:55 | SNDK closes a 5-min bar above VWAP 859.18 | The bearish reversal read on SNDK is cancelled |

## Trades

None taken (none reported by the user). The scanner sent no ACTION entry signal all session.

| Idea | Call | Result at the close | Verdict |
|---|---|---|---|
| Chase NBIZ at 43.13 (12:33), $500 | Skip | 42.63: −$9 after fees (peak 44.24: +$10 before fees) | Skipping was right on the R:R, neutral on the result |
| AMDL early at 48.40 (14:25), before the VWAP reclaim | Not taken (asked after the fact) | 48.96: +$3 after fees at $500 | Not proven either way; logged as a rule idea to test |
| SNXX quick long ~3.46 (15:28) | Skip: the 15:25 close failed under VWAP | 3.45: −$4 after fees at $300 | Waiting for the close was right; the trigger failed on that bar |
| AAON at the close (15:20, +19.6% earnings gap fading) | Skip: plan for tomorrow instead | 74.99 | Pending (11-11 plan below) |

## What we learned

1. **We joined at 12:32, after the day's big move.** NBIZ's +20% run (10:00–11:45) was over. Start the
   watchers before 09:45 on any day the user plans to trade.
2. **A higher high above yesterday, then a VWAP loss, was the day's whole trade.** The playbook has no
   ACTION for it (VWAP loss alone is ruled out, the failed bounce is backtested negative). The
   conditional version is untested: backtest it before using it (`vwap-loss-after-higher-high`).
3. **The 15:00 stretch reversal showed up again.** Everything stretched under VWAP bounced from ~15:00, and
   SNDK closed above VWAP. Both bearish RSI divergences after 14:00 (AMD 14:25, SNDK 15:05) failed into
   that bounce. Late in the day, don't trust a bearish divergence against a stretched name.
4. **Quick +2% scalps don't pay at the user's fees.** With a 4% stop and $3 fees, a +2% target needs a 77%
   win rate at $500. The user asked twice for "a quick run"; both times the honest answer was no.
5. **The user's early-entry question is a good one.** Entering below VWAP (AMDL 48.40) had a better R:R
   (about 2.1 vs 1.4 at $500) than waiting for the reclaim. The failed-bounce short being negative hints
   that VWAP rejections aren't reliable, which supports the idea. Test "anticipation long" (higher low +
   a close near the bar high on ≥ 1.5× time-of-day volume, within 1.5% of VWAP).

## Plan for 11-11 (written before the open)

No reversal evidence on the watchlist at 15:46, so intraday only, entries after 10:00.

- **SNDK** closed above VWAP: SNXX on a VWAP hold after 10:00 ($300, untested); SNDQ only on a break and failed
  retest of 826.7.
- **NBIS** closed at 20% of its range: the weakest. NBIZ only on a break of 130.9, out the same day
  (earnings Thursday). CoreWeave (+2.2% into the close, also reporting this week) is a warning for NBIZ.
- **AMD**: AMDL above 539.3 or on a VWAP reclaim; DAMD on a break of 500.3.
- **INTC**: INTW on a VWAP hold; stay out below 56.85.
- **AAON** (1× stock): buy only above 80.10 (island-reversal bottom, weak, $300). Gap support 71.6.

## To do

- Backtest with `setup-lab`: `vwap-loss-after-higher-high` (NBIS 10:30), the anticipation long (AMD
  14:45), and the late-day VWAP reclaim after a stretched selloff (SNDK 15:20–15:55).
- Grade 11-11: did the key reversal follow through (NBIS especially)? Did AAON break 80.10 or lose 71.6?
- `watch_trending.py`: Asian indices printed 11-09 closes (Yahoo daily bars lag across time zones); fix
  the date handling.
