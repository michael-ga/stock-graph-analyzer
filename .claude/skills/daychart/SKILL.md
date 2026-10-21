---
name: daychart
description: Chart trading day(s) for a ticker to spot intraday patterns visually, or summarise a period. Use for requests like "graph of last 10 mondays for SNDK", "summarise last week for MSFT", "show me AAPL yesterday", "NVDA 2026-10-01..2026-10-10".
---

# daychart — picture of trading day(s)

One scripted command does everything (date parsing, one yfinance download,
standalone plotly HTML, stats to stdout). Do not write new chart code and do not
start Streamlit — just run the script.

## 1. Map the request to two arguments

- **TICKER** — the symbol, any case (`sndk` → `SNDK`).
- **WHEN** — a date phrase the script understands:
  `today` · `yesterday` · `this week` · `last week` · `last 3 weeks` ·
  `this month` · `last month` · `last 5 days` · `last monday` ·
  `last 10 mondays` · `last 4 fri` · `2026-10-13` · `2026-10-13,2026-10-15` ·
  `2026-10-01..2026-10-10`.
  Anything else, convert it yourself to explicit ISO dates.

Choose the view:
- Pattern / "graph of" / specific days → default (one small chart per day +
  all days overlaid as % from open).
- "summarise" / "how did X do" / a continuous period → add `--summary`
  (one continuous candlestick + volume).

## 2. Run it

```
.venv/Scripts/python.exe -m stockanalyzer.charting.daychart SNDK "last 10 mondays"
.venv/Scripts/python.exe -m stockanalyzer.charting.daychart MSFT "last week" --summary
```

Optional: `--interval 5m|15m|30m|1h|1d` (auto otherwise: 5m within ~58 days,
1h within ~2 years, else daily), `--out path.html`, `--open` (browser).

## 3. Report

- stdout has a per-day table (open/high/low/close, chg%, range%, volume) and a
  period line — summarise from that text; no need to look at the image.
- Send the HTML file from the `chart:` line to the user (SendUserFile, render).
- Mention any `no data (holiday/future)` days and the bar interval used.
- For a "patterns" request, point out what the table shows (e.g. most Mondays
  closing below the open, ranges shrinking); the visual is for the user.
