"""🔎 Multi-day scan — find the replay days where the bots actually did something.

Pick tickers and days (last N sessions, a date range, or a calendar of market-open
days), run every bot over each ticker-day headlessly, and get the answer back as a
date × ticker grid, a scroll of day cards, or a flat table. Quiet days drop out;
any day can be opened straight into the bar-by-bar replay.

Everything decision-shaped lives in ``stockanalyzer.replay_scan`` (headless and
tested); this module is presentation and Streamlit wiring only.
"""
from __future__ import annotations

import calendar
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui_common
from stockanalyzer import replay, replay_scan, watchlist

_ROOT = Path(__file__).resolve().parent

SINGLE, SCAN = "🎬 Single day", "🔎 Multi-day scan"

_SECONDS_PER_DAY = 12        # measured: one ticker-day precompute + bot run
_CARDS_PAGE = 24

# Scan inputs outlive a flip to the single-day view (which doesn't draw them).
_KEEP = "session"

_STATUS = {   # status → (icon, cell background, label)
    "traded": ("🟢", "rgba(27,158,62,0.22)", "bots traded"),
    "signal": ("🟡", "rgba(240,180,0,0.22)", "signal, no fill"),
    "quiet": ("⚪", "", "quiet"),
    "error": ("⚠️", "rgba(229,57,53,0.15)", "no data"),
}
_SHOW = {"🟢 Traded": ("traded",), "🟡 Any signal": ("traded", "signal"),
         "All days": ("traded", "signal", "quiet", "error")}
_FAMILY = {"orb": "🚀", "rev": "🔄", "swing": "📈"}


# --------------------------------------------------------------------------- #
# Navigation into the single-day replay.
# --------------------------------------------------------------------------- #
def goto_replay(ticker: str, day: str) -> None:
    """Button callback: open ``ticker`` on ``day`` in the bar-by-bar replay.

    A callback (not inline code) because it sets widget keys — ``rp_mode`` among
    them — which Streamlit only allows before those widgets are drawn."""
    ss = st.session_state
    ss.rp_mode = SINGLE
    ss.rp_ticker = ticker
    ss.pop("rp_ticker_in", None)        # re-seed both pickers from rp_ticker/rp_goto
    ss.pop("rp_day_pick", None)
    ss.rp_goto = (ticker, pd.Timestamp(day))


# --------------------------------------------------------------------------- #
# Inputs.
# --------------------------------------------------------------------------- #
def _parse_tickers(text: str) -> list[str]:
    return list(dict.fromkeys(t for t in re.split(r"[\s,;]+", text.upper()) if t))


def _use_watchlist() -> None:
    ss = st.session_state
    try:
        followed = watchlist.load(user_id=ss.get("auth_user_id"))
    except Exception:
        followed = []
    if followed:
        ss.rs_tickers = ", ".join(followed)


def _histories(tickers: list[str], load) -> tuple[dict, list[str]]:
    hists, failed = {}, []
    for tk in tickers:
        try:
            hists[tk] = load(tk)
        except Exception:
            failed.append(tk)
    return hists, failed


def _calendar(days: list[pd.Timestamp], marks: dict) -> list[pd.Timestamp]:
    """Month grids of the window. Market-open days are checkboxes; weekdays the
    market was shut are struck through. ``marks`` maps a day to a status icon
    from earlier scans, so the calendar itself shows where the action was."""
    ss = st.session_state
    open_days = {d.normalize() for d in days}
    keys = {d: f"rs_cal_{d:%Y%m%d}" for d in open_days}

    def _set(pick) -> None:
        for d, k in keys.items():
            st.session_state[k] = bool(pick(d))

    b = st.columns([1, 1, 1.8, 2.2])
    b[0].button("Select all", on_click=_set, args=(lambda d: True,),
                use_container_width=True)
    b[1].button("Clear", on_click=_set, args=(lambda d: False,),
                use_container_width=True)
    b[2].button("Only days with events", on_click=_set,
                args=(lambda d: marks.get(d) in ("🟢", "🟡"),),
                use_container_width=True, disabled=not marks,
                help="Keep just the days an earlier scan found signals on")
    b[3].caption("🟢 bots traded · 🟡 signal only — from earlier scans")

    first, last = min(open_days), max(open_days)
    months = pd.period_range(first, last, freq="M")
    per_row = 3
    for start in range(0, len(months), per_row):
        cols = st.columns(per_row)
        for col, month in zip(cols, months[start:start + per_row]):
            with col.container(border=True):
                st.markdown(f"**{month.strftime('%B %Y')}**")
                head = st.columns(5)
                for c, name in zip(head, ("Mon", "Tue", "Wed", "Thu", "Fri")):
                    c.caption(name)
                for week in calendar.Calendar().monthdatescalendar(month.year, month.month):
                    row = st.columns(5)
                    for c, d in zip(row, week[:5]):
                        d = pd.Timestamp(d)
                        if d.month != month.month or d < first or d > last:
                            c.write("")
                        elif d in open_days:
                            c.checkbox(f"{d.day}{marks.get(d, '')}", key=keys[d],
                                       persist_state=_KEEP)
                        else:
                            c.caption(f"~~{d.day}~~", help="Market closed")
    return sorted(d for d, k in keys.items() if ss.get(k))


def _pick_days(days: list[pd.Timestamp], marks: dict) -> list[pd.Timestamp]:
    ss = st.session_state
    how = st.segmented_control(
        "Days", ["Last N sessions", "Date range", "Pick on calendar"],
        default="Last N sessions", required=True, key="rs_how",
        persist_state=_KEEP)
    if how == "Last N sessions":
        n = st.slider("Sessions", 1, len(days), min(10, len(days)), key="rs_n",
                      persist_state=_KEEP,
                      help="Counted back from the most recent session.")
        return days[-n:]
    if how == "Date range":
        ss.setdefault("rs_range", (days[max(0, len(days) - 10)].date(), days[-1].date()))
        rng = st.date_input("From – to", key="rs_range", min_value=days[0].date(),
                            max_value=days[-1].date(), format="YYYY-MM-DD",
                            persist_state=_KEEP)
        if not isinstance(rng, (tuple, list)) or len(rng) != 2:
            st.caption("Pick an end date too.")
            return []
        lo, hi = (pd.Timestamp(x) for x in rng)
        return [d for d in days if lo <= d <= hi]
    return _calendar(days, marks)


# --------------------------------------------------------------------------- #
# Results.
# --------------------------------------------------------------------------- #
def _cell(r: replay_scan.DayResult | None) -> str:
    if r is None:
        return "· not scanned"
    icon = _STATUS[r.status][0]
    if r.status == "traded":
        return f"{icon} {len(r.trades)}T {r.net_r:+.1f}R"
    if r.status == "signal":
        return f"{icon} {r.signal_bars} sig"
    if r.status == "error":
        return f"{icon} no data"
    move = r.change_pct
    return f"{icon} {move:+.1f}%" if move is not None else icon


def _cell_bg(r: replay_scan.DayResult | None) -> str:
    """Traded days shade by outcome — a losing day must not read as a good one."""
    if r is None:
        return ""
    if r.status == "traded" and r.net_r < 0:
        return "rgba(229,57,53,0.22)"
    return _STATUS[r.status][1]


def _grid(results, show, tickers) -> None:
    keep = [r for r in results if r.status in show]
    if not keep:
        st.info("No days match this filter.")
        return
    g = replay_scan.grid(results, tickers)
    g = g[g.apply(lambda row: any(r is not None and r.status in show for r in row), axis=1)]
    text = g.map(_cell)
    text.index = [f"{pd.Timestamp(d):%a %Y-%m-%d}" for d in g.index]

    def _bg(col):
        out = []
        for r in g[col.name]:
            bg = _cell_bg(r)
            out.append(f"background-color: {bg}" if bg else "")
        return out

    st.caption("Click a cell to inspect that day; open it in the replay from there.")
    picked = st.dataframe(text.style.apply(_bg, axis=0), use_container_width=True,
                          on_select="rerun", selection_mode="single-cell",
                          key="rs_grid_pick", height=min(38 * (len(g) + 1) + 4, 640))
    cells = (picked.selection or {}).get("cells") or [] if picked else []
    if cells:
        row, col = cells[0]
        r = g.iloc[row][col] if col in g.columns and row < len(g) else None
        if r is not None:
            _card(r, key="grid")


def _mini_chart(r: replay_scan.DayResult) -> go.Figure:
    x = list(r.clocks)
    fig = go.Figure(go.Scatter(x=x, y=list(r.closes), mode="lines",
                               line=dict(width=1.6, color="#5c7cfa"),
                               hovertemplate="%{x} %{y:.2f}<extra></extra>",
                               showlegend=False))
    if r.prev_close:
        fig.add_hline(y=r.prev_close, line=dict(dash="dot", width=1, color="#888"))
    sig = {}
    for s in r.signals:
        sig.setdefault(s["t"], []).append(s["bot"])
    px = dict(zip(r.clocks, r.closes))
    if sig:
        fig.add_trace(go.Scatter(
            x=list(sig), y=[px.get(t) for t in sig], mode="markers",
            marker=dict(symbol="diamond", size=7, color="#f0b400"),
            hovertext=[f"{t}: {', '.join(b)}" for t, b in sig.items()],
            hoverinfo="text", showlegend=False))
    for t in r.trades:
        if not t.get("in"):
            continue
        win = (t.get("pnl_pct") or 0) > 0
        fig.add_trace(go.Scatter(
            x=[t["in"], t.get("out") or x[-1]], y=[t["entry"], t.get("exit") or t["entry"]],
            mode="lines+markers", line=dict(width=2, color="#1b9e3e" if win else "#e53935"),
            marker=dict(size=7, symbol=["triangle-up", "x"]),
            hovertext=f"{t['trader']} {t.get('why')} {t.get('r') or 0:+.2f}R",
            hoverinfo="text", showlegend=False))
    fig.update_layout(height=170, margin=dict(l=4, r=4, t=4, b=4),
                      xaxis=dict(showgrid=False, nticks=6),
                      yaxis=dict(showgrid=False, side="right"))
    return fig


def _card(r: replay_scan.DayResult, key: str) -> None:
    icon, _, label = _STATUS[r.status]
    with st.container(border=True):
        head, btn = st.columns([3, 1.2])
        move = f" · {r.change_pct:+.2f}%" if r.change_pct is not None else ""
        head.markdown(f"**{r.ticker}** · {r.date:%a %d %b %Y}{move}  \n"
                      f"{icon} {label}"
                      + (f" · first signal {r.first_signal}" if r.first_signal else "")
                      + (" · " + " ".join(_FAMILY[f] for f in r.families) if r.families else ""))
        btn.button("🎬 Replay", key=f"rs_go_{key}_{r.ticker}_{r.day}",
                   on_click=goto_replay, args=(r.ticker, r.day),
                   use_container_width=True, disabled=r.status == "error",
                   help="Open this day in the bar-by-bar replay")
        if r.error:
            st.caption(r.error)
            return
        st.plotly_chart(_mini_chart(r), use_container_width=True,
                        config={"displayModeBar": False},
                        key=f"rs_fig_{key}_{r.ticker}_{r.day}")
        if r.trades:
            st.markdown(f"**{len(r.trades)} trade(s) · {r.wins}W/{r.losses}L · "
                        f"{r.net_r:+.2f}R · ${r.net_usd:+,.2f}** "
                        f"<span style='opacity:.7'>({r.ideas} idea(s))</span>",
                        unsafe_allow_html=True)
            df = pd.DataFrame(r.trades)[["trader", "in", "out", "entry", "exit",
                                         "why", "r", "pnl_pct"]]
            st.dataframe(ui_common.pnl_style(df, pct_cols=["pnl_pct"], r_cols=["r"],
                                             price_cols=["entry", "exit"]),
                         hide_index=True, use_container_width=True)
        elif r.signals:
            bots = sorted({s["bot"] for s in r.signals})
            st.caption(f"Signals on {r.signal_bars} bar(s) from {', '.join(bots)} — "
                       "none filled.")
        if r.block_reason:
            st.caption(f"Swing plan held back on {r.block_bars}/{r.bars} bars by: "
                       + r.block_reason.replace("*", ""))


def _cards(results, show) -> None:
    ss = st.session_state
    keep = sorted((r for r in results if r.status in show),
                  key=lambda r: (r.day, r.ticker), reverse=True)
    if not keep:
        st.info("No days match this filter.")
        return
    limit = ss.setdefault("rs_cards_limit", _CARDS_PAGE)
    cols = st.columns(2)
    for n, r in enumerate(keep[:limit]):
        with cols[n % 2]:
            _card(r, key="cards")
    if len(keep) > limit:
        if st.button(f"Show more ({len(keep) - limit} left)"):
            ss.rs_cards_limit = limit + _CARDS_PAGE
            st.rerun()


def _table(results, show) -> None:
    rows = [{
        "day": r.day, "ticker": r.ticker, "status": f"{_STATUS[r.status][0]} {r.status}",
        "trades": len(r.trades), "W": r.wins, "L": r.losses, "net R": r.net_r,
        "net $": r.net_usd, "ideas": r.ideas, "signal bars": r.signal_bars,
        "first": r.first_signal, "bots": " ".join(_FAMILY[f] for f in r.families),
        "day %": r.change_pct, "range %": r.range_pct, "held back by": r.block_reason,
    } for r in results if r.status in show]
    if not rows:
        st.info("No days match this filter.")
        return
    df = pd.DataFrame(rows).sort_values(["day", "ticker"], ascending=[False, True])
    st.dataframe(ui_common.pnl_style(df, pct_cols=["day %"], usd_cols=["net $"],
                                     r_cols=["net R"]),
                 hide_index=True, use_container_width=True)
    st.download_button("⬇ CSV", df.to_csv(index=False).encode(),
                       file_name="replay-scan.csv", mime="text/csv")


def _summary(results) -> None:
    by = {s: [r for r in results if r.status == s] for s in _STATUS}
    c = st.columns(5)
    c[0].metric("Ticker-days", len(results))
    c[1].metric("🟢 Bots traded", len(by["traded"]))
    c[2].metric("🟡 Signal only", len(by["signal"]))
    c[3].metric("⚪ Quiet", len(by["quiet"]))
    net = sum(r.net_r for r in by["traded"])
    ideas = sum(r.ideas for r in by["traded"])
    c[4].metric("Bots' net R", f"{net:+.2f}R", f"{ideas} idea(s)", delta_color="off")


# --------------------------------------------------------------------------- #
# Page.
# --------------------------------------------------------------------------- #
def render(load) -> None:
    """``load(ticker) -> history`` — the replay page's cached history loader."""
    ss = st.session_state
    ss.setdefault("rs_tickers", ss.get("rp_ticker") or "AAPL")
    st.caption("Run every bot over a list of tickers and days, then skip straight "
               "to the days where something happened. Each ticker-day takes "
               f"~{_SECONDS_PER_DAY} s the first time; results are cached until "
               "the engine's code changes.")

    c1, c2 = st.columns([4, 1])
    text = c1.text_input("Tickers", key="rs_tickers", persist_state=_KEEP,
                         help="Comma- or space-separated, e.g. AMD, INTC, NVDA")
    c2.write("")
    c2.button("⭐ My watchlist", on_click=_use_watchlist, use_container_width=True)
    tickers = _parse_tickers(text)
    if not tickers:
        return

    with st.spinner("Loading intraday history…"):
        hists, failed = _histories(tickers, load)
    if failed:
        st.warning(f"Couldn't load: {', '.join(failed)}")
    tickers = [t for t in tickers if t in hists]
    days = sorted(set().union(*(replay.replayable_days(h) for h in hists.values()))
                  if hists else set())
    if not days:
        st.warning("No replayable sessions in the 5-minute window.")
        return

    cache = replay_scan.cache_path(_ROOT)
    marks: dict = {}
    every = replay_scan.plan(tickers, days, cache)
    rank = {"🟢": 2, "🟡": 1}
    for (_, d), r in every.cached.items():
        icon = _STATUS[r.status][0]
        if rank.get(icon, 0) > rank.get(marks.get(d, ""), 0):
            marks[d] = icon
        marks.setdefault(d, "")

    chosen = _pick_days(days, marks)
    if not chosen:
        st.info("Pick at least one day.")
        return
    todo = replay_scan.plan(tickers, chosen, cache)
    extra = ss.setdefault("rs_extra", {})     # this run's uncacheable rows
    left = [j for j in todo.to_compute if j not in extra]

    run_col, info_col = st.columns([1, 3])
    go_btn = run_col.button(f"🔎 Scan {len(left)} day(s)" if left else "✅ All scanned",
                            type="primary", disabled=not left, use_container_width=True)
    info_col.caption(f"{len(todo.jobs)} ticker-day(s) selected · "
                     f"{len(todo.jobs) - len(left)} already scanned"
                     + (f" · ~{max(1, round(len(left) * _SECONDS_PER_DAY / 60))} min "
                        "to scan the rest (you can stop any time — finished days "
                        "are kept)" if left else ""))

    if go_btn:
        bar = st.progress(0.0, text="Starting…")
        live = st.empty()
        found: list[str] = []
        sub = replay_scan.ScanPlan(jobs=left)

        def _tick(done, total, r):
            if r.error or not r.complete:
                extra[(r.ticker, r.date)] = r
            if r.status in ("traded", "signal"):
                found.append(f"{_STATUS[r.status][0]} {r.ticker} {r.date:%a %d %b}")
            bar.progress(done / total, text=f"{done}/{total} · {r.ticker} {r.day}")
            live.caption("Found: " + (" · ".join(found[-12:]) or "nothing yet"))

        replay_scan.run(sub, load, cache, progress=_tick)
        st.rerun()                 # redraw the button, counts and calendar marks

    results = [todo.cached.get(j) or extra.get(j) for j in todo.jobs]
    results = [r for r in results if r is not None]
    if not results:
        return

    st.divider()
    _summary(results)
    show_lbl = st.segmented_control("Show", list(_SHOW), default="🟢 Traded",
                                    required=True, key="rs_show",
                                    persist_state=_KEEP)
    show = _SHOW[show_lbl]
    t_grid, t_cards, t_table = st.tabs(["🗓 Grid", "🃏 Day cards", "📋 Table"])
    with t_grid:
        _grid(results, show, tickers)
    with t_cards:
        _cards(results, show)
    with t_table:
        _table(results, show)
