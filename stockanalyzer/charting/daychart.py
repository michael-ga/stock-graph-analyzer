"""Quick "picture of the trading day" charts for eyeballing patterns.

Scripted end to end, no LLM in the loop: a tiny date-phrase parser picks the
days, ONE yfinance download covers them all, and plotly writes a standalone HTML
page (plotly.js from CDN, so the file is small) — no kaleido, no Streamlit, no
analysis engine. A per-day stats table is also printed to stdout so a caller can
summarise without opening the chart.

    python -m stockanalyzer.charting.daychart SNDK "last 10 mondays"
    python -m stockanalyzer.charting.daychart MSFT "last week" --summary

Two views:
  * days (default) — one small intraday chart per day, x-axis aligned on
    time-of-day, plus an overlay of every day's % move from the open. This is
    the "spot the recurring shape" view.
  * summary — one continuous chart over the whole span with volume, plus the
    period stats. This is the "what happened last week" view.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_UP = "#26a69a"
_DOWN = "#ef5350"
_VWAP = "#ffa726"
_OUT_DIR = Path(".local/charts")

_WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "twenty": 20, "thirty": 30,
}

# yfinance only serves fine intraday bars for recent history.
_MAX_AGE_5M = 58
_MAX_AGE_1H = 725


# ---------------------------------------------------------------- date phrases

def _weekdays_between(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _num(tok: str | None, default: int = 1) -> int:
    if not tok:
        return default
    return int(tok) if tok.isdigit() else _NUM_WORDS[tok]


def resolve_days(spec: str, today: date | None = None) -> list[date]:
    """Turn a date phrase into a sorted list of weekdays.

    Market holidays are NOT removed here — they simply come back with no bars
    and are dropped after the fetch. Supported phrases (case-insensitive):

      today | yesterday | this week | last week | last N weeks
      this month | last month | last N days
      last monday | last 10 mondays | last 3 fri
      2026-10-13 | 2026-10-01..2026-10-10 | 2026-10-13,2026-10-15
    """
    today = today or date.today()
    s = spec.strip().lower()
    s = re.sub(r"\b(past|previous|the)\b", lambda m: "last" if m.group(1) != "the" else "", s)
    s = re.sub(r"\s+", " ", s).strip()
    num = r"(\d+|" + "|".join(_NUM_WORDS) + r")"

    # Explicit ISO dates: single, A..B range, or comma list.
    if re.fullmatch(r"[\d\-.,: ]+", s):
        if ".." in s or ":" in s:
            a, b = re.split(r"\.\.|:", s, maxsplit=1)
            return _weekdays_between(date.fromisoformat(a.strip()), date.fromisoformat(b.strip()))
        return sorted({date.fromisoformat(p.strip()) for p in s.split(",") if p.strip()})

    if s == "today":
        return [today]
    if s == "yesterday":
        return [_prev_weekday(today)]

    monday = today - timedelta(days=today.weekday())
    if s == "this week":
        return _weekdays_between(monday, today)
    if m := re.fullmatch(rf"last(?: {num})? weeks?", s):
        n = _num(m.group(1))
        return _weekdays_between(monday - timedelta(weeks=n), monday - timedelta(days=1))

    first = today.replace(day=1)
    if s == "this month":
        return _weekdays_between(first, today)
    if s == "last month":
        prev_last = first - timedelta(days=1)
        return _weekdays_between(prev_last.replace(day=1), prev_last)

    if m := re.fullmatch(rf"last {num} (?:trading )?days?", s):
        n, out, d = _num(m.group(1)), [], today
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d)
            d -= timedelta(days=1)
        return sorted(out)

    if m := re.fullmatch(rf"last(?: {num})? ([a-z]+?)s?", s):
        key = m.group(2)[:3]
        if key in _WEEKDAYS and _WEEKDAYS[key] < 5:
            n, wd = _num(m.group(1)), _WEEKDAYS[key]
            d = today - timedelta(days=(today.weekday() - wd) % 7)
            return sorted(d - timedelta(weeks=i) for i in range(n))

    raise ValueError(
        f"Can't read date phrase {spec!r}. Try e.g. 'last 10 mondays', 'last week', "
        "'last 5 days', '2026-10-13', '2026-10-01..2026-10-10'."
    )


# ---------------------------------------------------------------- data

def pick_interval(days: list[date], summary: bool, today: date | None = None) -> str:
    today = today or date.today()
    age = (today - min(days)).days
    span = (max(days) - min(days)).days
    if summary and span > 21:
        return "1d"
    if age <= _MAX_AGE_5M:
        return "5m" if not summary or span <= 7 else "30m"
    if age <= _MAX_AGE_1H:
        return "1h"
    return "1d"


def fetch_bars(ticker: str, days: list[date], interval: str) -> pd.DataFrame:
    """One download spanning all requested days, filtered to exactly those days.
    Regular session only (no pre/post) so day shapes are comparable."""
    import yfinance as yf

    df = yf.download(
        ticker, start=min(days).isoformat(),
        end=(max(days) + timedelta(days=1)).isoformat(),
        interval=interval, auto_adjust=False, progress=False, threads=False,
        prepost=False,
    )
    if df is None or len(df) == 0:
        raise RuntimeError(f"yfinance returned no {interval} data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    wanted = set(days)
    return df[[d in wanted for d in df.index.date]].dropna(subset=["close"])


# ---------------------------------------------------------------- stats

@dataclass
class DayStats:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def change_pct(self) -> float:
        return (self.close / self.open - 1) * 100 if self.open else 0.0

    @property
    def range_pct(self) -> float:
        return (self.high / self.low - 1) * 100 if self.low else 0.0


def day_stats(df: pd.DataFrame) -> list[DayStats]:
    out = []
    for d, g in df.groupby(df.index.date):
        out.append(DayStats(d, float(g["open"].iloc[0]), float(g["high"].max()),
                            float(g["low"].min()), float(g["close"].iloc[-1]),
                            float(g["volume"].sum())))
    return out


def stats_text(ticker: str, spec: str, stats: list[DayStats]) -> str:
    lines = [f"{ticker} - {spec} ({len(stats)} trading days)",
             f"{'date':<14}{'open':>9}{'high':>9}{'low':>9}{'close':>9}{'chg%':>7}{'rng%':>6}{'vol':>9}"]
    for s in stats:
        lines.append(f"{s.day:%a %Y-%m-%d}{s.open:>9.2f}{s.high:>9.2f}{s.low:>9.2f}"
                     f"{s.close:>9.2f}{s.change_pct:>+7.2f}{s.range_pct:>6.2f}{_human(s.volume):>9}")
    if stats:
        o, c = stats[0].open, stats[-1].close
        hi = max(stats, key=lambda s: s.high)
        lo = min(stats, key=lambda s: s.low)
        ups = sum(s.change_pct > 0 for s in stats)
        lines.append(
            f"period: {o:.2f} -> {c:.2f} ({(c / o - 1) * 100:+.2f}%), "
            f"high {hi.high:.2f} ({hi.day:%a %m-%d}), low {lo.low:.2f} ({lo.day:%a %m-%d}), "
            f"{ups}/{len(stats)} up days, avg range {sum(s.range_pct for s in stats) / len(stats):.2f}%"
        )
    return "\n".join(lines)


def _human(v: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= div:
            return f"{v / div:.1f}{unit}"
    return f"{v:.0f}"


# ---------------------------------------------------------------- figures

def _vwap(g: pd.DataFrame) -> pd.Series:
    tp = (g["high"] + g["low"] + g["close"]) / 3
    vol = g["volume"].replace(0, pd.NA).fillna(0)
    cum = vol.cumsum().replace(0, pd.NA)
    return ((tp * vol).cumsum() / cum).astype(float)


def days_figure(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Small multiples: one intraday candle chart per day, time-of-day x-axis."""
    groups = list(df.groupby(df.index.date))
    n = len(groups)
    cols = min(5, n) or 1
    rows = -(-n // cols)
    titles = []
    for d, g in groups:
        s = day_stats(g)[0]
        titles.append(f"{d:%a %b %d}  {s.change_pct:+.1f}%  rng {s.range_pct:.1f}%")
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles,
                        horizontal_spacing=0.03, vertical_spacing=0.09 if rows > 1 else 0.1)
    for i, (d, g) in enumerate(groups):
        r, c = i // cols + 1, i % cols + 1
        x = g.index.strftime("%H:%M")
        fig.add_trace(go.Candlestick(
            x=x, open=g["open"], high=g["high"], low=g["low"], close=g["close"],
            increasing_line_color=_UP, decreasing_line_color=_DOWN,
            increasing_fillcolor=_UP, decreasing_fillcolor=_DOWN,
            line=dict(width=1), showlegend=False, name=str(d),
        ), row=r, col=c)
        if g["volume"].sum() > 0:
            fig.add_trace(go.Scatter(x=x, y=_vwap(g), mode="lines", showlegend=False,
                                     line=dict(color=_VWAP, width=1), name="VWAP"),
                          row=r, col=c)
    fig.update_xaxes(type="category", nticks=5, rangeslider_visible=False, showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(120,130,140,0.18)", tickfont=dict(size=9))
    fig.update_annotations(font_size=11)
    fig.update_layout(
        template="plotly_dark", height=max(320, 260 * rows),
        title=f"{ticker} — intraday by day (orange = VWAP)",
        margin=dict(l=40, r=20, t=70, b=30),
    )
    return fig


def overlay_figure(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Every day's % move from its own open on one time-of-day axis. Older days
    fade; the latest is bold — recurring shapes jump out here."""
    groups = list(df.groupby(df.index.date))
    fig = go.Figure()
    n = len(groups)
    for i, (d, g) in enumerate(groups):
        latest = i == n - 1
        pct = (g["close"] / g["open"].iloc[0] - 1) * 100
        fig.add_trace(go.Scatter(
            x=g.index.strftime("%H:%M"), y=pct, mode="lines", name=f"{d:%a %m-%d}",
            line=dict(width=2.5 if latest else 1.2),
            opacity=1.0 if latest else 0.35 + 0.5 * i / max(n - 1, 1),
        ))
    fig.add_hline(y=0, line=dict(color="rgba(200,200,200,0.4)", dash="dot"))
    fig.update_xaxes(type="category", nticks=8, showgrid=False)
    fig.update_layout(template="plotly_dark", height=420,
                      title=f"{ticker} — % from open, all days overlaid",
                      yaxis_title="% from open", margin=dict(l=50, r=20, t=60, b=30))
    return fig


def summary_figure(df: pd.DataFrame, ticker: str, interval: str) -> go.Figure:
    """One continuous chart over the span, gaps (nights/weekends) collapsed."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22],
                        vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing_line_color=_UP, decreasing_line_color=_DOWN,
        increasing_fillcolor=_UP, decreasing_fillcolor=_DOWN, line=dict(width=1),
        showlegend=False, name="price"), row=1, col=1)
    colors = [_UP if c >= o else _DOWN for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], marker_color=colors, opacity=0.6,
                         showlegend=False, name="volume"), row=2, col=1)
    breaks = [dict(bounds=["sat", "mon"])]
    if interval != "1d":
        breaks.append(dict(bounds=[16, 9.5], pattern="hour"))
        # Holidays inside the span would otherwise show as a flat full-day gap.
        present = set(df.index.date)
        holes = [d.isoformat() for d in _weekdays_between(df.index[0].date(), df.index[-1].date())
                 if d not in present]
        if holes:
            breaks.append(dict(values=holes, dvalue=24 * 3600 * 1000))
    fig.update_xaxes(rangebreaks=breaks, rangeslider_visible=False, showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(120,130,140,0.18)")
    fig.update_layout(template="plotly_dark", height=620,
                      title=f"{ticker} — {interval} bars",
                      margin=dict(l=50, r=20, t=60, b=30))
    return fig


def build_html(df: pd.DataFrame, ticker: str, spec: str, interval: str, summary: bool) -> str:
    stats = day_stats(df)
    if summary or interval == "1d":
        figs = [summary_figure(df, ticker, interval)]
    else:
        figs = [days_figure(df, ticker), overlay_figure(df, ticker)]
    parts = [f.to_html(full_html=False, include_plotlyjs="cdn" if i == 0 else False)
             for i, f in enumerate(figs)]
    table = stats_text(ticker, spec, stats).replace("&", "&amp;").replace("<", "&lt;")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{ticker} {spec}</title>"
        "<style>body{background:#111418;color:#cfd8dc;font-family:system-ui,sans-serif;"
        "margin:0;padding:12px}pre{font-size:12px;overflow-x:auto}</style></head><body>"
        + "".join(parts)
        + f"<pre>{table}</pre></body></html>"
    )


# ---------------------------------------------------------------- CLI

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "chart"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Picture of trading day(s) for a ticker.")
    p.add_argument("ticker")
    p.add_argument("when", nargs="+", help="date phrase, e.g. 'last 10 mondays'")
    p.add_argument("--summary", action="store_true",
                   help="one continuous chart over the span instead of one chart per day")
    p.add_argument("--interval", help="force bar size (1m,5m,15m,30m,1h,1d)")
    p.add_argument("--out", help="output .html path (default .local/charts/...)")
    p.add_argument("--open", action="store_true", help="open the chart in the browser")
    a = p.parse_args(argv)

    ticker = a.ticker.upper()
    spec = " ".join(a.when)
    days = resolve_days(spec)
    interval = a.interval or pick_interval(days, a.summary)
    df = fetch_bars(ticker, days, interval)
    if df.empty:
        print(f"No {interval} bars for {ticker} on {spec} (holidays / too old for intraday?)",
              file=sys.stderr)
        return 1

    out = Path(a.out) if a.out else _OUT_DIR / (
        f"{ticker}_{_slug(spec)}{'_summary' if a.summary else ''}_{datetime.now():%Y%m%d-%H%M%S}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(df, ticker, spec, interval, a.summary), encoding="utf-8")

    missing = sorted(set(days) - set(df.index.date))
    print(stats_text(ticker, spec, day_stats(df)))
    if missing:
        print("no data (holiday/future): " + ", ".join(f"{d:%Y-%m-%d}" for d in missing))
    print(f"interval: {interval}")
    print(f"chart: {out.resolve()}")
    if a.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
