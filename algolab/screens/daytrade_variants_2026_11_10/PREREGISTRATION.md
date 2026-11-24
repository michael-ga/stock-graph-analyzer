# Pre-registration: day-trade variants of 2026-11-10

Written **before** any of these variants was run. Source: Phase 2 of
`docs/plans/2026-11-10-daytrade-optimization-plan.md`. Each spec below is final; a
change after the first run is a new variant with its own name.

## Honesty note

Three of the five ideas came from splits run on the same 59 sessions (2026-08-10 →
2026-10-30) they would be screened on: the ADX filter (H15), the peers filter (H16)
and the late-entry cut (H18). Their numbers on that window are **in-sample and prove
nothing**. They are implemented so they collect on new sessions. Only `orb-sip` is a
spec from outside this data (Zarattini, Barbon, Aziz 2024), so it alone gets a
screen + holdout read now.

## Common rules

- 5-minute bars, long only (the inverse ETF is the short side), one trade per
  variant per ticker-day, judged stop-first, flat at the 15:55 bar close
  (`setups.harness.judge`, the replay's judge).
- Cost: reported twice, the harness's $0.02/share and the common basis
  `max($0.02/share, 10 bps)` (`stockanalyzer.costs`). **Pass/kill uses the common
  basis.**
- Universe: the replay A/B `full` tickers (INTC AMD NVDA TSLA PLTR SMCI MU SNDK SNXX
  SNDQ NBIZ NBIS).
- "New sessions" = sessions after 2026-10-30.

## Variants

| Name | Where | Spec |
|---|---|---|
| `orb-sip` | `setups/orb_sip.py` | The 09:30 5-min bar closes above its open. Its volume ≥ 1.0× the mean 09:30-bar volume of the prior ≤ 14 sessions (≥ 5 needed). Buy-stop at that bar's high, filled by the first bar from 09:35 to the 15:00 bar that trades through it (at the bar's open if it gaps over). Stop = entry − 0.10 × daily ATR(14) of the days before today. No target: out at the 15:55 close (target is a 50R sentinel). |
| `xfade-chop` | `setups/double_bottom.py` row | `xfade-dbl-bottom` unchanged, plus: 5-min ADX(14) < 20 on the bar before the entry bar, computed on the previous session + today's regular bars. |
| `sb-mkt` | `setups/silver_bullet.py` row | `sb-am-bias` unchanged, plus: at the fill bar QQQ is above its 09:30 open (below it for an inverse ETF). No QQQ bars for the day = no trade. QQQ replaces the "median of the peers" of the exploratory split, because a fixed market proxy is available live and in every backtest; that is a spec change made before the first run. |
| `rev-strong-vol`, `rev-trend-vol` | `setups/reversal.py`, replay/backtest only (`REVERSAL_CANDIDATES`) | The live variant (`rev-strong-touch`, `rev-trend-confirm`) unchanged, except: stop = the lower of the support stop and entry − 1.5 × the median regular-session 5-min bar range of the intraday frame; target unchanged (~3% aim under resistance); skip if the R:R left is < 1.5; no entry after 11:00. |
| ORB exit (plan 2.2) | `TradeRepository.mark` | No new bot. The replay shows `bot-gap-fixed` already exits at the close on 24 of 29 trades (the 2R target filled once), so it is the close-only control; what was missing is that live day trades held overnight. Live bot day trades now close at 15:58 ET (`DAY_FLAT_ET`), matching the replay. |

## Keep / kill

| Variant | Read now (2026-08-10 → 10-30) | Keep / kill, new sessions only |
|---|---|---|
| `orb-sip` | Screen = latest 40 sessions, holdout = the 19 before. Pass: ≥ 8 trades; common-basis avg net R > 0 in both halves of the screen, without the best date, on both instrument types (2x ETFs, stocks) and on the holdout. | If it passes: keep at ≥ 30 new dates with avg > 0 and > 0 without the best date. If it fails the screen: record it as failed, don't tune it. |
| `xfade-chop` | Report only (in-sample) | H15: > 0 at ≥ 100 dates and without the best date |
| `sb-mkt` | Report only (in-sample) | H16: > 0 at ≥ 50 dates |
| `rev-*-vol` | Report only (in-sample) | H18: > 0 at ≥ 30 dates each, then an `[ALGO]` PR to make it a live bot |

## Results (added after the runs, 2026-11-10)

Replay A/B, 12 tickers, every session 2026-08-10 → 2026-10-30 (`algo_replay_ab.py --mode full --all-days`).

| Variant | Trades / dates | Win % | Net R (harness) | Net R, common cost | Read |
|---|--:|--:|--:|--:|---|
| `orb-sip` | 103 / 43 | 18 | −18.2 | −33.3 | **Failed the screen**: screen avg −0.17 R, halves +0.64 / −1.18, without the best date −0.48, stocks −0.31, holdout +0.08 (17 trades). Removed from `SETUPS`; not tuned. |
| `xfade-chop` | 64 / 41 | 61 | +11.0 | +2.3 | in-sample, collecting (H15) |
| `sb-mkt` | 16 / 12 | 56 | +18.3 | +15.0 | in-sample, collecting (H16) |
| `rev-strong-vol` | 13 / 11 | 46 | +6.4 | +5.0 | in-sample, collecting (H18) |
| `rev-trend-vol` | 4 / 4 | 50 | +2.6 | +2.3 | in-sample, collecting (H18) |
