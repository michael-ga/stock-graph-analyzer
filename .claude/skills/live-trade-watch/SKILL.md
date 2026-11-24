---
name: live-trade-watch
description: Run a live intraday trading watch for the user's leveraged ETFs (SNXX/SNDQ, NBIZ, INTW, AMDL, SOXL/SOXS…) — live price sampling, setup alerts with entry/stop/target and $300/$500 sizing, a news watcher, a desktop "Trade Radar" window with a beep, and an open-position tracker. Use for "start a trade on X", "watch these tickers", "notify me for potential", "monitor news", "track my position", "where do I put the stop", or any live-session request during market hours.
---

# live-trade-watch — live session: setups, news, popup window, position tracking

The user trades small manual positions ($300–500, $1.50 + $1.50 fees) in 2× single-stock
ETFs. Your job in a live session is to **watch, filter and give clear orders**, not to
narrate the tape. Everything here comes from the 2026-11-03 → 11-05 live sessions;
their notes and ledgers are in `algolab/live/` (read `README.md` there first).

## 1. Start-up (do all of it, in this order)

1. **Clock and data.** Check the time in ET (`common.now()` uses the real America/New_York
   zone, daylight time included). Before 09:30 or after 16:00, say so: setups only run in
   the session.
2. **Map each ETF to its underlying with signed leverage.** Signals are read on the
   underlying, never on the ETF. Known links: `SNDK=SNXX:2,SNDQ:-2`, `NBIS=NBIZ:-2`,
   `INTC=INTW:2`, `AMD=AMDL:2,DAMD:-2`, `AMZN=AMZU:2,AMZD:-1` (AMZD is a **1×** inverse; AMZZ is a thin 2× long), `SMH=SOXL:3,SOXS:-3` (SOXL tracks a chip
   index; SMH is a close proxy). For anything new, check the fund name with
   `yf.Ticker(t).info["longName"]` ("2X Short" → negative), or the `etf_links` table.
3. **Start the three watchers** with the Monitor tool (`timeout_ms` 1800000) and the
   window with Bash `run_in_background`:
   ```
   .venv/Scripts/python.exe -u .claude/skills/live-trade-watch/scripts/watch_setups.py --watch SNDK=SNXX:2,SNDQ:-2 --watch NBIS=NBIZ:-2 --watch INTC=INTW:2
   .venv/Scripts/python.exe -u .claude/skills/live-trade-watch/scripts/watch_news.py --q "chip stocks" --q "SanDisk stock" --q "Nebius stock" --q "Intel stock" --q "AI regulation Congress"
   .venv/Scripts/pythonw.exe .claude/skills/live-trade-watch/scripts/radar_gui.py
   ```
   Prefix each Monitor command with `PYTHONIOENCODING=utf-8` and pipe it through
   `grep --line-buffered -v "Warning\|delisted\|Failed download"`. Tune the news `--q`
   queries to the day's story (the catalyst, the tickers and their sector).
4. **Re-arm every Monitor when it expires** (30 minutes max) until 16:00. After a session
   restart, every watcher is gone, so start them again. Relaunch the window only if it
   closed (check with `tasklist //FI "IMAGENAME eq pythonw.exe"`).
5. Post a short **starting picture**: one table (underlying, price, VWAP, day low, ETF
   prices), the day's driver in one line, and which setup each name is closest to.

## 2. Message discipline: ACTION vs WATCH vs INFO

Every script line carries a tag. **How you relay it matters more than anything else
here.** The user acts on your message within seconds.

| Tag | Meaning | How you relay it |
|---|---|---|
| `🚨 ACTION` | Do something now: enter, exit, move the stop, time stop | **A vivid block (template below). Nothing else in the message.** |
| `👀 WATCH` | A setup is forming, a ⛔ SKIP, a new low, a market-moving headline | **One or two lines:** what happened and "no action" or what would trigger action |
| `· INFO` | Heartbeat, start-up, feed status | **Don't relay**, unless the feed changed or the user asked. At most a one-line status every ~15 min. |

Rules:
- **Rejected signals are summarized, not repeated.** The R:R gate rejects almost everything (8,737 of
  8,752 scanner candidates in the 59-session backtest), so the scanner prints **one** WATCH line per
  underlying every 15 minutes with the counts and the target a $300 trade would need (+8.5% on the ETF
  with a 4% stop). Relay it only if the user asked why nothing fires.
- **Every ACTION carries its evidence grade** (`🟡 weak evidence`, `❔ untested`; `⛔ backtested
  negative` rules are WATCH-only). No rule here is graded `📊 tested` today. Say the grade in the block's first line.
- **Lead with the action.** First line = what to do (`BUY SNDQ`, `SELL NOW`, `MOVE STOP`).
  Reasons come after, in one line.
- **Every order is a number** the user can type into the broker, each with its order
  type: *buy limit*, *stop-market*, *sell limit*. Never "around", never a range
  without a maximum.
- **Repeated or old headlines get one line** ("repeat, no change"). Don't re-explain the
  day's story for every headline.
- If a signal repeats while the user is already in that trade, say "confirms your
  trade, don't add", once.
- Never say "buy" without the stop in the same message.

**ACTION block template (entry):**
```
## 🔴 TRADE READY: BUY SNDQ (short SNDK) · 11:25
SNDK bounced to VWAP 880.8 and the 5-min candle closed below it.

| Step | Order |
|---|---|
| 1️⃣ Buy limit | **32.15** (skip above 32.60) |
| 2️⃣ Stop-market, right after the fill | **30.87** |
| 3️⃣ Sell limit (target) | **35.50** |
| ⏰ Time stop | sell by **14:55** |
| $300 / $500 | 9 sh −$15 / +$29 · 15 sh −$23 / +$50 |

Cancel if: the next 5-min close is above VWAP, or no fill by +15 min. Tell me your fill.
```
**ACTION block (exit / adjust):** a header (`## 🛑 SELL SNDQ NOW` or `## 🟢 MOVE STOP →
32.48`), the exact order, then one line of why.

## 3. The playbook (rules the user agreed to; cite them when relevant)

| Rule | Why (source) |
|---|---|
| No entries in the first 15–30 min; mark the opening range | 45–49% of day highs and lows are set by 10:00 (algolab/live 11-03) |
| **Failed bounce to VWAP is WATCH only** (⛔ backtested negative: 14 trades in 59 sessions × 9 names, 14% win, −$8.15 each at $300); long = VWAP reclaim, $300 max against the trend (❔ untested: the R:R gate passed 0 of 675) | 2026-11-10 backtest of the coded rules (`algolab/screens/daytrade_review_2026_11_10/skill_backtest.py`) |
| **Stop-market at least 4% from entry** on a 2× ETF when VIX is ~20+ | 11-05: a 0.5% stop was hit by noise within minutes (`stop-too-tight`) |
| Take only if R:R ≥ 1.5 **after the $3 fees**; skip if price is > 1.5% above entry | 10-31 study: R:R < 1 alerts drove the losses; 11-03 chase cost −$8.17 |
| One bet per underlying, and per sector: SNDQ + NBIZ together = double size | 11-03 / 11-05 |
| Never stop-limit on 2× ETFs | 11-03: the stop-limit was skipped as price gapped through it |
| +1R → move the stop to breakeven + fees; give back half → sell | 11-03 AMDL gave back about $5.75 of open profit |
| Time stops: shorts by 14:55 (15:00 stretch reversal), longs by 15:50 | 10-23 study, validated live on 11-03 |
| Overnight only if the **final-minutes** close test passes, and never 2×/3× on an event night unless sized for the worst gap | 11-03 close-test flip; 11-04 earnings study |
| Earnings: options size the move, nothing on the chart gives the direction | `LEARNINGS.md` 2026-11-04 (Ruled out + H13) |
| VWAP loss alone has no edge; it's a plan level, not a prediction | `LEARNINGS.md` 2026-11-03 (Ruled out) |

### Evidence grades (shown on every signal; the source of truth is `stockanalyzer/setups/vwap_rules.py` `EVIDENCE`)

| Rule | Grade | What the evidence says |
|---|---|---|
| Failed gap-up (`gap_check`, short) | 🟡 weak evidence, $300 | coded rules, 2 years: +$1.10 a trade at $300 (155 trades, 28 dates); ≤ $0 without the best date |
| Reclaimed gap-down (`gap_check`, long) | ⛔ backtested negative | coded rules, 2 years: −$3.88 a trade at $300, negative in both halves; **WATCH only** |
| Failed bounce to VWAP | ⛔ backtested negative | 14 trades, 14% win, −$8.15 each at $300; **WATCH only** |
| VWAP reclaim | ❔ untested | the gate passed 0 of 675 candidates in 59 sessions |
| Trend pullback ("Holy Grail") | ❔ untested | the gate passed 0 of 2,092 candidates in 59 sessions |
| Oversold bounce | ❔ untested, ⚠️ risky | 2 candidates in 59 sessions |
| `xfade-dbl-bottom` on the ETFs | 📄 paper only | +0.24R on 2x ETFs in-sample, about −$1.28 a trade at $300 after fees; the scanner starts `paper_trade.py` itself |

The rules themselves live in `stockanalyzer/setups/vwap_rules.py`; the scanner and the backtest both call
it, so a change there is a change to both. Re-run the backtest after any change.

### Gap setups: failed gap-up (short) / reclaimed gap-down (long), 10:30 → close

The 2-year study of 2026-10-23 (memory `intraday-pattern-study`; 51 stocks, 488 sessions, its
own definitions) found, in a **down market (SPY under its 50-day)** on days when **several
stocks do the same thing**, +2.73% / +0.84% per trade at 2× for shorting failed gap-ups and
+0.58% / +1.57% for buying reclaimed gap-downs. **The watcher's coded version does not
reproduce that.** Re-run on 2023-12-21 → 2026-11-09 hourly bars, 22 names, $300 on a 2x ETF
after $3 (`algolab/screens/gap_setup_revalidation_2026_11_10/revalidate.py`):

| Coded rule, down market + breadth ≥ 3/20 | Trades / dates | Win % | $ per trade | Halves | Without the best date |
|---|--:|--:|--:|---|--:|
| Failed gap-up → inverse ETF | 155 / 28 | 52 | +$1.10 | +$1.78 / +$0.35 | −$0.03 |
| Reclaimed gap-down → long ETF | 136 / 17 | 45 | −$3.88 | −$3.64 / −$4.35 | −$5.35 |

Up-market and no-breadth cells were negative too. So the failed gap-up is an ACTION graded
🟡 weak at $300 max, and the reclaimed gap-down is WATCH only. The gap between the study and
the coded rule (20-name basket vs 51, ≥ 3 vs ≥ 6, hourly stop-first here) is open.

| | Failed gap-up → buy the inverse ETF | Reclaimed gap-down → buy the long ETF |
|---|---|---|
| Gap | open ≥ +2% over yesterday's close | open ≤ −2% |
| First hour (09:30–10:30) | closes **below** the open | closes **above** the open |
| Market | SPY under its 50-day (**yesterday's** close, no lookahead) | same |
| Breadth | ≥ 3 of a 20-stock basket do the same (the study used ≥ 6 of 51) | same |
| Orders | entry 10:30–11:30 · stop beyond the **first-hour high** · target = **gap fill** (yesterday's close) · **hold to 15:50** | stop under the **first-hour low** · same target and exit |

`gap_check` runs once per underlying between 10:30 and 11:30. If a condition fails, it posts a
WATCH saying which one. Relay a failed gap-up ACTION as "weak evidence, $300 max".

### Swing / overnight candidates: `scan_swing.py` (run it at 15:45, then share the plan)

```
.venv/Scripts/python.exe .claude/skills/live-trade-watch/scripts/scan_swing.py --watch SNDK=SNXX:2,SNDQ:-2 --also DD,TBBB
```
To run it automatically, start a background Bash that waits with a **Python** clock check
(Git Bash ignores `TZ=America/New_York`, so `date` checks fire at the wrong time):
`until .venv/Scripts/python.exe -c "import sys; sys.path.insert(0, '.claude/skills/live-trade-watch/scripts'); import common; sys.exit(0 if common.now().strftime('%H%M') >= '1546' else 1)"; do sleep 20; done; …scan_swing.py …`

Daily-bar Murphy evidence: **key reversal day** (new 20-day low, close above yesterday's close
in the upper part of the range), **selling / buying climax** (the same on ≥ 2× volume),
**island reversal**, **daily RSI divergence**, and the **MACD histogram turning**. One piece of
evidence = WEAK, two = MODERATE, three or more = STRONG. The plan is always for **tomorrow
after 10:00, on a break of today's high (long) or low (short)**, with the stop beyond the
other end of today's range and targets at the 33% / 50% retracement, plus the ETF levels.
It never says buy at the close: our earnings and gap studies found no high-probability
overnight direction, and Murphy's signals are untested on our data. Log every candidate in
`murphy_ledger.csv` and grade it the next day.

### Trend continuation: TREND PULLBACK ("Holy Grail") and flag breaks

For catching the **next leg** of a strong trend without waiting for VWAP:
- **Trend pullback** (Linda Raschke's "Holy Grail" in *Street Smarts*, plus Murphy's trend rules):
  5-min **ADX(14) ≥ 30** with −DI on top, a candle **rallies into the 5-min 20-EMA and closes
  back below it** (bearish candle, below VWAP) → buy the inverse ETF · stop above that candle
  or the EMA · target = the day low. The mirror works for uptrends. It triggers earlier than
  the VWAP setup, but its target (the day low) is closer, so the R:R gate often skips it late
  in the day.
- **Flag break** (Murphy ch. 6): a close outside the last hour's range on ≥ 1.2× volume →
  **WATCH only**. Don't chase the break; the entry is the first retest of the broken level
  that holds.

### ⚠️ RISKY setup: OVERSOLD-BOUNCE to VWAP (long, counter-trend)

Added 2026-11-05 at the user's request, **flagged as risky on every message**. It buys the
long ETF for a bounce back **up to** VWAP while the trend is still down.

| Step | Rule |
|---|---|
| Stretch | the day's low is **≥ 4% below VWAP** |
| Structure | a **higher low** after that low, then a fresh 5-min **close above the bounce high** between them |
| Evidence the selling is ending (one or more) | a **bullish RSI divergence** at the higher low, **VIX 10%+ off its high**, or **after 15:00** (stretch-reversal window) |
| Orders | buy the long ETF · **stop-market under the higher low** (at least 4%) · **target = VWAP** · $300 max · time stop 15:50 |
| Gate | only an ACTION if R:R ≥ 1.5 at the trigger; otherwise a WATCH |

How to relay it: open the ACTION block with **"⚠️ RISKY"**, say it's against the trend and
not backtested, and say what the evidence was. The window shows it in amber. A replay on
2026-11-05 SNDK found it would have triggered at 11:10 (close 861.89 above the 843.96
bounce high, with a bullish RSI divergence). The stop under the 819 higher low was about
−14% on SNXX against about +4% to VWAP, so it would have been a WATCH/skip. Bounce trades
are often too late by the time they confirm, and the R:R gate is what protects you.

### Murphy checks attached to every signal (they set the size; none of them blocks a trade)

`watch_setups.py` reads them from the underlying's completed 5-min candles (`common.context`, which uses
the repo's `indicators.rsi` / `indicators.adx`) and adds them to the `why` of each signal. Any
weak check turns the ACTION into "use the $300 size".

| Check | Murphy's idea | What it changes |
|---|---|---|
| **Volume on the signal candle** (strong ≥ 1.5×, weak < 0.8× the same clock candle's 14-session average) | volume confirms price (ch. 7) | weak → $300 size; say "weak volume" in the block |
| **ADX(14) on 5-min, with +DI/−DI** | trending (≥ 25) vs choppy (< 20) markets need different tools (directional movement) | chop → $300 size; trend in your direction → normal size |
| **Swing trend** (last two swing highs/lows: HH/HL, LH/LL, mixed) | the definition of trend (ch. 4) | trade with it; "mixed" = expect chop |
| **RSI(14) divergence at a fresh swing** | divergence, not the oversold level, is the signal (ch. 10) | 👀 WATCH alert. Bullish divergence while holding SNDQ/NBIZ = take profit sooner; bearish while long = tighten the stop. A divergence against a new signal → $300 size. |
| **VIX** (level, % vs yesterday, % off today's high) | sentiment / contrary opinion (ch. 10) | extreme (≥ 30 or +20% on the day) → bounces get violent: shorts take profit sooner, $300 size. Easing 10%+ off the high → short-covering risk. |

In fallback mode (Finnhub, no volume) the context is from Yahoo's last bars and is
marked "(stale)" in the window. Say so if you quote it.

### Signal-quality rules learned live (don't relax them)

| Rule | Learned from |
|---|---|
| **A failed bounce needs a rejection candle:** the 5-min candle reaches VWAP, closes below it **and** closes red or in the lower half of its range. An up candle closing at its high just under VWAP is buying pressure, not a failure. | 2026-11-05 14:35 NBIS false NBIZ alert (NBIS broke VWAP on the next candle) |
| **No new entries within 45 minutes of the trade's time stop** (shorts 14:55 → none after ~14:10; longs 15:50 → none after ~15:05). | same alert fired 15 min before 14:55 |
| **Pivots in the first ~75 min can't be used for RSI divergence** (RSI isn't valid yet). | 2026-11-05 13:55 false AMZN bearish divergence |
| **Check the feed before trusting a signal:** a frozen price stream makes every level stale. | 2026-11-05 Yahoo froze 12:10 → 13:50 |
| If an ACTION looks wrong when you relay it (late, weak, against the tape), **say so and recommend skipping**, then fix the rule and log it in `behavior_ledger.csv`. | all of the above |

Say it plainly when a setup is only marginal (light volume, a near-miss on R:R), and
say it when the user may be revenge-trading after a loss.

## 4. When the user reports a fill

1. Repeat the exact orders (stop-market and target) and ask them to confirm both are live.
2. Start the tracker as a Monitor (it drives the "OPEN" card in the window):
   ```
   .venv/Scripts/python.exe -u .claude/skills/live-trade-watch/scripts/track_position.py --etf SNDQ --und SNDK --lev -2 --entry 32.15 --shares 9 --stop 30.87 --target 35.53 --exit 14:55
   ```
3. While the position is open, relay only the tracker's ACTIONs and the news WATCHes that
   change the thesis. The underlying crossing VWAP against the trade is a **WATCH, not an exit**
   (ruled out as a standalone exit, LEARNINGS 2026-11-03); the stop-market is the exit. The tracker
   logs the price at the VWAP cross and the real exit to `algolab/live/vwap_exit_ledger.csv`. Treat repeat entry signals for the same underlying as "confirms,
   don't add".
4. If the user's numbers differ from the plan (a tighter stop, a bigger size), say what
   that changes in one line, then go with their numbers.

## 4b. The user's ideas vs the bot's call (paper trades)

When the user has an idea the scanner skipped (or the reverse), and they don't take it, paper-track it
so intuition and rules get compared on real outcomes:
```
.venv/Scripts/python.exe -u .claude/skills/live-trade-watch/scripts/paper_trade.py --und INTC --etf INTW --lev 2 --side long --stop-und 53.75 --targets-und 55.30,56.30 --exit 15:50 --label "user: …" --bot "SKIP: …"
```
Run it with Bash `run_in_background` (pipe through `grep --line-buffered` if you filter, or the output
only appears at the end). It enters at live quotes, sells half at target 1 and moves the stop to
breakeven, then exits at target 2, the stop or the time stop, and appends a row to
`algolab/live/paper_trades.csv` (idea, bot call, result %, $ on $300 after fees, best and worst move,
and who was right). Report the verdict plainly either way.

## 4c. Running without Claude

The scripts don't need Claude: Monitor only relays their output to chat. To run a session alone
(for example when usage runs out), double-click `scripts/start_session.bat` (edit `WATCH` / `NEWS`
in it first). It starts the scanner and the news watcher minimized, logging to
`.local/live/setups.log` and `news.log`, plus the Trade Radar window, which beeps and shows the trade
card, the open position and the latest headlines. Everything stops by itself after 16:00 ET. Without
Claude there's no judgment layer: the coded rules (R:R gate, rejection candle, late-entry block) are
the only filter. Read the logs afterwards for the session note.

## 5. Data feeds and failure modes

- Yahoo 5-min bars give price, VWAP and the day low. If Yahoo's last bar is more than 12
  minutes old during the session (it froze from 12:10 on 11-05), `watch_setups.py`
  switches to **Finnhub quotes** (`FINNHUB_KEY` from `.env`) and holds VWAP at Yahoo's
  last value. The feed shows on every heartbeat and in the window header. Tell the user
  once when it switches.
- If prices stop changing between heartbeats, check the last bar time before trusting
  any signal.
- Yahoo news is unreliable, so `watch_news.py` uses Google News RSS (no key).
- `popup.py` (Windows message box) is the fallback when the window isn't open:
  `watch_setups.py --popup`.

## 6. After the session (always)

Write `algolab/live/YYYY-MM-DD-<slug>.md` (context, plan, timeline, trades, verdicts,
lessons). Add rows to `murphy_ledger.csv` (every rule observed: validated, challenged
or inconclusive) and `behavior_ledger.csv` (each mistake or good habit, with its $
cost), then run `python algolab/live/summarize.py`. Things to check later go into the
memory follow-up note, or into a scheduled check if the user wants one.

## Files

| File | Role |
|---|---|
| `scripts/common.py` | clock, Yahoo/Finnhub feeds, ETF mapping, sizing (`FEE`, `MIN_STOP`, `SIZES`), message tags, state file |
| `scripts/watch_setups.py` | setup scanner (the `vwap_rules` rules, the gap setup, new low, time stops, the 📄 xfade paper tracker) plus the Murphy checks (time-of-day volume, ADX, swing trend, RSI divergence, VIX) → tagged lines + `.local/live/state.json` |
| `stockanalyzer/setups/vwap_rules.py` | the scanner's rules and their evidence grades (one source for the scanner and its backtest) |
| `scripts/watch_news.py` | Google News headlines, market-moving only (`--all` for everything) |
| `scripts/radar_gui.py` | the "Trade Radar" window: trade-ready card with entry/stop/target and size advice, a beep, copy orders, a VIX line, per-ticker VWAP gauge with RSI/ADX/volume and divergence badges, open-position card |
| `scripts/scan_swing.py` | end-of-day Murphy reversal scanner (key reversal, climax, island, RSI divergence, MACD turn) → tomorrow's trigger, stop and targets, in ETF terms too |
| `scripts/track_position.py` | open-position alerts (stop, target, +1R, giveback, time stop; VWAP against as a logged WATCH) → `.local/live/position.json`, `algolab/live/vwap_exit_ledger.csv` |
| `scripts/popup.py` | message-box fallback |

`.local/` is gitignored, so live state never gets committed.
