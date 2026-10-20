"""🎬 Replay — trade a past session bar by bar, with the future hidden.

Pick a historical trading day and the chart animates forward like a live tape:
09:45 → 15:55, one 5-minute bar at a time. At every step the plot holds only the
bars that had actually printed, so a setup has to be judged on what was knowable
*then*. Play, pause, step, scrub. Trade it yourself, let the bots trade it, or
both and compare at the close.

Everything decision-shaped lives in ``stockanalyzer.replay`` (headless and
tested); this module is presentation and Streamlit wiring only.

Two performance facts shape the whole page:
  • The day's decisions are precomputed once (~10 s), so stepping is a lookup.
  • Rebuilding the plotly figure costs ~0.25 s, which is the real frame budget —
    so faster playback advances MORE BARS PER TICK rather than ticking faster.
    (app.py's live mode learned this the hard way; see the note at _LIVE_FAST_S.)
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import replay_scan_page
import ui_common
from stockanalyzer import replay, virtualbook
from stockanalyzer.charting import candlestick_figure
from stockanalyzer.data.resample import available_intervals, resample_ohlcv
from stockanalyzer.analysis.engine import analyze_timeframe

_ROOT = Path(__file__).resolve().parent

# label → (seconds between ticks, bars advanced per tick). The period never drops
# below 0.5 s: a plotly rebuild takes ~0.25 s server-side, and a fragment whose
# body outlasts its own run_every queues up and makes the tape stutter.
_SPEEDS: dict[str, tuple[float, int]] = {
    "🐢 0.5×": (2.0, 1),
    "▶ 1×": (1.0, 1),
    "⏩ 2×": (0.5, 1),
    "⏩ 5×": (0.5, 3),
    "🚀 max": (0.5, 8),
}
_DEFAULT_SPEED = "▶ 1×"

_VISIBLE_BARS = 90        # width of the scrolling window
_LEAD_BARS = 12           # breathing room to the right of "now"

_BOT_LABELS = {"orb": "🚀 Gap-and-Go", "rev": "🔄 Reversal @ support",
               "swing": "📈 Swing plan"}


# --------------------------------------------------------------------------- #
# Data loading.
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False, ttl=60 * 30)
def _history(ticker: str) -> dict:
    """The ~60 days of intraday bars. Cached because it is the only network cost —
    but note only the RAW frames are cached: the precomputed session is far too
    big to round-trip through cache_data's pickle on every rerun."""
    return replay.load_history(ticker)


def _reset_session() -> None:
    """Drop everything tied to the loaded day (a session holds ~7 MB of frames)."""
    ss = st.session_state
    for k in ("rp_sess", "rp_i", "rp_applied", "rp_play", "rp_revealed",
              "rp_events", "rp_resample_memo", "rp_next_at"):
        ss.pop(k, None)


def _load_session(ticker: str, day: pd.Timestamp, hist: dict) -> None:
    ss = st.session_state
    bar = st.progress(0.0, text=f"Rebuilding {ticker} {day:%Y-%m-%d} bar by bar…")

    def _tick(done: int, total: int) -> None:
        bar.progress(done / max(total, 1),
                     text=f"Rebuilding the session as it happened… {done}/{total} bars")

    sess = replay.precompute_day(hist["i5"], hist["i30"], hist["daily"], day,
                                 hist["prev_close"].get(day), ticker=ticker,
                                 progress=_tick)
    bar.empty()
    if not sess.bars:
        st.error(f"No usable session bars for {ticker} on {day:%Y-%m-%d}.")
        return
    _reset_session()
    ss.rp_sess = sess
    ss.rp_i = 0
    ss.rp_applied = -1
    ss.rp_play = False
    ss.rp_revealed = False
    ss.rp_next_at = 0.0
    ss.rp_events = []
    ss.rp_attempt = replay.next_attempt(_ROOT, ticker, day)
    ss.rp_db = replay.replay_db_path(_ROOT, ticker, day, ss.rp_attempt)


def _setup_row() -> None:
    """Ticker + day picker. Outside the tape fragment — it changes rarely, and
    reloading history mid-playback would be nonsense anyway."""
    ss = st.session_state
    ss.setdefault("rp_ticker", ss.get("ticker") or "AAPL")

    c1, c2, c3 = st.columns([1.2, 1.6, 1])
    ticker = c1.text_input("Ticker", key="rp_ticker_in",
                           value=ss.rp_ticker).strip().upper()
    if not ticker:
        return

    try:
        with st.spinner(f"Loading {ticker} intraday history…"):
            hist = _history(ticker)
    except Exception as exc:
        st.error(f"Couldn't load {ticker}: {exc}")
        return

    days = replay.replayable_days(hist)
    if not days:
        st.warning("No replayable sessions in the 5-minute data window.")
        return

    labels = [f"{d:%a %Y-%m-%d}" for d in days]
    # A day opened from the multi-day scan: preselect it, then load it below.
    goto = ss.pop("rp_goto", None)
    if goto and (goto[0] != ticker or goto[1] not in days):
        goto = None
    loaded = ss.get("rp_sess")
    if goto:
        default = days.index(goto[1])
    elif loaded is not None and loaded.ticker == ticker and loaded.day in days:
        default = days.index(loaded.day)       # back from the scan: keep the tape's day
    else:
        default = len(labels) - 1
    pick = c2.selectbox("Trading day", labels, index=default,
                        key="rp_day_pick",
                        help="Only ~60 days of 5-minute bars exist at this "
                             "granularity. The earliest session is excluded — it "
                             "has no prior day for context.")
    day = days[labels.index(pick)]

    sess = ss.get("rp_sess")
    stale = (sess is None or sess.ticker != ticker or sess.day != day)
    c3.write("")
    if c3.button("🎬 Load day" if stale else "↻ Reload day",
                 type="primary" if stale else "secondary",
                 use_container_width=True) or (goto and stale):
        ss.rp_ticker = ticker
        _load_session(ticker, day, hist)
        st.rerun()

    if stale:
        st.info("Pick a day and press **Load day**. The session is rebuilt bar by "
                "bar first — about ten seconds — so playback afterwards is instant.")


# --------------------------------------------------------------------------- #
# Chart.
# --------------------------------------------------------------------------- #
def _report_at(sess, bar, minutes: int):
    """The report to draw: the as-of D1 frame, rolled up if the user picked a
    coarser candle. Memoized per (bar, interval) — see the note in app.py's
    `_resampled_report` on why this is a session-state memo and not cache_data."""
    if bar.d1 is None:
        return None
    native = available_intervals(bar.d1.df)
    if not native or minutes == native[0]:
        return bar.d1
    ss = st.session_state
    memo = ss.setdefault("rp_resample_memo", {})
    key = (bar.i, minutes)
    if key not in memo:
        try:
            memo[key] = analyze_timeframe(resample_ohlcv(bar.d1.df, minutes))
        except Exception:
            return bar.d1                      # a view option must never break the chart
        if len(memo) > 24:                     # keep only a small trailing window
            for old in list(memo)[:-24]:
                memo.pop(old, None)
    return memo[key]


def _focus(df, bar, minutes: int, keep_prices) -> tuple | None:
    """A fixed-width window ending just past ``bar`` — so the chart scrolls like a
    real tape instead of shrinking every candle as the day fills in.

    Deliberately *not* app.py's `_stable_focus`, which pins the viewport forever;
    that is the right answer for a live chart and the wrong one here.
    """
    step = pd.Timedelta(minutes=minutes)
    x1 = bar.t + _LEAD_BARS * step
    x0 = x1 - (_VISIBLE_BARS + _LEAD_BARS) * step
    win = df[(df.index >= x0) & (df.index <= bar.t)]
    if not len(win):
        return None
    lo, hi = float(win["low"].min()), float(win["high"].max())
    if hi <= lo:
        return None
    span = hi - lo
    for p in (keep_prices or []):
        if p and lo - 0.5 * span <= p <= hi + 0.5 * span:
            lo, hi = min(lo, p), max(hi, p)
    pad = (hi - lo) * 0.08
    return x0, x1, lo - pad, hi + pad


def _plan_lines(fig, rows, bar) -> None:
    """Entry / stop / target rails for every open position, plus its R:R band."""
    for row in rows:
        entry, stop, target = row.get("entry"), row.get("stop"), row.get("target")
        if not (entry and stop and target):
            continue
        fig.add_hrect(y0=entry, y1=target, line_width=0,
                      fillcolor="rgba(27,158,62,0.10)", row=1, col=1)
        fig.add_hrect(y0=stop, y1=entry, line_width=0,
                      fillcolor="rgba(229,57,53,0.10)", row=1, col=1)
        for price, color, tag in ((entry, "#1b9e3e", "entry"),
                                  (stop, "#e53935", "stop"),
                                  (target, "#1b9e3e", "target")):
            fig.add_hline(y=price, line_color=color, line_width=1,
                          line_dash="dot", row=1, col=1)
            fig.add_annotation(x=0, xref="x domain", y=price, yref="y",
                               text=f"{row['trader']} {tag} ${price:.2f}",
                               showarrow=False, xanchor="left", font=dict(size=9),
                               bgcolor="rgba(0,0,0,0.55)", font_color=color,
                               row=1, col=1)
    fig.add_hline(y=bar.close, line_color="#ffeb3b", line_width=1, row=1, col=1)
    fig.add_annotation(x=1, xref="x domain", y=bar.close, yref="y",
                       text=f"● {bar.clock} ${bar.close:.2f}", showarrow=False,
                       xanchor="right", font=dict(size=11, color="#ffeb3b"),
                       bgcolor="rgba(0,0,0,0.55)", row=1, col=1)


def _markers(fig, sess, upto: int) -> None:
    """Where the bots fired, and where you bought — strictly at or before ``upto``."""
    fired = [sess.bars[i] for i in sess.fired_bars if i <= upto]
    if fired:
        fig.add_trace(go.Scatter(
            x=[b.t for b in fired], y=[b.low * 0.998 for b in fired],
            mode="markers", marker=dict(symbol="triangle-up", size=11,
                                        color="#26c281"),
            name="bot signal", hovertext=[_fire_tip(b) for b in fired],
            hoverinfo="text", showlegend=False), row=1, col=1)


def _fire_tip(bar) -> str:
    parts = []
    if bar.bots.orb.fired:
        parts.append("Gap-and-Go")
    parts += [d.variant for d in bar.bots.fired_reversals]
    if bar.go:
        parts.append("Swing GO")
    return f"{bar.clock} — {', '.join(parts)}"


def _my_entries(fig, sess, rows) -> None:
    xs, ys = [], []
    for row in rows:
        ts = row.get("activated_ts") or row.get("opened_ts")
        if row.get("trader") != "me" or not ts:
            continue
        hit = [b for b in sess.bars if b.epoch == ts]
        if hit:
            xs.append(hit[0].t)
            ys.append(row["entry"])
    if xs:
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers",
                                 marker=dict(symbol="diamond", size=12,
                                             color="#4fc3f7"),
                                 name="my entry", showlegend=False), row=1, col=1)


def _chart(sess, bar, minutes: int, rows) -> None:
    rep = _report_at(sess, bar, minutes)
    if rep is None:
        st.info("Not enough history at this bar to draw a chart.")
        return
    fig = candlestick_figure(
        rep, title=f"{sess.ticker} — {sess.day:%a %d %b %Y} · {bar.clock} ET")
    # candlestick_figure keys uirevision off the title, which changes every bar —
    # that would reset the user's pan/zoom on every frame. Pin it to the *view*
    # instead, so the clock can animate while the viewport stays put.
    fig.update_layout(uirevision=f"replay|{sess.ticker}|{sess.day:%Y%m%d}|{minutes}")

    keep = [bar.close]
    for row in rows:
        keep += [row.get("entry"), row.get("stop"), row.get("target")]
    _plan_lines(fig, rows, bar)
    _markers(fig, sess, bar.i)
    _my_entries(fig, sess, rows)
    ui_common.apply_focus(fig, _focus(rep.df, bar, minutes, keep))
    st.plotly_chart(fig, use_container_width=True, config=ui_common.PLOTLY_CFG)


# --------------------------------------------------------------------------- #
# Transport.
# --------------------------------------------------------------------------- #
def _advance(sess, period: float, per_tick: int) -> None:
    """Move the tape on if it's playing and the tick is due.

    The due-time gate is a backlog guard, not the clock: if a frame overruns its
    run_every, Streamlit queues the next tick, and without this the queue would
    drain as a burst of double-advances.
    """
    ss = st.session_state
    now = time.time()
    if not ss.rp_play or now < ss.get("rp_next_at", 0.0):
        return
    ss.rp_next_at = now + period
    target = min(ss.rp_i + per_tick, sess.last)
    _apply_through(sess, target)
    ss.rp_i = target
    if target >= sess.last:
        ss.rp_play = False
        st.rerun(scope="app")          # stop the clock (run_every rebinds on a full run)


def _apply_through(sess, upto: int) -> None:
    """Run the book forward to ``upto``. Every bar's side effects run, even when
    playback skipped the frame — speed must never let a stop go unhit."""
    ss = st.session_state
    db = ss.rp_db
    for j in range(ss.rp_applied + 1, upto + 1):
        ss.rp_events = (ss.get("rp_events", []) +
                        replay.apply_bar(sess, j, ss.rp_bots, db))[-40:]
        ss.rp_applied = j


def _transport(sess, bar) -> None:
    ss = st.session_state
    c = st.columns([0.8, 0.8, 1.1, 0.8, 1.3, 1.1, 1.4])

    if c[0].button("⏮", help="Restart the day on a fresh book",
                   use_container_width=True):
        ss.rp_attempt = replay.next_attempt(_ROOT, sess.ticker, sess.day)
        ss.rp_db = replay.replay_db_path(_ROOT, sess.ticker, sess.day, ss.rp_attempt)
        ss.rp_i, ss.rp_applied, ss.rp_play = 0, -1, False
        ss.rp_events, ss.rp_revealed = [], False
        st.rerun(scope="app")

    if c[1].button("◀", help="Step back one bar (review only — trades stand)",
                   disabled=ss.rp_i <= 0, use_container_width=True):
        ss.rp_i -= 1
        ss.rp_play = False
        st.rerun(scope="app")

    playing = ss.rp_play
    if c[2].button("⏸ Pause" if playing else "▶ Play", type="primary",
                   disabled=ss.rp_i >= sess.last and not playing,
                   use_container_width=True):
        ss.rp_play = not playing
        ss.rp_next_at = 0.0
        st.rerun(scope="app")          # run_every only rebinds on a full script run

    if c[3].button("▶|", help="Step forward one bar",
                   disabled=ss.rp_i >= sess.last, use_container_width=True):
        _apply_through(sess, ss.rp_i + 1)
        ss.rp_i += 1

    speed = c[4].selectbox("Speed", list(_SPEEDS), key="rp_speed_pick",
                           index=list(_SPEEDS).index(ss.rp_speed),
                           label_visibility="collapsed")
    if speed != ss.rp_speed:
        ss.rp_speed = speed
        st.rerun(scope="app")

    intervals = available_intervals(bar.d1.df) if bar.d1 is not None else [5]
    ivl = c[5].selectbox("Candle", intervals, key="rp_ivl_pick",
                         index=intervals.index(ss.rp_ivl) if ss.rp_ivl in intervals else 0,
                         format_func=lambda m: f"{m}m", label_visibility="collapsed")
    ss.rp_ivl = ivl

    c[6].metric("Session clock", f"{bar.clock} ET",
                f"{bar.close - (sess.prev_close or bar.close):+.2f} vs prev close",
                label_visibility="collapsed")

    # The slider mirrors rp_i, which is the truth. Assigning the widget's key is
    # only legal *before* the widget exists on this run — same rule as app.py's
    # cross-page ticker handoff.
    if ss.get("rp_scrub") != ss.rp_i:
        ss["rp_scrub"] = ss.rp_i
    picked = st.slider("Bar", 0, sess.last, key="rp_scrub",
                       format="", label_visibility="collapsed")
    if picked != ss.rp_i:
        ss.rp_i = picked
        ss.rp_play = False
        if picked > ss.rp_applied:
            _apply_through(sess, picked)
        st.rerun(scope="app")


# --------------------------------------------------------------------------- #
# Bot panel.
# --------------------------------------------------------------------------- #
def _chip(ok: bool, label: str) -> str:
    return f"{'✅' if ok else '❌'} {label}"


def _bot_panel(bar, live: bool = False) -> None:
    ss = st.session_state
    st.markdown("##### 🤖 What the bots see right now")
    st.caption("Live — what the bots see on this refresh, including the gate "
               "that's holding each one back." if live else
               "Every bar, every bot — including the gate that's holding it back. "
               "Toggle a bot on to let it trade this replay alongside you.")

    g = bar.bots.orb
    with st.container(border=True):
        head, tog = st.columns([4, 1])
        head.markdown(f"**{_BOT_LABELS['orb']}** — "
                      + ("🟢 **FIRED**" if g.fired else f"⚪ {g.reason}"))
        if not live:
            ss.rp_bots["orb"] = tog.toggle("auto", value=ss.rp_bots["orb"],
                                           key="rp_bot_orb", label_visibility="collapsed")
        head.caption(" · ".join([
            _chip(g.gap_up, "gap-up ≥2%"), _chip(g.in_window, "09:45–11:30"),
            _chip(g.broke_or_high, "broke OR-high"),
            _chip(g.vol_ok, f"volume {g.rvol:.1f}×" if g.rvol else "volume"),
        ]))

    fired_rev = bar.bots.fired_reversals
    with st.container(border=True):
        head, tog = st.columns([4, 1])
        head.markdown(f"**{_BOT_LABELS['rev']}** — " + (
            "🟢 **FIRED**: " + ", ".join(d.variant for d in fired_rev)
            if fired_rev else f"⚪ {bar.bots.reversals[0].reason}"))
        if not live:
            ss.rp_bots["rev"] = tog.toggle("auto", value=ss.rp_bots["rev"],
                                           key="rp_bot_rev", label_visibility="collapsed")
        with head.expander("all four variants"):
            for d in bar.bots.reversals:
                st.markdown(f"`{d.variant}` {'🟢' if d.fired else '⚪'} {d.reason}")
                st.caption(" · ".join([
                    _chip(d.at_support, f"at support (conf {d.support_conf})"),
                    _chip(d.trend_ok, "trend ok"), _chip(d.reversed_up, "turned up"),
                    _chip(d.aim_ok, "aim ≥2.5%")]))

    armed = replay.armed_swing_bots(bar)
    with st.container(border=True):
        head, tog = st.columns([4, 1])
        head.markdown(f"**{_BOT_LABELS['swing']}** — " + (
            "🟢 **" + ", ".join(armed) + "**" if armed
            else f"⚪ {bar.bots.swing_block}"))
        if not live:
            ss.rp_bots["swing"] = tog.toggle("auto", value=ss.rp_bots["swing"],
                                             key="rp_bot_swing",
                                             label_visibility="collapsed")
        head.caption(f"{bar.setup} · {bar.kind} · score {bar.score}% "
                     f"({bar.earned}/{bar.denom} pts) · R:R {bar.rr:.1f}"
                     + (f" · trigger \\${bar.trigger:.2f}" if bar.trigger else ""))
        # Each bot's own rule, so a blocked family still shows which member came
        # closest — the whole point of watching a replay rather than a result.
        head.caption(" · ".join(
            f"{'🟢' if name in armed else '⚪'} {name}"
            for name, _ in replay.SWING_BOTS))
        if not bar.actionable or bar.extended:
            head.caption("⛔ watch-only: the plan is "
                         + ("not actionable" if not bar.actionable
                            else "extended / distribution risk")
                         + " — no swing bot may enter.")
        if bar.lost:
            with head.expander("points lost"):
                for name, w in bar.lost[:6]:
                    st.markdown(f"−{w} &nbsp; {name}", unsafe_allow_html=True)

    if bar.bots.contradiction:
        st.warning(f"⚠️ {bar.bots.contradiction}", icon="⚠️")


# --------------------------------------------------------------------------- #
# Manual trading.
# --------------------------------------------------------------------------- #
def _sources(bar) -> dict:
    """Level presets the Buy panel can pull from, keyed by label."""
    out = {"Swing plan": (bar.entry, bar.stop, bar.target)}
    g = bar.bots.orb
    if g.entry:
        out["Gap-and-Go"] = (g.entry, g.stop, g.target)
    for d in bar.bots.fired_reversals:
        out[f"Reversal ({d.variant})"] = (d.entry, d.stop, d.target)
    out["Freehand"] = (bar.close, round(bar.close * 0.99, 2),
                       round(bar.close * 1.02, 2))
    return out


def _trade_panel(sess, bar, rows, reviewing: bool) -> None:
    ss = st.session_state
    st.markdown("##### 📒 Your book")

    if reviewing:
        st.info(f"🔍 Reviewing **{bar.clock}** — the book has already run to "
                f"**{sess.bars[ss.rp_applied].clock}**. Catch up (or ⏮ restart) "
                f"to trade. Stepping back rewinds the *view*, never the trades.")
    else:
        _buy_form(sess, bar, rows)

    mine = [r for r in rows if r.get("status") in ("open", "pending")]
    if not mine:
        st.caption("No open positions.")
    for row in mine:
        _position_card(sess, bar, row)


def _buy_form(sess, bar, rows) -> None:
    ss = st.session_state
    if any(r.get("trader") == "me" and r.get("status") in ("open", "pending")
           for r in rows):
        st.success("You're already in this name — manage the position below.")
        return

    presets = _sources(bar)
    # Widget keys must NOT contain the bar index, or every field would reset on
    # each tick. The trade-off is that `value=` stops tracking the plan, so the
    # pull button below writes the state keys explicitly instead.
    k = f"{sess.ticker}_{sess.day:%Y%m%d}"
    ss.setdefault(f"rp_entry_{k}", float(bar.close))
    ss.setdefault(f"rp_stop_{k}", round(float(bar.close) * 0.99, 2))
    ss.setdefault(f"rp_tgt_{k}", round(float(bar.close) * 1.02, 2))
    ss.setdefault(f"rp_amt_{k}", 1000.0)

    src = st.selectbox("Levels from", list(presets), key=f"rp_src_{k}")
    if st.button("↺ Pull levels from this setup", use_container_width=True):
        entry, stop, target = presets[src]
        ss[f"rp_entry_{k}"] = round(float(entry), 2)
        ss[f"rp_stop_{k}"] = round(float(stop), 2)
        ss[f"rp_tgt_{k}"] = round(float(target), 2)
        st.rerun(scope="fragment")

    c1, c2, c3 = st.columns(3)
    entry = c1.number_input("Entry", key=f"rp_entry_{k}", step=0.01, format="%.2f")
    stop = c2.number_input("Stop", key=f"rp_stop_{k}", step=0.01, format="%.2f")
    target = c3.number_input("Target", key=f"rp_tgt_{k}", step=0.01, format="%.2f")
    amount = st.number_input("Stake ($)", key=f"rp_amt_{k}", min_value=10.0,
                             step=100.0)

    rr = (target - entry) / (entry - stop) if entry > stop else 0.0
    risk = (entry - stop) / entry * 100 if entry else 0.0
    st.caption(f"Risking **{risk:.1f}%** for **{rr:.1f}R**. "
               f"Fill is this bar's close (${bar.close:.2f}).")

    if st.button(f"📒 Buy at {bar.clock}", type="primary", use_container_width=True,
                 disabled=not (entry > stop and target > entry)):
        virtualbook.open_position(
            ticker=sess.ticker, trader="me", entry=entry, stop=stop, target=target,
            kind="immediate", horizon_days=1, stake=amount, init_stop=stop,
            snapshot={**bar.snapshot, "manual_levels": True, "replay_source": src},
            now=bar.epoch, path=ss.rp_db)
        st.toast(f"Bought {sess.ticker} at {bar.clock} — ${entry:.2f}", icon="📒")
        st.rerun(scope="fragment")


def _position_card(sess, bar, row) -> None:
    ss = st.session_state
    entry = float(row["entry"])
    init = float(row.get("init_stop") or row["stop"])
    unreal_r = (bar.close - entry) / (entry - init) if entry > init else 0.0
    unreal_pct = (bar.close / entry - 1) * 100
    mfe, mae = replay.excursions(sess, row, ss.rp_i)

    with st.container(border=True):
        head, act = st.columns([4, 1])
        # Escape the dollars: Streamlit's markdown reads a $…$ pair as LaTeX, so
        # three prices in one line would render as maths.
        head.markdown(f"**{row['trader']}** · {row['status']} · entry "
                      f"\\${entry:.2f} → stop \\${row['stop']:.2f} / target "
                      f"\\${row['target']:.2f}")
        head.caption(f"{unreal_r:+.2f}R ({unreal_pct:+.2f}%) · "
                     f"MFE {mfe:+.1f}% / MAE {mae:+.1f}% · "
                     f"opened {_bar_clock(sess, row)}")
        if row["trader"] == "me" and act.button(
                "Close", key=f"rp_close_{row['id']}", use_container_width=True):
            virtualbook.close_position(row["id"], bar.close, reason="manual",
                                       now=bar.epoch, path=ss.rp_db)
            st.rerun(scope="fragment")


def _bar_clock(sess, row) -> str:
    """Render the ET session time from the bar epoch.

    Never show ``row["opened"]`` — the store formats that with the *machine's*
    timezone, which is meaningless for a replayed Eastern session.
    """
    ts = row.get("activated_ts") or row.get("opened_ts")
    hit = [b for b in sess.bars if b.epoch == ts]
    return hit[0].clock if hit else "—"


# --------------------------------------------------------------------------- #
# End-of-session review.
# --------------------------------------------------------------------------- #
def _review(sess) -> None:
    """The reveal. Lookahead is finally allowed — this is the only place that
    reads the full session frame."""
    ss = st.session_state
    st.divider()
    st.markdown("### 🔓 The day, revealed")

    closed = replay.force_flat(sess, ss.rp_db)
    if closed:
        st.caption(f"Flattened {len(closed)} position(s) at the close — a day "
                   "trade doesn't hold overnight.")

    card = replay.scorecard(sess, ss.rp_db)
    last = sess.bars[-1]
    fig = candlestick_figure(last.d1, title=f"{sess.ticker} — {sess.day:%d %b %Y} full session")
    fig.update_layout(uirevision=f"replay-review|{sess.ticker}|{sess.day:%Y%m%d}")
    for row in card["book"]:
        _trade_overlay(fig, sess, row)
    ui_common.apply_focus(fig, _focus(last.d1.df, last, ss.rp_ivl,
                                      [r.get("entry") for r in card["book"]]))
    st.plotly_chart(fig, use_container_width=True, config=ui_common.PLOTLY_CFG)

    _scorecard_tables(sess, card)
    _missed(sess)

    reason, n, total = replay.blocked_digest(sess)
    if reason:
        st.info(f"**What kept the swing plan out:** {reason} — on {n}/{total} bars.")


def _trade_overlay(fig, sess, row) -> None:
    a = row.get("activated_ts") or row.get("opened_ts")
    b = row.get("closed_ts")
    xa = next((x.t for x in sess.bars if x.epoch == a), None)
    xb = next((x.t for x in sess.bars if x.epoch == b), sess.bars[-1].t)
    if xa is None:
        return
    win = float(row.get("pnl_pct") or 0) > 0
    color = "#1b9e3e" if win else "#e53935"
    fig.add_trace(go.Scatter(
        x=[xa, xb], y=[row["entry"], row.get("exit_price") or row["entry"]],
        mode="lines+markers", line=dict(color=color, width=2),
        marker=dict(size=9, symbol=["triangle-up", "x"]),
        name=row["trader"], hovertext=f"{row['trader']} {row.get('close_reason','')} "
                                      f"{row.get('pnl_pct', 0):+.2f}%",
        hoverinfo="text", showlegend=False), row=1, col=1)


def _scorecard_tables(sess, card) -> None:
    rows = []
    for r in card["book"]:
        rows.append({
            "trader": r["trader"], "setup": (r.get("snapshot") or {}).get("setup", "?"),
            "in": _bar_clock(sess, r), "entry": r["entry"],
            "exit": r.get("exit_price"), "why": r.get("close_reason") or r["status"],
            "R": replay.trade_r(r), "P&L %": r.get("pnl_pct"),
            "P&L $": r.get("pnl_usd"),
        })
    if not rows:
        st.info("Nobody traded this session — neither you nor the bots.")
        return
    st.markdown("#### Every trade")
    st.dataframe(ui_common.pnl_style(pd.DataFrame(rows), pct_cols=["P&L %"],
                                     usd_cols=["P&L $"], r_cols=["R"],
                                     price_cols=["entry", "exit"]),
                 use_container_width=True, hide_index=True)

    st.markdown("#### You vs the bots")
    traders = card["stats"].get("traders") or {}
    order = [t for t in (["me"] + sorted(traders)) if t in traders]
    order = list(dict.fromkeys(order))                  # "me" first, then the bots
    cols = st.columns(max(1, min(len(order), 5)))
    for i, name in enumerate(order):
        s = traders[name]
        cols[i % len(cols)].metric(
            name, f"${s['total_pnl_usd']:+,.2f}",
            f"{s['wins']}/{s['n']} wins · {s['avg_pnl_pct'] or 0:+.2f}% avg")

    ideas = card["ideas"].get("ideas") or []
    st.caption(f"{len(ideas)} distinct **idea(s)** behind those rows — several "
               "bots acting on one plan is one idea, not several. That's the "
               "number to judge the algorithm on.")


def _missed(sess) -> None:
    missed = replay.missed_signals(sess, st.session_state.rp_db)
    if not missed:
        return
    st.markdown("#### Signals you passed on")
    st.caption("What standing aside actually cost — judged stop-first over the "
               "rest of the session.")
    for m in missed:
        icon = {"target": "🎯", "stop": "🛑", "eod": "⏹"}.get(m["status"], "•")
        st.markdown(f"{icon} **{m['name']}** at {m['t']} — entry \\${m['entry']:.2f} "
                    f"→ **{m['status']}, {m['r']:+.1f}R**")


# --------------------------------------------------------------------------- #
# Page.
# --------------------------------------------------------------------------- #
def _tape_body(sess, period: float, per_tick: int) -> None:
    ss = st.session_state
    _advance(sess, period, per_tick)
    bar = sess.bar_at(ss.rp_i)
    reviewing = ss.rp_i < ss.rp_applied

    _transport(sess, bar)
    rows = virtualbook.load(path=ss.rp_db)
    rows = [r for r in rows if (r.get("ticker") or "").upper() == sess.ticker]

    left, right = st.columns([2.4, 1])
    with left:
        _chart(sess, bar, ss.rp_ivl, [r for r in rows
                                      if r.get("status") in ("open", "pending")])
    with right:
        _bot_panel(bar)
        _trade_panel(sess, bar, rows, reviewing)

    if ss.rp_i >= sess.last:
        ss.rp_revealed = True
    if ss.rp_revealed:
        _review(sess)
    elif not ss.rp_play:
        if st.button("🔓 Reveal the day now (ends the session)"):
            _apply_through(sess, sess.last)
            ss.rp_i = sess.last
            ss.rp_revealed = True
            st.rerun(scope="app")


def render() -> None:
    ss = st.session_state
    ss.setdefault("rp_speed", _DEFAULT_SPEED)
    ss.setdefault("rp_ivl", 5)
    ss.setdefault("rp_bots", {"orb": False, "rev": False, "swing": False})
    ss.setdefault("rp_attempt", 0)
    ss.setdefault("rp_revealed", False)
    ss.setdefault("rp_next_at", 0.0)

    st.subheader("🎬 Replay — trade a past day with the future hidden")
    # Seeded here, not via default= — the scan's "Replay" buttons set it too.
    ss.setdefault("rp_mode", replay_scan_page.SINGLE)
    mode = st.segmented_control("Mode", [replay_scan_page.SINGLE, replay_scan_page.SCAN],
                                required=True, key="rp_mode",
                                label_visibility="collapsed")
    if mode == replay_scan_page.SCAN:
        ss.rp_play = False                 # the tape fragment isn't drawn here
        replay_scan_page.render(_history)
        return

    st.caption("The chart only ever holds the bars that had actually printed. "
               "Practice trades go to their own book — your real trade record is "
               "never touched.")
    _setup_row()

    sess = ss.get("rp_sess")
    if sess is None:
        return

    period, per_tick = _SPEEDS[ss.rp_speed]

    # `run_every` binds when the fragment is DECLARED, so play/pause and speed
    # changes trigger a full rerun to rebind it. Everything that changes per bar
    # lives inside the fragment — a widget outside it would freeze mid-playback.
    @st.fragment(run_every=(f"{period}s" if ss.rp_play else None))
    def _tape():
        with ui_common.Timed("replay · tape", period):
            _tape_body(sess, period, per_tick)

    _tape()

    if ss.get("show_perf"):
        with st.expander("⏱ Frame timings"):
            ui_common.perf_panel()
