---
name: setup-lab
description: Turn a trading-setup idea (text, video notes, ICT / price-action jargon, a table of setups) into a tested setup module in stockanalyzer/setups/ and backtest it bar by bar over the last 40–60 sessions. Use for "implement this setup", "which of these ideas works on SNXX", "backtest this strategy", "code this idea", or when ranking several setup ideas against tickers.
---

# setup-lab — idea → setup in code → honest backtest

A setup is only real once it is a **pure as-of function** the harness can step
bar by bar. Everything below exists to get there without fooling ourselves.
Read `algolab/LEARNINGS.md` first (CLAUDE.md requires it); its 2026-10-25 entry
is the worked example of this whole skill (12 ideas → 2 modules).

## 1. Write the spec card before any code

Fill every field with something a program can decide. Anything you can't fill
is either dropped (say so in the module docstring) or the idea isn't codeable.

| Field | Question | Repo default |
|---|---|---|
| Instrument / side | Long only. Short = long the inverse ETF (SNDQ for SNDK, NBIZ for NBIS). | long |
| Bars | 5-min. yfinance gives 60 sessions of 5m; 1m only ~7–30 days. | 5m |
| Windows (ET) | Exact HH:MM for setup formation and for entry. Bars are labelled by their start: the 10:25 bar closes at 10:30. | |
| Levels | Which reference prices, from which bars (see glossary). | |
| Trigger | Market at a bar **close**, or a resting **limit/stop** that a later bar trades through (`filled_intrabar=True`). | close |
| Stop | A price, plus `STOP_BUFFER` (0.1%). | |
| Target | A price or an R multiple. Skip if < `MIN_RR` (1R). | |
| Filters | Bias etc., each one objective (e.g. daily close > 20-day mean). | none |
| Exit | Stop-first; flat at the 15:55 bar close; one trade per variant per day. | fixed |
| Not modelled | SMT vs NQ/ES/YM, news calendar, London session (stocks don't trade it), discretion. | list them |

### Glossary: jargon → code primitive

| Term | Code |
|---|---|
| FVG (bullish) / BISI | bars k-2, k-1, k: `low[k] > high[k-2]` (bar k-1 the displacement) |
| CE (consequent encroachment) | midpoint of the gap / wick: `(high[k-2] + low[k]) / 2` |
| Premium / discount | above / below 50% of the dealing range (session low..high so far) |
| Liquidity (buy/sell side) | highs/lows others' stops sit beyond: session, pre-market, yesterday, yesterday 13:30–16:00 (PM), 09:30–12:00 (AM), lunch 12:00–13:30 |
| Sweep / raid / Judas swing / turtle soup | a bar trades beyond a level; the trigger is a later **close back inside** |
| MSS (market structure shift) | close above the high of the last few bars before the sweep low |
| Killzone / Silver Bullet / macro | a fixed ET window → `cfg.window_start/window_end` |
| Range / consolidation | last N bars' high-low ≤ k × daily ATR |
| Sprint vs running break | breakout bar range vs the median bar range |
| Overextension | drop ≥ k × median bar range within N bars |
| Pivot high/low | a bar's high/low beyond its 2 neighbours each side (needs later bars → only usable once they exist) |
| Neckline | highest high between two bottoms |

## 2. Check the data can carry it

- Run the harness once on the tickers; it skips any with < 60/78 bars per
  session (NBIC trades ~9/78 — untradeable intraday; use the underlying for
  research and say so).
- yfinance pre-market bars carry volume 0, and bar ÷ trailing-mean volume is a
  time-of-day clock, not a volume gauge (LEARNINGS 2026-10-21). Avoid volume
  rules until a same-slot average exists.
- Daily 2x ETFs are an affine map of the underlying within a day, so R multiples
  and level crossings carry over; costs don't ($0.02 on an $8 ETF is 0.25%).

## 3. Pre-register

Before the first run, write down in the module docstring (and later the
LEARNINGS entry): the rules, the variant(s), the window (default: latest 40
sessions to pick, the 20 before them as an untouched holdout), and the pass
rule. Default pass rule: ≥ 8 trades; avg net R > 0 in both halves; > 0 with the
best day removed; > 0 on each underlying; then > 0 on the holdout.
Screening many ideas? Code them all to the same rules first, run once, pick by
the rule — don't add variants after seeing results.

## 4. Code it

1. New file `stockanalyzer/setups/<name>.py`, modelled on `silver_bullet.py` /
   `double_bottom.py`:
   - `NAME`, a frozen `Config` dataclass (its `name` is the cohort id), `VARIANTS`.
   - `signal(ctx: SetupContext, cfg) -> SetupDecision` using `base.no` / `base.fire`
     (`fire` enforces stop < entry and ≥ 1R). Read only `ctx`; `ctx.session`'s last
     bar is "now". Fire only **on** the bar the entry happens, so the harness's
     "first fire" is the trade.
   - Arrays via `s[col].to_numpy()`, times via `base.hhmm` — the harness calls
     `signal` once per bar, so keep it O(bars).
2. Register it in `stockanalyzer/setups/__init__.py` (`SETUPS`).
3. Tests in `tests/test_setups.py` (copy the existing pattern):
   - fires on a hand-built session with exact entry / stop / target;
   - stays out when each key rule fails (bias, window, void pattern);
   - add it to `test_future_bars_never_change_the_entry`.
4. `orb.py` / `reversal.py` live here too but keep their own signatures (the
   live radar and replay call them). Don't convert them in a setup PR.

## 5. Backtest

```bash
.venv/Scripts/python.exe -m stockanalyzer.setups.harness SNXX SNDQ NBIZ NBIS --setup <name> --days 40 --null 200
.venv/Scripts/python.exe -m stockanalyzer.setups.harness SNXX SNDQ NBIZ NBIS --setup <name> --days 20 --offset 40
.venv/Scripts/python.exe -m stockanalyzer.setups.harness SNXX --setup <name> --days 40 --trades
```

About 30 s for 4 tickers × 40 days (network included). Run it in the
background if you add many tickers.

Read the table: `n` vs `days` (pairs on one date are one bet), `avg_net` after
the $0.02/share spread, `half1`/`half2`, `no_best_day`, `per_ticker`. `--null`
re-enters every trade at a random bar with the same risk and target: a setup
that doesn't beat it has no timing edge, whatever its average says.

## 6. Report and record

- Report per setup: n, distinct days, win %, avg net R, both halves, best day
  removed, holdout, null p, and what the spec could not model. Say plainly when n
  is too small (< ~30 days) to conclude anything.
- Add a dated `algolab/LEARNINGS.md` entry: the spec, the numbers, the verdict,
  and an Open-hypotheses row with its keep/kill rule.
- **Never** wire a setup into the live bots (`app.py`, `radar_worker.py`,
  `replay.py`) in the same change. That changes bot decisions, so it is its own
  `[ALGO]` PR with the user's explicit approval. In that PR add the module to
  `LIVE_SETUPS` in `scripts/algo_gate.py`, so the gate treats it as core decision
  code from then on, and run the `algo-gate` skill.
- A PR that only adds a backtest-only setup should get `PASS` from the gate: the
  file shows under `backtest` and no bot decision changes.
- Run the full `pytest` before declaring done; compare failures with the base
  branch rather than assuming they are yours or not.
