"""Stock Graph Analyzer — non-expert dashboard.

Enter a ticker (or pick a followed one) → fetch OHLCV across 1D…5Y → run the
technical-analysis engine → show a big price header, a plain-English recommendation
with a conviction gauge + go/no-go traffic light tailored to what you want to do
(Buy / Sell / Own), practical "what could happen next" scenarios, and the charts.

All explanations are rule-based (no AI). Educational only — not financial advice.

Run:  streamlit run app.py
"""
from __future__ import annotations

import locale
import os
import threading
import time

import plotly.graph_objects as go
from dotenv import load_dotenv

import streamlit as st

from stockanalyzer import papertrade, swingwatch, virtualbook, watchlist
from stockanalyzer.analysis.engine import CATEGORY_WEIGHTS, analyze_timeframe
from stockanalyzer.explain.swing import build_swing_plan
from stockanalyzer.analysis.signals import Direction
from stockanalyzer.charting import candlestick_figure
from stockanalyzer.data.realtime import RealtimeStream, summarize, ticks_to_candles
from stockanalyzer.data.schema import Timeframe
from stockanalyzer.explain import UseCase, build_recommendation, timeframe_caption
from stockanalyzer.explain.glossary import explain_signal
from stockanalyzer.live import assess, make_tick
from stockanalyzer.live_events import LiveState, diff_states
from stockanalyzer.pipeline import analyze_ticker
from stockanalyzer.strategy import Strategy, SwingPace
from stockanalyzer.verdict.aggregate import build_verdict
# NOTE: stockanalyzer.vision imports OpenCV (cv2). It is imported lazily inside
# _render_image_mode so a cv2 load failure on a server can never blank the app.

load_dotenv()

try:
    locale.setlocale(locale.LC_TIME, "")
except locale.Error:
    pass


def _fmt_ts(ts) -> str:
    """Format a Unix timestamp (or formatted string) to the user's locale."""
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    try:
        return time.strftime("%x %X", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return str(ts)


def _bridge_cloud_secrets() -> None:
    """On Streamlit Cloud, keys live in st.secrets — mirror them into os.environ
    so the data clients (which read env vars) pick them up. Harmless locally."""
    try:
        for k in ("FINNHUB_KEY", "TWELVEDATA_KEY"):
            v = st.secrets.get(k)
            if v and not os.environ.get(k):
                os.environ[k] = str(v)
    except Exception:
        pass


_bridge_cloud_secrets()
st.set_page_config(page_title="Stock Graph Analyzer", layout="wide")

_DIR_EMOJI = {Direction.BULL: "🟢", Direction.BEAR: "🔴", Direction.NEUTRAL: "⚪"}
# Pan on left-drag (dragmode set per-figure), wheel zooms, double-click resets +
# autoscales. Reset/autoscale buttons stay in the modebar for one-click clearing.
_PLOTLY_CFG = {
    "scrollZoom": True,
    "displayModeBar": True,
    "displaylogo": False,
    "doubleClick": "reset+autosize",
    "modeBarButtonsToAdd": ["resetScale2d"],
}
_SEED_TICKERS = ["AAPL", "MSFT", "NVDA"]
_USECASE_BY_LABEL = {uc.label: uc for uc in UseCase}
_STRATEGY_BY_LABEL = {s.label: s for s in Strategy}
_PACE_BY_LABEL = {p.label: p for p in SwingPace}


# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False, ttl=60 * 20)
def _run(ticker: str, prefer: str | None):
    return analyze_ticker(ticker, prefer=prefer)


def _badge(direction: Direction) -> str:
    return f"{_DIR_EMOJI[direction]} {direction.value}"


def main() -> None:
    ss = st.session_state
    ss.setdefault("ticker", "MSFT")
    ss.setdefault("submitted", False)

    st.title("📈 Stock Graph Analyzer")
    st.caption("Plain-English technical analysis (rules from John Murphy's *Technical "
               "Analysis of the Financial Markets*). Rule-based, no AI. **Not financial advice.**")

    mode, prefer, usecase, strategy, pace, uploaded, live_on, buy_price = _sidebar()

    flash = ss.pop("vb_flash", None)
    if flash:
        st.success(flash + " — see the **💼 Virtual portfolio** panel below.")

    # Quiet swing radar — always visible while tickers are tracked.
    tracked = swingwatch.load()
    if tracked:
        _radar_panel(tracked)
    _portfolio_panel()

    if mode == "Image fallback":
        if uploaded is None:
            st.info("Upload a chart or info screenshot in the sidebar, then press Analyze.")
        else:
            _render_image_mode(uploaded)
        return

    if not ss.submitted or not ss.ticker:
        st.info("Pick a followed ticker or type one in the sidebar, then press **Analyze**.")
        return

    ticker = ss.ticker
    with st.spinner(f"Analyzing {ticker}…"):
        result = _run(ticker, None if prefer == "auto" else prefer)

    if not result.reports:
        st.error(f"Couldn't find data for '{ticker}'. Check the symbol. ({result.errors})")
        return

    if prefer == "twelvedata" and result.provider != "twelvedata":
        st.info("ℹ️ TWELVEDATA_KEY isn't set — using free yfinance data instead. "
                "(Add the key to .env to use Twelve Data.)")

    # Live mode takes over the whole page with a 1-second real-time dashboard.
    if live_on and RealtimeStream(ticker).available:
        _live_dashboard(ticker, prefer, usecase, strategy, pace, buy_price, result)
        return

    _price_header(result)
    _extended_alert(result)
    # Recompute the verdict for the chosen strategy (swing weights short timeframes).
    sent = result.sentiment.score if (result.sentiment and result.sentiment.available) else None
    verdict = build_verdict(result.reports, sent, strategy, pace)
    rec = build_recommendation(ticker, verdict, result.reports, usecase, strategy, pace,
                               context=_reco_context(result))
    _recommendation_section(result, rec, usecase)
    current_price = result.quote.price if result.quote else None
    _next_steps(rec, buy_price=buy_price, current_price=current_price, usecase=usecase,
                reports=result.reports, verdict=verdict)

    for n in result.notices:
        st.caption(f"ℹ️ {n}")

    if live_on:   # requested but no FINNHUB_KEY / websocket → 60s polling fallback
        _live_section(ticker, prefer, usecase, rec, result)

    _render_company(result)
    _signals_section(result)
    _render_tabs(ticker, result)
    st.divider()
    st.caption("⚠️ Scenarios are rule-based projections from support/resistance levels — "
               "not predictions. This tool is educational and **not financial advice.**")


# --------------------------------------------------------------------------- #
def _sidebar():
    ss = st.session_state
    with st.sidebar:
        st.header("What do you want to do?")
        usecase = _USECASE_BY_LABEL[st.radio(
            "Your situation", [uc.label for uc in UseCase], key="usecase_label",
            label_visibility="collapsed")]

        strategy = _STRATEGY_BY_LABEL[st.radio(
            "Strategy", [s.label for s in Strategy], key="strategy_label",
            help="Investor = long-term, breakout-confirmation guidance. "
                 "Swing = enter near reversals with a stop & target at reward:risk ≥ 2:1.")]

        pace = SwingPace.STANDARD
        if strategy == Strategy.SWING:
            pace = _PACE_BY_LABEL[st.radio(
                "Swing pace", [p.label for p in SwingPace], key="pace_label",
                horizontal=True,
                help="Fast = 1–3 day moves on the 5D/1D charts, tighter stops & targets. "
                     "Standard = days-to-2-weeks on the 1-month chart.")]

        st.divider()
        st.subheader("Stock")

        # Quick-pick chips for followed tickers, shown ABOVE the input.
        followed = watchlist.load()
        if followed:
            st.caption("⭐ Following — tap to analyze:")
            cols = st.columns(min(4, len(followed)))
            for i, sym in enumerate(followed):
                if cols[i % len(cols)].button(sym, key=f"chip_{sym}", use_container_width=True):
                    ss.ticker = sym
                    ss.submitted = True
                    st.rerun()
        else:
            st.caption(f"No followed tickers yet. Try: {', '.join(_SEED_TICKERS)}")

        st.text_input("Ticker symbol", key="ticker", help="e.g. AAPL, MSFT, NVDA")
        c1, c2 = st.columns([1, 1])
        if c1.button("🔎 Analyze", type="primary", use_container_width=True):
            ss.submitted = True
            st.rerun()
        followed_now = watchlist.is_followed(ss.ticker)
        if c2.button("★ Unfollow" if followed_now else "☆ Follow", use_container_width=True):
            watchlist.toggle(ss.ticker)
            st.rerun()

        # --- Swing radar: quiet tracking + escalating alerts ---
        st.divider()
        st.subheader("📡 Swing radar")
        st.caption("Track tickers quietly for **daily (fast) swings** — you get a toast "
                   "when the swing score climbs past **60% → 70% → 80%**.")
        tracked_now = swingwatch.load()
        r1c, r2c = st.columns([2, 1])
        radar_add = r1c.text_input("Add to radar", key="radar_add",
                                   placeholder="e.g. NVDA", label_visibility="collapsed")
        if r2c.button("➕ Track", use_container_width=True) and radar_add.strip():
            swingwatch.add(radar_add)
            st.rerun()
        if tracked_now:
            cols = st.columns(min(3, len(tracked_now)))
            for i, sym in enumerate(tracked_now):
                if cols[i % len(cols)].button(f"✕ {sym}", key=f"radar_rm_{sym}",
                                              use_container_width=True,
                                              help=f"Stop tracking {sym}"):
                    swingwatch.remove(sym)
                    st.rerun()

        # Buy-price input — only shown when "I Own" is selected.
        buy_price: float | None = None
        if usecase == UseCase.OWN:
            bp = st.number_input(
                "📌 Your buy price (per share)",
                min_value=0.0, value=0.0, step=0.01, format="%.2f",
                key="buy_price",
                help="What you paid — the app adjusts stop/target advice to show your "
                     "real P&L (profit locked / still at loss / risk-free zone).")
            buy_price = bp if bp > 0.0 else None

        st.divider()
        st.subheader("Options")
        mode = st.radio("Source", ["Ticker (API)", "Image fallback"],
                        help="API is exact. Image-reading a screenshot is approximate.")
        prefer = st.selectbox("Data provider", ["auto", "yfinance", "twelvedata"],
                              help="auto uses Twelve Data if TWELVEDATA_KEY is set, else "
                                   "yfinance. Pre-market/after-hours data comes from "
                                   "yfinance (free); Twelve Data needs a paid plan for it.")
        live_on = st.toggle("🔴 Live mode (real-time)", value=True,
                            help="Turns the page into a real-time dashboard: tick-by-tick "
                                 "price, plan/stop/target & P&L recomputed every second, "
                                 "signals re-run ~45s, with flip alerts. Needs FINNHUB_KEY. "
                                 "Turn off for the full static multi-timeframe view.")
        uploaded = None
        if mode == "Image fallback":
            uploaded = st.file_uploader("Screenshot", type=["png", "jpg", "jpeg"])
    return mode, prefer, usecase, strategy, pace, uploaded, live_on, buy_price


# --------------------------------------------------------------------------- #
# Virtual paper-trading book.
# --------------------------------------------------------------------------- #
def _plan_snapshot(plan, reports=None, verdict=None, rec=None) -> dict:
    """Full decision context stored with every virtual trade — signals,
    indicators, verdict, swing checks, recommendation — for post-hoc analysis."""
    snap = dict(
        score=plan.score, label=plan.score_label, setup=plan.setup,
        kind=plan.kind, rr=plan.rr, daily_atr_pct=plan.daily_atr_pct,
        guidance=plan.guidance,
        failed_checks=[c.name for c in plan.checks if c.weight and not c.ok and not c.na],
        checks=[dict(name=c.name, ok=c.ok, na=c.na, detail=c.detail, weight=c.weight)
                for c in plan.checks],
        reasons=list(plan.reasons),
        bias=plan.bias.value,
        confidence=getattr(plan, "confidence", None),
        entry_note=plan.entry_note,
        atr_source=plan.atr_source,
        fast_mover=plan.fast_mover,
    )
    if reports:
        tf_data = {}
        for tf, rep in reports.items():
            tf_key = tf.value if hasattr(tf, "value") else str(tf)
            sigs = [dict(name=s.name, direction=s.direction.value,
                         strength=round(s.strength, 3), category=s.category,
                         evidence=s.evidence)
                    for s in rep.signals]
            indicators = {}
            for col in ("close", "sma20", "sma50", "sma200", "ema20",
                        "rsi", "macd", "macd_signal", "macd_hist",
                        "stoch_k", "stoch_d", "atr"):
                if col in rep.df.columns:
                    series = rep.df[col].dropna()
                    if not series.empty:
                        indicators[col] = round(float(series.iloc[-1]), 4)
            tf_data[tf_key] = dict(signals=sigs, bias_score=rep.bias_score,
                                   trend_dir=rep.trend.direction.value,
                                   indicators=indicators)
        snap["timeframes"] = tf_data
    if verdict:
        snap["verdict"] = dict(
            label=verdict.label, direction=verdict.direction.value,
            score=verdict.score, confidence=verdict.confidence,
            per_timeframe=verdict.per_timeframe)
    if rec:
        snap["recommendation"] = dict(
            go_score=rec.go_score, light_color=rec.light_color,
            preset=rec.preset.label if hasattr(rec.preset, "label") else str(rec.preset),
            bullish_pct=rec.bullish_pct)
    return snap


def _quiet_price(tk: str) -> float | None:
    try:
        res = _run_quiet(tk)
        if res.quote:
            return float(res.quote.price)
        for tf in (Timeframe.D1, Timeframe.D5, Timeframe.M1):
            rep = res.reports.get(tf)
            if rep is not None:
                return float(rep.meta.get("last_close", 0)) or None
    except Exception:
        pass
    return None


def _virtual_buy_button(plan, ticker: str, key_suffix: str = "",
                        reports=None, verdict=None, rec=None) -> None:
    """The 'simulate this trade' button — with optional manual stop/target override."""
    if plan.kind == "no_trade" or not ticker:
        return
    pending = plan.kind == "breakout_wait"
    k = f"{ticker}_{key_suffix}"

    if virtualbook.has_open(ticker, "me"):
        st.success(f"💼 You already hold a virtual position in {ticker} — see the "
                   "**Virtual portfolio** panel at the top to manage it.")
        return

    amount = st.number_input(
        "Amount to simulate ($)", min_value=10.0, value=1000.0, step=100.0,
        format="%.0f", key=f"vb_amt_{k}",
        help="Your simulated stake — P&L is computed in these dollars so you can "
             "track real ratios and trend over time.")
    with st.expander("✎ Adjust stop / target (optional)"):
        c1, c2, c3 = st.columns(3)
        entry = c1.number_input("Entry / trigger", value=float(plan.entry),
                                step=0.01, format="%.2f", key=f"vb_entry_{k}")
        stop = c2.number_input("Stop", value=float(plan.stop), step=0.01,
                               format="%.2f", key=f"vb_stop_{k}")
        target = c3.number_input("Target", value=float(plan.target1), step=0.01,
                                 format="%.2f", key=f"vb_tgt_{k}")
        if stop < entry < target:
            risk = (entry - stop) / entry * 100
            rew = (target - entry) / entry * 100
            rr = rew / risk if risk else 0
            st.caption(f"Aim **{rew:+.1f}%** (${amount * rew / 100:+,.0f}) · "
                       f"risk **{-risk:.1f}%** (${-amount * risk / 100:,.0f}) · "
                       f"R:R **{rr:.1f}:1**")
        else:
            st.caption("⚠️ Need stop < entry < target for a long trade.")

    label = ("📒 Arm virtual breakout order" if pending
             else "📒 Virtual BUY — simulate this trade")
    if st.button(label, key=f"vbuy_{k}", use_container_width=True, type="primary"):
        if not (stop < entry < target):
            st.warning("Set stop < entry < target first.")
            return
        manual = (abs(stop - plan.stop) > 1e-6 or abs(target - plan.target1) > 1e-6
                  or abs(entry - plan.entry) > 1e-6)
        snap = _plan_snapshot(plan, reports, verdict, rec)
        snap["manual_levels"] = manual
        virtualbook.open_position(
            ticker=ticker, trader="me", entry=entry, stop=stop, target=target,
            kind=plan.kind, trigger=(entry if pending else plan.trigger),
            horizon_days=3 if "1–3" in plan.horizon else 10, stake=amount, snapshot=snap)
        msg = (f"Armed: fills if {ticker} crosses ${entry:.2f}" if pending
               else f"Bought {ticker} at ${entry:.2f} "
                    f"(stop ${stop:.2f} / target ${target:.2f})")
        st.toast(f"💼 {msg} — ${amount:,.0f} stake", icon="📒")
        st.session_state["vb_flash"] = f"💼 Virtual position opened in {ticker}."
        st.rerun()
    st.caption("Auto-closes at stop/target · tracked in the **💼 Virtual portfolio** "
               "panel at the top with your stake and P&L.")


_BOTS = (
    # (name, condition, uses pending trigger)  — contrasting strategies so the
    # data shows which rules actually make money.
    ("bot-GO",  lambda p: p.go, False),
    ("bot-70",  lambda p: p.score >= 70 and p.kind == "immediate" and not p.go, False),
    ("bot-BRK", lambda p: p.kind == "breakout_wait" and p.score >= 55, True),
)


def _run_bots(ticker: str, plan, reports=None, verdict=None, rec=None) -> None:
    """Auto virtual-traders: each bot opens (at most one) position per ticker
    when its rule matches the current plan."""
    for name, cond, _pending in _BOTS:
        try:
            if cond(plan) and not virtualbook.has_open(ticker, name):
                virtualbook.open_position(
                    ticker=ticker, trader=name, entry=plan.entry, stop=plan.stop,
                    target=plan.target1, kind=plan.kind, trigger=plan.trigger,
                    horizon_days=3,
                    snapshot=_plan_snapshot(plan, reports, verdict, rec))
                st.toast(f"🤖 {name} opened a virtual position in {ticker} "
                         f"(score {plan.score}%)", icon="🤖")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Swing radar — quiet tracking with escalating notices (60 / 70 / 80%).
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False, ttl=10)
def _run_quiet(ticker: str):
    """Lightweight scan for the radar: technicals only, served from cache."""
    return analyze_ticker(ticker, include_fundamentals=False)


@st.cache_data(show_spinner=False, ttl=900)
def _quiet_sentiment(ticker: str) -> float | None:
    """Live analyst/news sentiment for the radar — cached 15 min so the 10s
    technical scan never burns Finnhub rate limit on news fetches."""
    try:
        from datetime import datetime, timedelta

        from stockanalyzer.data.finnhub import FinnhubClient
        from stockanalyzer.sentiment.score import score_sentiment

        client = FinnhubClient()
        if not client.available:
            return None
        to = datetime.now()
        frm = to - timedelta(days=7)
        info = client.company_info(ticker, frm.strftime("%Y-%m-%d"),
                                   to.strftime("%Y-%m-%d"))
        res = score_sentiment(info)
        return res.score if res.available else None
    except Exception:
        return None


def _radar_plan(res, sentiment: float | None = None):
    for tf in (Timeframe.D5, Timeframe.D1, Timeframe.M1):
        rep = res.reports.get(tf)
        if rep is not None:
            break
    else:
        return None
    inv = round((build_verdict(res.reports, sentiment).score + 1) / 2 * 100)
    return build_swing_plan(rep, UseCase.BUY, SwingPace.FAST,
                            all_reports=res.reports,
                            context={"investor_pct": inv, "sentiment": sentiment})


def _radar_card(tk: str, plan) -> None:
    if plan.go:
        color, head = "#1b9e3e", "🟢 GO"
        instr = (f"Entry ${plan.entry:.2f} · stop ${plan.stop:.2f} · "
                 f"tgt ${plan.target1:.2f} · R:R {plan.rr:.1f}:1")
    elif plan.kind == "breakout_wait":
        color, head = "#f9a825", "⏳ WAIT"
        instr = f"Close above ${plan.trigger:.2f} → tgt ${plan.target1:.2f}"
    elif plan.light == "forming":
        color, head = "#f9a825", "🟡 FORMING"
        instr = plan.setup
    else:
        color, head = "#546e7a", "⚪ NO SWING"
        instr = "no clean setup right now"
    bells = "🔔" * sum(1 for lv in swingwatch.LEVELS if plan.score >= lv)
    st.markdown(
        f"<div style='background:{color}26;border:1px solid {color};border-radius:10px;"
        f"padding:8px 10px;margin:2px 0'>"
        f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
        f"<b style='font-size:1.1em'>{tk}</b>"
        f"<span style='color:{color};font-weight:700'>{plan.score}% {bells}</span></div>"
        f"<div style='font-size:0.85em;font-weight:600'>{head} · {plan.score_label}</div>"
        f"<div style='font-size:0.8em;color:#bbb'>{instr}</div></div>",
        unsafe_allow_html=True)
    if st.button(f"🔎 Open {tk}", key=f"radar_open_{tk}", use_container_width=True):
        st.session_state.ticker = tk
        st.session_state.submitted = True
        st.rerun(scope="app")


def _radar_panel(tracked: list[str]) -> None:
    @st.fragment(run_every="10s")
    def _radar():
        ss = st.session_state
        ss.setdefault("radar_levels", {})
        st.markdown("#### 📡 Swing radar — quiet daily-swing watch")
        cols = st.columns(min(4, max(1, len(tracked))))
        for i, tk in enumerate(tracked):
            with cols[i % len(cols)]:
                try:
                    res = _run_quiet(tk)
                    plan = (_radar_plan(res, _quiet_sentiment(tk))
                            if res.reports else None)
                except Exception:
                    plan = None
                if plan is None:
                    st.caption(f"{tk}: no data")
                    continue
                fired = swingwatch.new_notice(ss.radar_levels.get(tk, 0), plan.score)
                ss.radar_levels[tk] = swingwatch.notice_level(plan.score)
                if fired:
                    stored = papertrade.record(dict(
                        ts=time.time(), date=time.strftime("%x %X"),
                        ticker=tk, level=fired[0], score=plan.score,
                        label=plan.score_label, kind=plan.kind, setup=plan.setup,
                        entry=plan.entry, stop=plan.stop, target=plan.target1,
                        rr=plan.rr, trigger=plan.trigger, horizon_days=3,
                        guidance=plan.guidance, status="open", result_pct=0.0))
                    note = " · 📒 recorded for paper trading" if stored else ""
                    st.toast(f"📡 {tk}: {fired[1]} — {plan.guidance[:80]}{note}", icon="🔔")
                # Virtual trading: bots act on the scan; positions mark to price.
                _run_bots(tk, plan, reports=res.reports)
                px = _quiet_price(tk)
                if px:
                    for chg in virtualbook.mark(tk, px):
                        if chg["status"] == "closed":
                            st.toast(f"💼 {chg['trader']} closed {tk}: "
                                     f"{chg['close_reason']} ({chg['pnl_pct']:+.1f}%)",
                                     icon="💼")
                        else:
                            st.toast(f"💼 {chg['trader']}'s breakout order filled in {tk}",
                                     icon="🚀")
                _radar_card(tk, plan)
        st.caption("Scans every ~2½ min (cached, rate-safe) · fast 1–3 day pace · "
                   "🔔 = score reached 60 / 70 / 80% · each new level is toasted **and "
                   "recorded as a paper-trade proposition** below.")

    _radar()
    _journal_panel()
    st.divider()


def _pnl_style(df, pct_cols=(), usd_cols=(), price_cols=()):
    """Return a pandas Styler: green for gains, red for losses on P&L columns."""
    import pandas as pd

    def _bg(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return ""
        if pd.isna(x):
            return ""
        if x > 0:
            return "background-color:rgba(27,158,62,0.22);color:#0b6b2a;font-weight:600"
        if x < 0:
            return "background-color:rgba(229,57,53,0.22);color:#a01711;font-weight:600"
        return "color:gray"

    sty = df.style
    color_cols = [c for c in (list(pct_cols) + list(usd_cols)) if c in df.columns]
    if color_cols:
        sty = sty.map(_bg, subset=color_cols)
    fmt = {}
    for c in pct_cols:
        if c in df.columns:
            fmt[c] = lambda v: "" if pd.isna(v) else f"{v:+.2f}%"
    for c in usd_cols:
        if c in df.columns:
            fmt[c] = lambda v: "" if pd.isna(v) else f"${v:+,.2f}"
    for c in price_cols:
        if c in df.columns:
            fmt[c] = lambda v: "" if pd.isna(v) else f"${v:,.2f}"
    if fmt:
        sty = sty.format(fmt, na_rep="—")
    return sty


def _portfolio_panel() -> None:
    """💼 Virtual holdings — compact summary line + collapsible detail, refreshing
    every ~30s so unrealized P&L stays current."""
    if not virtualbook.load():
        with st.expander("💼 Virtual portfolio — empty (use the 📒 Virtual BUY button)",
                         expanded=False):
            st.info("No virtual positions yet. On any actionable plan press "
                    "**📒 Virtual BUY** to open a $1,000 paper position (stop + target "
                    "attached). Bots **bot-GO / bot-70 / bot-BRK** also trade automatically "
                    "as the radar scans, so the report card fills up on its own.")
        return

    @st.fragment(run_every="30s")
    def _folio():
        import pandas as pd

        book = virtualbook.load()
        live = [p for p in book if p["status"] in ("open", "pending")]
        # Mark holdings to fresh prices (auto-close stop/target hits).
        prices: dict[str, float] = {}
        for tk in {p["ticker"] for p in live}:
            px = _quiet_price(tk)
            if px:
                prices[tk] = px
                for chg in virtualbook.mark(tk, px):
                    if chg["status"] == "closed":
                        st.toast(f"💼 {chg['trader']} closed {tk}: {chg['close_reason']} "
                                 f"({chg['pnl_pct']:+.1f}%)", icon="💼")
        book = virtualbook.load()
        live = [p for p in book if p["status"] in ("open", "pending")]
        s = virtualbook.stats(book)
        tot = s["totals"]
        unreal = sum((prices.get(p["ticker"], p["entry"]) - p["entry"]) * p["shares"]
                     for p in live if p["status"] == "open")
        wr = f"{tot['win_rate']}%" if tot["win_rate"] is not None else "—"
        pnl_color = "🟢" if tot["total_pnl_usd"] + unreal >= 0 else "🔴"
        head = (f"💼 Virtual portfolio — {len(live)} live · "
                f"realized ${tot['total_pnl_usd']:+,.0f} · unrealized ${unreal:+,.0f} "
                f"{pnl_color} · win rate {wr}")
        with st.expander(head, expanded=False):
            if live:
                st.markdown("**Live positions** _($1,000 virtual stake each)_")
                rows = []
                for p in live:
                    cur = prices.get(p["ticker"])
                    upnl = ((cur / p["entry"] - 1) * 100) if (cur and p["status"] == "open") else None
                    udollar = (upnl / 100 * p.get("stake", 1000.0)) if upnl is not None else None
                    rows.append({
                        "ticker": p["ticker"], "trader": p["trader"],
                        "status": "⏳ armed" if p["status"] == "pending" else "📈 open",
                        "opened": _fmt_ts(p.get("opened_ts") or p["opened"]),
                        "stake $": p.get("stake", 1000.0),
                        "entry": p["entry"], "now": cur,
                        "stop": p["stop"], "target": p["target"],
                        "trigger": p.get("trigger"),
                        "P&L %": (round(upnl, 2) if upnl is not None else None),
                        "P&L $": (round(udollar, 2) if udollar is not None else None),
                    })
                st.dataframe(
                    _pnl_style(pd.DataFrame(rows),
                               pct_cols=["P&L %"], usd_cols=["P&L $"],
                               price_cols=["stake $", "entry", "now", "stop",
                                           "target", "trigger"]),
                    use_container_width=True, height=min(250, 60 + 36 * len(rows)))
                sel = st.selectbox(
                    "Close a position manually",
                    ["—"] + [f"{p['ticker']} · {p['trader']} · {p['id']}" for p in live],
                    key="vb_close_sel")
                if sel != "—" and st.button("✂ Close at market", key="vb_close_btn"):
                    pid = sel.split(" · ")[-1]
                    pos = next((p for p in live if p["id"] == pid), None)
                    px = prices.get(pos["ticker"]) if pos else None
                    closed = virtualbook.close_position(pid, px or (pos or {}).get("entry", 0))
                    if closed:
                        st.toast(f"💼 Closed {closed['ticker']} "
                                 f"({closed.get('pnl_pct', 0):+.1f}%)", icon="✂")
                        st.rerun(scope="fragment")

            # The improvement evidence: who/what actually makes money.
            closed_n = s["totals"]["n"]
            if closed_n:
                st.markdown("**📊 Algorithm evidence** _(closed trades)_")
                c = st.columns(max(2, min(4, len(s["traders"]) + 1)))
                c[0].metric("All traders", wr,
                            f"{closed_n} trades · ${tot['total_pnl_usd']:+,.0f}",
                            delta_color="off")
                for i, (name, a) in enumerate(s["traders"].items(), start=1):
                    if i >= len(c):
                        break
                    twr = f"{a['win_rate']}%" if a["win_rate"] is not None else "—"
                    c[i].metric(name, twr,
                                f"{a['n']} trades · ${a['total_pnl_usd']:+,.0f}",
                                delta_color="off")
                band_rows = [{"score band": k, **v} for k, v in s["bands"].items()]
                setup_rows = [{"setup": k, **v} for k, v in s["setups"].items()]
                b1, b2 = st.columns(2)
                with b1:
                    st.caption("By score band — does a higher score win more?")
                    st.dataframe(_pnl_style(pd.DataFrame(band_rows),
                                            pct_cols=["avg_pnl_pct"],
                                            usd_cols=["total_pnl_usd"]),
                                 use_container_width=True)
                with b2:
                    st.caption("By setup — which patterns deliver?")
                    st.dataframe(_pnl_style(pd.DataFrame(setup_rows),
                                            pct_cols=["avg_pnl_pct"],
                                            usd_cols=["total_pnl_usd"]),
                                 use_container_width=True)
                hist = [p for p in book if p["status"] == "closed"]
                st.markdown(f"**Trade history ({len(hist)})**")
                h_rows = [{
                    "closed": _fmt_ts(p.get("closed_ts") or p.get("closed")),
                    "ticker": p["ticker"],
                    "trader": p["trader"], "reason": p.get("close_reason"),
                    "stake $": p.get("stake", 1000.0),
                    "entry": p["entry"], "exit": p.get("exit_price"),
                    "P&L %": p.get("pnl_pct"), "P&L $": p.get("pnl_usd"),
                    "score": p.get("snapshot", {}).get("score"),
                    "setup": p.get("snapshot", {}).get("setup"),
                } for p in reversed(hist)]
                st.dataframe(
                    _pnl_style(pd.DataFrame(h_rows),
                               pct_cols=["P&L %"], usd_cols=["P&L $"],
                               price_cols=["stake $", "entry", "exit"]),
                    use_container_width=True, height=240)
            st.caption("Manual buys = trader **me** · bots: **bot-GO** (strict GO only) · "
                       "**bot-70** (score ≥70, ignores the GO gate) · **bot-BRK** (armed "
                       "breakout orders) — compare win rates to see which rules earn their "
                       "place in the algorithm. Virtual money only.")

    _folio()


def _journal_panel() -> None:
    """Paper-trade journal: recorded propositions + the per-level report card."""
    recs = papertrade.load()
    n = len(recs)
    head = (f"📒 Paper-trade journal — {n} proposition{'s' if n != 1 else ''} recorded"
            if n else "📒 Paper-trade journal — empty (auto-fills on ≥60% alerts)")
    with st.expander(head, expanded=False):
        if not recs:
            st.info("Nothing recorded yet. When a tracked ticker's swing score climbs "
                    "past **60% / 70% / 80%**, the radar logs the full proposition here "
                    "(entry, stop, target, score, setup) and later judges the outcome "
                    "against real prices — that's the algorithm's report card.")
            return
        if st.button("↻ Evaluate outcomes against market data"):
            frames = {}
            for tk in {r.get("ticker", "") for r in recs if r.get("ticker")}:
                try:
                    res = _run_quiet(tk)
                    rep = (res.reports.get(Timeframe.M1) or res.reports.get(Timeframe.M6)
                           or res.reports.get(Timeframe.Y1))
                    if rep is not None:
                        frames[tk] = rep.df
                except Exception:
                    continue
            recs = papertrade.evaluate_all(frames)
            st.success("Outcomes updated.")

        # Report card per alert level — the calibration loop.
        stats = papertrade.summarize(recs)
        st.markdown("**Algorithm report card** _(win = target hit; expired counts by sign; "
                    "open/not-triggered excluded from win rate)_")
        cols = st.columns(4)
        for col, key in zip(cols, (60, 70, 80, "all")):
            s = stats[key]
            wr = f"{s['win_rate']}%" if s["win_rate"] is not None else "—"
            avg = f"{s['avg_result']:+.1f}%" if s["avg_result"] is not None else "—"
            name = f"≥{key}%" if key != "all" else "All levels"
            col.metric(name, wr, f"{s['n']} props · avg {avg}", delta_color="off")

        # The journal itself, newest first.
        import pandas as pd
        emoji = {"target_hit": "🎯 target", "stop_hit": "🛑 stop", "expired": "⌛ expired",
                 "not_triggered": "🚫 no trigger", "open": "⏳ open"}
        rows = [{
            "when": r.get("date", ""), "ticker": r.get("ticker", ""),
            "level": f"≥{r.get('level', '')}%", "score": f"{r.get('score', '')}%",
            "kind": r.get("kind", ""), "setup": r.get("setup", ""),
            "entry": r.get("entry"), "stop": r.get("stop"), "target": r.get("target"),
            "R:R": r.get("rr"), "status": emoji.get(r.get("status", "open"), r.get("status")),
            "result %": r.get("result_pct", 0.0),
        } for r in reversed(recs)]
        st.dataframe(_pnl_style(pd.DataFrame(rows), pct_cols=["result %"],
                                price_cols=["entry", "stop", "target"]),
                     use_container_width=True, height=260)
        st.caption("Statuses update when you press Evaluate (or just leave records to "
                   "mature — fast props expire after 3 trading days at mark-to-market). "
                   "Use the per-level win rates to keep tuning the algorithm: if ≥80% "
                   "props don't beat ≥60% ones, the score needs recalibration.")


def _price_header(result) -> None:
    q = result.quote
    name = result.company.fundamentals.name if (result.company and result.company.available) else ""
    title = f"{result.ticker}" + (f" — {name}" if name else "")
    if q is None:
        st.subheader(title)
        return
    up = q.change >= 0
    color = "#1b9e3e" if up else "#e53935"
    arrow = "▲" if up else "▼"
    badge = ""
    if q.session == "pre-market":
        badge = "<span style='background:#5b3a86;color:#fff;padding:2px 8px;border-radius:6px;font-size:0.7em'>🌅 PRE-MARKET</span>"
    elif q.session == "after-hours":
        badge = "<span style='background:#2b4a86;color:#fff;padding:2px 8px;border-radius:6px;font-size:0.7em'>🌙 AFTER-HOURS</span>"
    change_label = "vs prior close" if q.session != "regular" else ""
    st.markdown(
        f"<div style='display:flex;align-items:baseline;gap:16px;flex-wrap:wrap'>"
        f"<span style='font-size:1.4em;font-weight:600'>{title}</span>"
        f"<span style='font-size:2.0em;font-weight:700'>${q.price:,.2f}</span>"
        f"<span style='font-size:1.2em;color:{color};font-weight:600'>"
        f"{arrow} {q.change:+,.2f} ({q.change_pct:+.2f}%) {change_label}</span>"
        f"{badge}</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Source: {q.source} · provider: {result.provider}")


def _extended_alert(result) -> None:
    """Prominent heads-up when a pre-market / after-hours move is detected on 1D."""
    rep = result.reports.get(Timeframe.D1)
    if rep is None:
        return
    for s in rep.signals:
        if s.name.startswith(("premarket_", "afterhours_")):
            icon = "🌅" if s.name.startswith("premarket_") else "🌙"
            (st.success if s.direction == Direction.BULL else st.warning)(
                f"{icon} **Extended-hours move:** {s.evidence} "
                "_(watch the open — may shift the near-term trend.)_")
            return


def _gauge(go_score: int, color: str, title: str) -> go.Figure:
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=go_score,
        number={"suffix": "%"},
        title={"text": title, "font": {"size": 14}},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 45], "color": "#5c2b2b"},
                {"range": [45, 65], "color": "#5c552b"},
                {"range": [65, 100], "color": "#2b5c33"},
            ],
            "threshold": {"line": {"color": "white", "width": 3}, "value": go_score},
        },
    ))
    fig.update_layout(height=230, margin=dict(l=20, r=20, t=50, b=10), template="plotly_dark")
    return fig


def _recommendation_section(result, rec, usecase: UseCase) -> None:
    p = rec.preset
    st.markdown(
        f"<div style='background:{p.color}22;border-left:6px solid {p.color};"
        f"padding:12px 16px;border-radius:8px;margin:8px 0'>"
        f"<span style='font-size:1.6em;font-weight:700'>{p.emoji} {p.label}</span></div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 1])
    with left:
        st.plotly_chart(_gauge(rec.go_score, rec.light_color,
                               f"Go-score · {usecase.label}"), use_container_width=True,
                        config={"displayModeBar": False})
    with right:
        st.markdown(
            f"<div style='background:{rec.light_color};color:white;text-align:center;"
            f"padding:18px;border-radius:10px;font-size:1.4em;font-weight:700;margin-top:30px'>"
            f"{rec.light_label}</div>", unsafe_allow_html=True)
        for r in rec.light_reasons:
            st.caption(r)

    st.markdown(rec.summary)


def _cost_basis_block(buy_price: float, current: float,
                       stop: float, target: float) -> None:
    """Show P&L at current price, at stop, and at target — all relative to buy price."""
    pnl_now  = (current - buy_price) / buy_price * 100
    pnl_stop = (stop    - buy_price) / buy_price * 100
    pnl_tgt  = (target  - buy_price) / buy_price * 100
    locked   = stop >= buy_price           # stop is above cost → worst case = still profitable

    st.markdown("---")
    st.markdown("**📌 Your position — adjusted to your buy price**")
    c0, c1, c2, c3 = st.columns(4)
    c0.metric("You bought at",   f"${buy_price:.2f}")
    c1.metric("Unrealised P&L",  f"{pnl_now:+.1f}%",
              f"${current - buy_price:+.2f} / share")
    c2.metric("If stopped out",  f"{pnl_stop:+.1f}% from cost",
              "✅ still profit" if locked else "❌ still a loss",
              delta_color="off")
    c3.metric("If target hit",   f"{pnl_tgt:+.1f}% from cost",
              f"${target - buy_price:+.2f} / share")

    # Plain-English advice keyed to where the user actually stands.
    if pnl_now >= 20:
        msg = (f"🟢 Up **{pnl_now:.1f}%** — strong gain. "
               "Consider raising your stop to lock in more profit so a reversal can't wipe it out.")
    elif pnl_now >= 5:
        if locked:
            msg = (f"🟢 Up **{pnl_now:.1f}%** and your stop (${stop:.2f}) is **above** your "
                   f"${buy_price:.2f} cost — this is now a **risk-free trade**. "
                   "Even if it reverses to the stop, you still close in profit.")
        else:
            msg = (f"🟡 Up **{pnl_now:.1f}%** but your stop (${stop:.2f}) is still below "
                   f"your ${buy_price:.2f} cost. Tip: raise the stop to "
                   f"~${buy_price * 1.002:.2f} to make this trade risk-free.")
    elif pnl_now >= -3:
        msg = (f"🟡 Near break-even ({pnl_now:+.1f}%). "
               f"Stop at ${stop:.2f} limits downside to **{pnl_stop:.1f}%** from what you paid.")
    else:
        msg = (f"🔴 Down **{abs(pnl_now):.1f}%** from your buy price. "
               f"Honouring the stop at ${stop:.2f} caps the total loss at "
               f"**{pnl_stop:.1f}%** — don't let a manageable loss turn into a big one.")

    st.info(msg)


def _reco_context(result) -> dict:
    """Earnings/Street context for the swing checks (degrades to empty)."""
    ctx: dict = {}
    if getattr(result, "earnings_date", None):
        try:
            from datetime import date
            ed = date.fromisoformat(result.earnings_date)
            ctx["earnings_days"] = max(0, (ed - date.today()).days)
            ctx["earnings_date"] = result.earnings_date
        except ValueError:
            pass
    av = result.company.analyst if (result.company and result.company.available) else None
    if av and av.target_mean:
        ctx["analyst_target"] = float(av.target_mean)
    if result.sentiment is not None and result.sentiment.available:
        ctx["sentiment"] = result.sentiment.score
    return ctx


def _swing_score_block(plan) -> None:
    """0–100% swing quality score + the plain-English checklist behind it."""
    pct = max(0, min(100, plan.score))
    color = ("#1b9e3e" if pct >= 75 else "#8bc34a" if pct >= 55
             else "#f9a825" if pct >= 35 else "#e53935")
    if plan.kind == "no_trade":
        title = "Conditions score"
        label = f"{plan.score_label} — but <b>no tradable setup, don't enter on this</b>"
    elif plan.kind == "breakout_wait":
        title = "Setup score (if the breakout triggers)"
        label = plan.score_label
    else:
        title = "Swing score"
        label = plan.score_label
    st.markdown(
        f"<div style='margin:6px 0 2px 0'><b>{title}: "
        f"<span style='color:{color};font-size:1.3em'>{pct}%</span></b> — {label} "
        f"<span style='color:gray;font-size:0.85em'>(setup · honest R:R · clear path · "
        f"5D/6M/1Y MA+MACD · bias · RSI · chase · extension · conflict)</span></div>"
        f"<div style='background:#333;border-radius:6px;height:10px'>"
        f"<div style='background:{color};width:{pct}%;height:10px;border-radius:6px'></div></div>",
        unsafe_allow_html=True)
    with st.expander("Why this score? — the checklist"):
        for c in plan.checks:
            icon = "➖" if c.na else ("✅" if c.ok else ("⚠️" if c.weight == 0 and not c.ok else
                                                        "ℹ️" if c.weight == 0 else "❌"))
            w = f"_(weight {c.weight})_" if c.weight else "_(info)_"
            st.write(f"{icon} **{c.name}** — {c.detail} {w}")


def _orders_guide(plan, usecase: UseCase, live_momentum: str | None = None,
                  reports=None, verdict=None, rec=None) -> None:
    """Exact broker instructions, driven by the plan kind."""
    long_side = plan.bias == Direction.BULL

    # The expert sentence is the headline.
    st.markdown(f"**🧭 {plan.guidance}**")

    if plan.kind == "no_trade":
        st.markdown("**📋 What to do instead**")
        if usecase == UseCase.OWN:
            steps = [
                f"1️⃣ **Protect** — *sell STOP* at **${plan.stop:.2f}** "
                f"({plan.stop_pct:+.1f}%) — below real structure, sized to the "
                f"~{plan.daily_atr_pct:.1f}%/day volatility so normal noise doesn't sell you out.",
                f"2️⃣ **Trim** — consider a *sell LIMIT* at **${plan.target1:.2f}** "
                f"({plan.target1_pct:+.1f}%, the first wall) to take some profit into strength.",
            ]
            if plan.daily_atr_pct >= 5:
                steps.append("⚠️ Volatility is high — if this stop feels too wide, reduce "
                             "position size instead of tightening the stop into noise.")
        else:
            steps = ["👀 **No orders to place.** Set price alerts at the watch levels in the "
                     "guidance above and re-check when one breaks."]
        for s in steps:
            st.write(s)
        st.caption("No aim/R:R shown — there is no trade here. · **not financial advice.**")
        return

    a, b, c2, d = st.columns(4)
    aim_label = "Aim (from trigger)" if plan.kind == "breakout_wait" else "You're aiming for"
    a.metric(aim_label, f"{plan.target1_pct:+.1f}%")
    b.metric("You're risking", f"{plan.stop_pct:+.1f}%")
    c2.metric("Reward : Risk", f"{plan.rr:.1f} : 1")
    d.metric("Score", f"{plan.score}%", plan.score_label, delta_color="off")

    st.markdown("**📋 Your orders — exactly what to place**")
    if plan.kind == "breakout_wait":
        trig = plan.trigger or plan.entry
        if long_side:
            steps = [
                f"1️⃣ **Arm the trigger** — *stop-limit BUY* just above **${trig:.2f}** "
                f"(entry ≈ ${plan.entry:.2f}). It only fills if the breakout actually happens.",
                f"2️⃣ **Stop-loss** (once filled) — *sell STOP* at **${plan.stop:.2f}**: "
                "back under the broken wall = failed breakout, get out.",
                f"3️⃣ **Take-profit** — *sell LIMIT* at **${plan.target1:.2f}** "
                f"({plan.target1_pct:+.1f}% from entry).",
            ]
        else:
            steps = [
                f"1️⃣ **Arm the trigger** — *stop SELL* just below **${trig:.2f}** "
                f"(entry ≈ ${plan.entry:.2f}).",
                f"2️⃣ **Stop-loss** (once filled) — *buy STOP* at **${plan.stop:.2f}**.",
                f"3️⃣ **Take-profit** — *buy LIMIT* at **${plan.target1:.2f}**.",
            ]
    elif usecase == UseCase.OWN:
        steps = [
            f"1️⃣ **Protect** — *sell STOP* at **${plan.stop:.2f}** "
            f"({plan.stop_pct:+.1f}%): if price falls there, you're sold out automatically.",
            f"2️⃣ **Take profit** — *sell LIMIT* at **${plan.target1:.2f}** "
            f"({plan.target1_pct:+.1f}%): locks the gain if price reaches the aim.",
        ]
    elif long_side:
        enter = (f"1️⃣ **Enter** — *buy LIMIT* at **${plan.entry:.2f}** "
                 "(or market if it's trading right there now). Don't chase higher.")
        if live_momentum == "falling":
            enter = (f"1️⃣ **Hold off** — live momentum is falling; wait for an upward "
                     f"tick, then *buy LIMIT* at **${plan.entry:.2f}**.")
        steps = [
            enter,
            f"2️⃣ **Stop-loss** — *sell STOP* at **${plan.stop:.2f}** "
            f"({plan.stop_pct:+.1f}%): your maximum planned loss "
            f"(sized to ~{plan.daily_atr_pct:.1f}%/day volatility).",
            f"3️⃣ **Take-profit** — *sell LIMIT* at **${plan.target1:.2f}** "
            f"({plan.target1_pct:+.1f}%): your profit aim.",
        ]
    else:
        enter = f"1️⃣ **Enter short** — *sell to open* ~**${plan.entry:.2f}**."
        if live_momentum == "rising":
            enter = (f"1️⃣ **Hold off** — live momentum is rising; wait for a downward "
                     f"tick, then *sell to open* ~**${plan.entry:.2f}**.")
        steps = [
            enter,
            f"2️⃣ **Stop-loss** — *buy STOP* at **${plan.stop:.2f}** "
            f"({plan.stop_pct:+.1f}%): caps the loss if it rallies.",
            f"3️⃣ **Take-profit** — *buy LIMIT* at **${plan.target1:.2f}**: "
            f"the {abs(plan.target1_pct):.1f}% drop is your gain.",
        ]
    for s in steps:
        st.write(s)
    st.caption("Place stop + take-profit together with the entry (ask your broker for a "
               "bracket / OCO order) · risk ~1% of your account per trade.")
    _virtual_buy_button(plan, st.session_state.get("ticker", ""),
                        key_suffix=("live" if live_momentum is not None else "static"),
                        reports=reports, verdict=verdict, rec=rec)


def _swing_card(rec, buy_price: float | None = None,
                usecase: UseCase = UseCase.BUY,
                reports=None, verdict=None) -> None:
    plan = rec.swing
    st.subheader("⚡ Swing trade plan")
    st.caption(f"Decision chart: {rec.decision_timeframe} · horizon {plan.horizon} · "
               "enter near the reversal, not after a big breakout.")
    if plan.setup == "No setup":
        st.info("No clean swing setup right now — wait for a pullback-to-support, an "
                "oversold bounce, or a volume breakout to line up.")
    cols = st.columns(4)
    cols[0].metric("Entry", f"${plan.entry:.2f}")
    cols[1].metric("Stop", f"${plan.stop:.2f}", f"{plan.stop_pct:+.1f}%")
    cols[2].metric("Target", f"${plan.target1:.2f}", f"{plan.target1_pct:+.1f}%")
    rr_color = "#1b9e3e" if plan.rr >= 2 else "#e53935"
    cols[3].markdown(
        f"<div style='text-align:center'><div style='font-size:0.8em;color:gray'>Reward:Risk</div>"
        f"<div style='font-size:1.6em;font-weight:700;color:{rr_color}'>{plan.rr:.1f}:1</div></div>",
        unsafe_allow_html=True)
    _swing_score_block(plan)
    _orders_guide(plan, usecase, reports=reports, verdict=verdict, rec=rec)
    st.caption(f"Setup: **{plan.setup}** · risk per share ≈ {plan.risk_pct:.1f}% · "
               f"2nd target ${plan.target2:.2f}" if plan.target2 else
               f"Setup: **{plan.setup}** · risk per share ≈ {plan.risk_pct:.1f}%")
    if plan.fast_mover:
        st.warning("⚡ Fast-mover signals active (gap/volume) — this can move quickly; "
                   "size small and honour the stop.")
    for r in plan.reasons:
        st.write(r)
    if buy_price:
        _cost_basis_block(buy_price, plan.entry, plan.stop, plan.target1)
    st.caption("Risk ~1% of your account per trade · only take it at R:R ≥ 2:1 · "
               "**not financial advice.**")


def _next_steps(rec, buy_price: float | None = None,
                current_price: float | None = None,
                usecase: UseCase = UseCase.BUY,
                reports=None, verdict=None) -> None:
    if getattr(rec, "swing", None) is not None:
        _swing_card(rec, buy_price, usecase, reports=reports, verdict=verdict)
        return
    st.subheader("✅ What to watch next")
    # Own + investor mode: show cost-basis block using scenario levels as stop/target.
    if buy_price and current_price:
        sc = rec.scenario
        stop_lvl = (sc.downside_levels[0] if (sc and sc.downside_levels)
                    else current_price * 0.97)
        tgt_lvl  = (sc.upside_levels[0]   if (sc and sc.upside_levels)
                    else current_price * 1.10)
        _cost_basis_block(buy_price, current_price, stop_lvl, tgt_lvl)
    a, b = st.columns(2)
    with a:
        st.markdown("**Decision checklist**")
        if rec.watch_items:
            for it in rec.watch_items:
                st.write(it)
        else:
            st.caption("No clear levels nearby.")
    with b:
        st.markdown(f"**Possible scenarios** _(from {rec.decision_timeframe} levels)_")
        if rec.scenario and rec.scenario.ordered:
            for line in rec.scenario.ordered:
                st.write(f"- {line}")
        else:
            st.caption("Not enough level data for scenarios.")


def _key_level(rec, usecase: UseCase) -> float | None:
    sc = rec.scenario
    if sc is None:
        return None
    if usecase in (UseCase.BUY, UseCase.OWN):
        return sc.upside_levels[0] if sc.upside_levels else None
    return sc.downside_levels[0] if sc.downside_levels else None


def _live_section(ticker, prefer, usecase, rec, result) -> None:
    """Polling fallback when no FINNHUB_KEY / websocket — 60s REST refresh."""
    st.divider()
    st.subheader("🔴 Live watch (polling)")
    st.caption("Real-time streaming needs a free FINNHUB_KEY in .env. Using 60-second "
               "REST polling instead — refines entry timing only.")
    intended = Direction.BULL if usecase in (UseCase.BUY, UseCase.OWN) else Direction.BEAR
    _polling_section(ticker, prefer, intended, _key_level(rec, usecase))


# --------------------------------------------------------------------------- #
# Live mode 2.0 — whole-page real-time dashboard
# --------------------------------------------------------------------------- #
_ENGINE_REFRESH_S = 45          # re-run the signal engine at most this often
_SEV_RANK = {"bad": 3, "good": 3, "warn": 2, "info": 1}
_SEV_COLOR = {"good": "#1b9e3e", "bad": "#e53935", "warn": "#f9a825", "info": "#90a4ae"}


def _live_dashboard(ticker, prefer, usecase, strategy, pace, buy_price, result) -> None:
    ss = st.session_state
    st.divider()
    h1, h2, h3 = st.columns([3, 1.4, 1])
    h1.subheader("🔴 Live mode — real-time dashboard")
    dur_label = h2.selectbox("Auto-stop after", ["3 min", "5 min", "10 min", "Until I stop"],
                             index=3, key="rt_dur")
    restart = h3.button("↻ Restart", use_container_width=True)
    max_secs = {"3 min": 180, "5 min": 300, "10 min": 600, "Until I stop": None}[dur_label]

    # (Re)start the stream on entry, ticker change, or explicit restart.
    if ss.get("rt_stream") is None or ss.get("rt_ticker") != ticker or restart:
        old = ss.get("rt_stream")
        if old is not None:
            old.stop()
        stream = RealtimeStream(ticker)
        stream.start()
        ss.rt_stream = stream
        ss.rt_ticker = ticker
        ss.rt_started_at = time.time()
        ss.live_engine = None
        ss.live_prev_state = None
        ss.live_events = []

    sent = result.sentiment.score if (result.sentiment and result.sentiment.available) else None
    ctx = dict(ticker=ticker, prefer=prefer, usecase=usecase, strategy=strategy, pace=pace,
               buy_price=buy_price, sent=sent, result=result, max_secs=max_secs)

    @st.fragment(run_every="1s")
    def _frame():
        _live_frame(ctx)

    _frame()


def _live_frame(ctx: dict) -> None:
    ss = st.session_state
    ticker = ctx["ticker"]
    stream = ss.get("rt_stream")
    now = time.time()
    elapsed = now - ss.get("rt_started_at", now)
    ended = ctx["max_secs"] is not None and elapsed >= ctx["max_secs"]
    if ended and stream is not None:
        stream.stop()

    # --- engine refresh (~45s): re-run the signal engine on a fresh 1D frame ---
    eng = ss.get("live_engine")
    if eng is None:
        ss.live_engine = eng = {"reports": dict(ctx["result"].reports), "ts": now}
    elif now - eng["ts"] > _ENGINE_REFRESH_S and not ended:
        try:
            res = analyze_ticker(ticker, timeframes=[Timeframe.D1],
                                 prefer=None if ctx["prefer"] == "auto" else ctx["prefer"],
                                 include_fundamentals=False, live_mode=True)
            d1 = res.reports.get(Timeframe.D1)
            if d1 is not None:
                eng["reports"] = {**ctx["result"].reports, Timeframe.D1: d1}
        except Exception:
            pass
        eng["ts"] = now
    reports = eng["reports"]
    d1 = reports.get(Timeframe.D1)
    engine_age = int(now - eng["ts"])

    # --- live price: latest tick → quote → engine close ---
    snap = stream.snapshot() if stream is not None else None
    live_price = None
    if snap and snap.ticks:
        live_price = snap.ticks[-1].price
    if live_price is None and ctx["result"].quote:
        live_price = ctx["result"].quote.price
    if live_price is None and d1 is not None:
        live_price = d1.meta.get("last_close")

    # --- recompute BOTH framings at the live price (cheap: cached reports) ---
    reco_ctx = _reco_context(ctx["result"])
    swing_verdict = build_verdict(reports, ctx["sent"], Strategy.SWING, ctx["pace"])
    swing_rec = build_recommendation(
        ticker, swing_verdict,
        reports, ctx["usecase"], Strategy.SWING, ctx["pace"], price_override=live_price,
        context=reco_ctx)
    inv_rec = build_recommendation(
        ticker, build_verdict(reports, ctx["sent"], Strategy.INVESTOR),
        reports, ctx["usecase"], Strategy.INVESTOR, price_override=live_price,
        context=reco_ctx)
    plan = swing_rec.swing
    primary = swing_rec if ctx["strategy"] == Strategy.SWING else inv_rec
    key_level = _key_level(primary, ctx["usecase"])
    stats = summarize(snap.ticks) if (snap and snap.ticks) else None
    live_momentum = stats.momentum if stats else None

    # --- render ---
    _live_header(ctx, snap, live_price, elapsed, engine_age, ended)
    _live_decision_strip(swing_rec, inv_rec, d1, ctx["pace"])
    if plan is not None:
        _swing_score_block(plan)
        _orders_guide(plan, ctx["usecase"], live_momentum=live_momentum,
                      reports=reports, verdict=swing_verdict, rec=swing_rec)

    # Flip detection → toast + event feed.
    cur = _build_live_state(swing_rec, inv_rec, d1, live_price, plan, key_level)
    events = diff_states(ss.get("live_prev_state"), cur)
    ss.live_prev_state = cur
    if events:
        feed = ss.get("live_events", [])
        for e in events:
            feed.insert(0, (time.strftime("%X"), e))
        ss.live_events = feed[:20]
        top_ev = max(events, key=lambda e: _SEV_RANK.get(e.severity, 0))
        st.toast(top_ev.text, icon="⚡")

    # Virtual book: bots act on the analyzed ticker too; mark holdings ~10s.
    if plan is not None and live_price and now - ss.get("vb_last_mark", 0) > 10:
        ss.vb_last_mark = now
        _run_bots(ticker, plan, reports=reports, verdict=swing_verdict, rec=swing_rec)
        for chg in virtualbook.mark(ticker, live_price):
            if chg["status"] == "closed":
                st.toast(f"💼 {chg['trader']} closed {ticker}: {chg['close_reason']} "
                         f"({chg['pnl_pct']:+.1f}%)", icon="💼")
            else:
                st.toast(f"💼 {chg['trader']}'s breakout order filled in {ticker}", icon="🚀")

    _live_chart(ticker, d1, plan, key_level, live_price, snap, reports=reports)
    _live_signal_chips(d1)
    if ctx["buy_price"] and plan is not None and live_price:
        _cost_basis_block(ctx["buy_price"], live_price, plan.stop, plan.target1)
    _live_event_feed()
    st.caption("Price updates ~1s · signals re-run ~45s · plan/levels/P&L recomputed at "
               "the live price · the multi-timeframe verdict is structural · "
               "**not financial advice.**")


def _live_header(ctx, snap, live_price, elapsed, engine_age, ended) -> None:
    result = ctx["result"]
    name = result.company.fundamentals.name if (result.company and result.company.available) else ""
    title = ctx["ticker"] + (f" — {name}" if name else "")
    prev_close = result.quote.prev_close if (result.quote and result.quote.prev_close) else None
    change = change_pct = 0.0
    if prev_close and live_price:
        change = live_price - prev_close
        change_pct = change / prev_close * 100 if prev_close else 0.0
    up = change >= 0
    color = "#1b9e3e" if up else "#e53935"
    arrow = "▲" if up else "▼"
    connected = snap.connected if snap else False
    dot = "🟢 LIVE" if connected and not ended else ("⏹ stopped" if ended else "🟡 connecting…")
    price_txt = f"${live_price:,.2f}" if live_price else "—"
    st.markdown(
        f"<div style='display:flex;align-items:baseline;gap:16px;flex-wrap:wrap'>"
        f"<span style='font-size:1.3em;font-weight:600'>{title}</span>"
        f"<span style='font-size:2.1em;font-weight:700'>{price_txt}</span>"
        f"<span style='font-size:1.2em;color:{color};font-weight:600'>"
        f"{arrow} {change:+,.2f} ({change_pct:+.2f}%) vs prior close</span>"
        f"<span style='font-size:0.9em;color:#888'>{dot}</span></div>",
        unsafe_allow_html=True)

    stats = summarize(snap.ticks) if (snap and snap.ticks) else None
    remain = "" if ctx["max_secs"] is None else f" · {max(0, int(ctx['max_secs'] - elapsed))}s left"
    if stats is not None:
        mom = {"rising": "📈 rising", "falling": "📉 falling", "flat": "➡️ flat"}[stats.momentum]
        st.caption(f"{stats.n_ticks} live trades · H ${stats.high:.2f} / L ${stats.low:.2f} · "
                   f"{mom} · signals refreshed {engine_age}s ago{remain}")
    else:
        st.caption(f"signals refreshed {engine_age}s ago{remain}")
        if not ended:
            st.info("📡 Stream is quiet — outside regular US market hours (9:30 am–4:00 pm ET) "
                    "there are no live trades. Showing the latest analysis values; the plan and "
                    "P&L still recompute against the last price.")


def _live_decision_strip(swing_rec, inv_rec, d1, pace: SwingPace = SwingPace.STANDARD) -> None:
    """The fast-decision core: swing read + long-term read + trend-change, side by side."""
    plan = swing_rec.swing
    c1, c2, c3 = st.columns(3)
    with c1:
        rr = f" · R:R {plan.rr:.1f}:1" if (plan and plan.kind != "no_trade") else ""
        score = f" · score {plan.score}%" if plan else ""
        pace_lbl = "fast 1–3d" if pace == SwingPace.FAST else "days–2wk"
        st.markdown(
            f"<div style='background:{swing_rec.light_color};color:white;padding:12px;"
            f"border-radius:10px;text-align:center'>"
            f"<div style='font-size:0.8em;opacity:0.85'>⚡ SWING ({pace_lbl})</div>"
            f"<div style='font-size:1.05em;font-weight:700'>{swing_rec.light_label}</div>"
            f"<div style='font-size:0.85em'>{(plan.setup if plan else '')}{rr}{score}</div></div>",
            unsafe_allow_html=True)
    with c2:
        p = inv_rec.preset
        st.markdown(
            f"<div style='background:{p.color};color:white;padding:12px;border-radius:10px;"
            f"text-align:center'><div style='font-size:0.8em;opacity:0.85'>📈 LONG-TERM</div>"
            f"<div style='font-size:1.05em;font-weight:700'>{p.emoji} {p.label}</div>"
            f"<div style='font-size:0.85em'>conviction {inv_rec.bullish_pct}% bullish</div></div>",
            unsafe_allow_html=True)
    with c3:
        tc = d1.trend_change if d1 else None
        if tc and tc.likely:
            st.markdown(
                f"<div style='background:#6a1b9a;color:white;padding:12px;border-radius:10px;"
                f"text-align:center'><div style='font-size:0.8em;opacity:0.85'>🔄 TREND CHANGE</div>"
                f"<div style='font-size:1.05em;font-weight:700'>{_badge(tc.direction)}</div>"
                f"<div style='font-size:0.85em'>confidence {tc.score:.0%}</div></div>",
                unsafe_allow_html=True)
        else:
            st.markdown(
                "<div style='background:#37474f;color:white;padding:12px;border-radius:10px;"
                "text-align:center'><div style='font-size:0.8em;opacity:0.85'>🔄 TREND</div>"
                "<div style='font-size:1.05em;font-weight:700'>Stable</div>"
                "<div style='font-size:0.85em'>no reversal flagged</div></div>",
                unsafe_allow_html=True)


def _sig_weight(s) -> float:
    """Effective engine contribution = category weight × signal strength."""
    return CATEGORY_WEIGHTS.get(s.category, 1.0) * s.strength


def _build_live_state(swing_rec, inv_rec, d1, live_price, plan, key_level) -> LiveState:
    live_sigs = [s for s in d1.signals if s.name != "trend"] if d1 else []
    names = frozenset(s.name for s in live_sigs)
    metas = tuple((s.name, s.direction.sign, round(_sig_weight(s), 2)) for s in live_sigs)
    tc = d1.trend_change if d1 else None
    return LiveState(
        light=(plan.light if plan else ""),
        swing_go=(plan.go if plan else False),
        rr=(plan.rr if plan else 0.0),
        swing_setup=(plan.setup if plan else ""),
        trend_change_likely=(tc.likely if tc else False),
        trend_change_dir=(tc.direction.value if tc else ""),
        preset_key=inv_rec.preset.key,
        preset_label=inv_rec.preset.label,
        signals=names,
        signal_meta=metas,
        price=live_price or 0.0,
        entry=(plan.entry if plan else None),
        stop=(plan.stop if plan else None),
        target=(plan.target1 if plan else None),
        key_level=key_level,
        kind=(plan.kind if plan else ""),
        trigger=(plan.trigger if plan else None),
        bull=(plan.bias == Direction.BULL if plan else True),
    )


# Order the frame picker the way a trading site does: shortest → longest.
_CHART_FRAMES = (Timeframe.D1, Timeframe.D5, Timeframe.M1, Timeframe.M6,
                 Timeframe.YTD, Timeframe.Y1, Timeframe.Y5)


def _focus_right(fig, df, frac: float = 0.4) -> None:
    """Open the view zoomed onto the most recent `frac` of bars (latest ticks),
    so freshly-added candles land in a focused right-hand window. uirevision keeps
    any later user pan/zoom; this only sets the *initial* range."""
    n = len(df)
    if n < 8:
        return
    start = df.index[int(n * (1 - frac))]
    fig.update_xaxes(range=[start, df.index[-1]], row=1, col=1)


def _live_chart(ticker, d1, plan, key_level, live_price, snap, reports=None) -> None:
    if d1 is None:
        st.info("No intraday chart available yet.")
        return

    reports = reports or {Timeframe.D1: d1}
    avail = [tf for tf in _CHART_FRAMES if reports.get(tf) is not None]
    ss = st.session_state
    ss.setdefault("live_chart_tf", Timeframe.D1)
    if ss.live_chart_tf not in avail:
        ss.live_chart_tf = Timeframe.D1
    labels = [tf.value for tf in avail]
    picker = getattr(st, "segmented_control", None) or getattr(st, "pills", None)
    if picker is not None and len(avail) > 1:
        chosen = picker("Timeframe", labels,
                        default=ss.live_chart_tf.value, key="live_chart_pick",
                        label_visibility="collapsed")
    elif len(avail) > 1:
        chosen = st.radio("Timeframe", labels,
                          index=labels.index(ss.live_chart_tf.value),
                          horizontal=True, key="live_chart_pick",
                          label_visibility="collapsed")
    else:
        chosen = Timeframe.D1.value
    tf = next((t for t in avail if t.value == chosen), Timeframe.D1)
    ss.live_chart_tf = tf

    rep = reports.get(tf, d1)
    is_live = tf == Timeframe.D1
    suffix = "live (1D · 5-min)" if is_live else tf.value
    fig = candlestick_figure(rep, title=f"{ticker} — {suffix}")

    # Plan overlays + the live price line belong on the live intraday frame only;
    # on the structural frames they'd be off-scale clutter.
    if is_live:
        if plan is not None and plan.trigger:
            fig.add_hline(y=plan.trigger, line=dict(color="#ff9800", width=1.6, dash="dot"),
                          annotation_text=f"🚀 trigger {plan.trigger:.2f}",
                          annotation_position="left", row=1, col=1)
        if plan is not None:
            for y, c, label in ((plan.entry, "#42a5f5", "entry"),
                                (plan.stop, "#ef5350", "stop"),
                                (plan.target1, "#26a69a", "target")):
                fig.add_hline(y=y, line=dict(color=c, width=1.2, dash="dash"),
                              annotation_text=f"{label} {y:.2f}", annotation_position="left",
                              row=1, col=1)
        if live_price:
            fig.add_hline(y=live_price, line=dict(color="#ffeb3b", width=1.4),
                          annotation_text=f"LIVE {live_price:.2f}", annotation_position="right",
                          row=1, col=1)

    _focus_right(fig, rep.df)
    st.plotly_chart(fig, use_container_width=True, config=_PLOTLY_CFG)
    if is_live and snap and len(snap.ticks) >= 2:
        _live_heartbeat(snap.ticks)


def _live_heartbeat(ticks) -> None:
    """A 'forming candle' heartbeat panel built from the raw tick stream."""
    candles = ticks_to_candles(ticks, interval_s=15)
    if len(candles) >= 2:
        fig = go.Figure(go.Candlestick(
            x=list(range(len(candles))), open=candles["open"], high=candles["high"],
            low=candles["low"], close=candles["close"],
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350"))
        caption = f"Heartbeat — {len(candles)} candles forming live (15-second buckets)"
    else:
        ys = [t.price for t in ticks[-300:]]
        color = "#26a69a" if ys[-1] >= ys[0] else "#ef5350"
        fig = go.Figure(go.Scatter(y=ys, mode="lines", line=dict(color=color, width=1.2)))
        caption = f"Heartbeat — last {len(ys)} trades"
    fig.update_layout(height=150, margin=dict(l=10, r=10, t=4, b=4), template="plotly_dark",
                      showlegend=False, xaxis_rangeslider_visible=False,
                      xaxis_visible=False, uirevision="heartbeat")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption(caption)


# Direction → (text color, translucent fill, border) — readable on light AND dark.
_CHIP_STYLE = {
    Direction.BULL: ("#0f6e56", "rgba(29,158,117,0.14)", "rgba(29,158,117,0.55)"),
    Direction.BEAR: ("#a32d2d", "rgba(226,75,74,0.14)", "rgba(226,75,74,0.55)"),
    Direction.NEUTRAL: ("#5f5e5a", "rgba(136,135,128,0.14)", "rgba(136,135,128,0.45)"),
}


def _live_signal_chips(d1) -> None:
    if d1 is None:
        return
    sigs = [s for s in d1.signals if s.name != "trend"]
    if not sigs:
        st.caption("**Live signals (1D):** none active right now.")
        return
    chips = []
    for s in sorted(sigs[:12], key=_sig_weight, reverse=True):
        term = explain_signal(s.name)
        fg, bg, bd = _CHIP_STYLE.get(s.direction, _CHIP_STYLE[Direction.NEUTRAL])
        layman = (term.layman or "").replace("'", "’")
        w = _sig_weight(s)
        wbadge = (f"<span style='background:{fg};color:#fff;border-radius:6px;"
                  f"padding:0 5px;margin-left:3px;font-size:0.82em;font-weight:500;"
                  f"font-variant-numeric:tabular-nums'>w&nbsp;{w:.1f}</span>")
        chips.append(
            f"<span title='{layman} · weight {w:.2f} (category × strength)' "
            f"style='display:inline-flex;align-items:center;gap:4px;"
            f"background:{bg};color:{fg};border:1px solid {bd};border-radius:999px;"
            f"padding:3px 6px 3px 11px;margin:3px 4px 0 0;font-size:0.82em;font-weight:500;"
            f"line-height:1.6;white-space:nowrap'>"
            f"{_DIR_EMOJI[s.direction]} {term.title}{wbadge}</span>")
    st.markdown("**Live signals (1D):**", help="Active intraday signals from the engine.")
    st.markdown(
        "<div style='display:flex;flex-wrap:wrap'>" + "".join(chips) + "</div>",
        unsafe_allow_html=True)


def _live_event_feed() -> None:
    feed = st.session_state.get("live_events", [])
    if not feed:
        st.caption("📋 Event feed — flips, new signals and level touches will appear here.")
        return
    st.markdown("**📋 Live event feed** _(newest first)_")
    rows = []
    for ts, e in feed[:12]:
        sign = getattr(e, "sign", 0)
        # Signal lines are colored by polarity (green up / red down); everything
        # else keeps its severity color.
        if sign > 0:
            border, txt = "#1d9e75", "#0f6e56"
        elif sign < 0:
            border, txt = "#e24b4a", "#a32d2d"
        else:
            border, txt = _SEV_COLOR.get(e.severity, "#90a4ae"), "inherit"
        rows.append(
            f"<div style='border-left:3px solid {border};padding:2px 8px;margin:2px 0;"
            f"color:{txt}'>"
            f"<span style='color:gray;font-size:0.8em'>{ts}</span> &nbsp;{e.text}</div>")
    st.markdown("".join(rows), unsafe_allow_html=True)


def _polling_section(ticker, prefer, intended: Direction, key_level: float | None) -> None:
    """Fallback: 60-second REST polling of the 1D frame (original behaviour)."""
    st.caption("Refreshes ~every 60s for up to 15 minutes, checking whether the signal "
               "*persists* — refines entry timing only, not the overall verdict.")
    ss = st.session_state
    ss.setdefault("live_ticks", [])
    ss.setdefault("live_count", 0)

    if st.button("↺ Restart live window"):
        ss.live_ticks = []
        ss.live_count = 0

    @st.fragment(run_every="60s")
    def _tick_box():
        if ss.live_count >= 15:
            st.info("Live window ended (15 minutes). Press **Restart live window** to run again.")
            _render_assurance(intended)
            return
        try:
            res = analyze_ticker(ticker, timeframes=[Timeframe.D1],
                                 prefer=None if prefer == "auto" else prefer,
                                 include_fundamentals=False, live_mode=True)
            rep = res.reports.get(Timeframe.D1)
            if rep is not None:
                ss.live_ticks.append(make_tick(rep, key_level, intended))
                ss.live_count += 1
        except Exception as exc:
            st.caption(f"live fetch skipped: {exc}")
        _render_assurance(intended)
        st.caption(f"Tick {ss.live_count}/15")

    _tick_box()


def _render_assurance(intended: Direction) -> None:
    ss = st.session_state
    a = assess(ss.live_ticks, intended)
    color = "#1b9e3e" if a.go else ("#f9a825" if a.pct >= 65 else "#9e9e9e")
    st.markdown(
        f"<div style='background:{color}22;border-left:6px solid {color};padding:10px 14px;"
        f"border-radius:8px'><b>Assurance {a.pct}%</b> · {a.message}</div>",
        unsafe_allow_html=True)


def _render_company(result) -> None:
    company = result.company
    if company is None:
        with st.expander("📊 Fundamentals, analyst & news"):
            st.caption("Set FINNHUB_KEY in .env to add fundamentals, analyst ratings, and news.")
        return
    if not company.available:
        return
    with st.expander("📊 Fundamentals, analyst & news", expanded=False):
        f = company.fundamentals
        cols = st.columns(6)
        cols[0].metric("Market cap (M)", f"{f.market_cap:,.0f}" if f.market_cap else "—")
        cols[1].metric("P/E", f"{f.pe:.1f}" if f.pe else "—")
        cols[2].metric("EPS", f"{f.eps:.2f}" if f.eps else "—")
        cols[3].metric("Beta", f"{f.beta:.2f}" if f.beta else "—")
        cols[4].metric("52w High", f"{f.high_52w:.2f}" if f.high_52w else "—")
        cols[5].metric("52w Low", f"{f.low_52w:.2f}" if f.low_52w else "—")
        av = company.analyst
        if av and av.total:
            st.write(f"**Analysts:** {av.strong_buy} strong-buy · {av.buy} buy · {av.hold} hold · "
                     f"{av.sell} sell · {av.strong_sell} strong-sell ({av.period})")
            if av.target_mean:
                st.write(f"Price target mean **{av.target_mean:.2f}** "
                         f"(low {av.target_low:.2f} / high {av.target_high:.2f})")
        if result.sentiment and result.sentiment.available:
            st.write(f"**News/analyst sentiment: {result.sentiment.score:+.2f}**")
        if company.news:
            st.markdown("**Recent news**")
            for n in company.news[:6]:
                sent = f" ({n.sentiment:+.2f})" if n.sentiment is not None else ""
                st.markdown(f"- [{n.headline}]({n.url}){sent}")


def _signals_section(result) -> None:
    """Dedicated always-visible detailed-signals block, one expander per timeframe."""
    st.subheader("🔍 Detailed signals — all timeframes")
    for tf, rep in result.reports.items():
        visible = [s for s in rep.signals if s.name != "trend"]
        label = f"{tf.value} ({len(visible)} signal{'s' if len(visible) != 1 else ''})"
        with st.expander(label, expanded=(tf == Timeframe.D1)):
            st.write(f"Trend: {_badge(rep.trend.direction)} — {rep.trend.evidence}")
            if rep.levels:
                st.write("**Key levels:** " + " · ".join(
                    f"{lv.kind} ~{lv.price:.2f}" for lv in rep.levels[:4]))
            if rep.trend_change.likely:
                st.warning(f"Possible trend change → {_badge(rep.trend_change.direction)} "
                           f"(score {rep.trend_change.score:.0%})")
            if visible:
                for s in visible:
                    term = explain_signal(s.name)
                    st.write(
                        f"- {_DIR_EMOJI[s.direction]} **{term.title}** "
                        f"(strength {s.strength:.2f}) — {term.layman}")
            else:
                st.caption("No signals fired on this timeframe.")


def _render_tabs(ticker, result) -> None:
    st.subheader("Charts")
    tabs = st.tabs([tf.value for tf in result.reports])
    for tab, (tf, rep) in zip(tabs, result.reports.items()):
        with tab:
            st.markdown(timeframe_caption(tf.value, rep))
            if tf in (Timeframe.D1, Timeframe.D5):
                st.caption("🌅🌙 Shaded bands = pre-market & after-hours (extended trading).")
            st.plotly_chart(candlestick_figure(rep, title=f"{ticker} — {tf.value}"),
                            use_container_width=True, config=_PLOTLY_CFG)
            with st.expander("ℹ️ What is this telling me?"):
                st.write(f"Trend (structure): {_badge(rep.trend.direction)} — {rep.trend.evidence}")
                if rep.levels:
                    st.write("**Key levels:** " + " · ".join(
                        f"{lv.kind} ~{lv.price:.2f}" for lv in rep.levels[:4]))
                if rep.trend_change.likely:
                    st.warning(f"Possible trend change → {_badge(rep.trend_change.direction)} "
                               f"(score {rep.trend_change.score:.0%})")


def _render_image_mode(uploaded) -> None:
    import numpy as np

    try:
        import cv2  # noqa: F401
        from stockanalyzer.vision import (ImageKind, classify_image,
                                          read_candles, read_info)
    except Exception as exc:                       # pragma: no cover
        st.error(f"Image mode needs OpenCV, which isn't available here: {exc}")
        return

    data = np.frombuffer(uploaded.getvalue(), np.uint8)
    bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if bgr is None:
        st.error("Could not decode that image.")
        return
    st.image(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), caption=uploaded.name, use_container_width=True)
    cls = classify_image(bgr)
    st.caption(f"Detected: **{cls.kind.value}** ({cls.confidence:.0%}) — {cls.reason}")
    st.warning("⚠️ Image-reading is approximate. Prefer Ticker (API) mode when possible.")

    if cls.kind == ImageKind.CHART:
        try:
            res = read_candles(bgr)
        except ValueError as exc:
            st.error(f"Could not read candles: {exc}")
            return
        st.caption(f"Recovered {res.n_candles} candles ({res.confidence:.0%}). {' '.join(res.notes)}")
        rep = analyze_timeframe(res.df)
        st.plotly_chart(candlestick_figure(rep, title=f"{uploaded.name} (from image)"),
                        use_container_width=True, config=_PLOTLY_CFG)
        st.write(f"Trend (structure): {_badge(rep.trend.direction)} — {rep.trend.evidence}")
    else:
        try:
            info = read_info(bgr)
        except RuntimeError as exc:
            st.error(str(exc))
            return
        st.caption(f"OCR backend: {info.ocr_backend}")
        if info.fields:
            st.json(info.fields)
        for hl in info.headlines:
            st.write(f"- {hl}")


def _running_in_streamlit() -> bool:
    """True when executed by the Streamlit runner (local, cloud, or AppTest)."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


def _start_keep_alive() -> None:
    """Ping this app's health endpoint every 10 min so Render free tier never idles."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not url:
        return  # not on Render, nothing to do

    import urllib.request

    def _ping() -> None:
        while True:
            time.sleep(600)  # 10 minutes
            try:
                urllib.request.urlopen(f"{url}/_stcore/health", timeout=10)
            except Exception:
                pass

    t = threading.Thread(target=_ping, daemon=True, name="keep-alive")
    t.start()


# Render whenever Streamlit runs this file — regardless of how it sets __name__.
# (A plain `python -c "import app"` has no Streamlit context, so it stays a no-op.)
if __name__ == "__main__" or _running_in_streamlit():
    _start_keep_alive()
    main()
