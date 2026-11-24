# 2026-11-16 — Chip export-rule selloff: managing two losing 2x swing holds and an RTB entry

**Type:** live position watch from 11:20 ET to the close. **No new entries from the scanner; one user entry
(RTB, a thin microcap).** **Ledger rows:** `murphy_ledger.csv` and `behavior_ledger.csv`, session
`2026-11-16-chip-export-selloff-swing-holds`.

Positions carried in (bought at Thursday 11-12's close for a planned month-long hold):

| | Shares | Entry | 11-16 ~close | Open P/L |
|---|--:|--:|--:|--:|
| SNXX (2x SNDK) | 51 | 4.03 | 2.985 | about −$53 |
| INTW (2x INTC) | 47 | 6.37 | 5.135 (low 5.01) | about −$58 |
| RTB (stock, entered 11-16 ~11:40) | 59 (assumed) | 5.07 | 4.50 | about −$34 |

## Context

- **Driver:** the US tightened and widened AI-chip export rules (five more countries; Cantwell pressing Commerce),
  on top of the Nvidia drama, with Nvidia earnings on **11-17 after the close** and Fed minutes this week.
  Memory sold hardest: MU −6%, SNDK −7%. BofA flagged NAND spot prices −20% in a month. INTC also had a new
  shareholder class action over its 10-30 crash.
- **Market:** SPY under its 50-day (down regime); Nasdaq −2%; VIX 19.3 → 24.05 intraday (+25%), 23.6 late.
- **SNDK 8-for-1 split** takes effect around 11-19/11-20: every SNDK level here must be divided by 8 afterwards
  (799 → ~99.9). ETF levels are unaffected.

## The day (underlyings)

| | Prev close | Open | Low | High | ~15:55 | Note |
|---|--:|--:|--:|--:|--:|---|
| SNDK | 885.01 | 827.49 | 799.00 | 838.48 | 811.92 | low tested the 11-05 climax low 803.97 and held on the close |
| INTC | 59.66 | 56.68 | 54.90 | 57.20 | 55.49 | new day low 13:35 on 1.4x volume; still above the 51.51 base low |

## Plan vs what happened

| Plan | What happened |
|---|---|
| Day-low stops (user's choice): SNXX 2.84, INTW 5.01 | SNXX low 2.88, not hit. **INTW low was exactly 5.01**: a stop-market at 5.01 may have filled; the 20-second tracker never saw the touch |
| Daily noise check: SNXX ~15%/day, INTW ~13%/day | Both stops sat inside one normal day's range; INTW's was hit (or touched) the same day, as predicted |
| Murphy read: SNDK double-bottom test with RSI bullish divergence vs an island-reversal top | Close 811.9 above 799: the test held for one day; the island (gap 11-11, gap 11-16) is still open. Unresolved |
| RTB: untradeable for this playbook (float 1.6M, ~30K sh/day, 24 of 30 days close below the open, every gap-up faded) | User bought 5.07 after a +13.5% gap-up; it faded to 4.50. Close-based exit rule (close < 4.60 → limit sell next morning) triggered |

## Lessons

1. **A stop inside one day's noise is an exit, not protection.** On 2x single-stock ETFs moving 13–15% a day, a
   4–10% stop gets hit by noise. To hold for weeks the choice is a structural stop (under the support that
   proves the idea wrong) with a smaller size, not a tight stop with full size.
2. **The 20-second tracker can miss a stop touch.** INTW printed 5.01 between polls. For stops, read the day's
   low, not only the last price.
3. **Thin microcaps break every rule here.** No stop-market, VWAP and volume signals mean little, and a few
   hundred shares move the price 3–5%. RTB's 30-day intraday picture (sold from the open on 24 of 30 days) said
   "never buy the morning gap"; the entry was exactly that.
4. **Hedging a 2x long with its 2x inverse costs more than selling part of the long.** It pays fees and
   daily-reset decay on both sides for the same net exposure.
5. Tooling: `daychart` crashed on bars with zero volume (`_vwap` used `pd.NA`); fixed to use NaN.

## Open for 11-17

- Confirm whether the INTW 5.01 stop filled.
- RTB: sell with a limit at the bid in the morning if the 16:00 close was under 4.60.
- Before 15:45: decide the size held into Nvidia's report (all, half, or flat). Playbook: no 2x into an event
  night unless sized for the worst gap.
- SNDK triggers: close above 848.50 (island filled), above 1,011 (double bottom confirmed), below 799 (test failed).
  After the split, divide by 8.
