# Stock Graph Analyzer

A personal stock technical-analysis app. Enter a ticker → it fetches OHLCV across
**1D / 5D / 1M / 6M / 1Y**, runs a technical-analysis engine (rules encoded from
John Murphy's *Technical Analysis of the Financial Markets*), and shows annotated
candlestick charts, a per-timeframe trend report, and an overall **explained verdict**.

> Educational only — not financial advice.

## Honest Targets & Calibrated Scoring (latest)

Calibrated against three real failure cases (INTC/MSFT/NOK screenshots → frozen as
regression fixtures in `tests/test_calibration.py`):

- **Honest targets:** target = next mapped resistance (sold just under the wall),
  capped by the volatility budget `dailyATR × √horizon_days` — the old fixed-%
  floor that inflated targets through ceilings is gone.
- **Plan kinds:** `immediate` / `breakout_wait` ("wait for a close above $X, then
  target $Y" with a 🚀 live trigger alert) / `no_trade` (watch-levels or, in Own
  mode, protect-and-trim) — each with a one-line expert `guidance` sentence.
- **Vol-aware stops:** sized from the *daily* ATR (6M frame), so a stop survives a
  day of noise; never the intraday chart's ATR.
- **Calibrated score:** setup (countertrend = half weight) · honest R:R · clear
  path (no walls in the way) · 5D/6M/1Y MA+MACD (n/a rows shown, never silently
  dropped) · engine bias agreement · RSI sanity · post-gap chase · overextension ·
  conflict with the long-term read — plus info rows (earnings proximity via the
  Finnhub calendar, Street target sanity, fast-mover).
- **GO is rarer and means it:** earnings inside the horizon block GO; countertrend
  setups can't GO against a sub-neutral investor read on the standard pace.

## Live Mode 2.0 — whole-page real-time dashboard

- Toggle **🔴 Live mode** → the page becomes a single live dashboard (needs a free `FINNHUB_KEY`).
- **Two cadences:** price ticks ~1s (Finnhub trade WebSocket); the signal engine re-runs ~45s
  on a fresh 1D frame (cache-safe, ~1 fetch/min).
- **Live decision strip:** ⚡ Swing (GO/forming + R:R) **and** 📈 Long-term read **and** the
  trend-change meter, side by side — fast buy/sell calls without toggling strategy.
- **Flip alerts:** a toast + a timestamped **event feed** when the light flips, a signal
  appears/clears, a trend change crosses, or price touches the stop/target/key level
  (`stockanalyzer/live_events.py`, pure + tested).
- **Live chart** with entry/stop/target lines, a live-price marker, zoom preserved across
  refreshes (`uirevision`), plus a tick **heartbeat** of forming candles.
- Plan, levels and your **P&L recompute every second** against the live price; the structural
  multi-timeframe verdict stays fixed (it doesn't change intraday — the UI says so).
- No key → graceful 60-second REST polling fallback.

## Swing-trading mode

- New **Strategy** toggle: *Long-term investor* vs *⚡ Swing trader (days–2wk)*.
- Swing mode **inverts the logic**: weights short timeframes, decides on the 1M chart,
  and produces a concrete **trade plan** — Entry (near the reversal, not a far breakout),
  Stop (tighter of swing-low / 1.8×ATR), Target (+10–15% / next resistance), and a
  **reward:risk badge** that only shows **GO at R:R ≥ 2:1**.
- Setups encoded from researched rulebook: trend-change reversal, pullback-to-20-EMA,
  oversold bounce, volume breakout, momentum/gap. Fast-mover (gap/volume) alert.
- **Selectable swing pace:** *Standard* (days–2wk, 1M chart, +10–15% target) or
  *Fast (1–3 days)* (5D/1D chart, tighter ~1.2×ATR stop, +6–12% target).
- Fixes the "wait for $17 on a $14.46 stock" problem — swing now says e.g.
  *"Entry $14.9, Stop $14.34 (−3.7%), Target $17.13 (+15%), R:R 4:1."*

## Extended-hours round

- **Pre-market & after-hours data** now included on intraday charts (yfinance `prepost=True`;
  the only free source). 1D went from ~78 regular bars to ~190 with extended sessions.
- **Shaded extended-hours bands** on 1D/5D charts; session classifier (`data/market_session.py`).
- **Pre/after-hours gap signal** (`analysis/premarket.py`) feeds the engine + trend-change
  meter, with a **heads-up alert** ("🌅 Pre-market +1.8% — possible early move").
- **Session-aware price header**: 🌅 PRE-MARKET / 🌙 AFTER-HOURS badge, change vs prior close.

## Non-expert UX round

- **Plain-English recommendation (no AI):** rule-based engine turns signals into a
  preset (Strong Buy → Strong Sell), a conviction **% gauge**, and a green/yellow/red
  **GO / CAUTION / NO-GO** traffic light tailored to your use-case (Buy / Sell / Own).
- **Practical scenarios + checklist:** "what could happen next" up/down levels and a
  go/no-go checklist with real prices — all derived from support/resistance.
- **Chart view:** mouse-wheel zoom, added **YTD** and **5Y** ranges (now 7), per-chart
  plain-language captions, jargon explained inline.
- **Current price header** at the top (color-coded change/%), like every trading site.
- **Watchlist:** follow tickers → quick-pick chips above the input (persisted).
- **Smart rate-limiting + caching:** token-bucket per provider (Twelve Data 8/min·800/day,
  Finnhub 60/min), per-timeframe TTL, stale-while-error.
- **Live buy-window mode:** refresh ~60s for 15 min and track signal *persistence* to
  confirm entry timing (refines timing only — not the structural verdict).

## Status — all phases built

- ✅ **Phase 1 (MVP):** API data (yfinance default, Twelve Data optional) + core graph
  analysis + Streamlit dashboard + tests.
- ✅ **Phase 2:** Finnhub fundamentals/analyst/news; sentiment blended into the verdict
  (70% technicals / 30% sentiment). Degrades to technicals-only without a key.
- ✅ **Phase 3:** image fallback — classify a screenshot as chart vs info panel, read
  candles from a chart via OpenCV, OCR an info panel (pluggable backend).
- ✅ **Phase 4:** news headlines scored with VADER (recency-weighted) and folded into
  the sentiment score.

See [the plan](../../.claude/plans/i-want-to-plan-happy-coral.md) for the original roadmap.

## Setup

```powershell
# from this folder
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# optional: copy .env.example to .env and add API keys (yfinance needs none)
```

## Run

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Then open the URL it prints (default http://localhost:8501).

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Layout

```
app.py                      Streamlit dashboard
stockanalyzer/
  data/        providers (yfinance / Twelve Data), Finnhub, OHLCV schema, cache
  analysis/    the engine: trend, levels, trendlines, indicators, candles,
               patterns, volume, trend-change scoring
  sentiment/   analyst + price-target + VADER news → sentiment score
  vision/      image fallback: classify, chart_reader (OpenCV), info_reader (OCR)
  charting/    plotly candlestick + annotations
  verdict/     multi-timeframe synthesis → explained verdict
  pipeline.py  fetch-all-timeframes + analyze + sentiment + verdict
tests/         golden-data detector, sentiment, and vision round-trip tests
```

## Using the image fallback

In the dashboard sidebar, switch **Mode** to *Image fallback* and upload a screenshot.
Chart screenshots are read via OpenCV (approximate, relative prices). Info/stat panels
need an OCR backend — install one:

```powershell
.\.venv\Scripts\python.exe -m pip install paddleocr paddlepaddle   # recommended
# or: pip install easyocr   |   or: install Tesseract + pip install pytesseract
```

## Notes

- Indicators (RSI/MACD/MA/stochastics/ATR) are computed directly in pandas/numpy —
  no TA-Lib (painful on Windows) and no pandas-ta (numpy-version fragility).
- The cache (`.cache/`) keeps multi-timeframe loads within free API limits.
