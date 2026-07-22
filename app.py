"""Stock Graph Analyzer — non-expert dashboard.

Enter a ticker (or pick a followed one) → fetch OHLCV across 1D…5Y → run the
technical-analysis engine → show a big price header, a plain-English recommendation
with a conviction gauge + go/no-go traffic light tailored to what you want to do
(Buy / Sell / Own), practical "what could happen next" scenarios, and the charts.

All explanations are rule-based (no AI). Educational only — not financial advice.

Run:  streamlit run app.py
"""
from __future__ import annotations

import hashlib
import json
import locale
import os
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum

import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv

import streamlit as st

from stockanalyzer import papertrade, session, swingwatch, virtualbook, watchlist
from stockanalyzer.auth import AuthError, AuthService
from stockanalyzer.user_management import UserManagementService
from stockanalyzer.config import ConfigurationError, Settings
from stockanalyzer.db.repositories.analysis_history import AnalysisHistoryRepository
from stockanalyzer.db.session import configure_engine
from stockanalyzer.analysis import reversal
from stockanalyzer.analysis.daycard import _intraday_frame, build_day_card
from stockanalyzer.analysis.engine import CATEGORY_WEIGHTS, analyze_timeframe
from stockanalyzer.explain.swing import _MIN_RR, build_swing_plan
from stockanalyzer.manage import _SPREAD_PER_SHARE
from stockanalyzer.analysis.signals import Direction
from stockanalyzer.charting import candlestick_figure
from stockanalyzer.data.realtime import RealtimeStream, summarize, ticks_to_candles
from stockanalyzer.data.resample import available_intervals, resample_ohlcv
from stockanalyzer.data.schema import Timeframe
from stockanalyzer.explain import UseCase, build_recommendation, timeframe_caption
from stockanalyzer.explain.glossary import explain_signal
from stockanalyzer.live import assess, make_tick
from stockanalyzer.live_events import LiveState, diff_states
from stockanalyzer.pipeline import analyze_ticker
from stockanalyzer.strategy import Strategy, SwingPace
from stockanalyzer.ui.history import render_history
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


def _require_login() -> bool:
    """Render the only public UI and return True for an active DB session."""
    try:
        settings = Settings.from_env()
        if not st.session_state.get("_database_configured"):
            configure_engine(settings.database_url)
            st.session_state["_database_configured"] = True
    except (ConfigurationError, RuntimeError):
        st.error("The application is not configured. Contact the administrator.")
        return False

    now = time.time()
    user_id = st.session_state.get("auth_user_id")
    last_activity = float(st.session_state.get("auth_last_activity", 0))
    current_user = None
    if (
        user_id
        and now - last_activity <= settings.auth_session_minutes * 60
    ):
        current_user = AuthService(
            settings.auth_max_failures, settings.auth_lockout_minutes
        ).browser_session_user(user_id, st.session_state.get("auth_session_revision"))
    if current_user is not None:
        st.session_state["auth_last_activity"] = now
        st.session_state["auth_is_admin"] = bool(current_user.is_admin)
        st.session_state["auth_must_change_password"] = bool(current_user.must_change_password)
        with st.sidebar:
            st.caption(f"Signed in as **{st.session_state.get('auth_username', 'user')}**")
            if st.button("Log out", use_container_width=True):
                for key in list(st.session_state):
                    if key.startswith("auth_"):
                        del st.session_state[key]
                st.rerun()
        if current_user.must_change_password:
            st.title("Change your password")
            st.info("An administrator reset your password. Choose a new password to continue.")
            with st.form("forced_password_change"):
                current = st.text_input("Current password", type="password")
                replacement = st.text_input("New password", type="password")
                confirmation = st.text_input("Confirm new password", type="password")
                submitted = st.form_submit_button("Change password", type="primary")
            if submitted:
                if replacement != confirmation:
                    st.error("New passwords must match")
                else:
                    try:
                        changed = AuthService().change_password(user_id, current, replacement)
                    except (AuthError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["auth_session_revision"] = changed.session_revision
                        st.session_state["auth_must_change_password"] = False
                        st.rerun()
            return False
        return True
    for key in list(st.session_state):
        if key.startswith("auth_"):
            st.session_state.pop(key, None)

    st.title("📈 Stock Graph Analyzer")
    st.caption("Private access — sign in to continue.")
    with st.form("login", clear_on_submit=False):
        username = st.text_input("Username", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if submitted:
        try:
            user = AuthService(settings.auth_max_failures, settings.auth_lockout_minutes).authenticate(
                username, password
            )
        except AuthError:
            st.error("Invalid username or password")
        else:
            st.session_state["auth_user_id"] = user.id
            st.session_state["auth_username"] = user.username
            st.session_state["auth_last_activity"] = now
            st.session_state["auth_session_revision"] = user.session_revision
            st.session_state["auth_is_admin"] = bool(user.is_admin)
            st.session_state["auth_must_change_password"] = bool(user.must_change_password)
            st.rerun()
    return False


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


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k.value if isinstance(k, Enum) else k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _save_analysis_history(result, verdict, strategy, pace, usecase) -> None:
    """Persist a compact decision snapshot without breaking the visible analysis."""
    try:
        frames = []
        freshness = []
        for tf, rep in result.reports.items():
            tf_name = tf.value if hasattr(tf, "value") else str(tf)
            if not rep.df.empty:
                freshness.append(str(rep.df.index[-1]))
            frames.append({
                "timeframe": tf_name,
                "bias_score": float(rep.bias_score),
                "trend_direction": rep.trend.direction.value,
                "last_close": float(rep.df["close"].iloc[-1]) if not rep.df.empty else None,
                "bar_count": len(rep.df),
                "signals": _jsonable(rep.signals),
                "levels": _jsonable(rep.levels),
                "trendlines": _jsonable(rep.trendlines),
                "trend_change": _jsonable(rep.trend_change),
            })
        identity = json.dumps(
            [st.session_state.auth_user_id, result.ticker, result.provider, freshness,
             _jsonable(verdict)], sort_keys=True, separators=(",", ":")
        )
        key = hashlib.sha256(identity.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        AnalysisHistoryRepository().save(
            st.session_state.auth_user_id, ticker=result.ticker, provider=result.provider,
            verdict=_jsonable(verdict), timeframes=frames, started_at=now,
            completed_at=now, idempotency_key=key, strategy=strategy.value,
            swing_pace=pace.value, use_case=usecase.value,
            quote=_jsonable(result.quote), sentiment=_jsonable(result.sentiment),
            errors=_jsonable(result.errors) if isinstance(result.errors, dict) else {"items": _jsonable(result.errors)},
            notices=_jsonable(result.notices),
        )
    except Exception as exc:
        st.caption(f"ℹ️ Analysis shown, but history could not be saved ({type(exc).__name__}).")


@st.cache_data(show_spinner=False, ttl=60)
def _run_fast(ticker: str, prefer: str | None):
    """Fast partial load: short timeframes only, no fundamentals. Powers the price
    header + day-trader card so they paint in ~1 fetch while the full
    multi-timeframe analysis (charts, fundamentals, sentiment) loads after. The
    short frames it fetches warm the on-disk cache, so the later full `_run`
    only pays for the longer ranges + Finnhub."""
    return analyze_ticker(
        ticker, timeframes=[Timeframe.D1, Timeframe.D5, Timeframe.M1],
        prefer=prefer, include_fundamentals=False)


def _badge(direction: Direction) -> str:
    return f"{_DIR_EMOJI[direction]} {direction.value}"


def main() -> None:
    ss = st.session_state
    ss.setdefault("ticker", "MSFT")
    ss.setdefault("submitted", False)
    ss.setdefault("page", "analyze")

    # An "Open" click (top-movers row or radar card) stashes the symbol here.
    # We apply it now — before _sidebar() instantiates the key="ticker" widget —
    # because Streamlit forbids assigning a widget-backed key once that widget
    # has been created in the same run.
    pending = ss.pop("_pending_ticker", None)
    if pending:
        ss.ticker = pending
        ss.submitted = True
        ss.page = "analyze"

    head_l, head_r = st.columns([4.2, 1])
    with head_l:
        st.title("📈 Stock Graph Analyzer")
        st.caption("Plain-English technical analysis (rules from John Murphy's *Technical "
                   "Analysis of the Financial Markets*). Rule-based, no AI. **Not financial advice.**")
    with head_r:
        # Mode switcher (top-right) — more modes will live here later.
        if ss.page == "movers":
            if st.button("📊 Analyzer", use_container_width=True,
                         help="Back to the full analyzer"):
                ss.page = "analyze"
                st.rerun()
        else:
            if st.button("🔥 Top movers", use_container_width=True,
                         help="Today's 10 most-active stocks — suggested swing candidates"):
                ss.page = "movers"
                st.rerun()

    mode, prefer, usecase, strategy, pace, uploaded, live_on, buy_price = _sidebar()

    if ss.page == "movers":
        _movers_page()
        return
    if ss.page == "history":
        render_history(st.session_state.auth_user_id)
        return
    if ss.page == "users" and st.session_state.get("auth_is_admin"):
        _users_page()
        return

    flash = ss.pop("vb_flash", None)
    if flash:
        st.success(flash + " — see the **💼 Virtual portfolio** panel below.")

    # Quiet swing radar — always visible while tickers are tracked.
    tracked = swingwatch.load(user_id=st.session_state.auth_user_id)
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
    prefer_arg = None if prefer == "auto" else prefer

    # --- Fast path: short frames only, no fundamentals → the price header and
    #     day-trader card paint immediately (Streamlit streams elements as they're
    #     created), so the user gets an actionable snapshot before the heavy
    #     multi-timeframe + fundamentals load finishes below.
    with st.spinner(f"Loading {ticker} snapshot…"):
        fast = _run_fast(ticker, prefer_arg)

    if not fast.reports:
        st.error(f"Couldn't find data for '{ticker}'. Check the symbol. ({fast.errors})")
        return

    if prefer == "twelvedata" and fast.provider != "twelvedata":
        st.info("ℹ️ TWELVEDATA_KEY isn't set — using free yfinance data instead. "
                "(Add the key to .env to use Twelve Data.)")

    _price_header(fast)
    _extended_alert(fast)
    _day_trader_card(fast)

    # Live mode renders the real-time dashboard *below* the card. It needs the full
    # context (sentiment, company, multi-timeframe verdict), so load that, then hand off.
    if live_on and RealtimeStream(ticker).available:
        with st.spinner("Loading analysis context…"):
            result = _run(ticker, prefer_arg)
        _live_dashboard(ticker, prefer, usecase, strategy, pace, buy_price, result)
        return

    # --- Full path: every timeframe + fundamentals → recommendation, company,
    #     signals, charts. Loads after the snapshot above is already on screen.
    with st.spinner(f"Loading full analysis for {ticker}…"):
        result = _run(ticker, prefer_arg)

    # Recompute the verdict for the chosen strategy (swing weights short timeframes).
    sent = result.sentiment.score if (result.sentiment and result.sentiment.available) else None
    verdict = build_verdict(result.reports, sent, strategy, pace)
    _save_analysis_history(result, verdict, strategy, pace, usecase)
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
def _users_page() -> None:
    """Admin user management; authorization is rechecked by every service call."""
    st.header("Users")
    service = UserManagementService()
    actor_id = st.session_state.auth_user_id
    with st.form("create_user"):
        username = st.text_input("New username")
        password = st.text_input("Temporary password", type="password")
        password_confirmation = st.text_input("Confirm temporary password", type="password")
        create = st.form_submit_button("Create user")
    if create:
        if password != password_confirmation:
            st.error("Temporary passwords must match")
        else:
            try:
                service.create_user(actor_id, username, password)
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.success("User created")
                st.rerun()

    try:
        users = service.list_users(actor_id)
    except PermissionError:
        st.error("Administrator access is required")
        return
    for user in users:
        cols = st.columns([3, 2, 2])
        cols[0].write(f"**{user.username}**" + (" · admin" if user.is_admin else ""))
        cols[1].write("Active" if user.is_active else "Disabled")
        desired = not user.is_active
        if cols[2].button("Enable" if desired else "Disable", key=f"active_{user.id}"):
            try:
                service.set_active(actor_id, user.id, desired)
            except (PermissionError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.rerun()
        lock_status = (
            f"Locked until {user.locked_until:%Y-%m-%d %H:%M UTC}"
            if user.locked_until else "Not locked"
        )
        st.markdown(
            f"Created: {user.created_at:%Y-%m-%d %H:%M UTC} · "
            f"Last login: {user.last_login_at:%Y-%m-%d %H:%M UTC} "
            if user.last_login_at else
            f"Created: {user.created_at:%Y-%m-%d %H:%M UTC} · Last login: Never"
        )
        st.markdown(f"Lock status: {lock_status}")
        with st.form(f"reset_{user.id}"):
            replacement = st.text_input(
                f"Temporary password for {user.username}", type="password"
            )
            reset_confirmation = st.text_input(
                f"Confirm temporary password for {user.username}", type="password"
            )
            reset = st.form_submit_button(f"Reset {user.username} password")
        if reset:
            if replacement != reset_confirmation:
                st.error("Temporary passwords must match")
            else:
                try:
                    service.reset_password(actor_id, user.id, replacement)
                except (PermissionError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.success("Password reset; existing sessions revoked")
                    st.rerun()


def _sidebar():
    ss = st.session_state
    with st.sidebar:
        nav1, nav2 = st.columns(2)
        if nav1.button("📊 Analyzer", use_container_width=True):
            ss.page = "analyze"
            st.rerun()
        if nav2.button("🕘 History", use_container_width=True):
            ss.page = "history"
            st.rerun()
        if st.session_state.get("auth_is_admin"):
            if st.button("👥 Users", use_container_width=True):
                ss.page = "users"
                st.rerun()
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
        followed = watchlist.load(user_id=st.session_state.auth_user_id)
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
        followed_now = watchlist.is_followed(ss.ticker, user_id=st.session_state.auth_user_id)
        if c2.button("★ Unfollow" if followed_now else "☆ Follow", use_container_width=True):
            watchlist.toggle(ss.ticker, user_id=st.session_state.auth_user_id)
            st.rerun()

        # --- Swing radar: quiet tracking + escalating alerts ---
        st.divider()
        st.subheader("📡 Swing radar")
        st.caption("Track tickers quietly for **daily (fast) swings** — you get a toast "
                   "when the swing score climbs past **60% → 70% → 80%**.")
        tracked_now = swingwatch.load(user_id=st.session_state.auth_user_id)
        r1c, r2c = st.columns([2, 1])
        radar_add = r1c.text_input("Add to radar", key="radar_add",
                                   placeholder="e.g. NVDA", label_visibility="collapsed")
        if r2c.button("➕ Track", use_container_width=True) and radar_add.strip():
            swingwatch.add(radar_add, user_id=st.session_state.auth_user_id)
            st.rerun()
        if tracked_now:
            cols = st.columns(min(3, len(tracked_now)))
            for i, sym in enumerate(tracked_now):
                if cols[i % len(cols)].button(f"✕ {sym}", key=f"radar_rm_{sym}",
                                              use_container_width=True,
                                              help=f"Stop tracking {sym}"):
                    swingwatch.remove(sym, user_id=st.session_state.auth_user_id)
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
        st.toggle("⏱ Show frame timings", value=False, key="show_perf",
                  help="Measure how long each live-mode redraw takes (p50/p95 per "
                       "fragment). Use it to tune the refresh cadences: any "
                       "fragment whose p95 exceeds its interval is the reason the "
                       "UI feels stuck.")
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
                        "stoch_k", "stoch_d", "atr",
                        "adx", "plus_di", "minus_di", "bb_upper", "bb_lower"):
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


def _price_of(res) -> float | None:
    """Last price from an already-loaded AnalysisResult."""
    if res is None:
        return None
    try:
        if res.quote:
            return float(res.quote.price)
        for tf in (Timeframe.D1, Timeframe.D5, Timeframe.M1):
            rep = res.reports.get(tf)
            if rep is not None:
                return float(rep.meta.get("last_close", 0)) or None
    except Exception:
        pass
    return None


def _quiet_price(tk: str) -> float | None:
    try:
        return _price_of(_run_quiet(tk))
    except Exception:
        return None


def _virtual_buy_button(plan, ticker: str, key_suffix: str = "",
                        reports=None, verdict=None, rec=None) -> None:
    """The 'simulate this trade' button — with optional manual stop/target override."""
    if plan.kind == "no_trade" or not ticker:
        return
    pending = plan.kind == "breakout_wait"
    k = f"{ticker}_{key_suffix}"

    if virtualbook.has_open(ticker, "me", user_id=st.session_state.auth_user_id):
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
            horizon_days=3 if "1–3" in plan.horizon else 10, stake=amount,
            snapshot=snap, user_id=st.session_state.auth_user_id)
        msg = (f"Armed: fills if {ticker} crosses ${entry:.2f}" if pending
               else f"Bought {ticker} at ${entry:.2f} "
                    f"(stop ${stop:.2f} / target ${target:.2f})")
        st.toast(f"💼 {msg} — ${amount:,.0f} stake", icon="📒")
        st.session_state["vb_flash"] = f"💼 Virtual position opened in {ticker}."
        st.rerun()
    st.caption("Auto-closes at stop/target · tracked in the **💼 Virtual portfolio** "
               "panel at the top with your stake and P&L.")


def _is_extended(p) -> bool:
    """The plan's own 'too stretched / not real demand' verdict (emerging path)."""
    es = getattr(p, "emerging", None)
    return bool(es and (es.extended or "distribution_risk" in es.flags))


# --------------------------------------------------------------------------- #
# Gap-and-Go (opening-range breakout) — Carter-style intraday rules.
# The decision logic lives in stockanalyzer.analysis.orb (pure + testable); this
# module keeps the Streamlit/session wiring and delegates the rules to it, so the
# live radar and daytrade_backtest.py share one code path.
# --------------------------------------------------------------------------- #
from stockanalyzer.analysis import orb as _orb  # noqa: E402

_GAP_MIN_PCT       = _orb.GAP_MIN_PCT
_OPENING_RANGE_MIN = _orb.OPENING_RANGE_MIN
_MIN_BREAKOUT_RVOL = _orb.MIN_BREAKOUT_RVOL
_ORB_STOP_BUFFER   = _orb.ORB_STOP_BUFFER
_ORB_TARGET_R      = _orb.ORB_TARGET_R

# Radar tiers → recompute cadence (seconds). Base fragment tick = 5s, so a far
# buildup recomputes ~1/12 as often as a hot name — CPU spent where it matters.
_RADAR_TIERS = {"HOT": 5, "WATCH_CLOSE": 10, "BUILDUP": 25, "PAUSED": 10, "FAR": 60}


def _radar_tier(plan, price, has_open: bool, phase: str, orange, orb_window: bool) -> str:
    """Cadence + state for one radar ticker. Gap-and-Go aware:
       opening_range -> PAUSED (range forming, NO entries); a GAP-UP inside the
       9:45–11:30 window -> escalate toward the OR-High breakout; everything else
       -> standard swing-score tiering."""
    if has_open:
        return "HOT"                              # always babysit an open position
    if phase in ("premarket", "closed"):
        return "FAR"
    if phase == "opening_range":
        return "PAUSED"                           # 9:30–9:45 ET: no entries
    # --- ORB branch: ONLY for a real gap-up, ONLY in the morning window ---
    if (orb_window and orange and orange.get("gap_up")
            and price and plan is not None):
        or_high = orange["high"]
        band = max(0.015, 0.6 * (plan.daily_atr_pct / 100.0))
        dist = or_high / price - 1                # >0 = still below the breakout
        if price >= or_high:
            return "HOT"                          # broke OR-High (entry still needs vol)
        if 0 <= dist <= band:
            return "WATCH_CLOSE"
        if 0 <= dist <= 2 * band:
            return "BUILDUP"
        return "FAR"
    # --- fallback: standard swing-score tiering (non-gappers / afternoon) ---
    if plan is None:
        return "FAR"
    if plan.go:
        return "HOT"
    band = max(0.015, 0.6 * (plan.daily_atr_pct / 100.0))
    trig_near = (plan.kind == "breakout_wait" and plan.trigger and price
                 and 0 <= plan.trigger / price - 1 <= band)
    near_rung = any(0 < (lv - plan.score) <= 3 for lv in swingwatch.LEVELS)
    if trig_near or near_rung:
        return "WATCH_CLOSE"
    if plan.light == "forming" or plan.kind == "breakout_wait":
        return "BUILDUP"
    return "FAR"


def _opening_range(tk: str, res) -> dict | None:
    """Cache the first-15-min High/Low + gap for a ticker, once per trading day.

    Returns ``None`` until the opening range is complete (or when there's no
    intraday data). Keyed by ET date in ``ss.opening_range`` so it resets daily.
    """
    if res is None:
        return None
    ss = st.session_state
    ss.setdefault("opening_range", {})
    ts = session.now_et()
    day = ts.strftime("%Y-%m-%d")
    cur = ss.opening_range.get(tk)
    if cur and cur.get("day") == day:
        return cur
    df = _intraday_frame(getattr(res, "reports", None))
    prev_close = res.quote.prev_close if (res.quote and res.quote.prev_close) else None
    try:
        rng = _orb.opening_range(df, prev_close, ts)
    except Exception:
        return None
    if rng is None:
        return None
    stored = rng.as_dict()
    ss.opening_range[tk] = stored
    return stored


def _gap_snapshot(tk: str, orange: dict, rvol: float | None,
                  entry: float, stop: float, target: float) -> dict:
    """Minimal decision snapshot for a Gap-and-Go trade. The shared setup/kind +
    identical entry/stop/target make ``bot-gap-mgd`` and ``bot-gap-fixed`` collapse
    to one ``cohort_id`` (the A/B pairing)."""
    rr = round((target - entry) / (entry - stop), 1) if entry > stop else _ORB_TARGET_R
    return dict(
        setup="gap_and_go_orb", kind="immediate", score=None, label="Gap-and-Go",
        rr=rr, daily_atr_pct=None,
        guidance=(f"ORB break of ${orange['high']:.2f} on "
                  f"{(rvol or 0):.1f}×vol — hard stop below OR-low ${orange['low']:.2f}, "
                  f"target ${target:.2f} (2R)."))


def _run_gap_bots(tk: str, reports, orange: dict | None, price: float | None) -> None:
    """Open the Gap-and-Go pair when the ORB rules line up: a real gap-up, inside
    the 9:45–11:30 window, price breaking the opening-range HIGH on >1.5× volume.
    Hard stop sits just below the opening-range LOW (Carter's ORB rule)."""
    if not (orange and orange.get("gap_up") and price and reports):
        return
    df = _intraday_frame(reports)
    dec = _orb.gap_and_go_signal(_orb.OpeningRange(**orange), df,
                                 float(price), session.now_et())
    if not dec.fired:
        return
    entry, init_stop, target, rvol = dec.entry, dec.stop, dec.target, dec.rvol
    snap = _gap_snapshot(tk, orange, rvol, entry, init_stop, target)
    for trader, managed in (("bot-gap-fixed", False), ("bot-gap-mgd", True)):
        try:
            if not virtualbook.has_open(
                    tk, trader, user_id=st.session_state.auth_user_id):
                virtualbook.open_position(
                    ticker=tk, trader=trader, entry=entry, stop=init_stop,
                    target=target, kind="immediate", horizon_days=1,
                    managed=managed, entry_rvol=rvol, init_stop=init_stop,
                    snapshot=snap, user_id=st.session_state.auth_user_id)
                st.toast(f"🚀 {trader} opened Gap-and-Go {tk} @ ${entry:.2f} "
                         f"(stop ${init_stop:.2f}, {(rvol or 0):.1f}×vol)", icon="🚀")
        except Exception:
            pass


_BOTS = (
    # (name, condition, uses pending trigger)  — contrasting strategies so the
    # data shows which rules actually make money.
    ("bot-GO",  lambda p: p.go, False),
    # bot-70 now respects the engine's own guardrails: a high score on a
    # with-trend momentum name, NOT an extended blowoff it should never chase.
    ("bot-70",  lambda p: p.score >= 70 and p.kind == "immediate" and not p.go
                and p.actionable and not _is_extended(p), False),
    ("bot-BRK", lambda p: p.kind == "breakout_wait" and p.score >= 55, True),
)


def _run_bots(ticker: str, plan, reports=None, verdict=None, rec=None,
              orange=None, price=None, phase=None) -> None:
    """Auto virtual-traders: each bot opens (at most one) position per ticker
    when its rule matches the current plan.

    Every swing bot is gated on the plan's own verdict: a non-actionable plan, an
    extended/distribution emerging read — these are watch-only and never become
    a position. This is the chokepoint that stops the radar from buying the very
    setups the engine flagged as bad (the SPCX/INTC/NOK class).

    The Gap-and-Go bots have their own ORB entry logic (gap + window + OR-High
    break + volume). No bot opens during the opening-range freeze (first 15 min)."""
    if phase == "opening_range":
        return                                    # 15-min freeze: no entries
    _run_gap_bots(ticker, reports, orange, price)
    if not getattr(plan, "actionable", True) or _is_extended(plan):
        return
    for name, cond, _pending in _BOTS:
        try:
            if cond(plan) and not virtualbook.has_open(ticker, name, user_id=st.session_state.auth_user_id):
                virtualbook.open_position(
                    ticker=ticker, trader=name, entry=plan.entry, stop=plan.stop,
                    target=plan.target1, kind=plan.kind, trigger=plan.trigger,
                    horizon_days=3,
                    snapshot=_plan_snapshot(plan, reports, verdict, rec),
                    user_id=st.session_state.auth_user_id)
                st.toast(f"🤖 {name} opened a virtual position in {ticker} "
                         f"(score {plan.score}%)", icon="🤖")
        except Exception:
            pass


def _reversal_snapshot(dec) -> dict:
    """Minimal decision snapshot for a reversal-at-support day trade — one row per
    variant so each risk profile is its own cohort in the trade book."""
    return dict(
        setup="reversal_support", kind="immediate", score=None,
        label=f"Reversal @ support ({dec.variant})", rr=dec.rr, daily_atr_pct=None,
        guidance=(f"{dec.variant}: {dec.reason} — hard stop ${dec.stop:.2f}, "
                  f"target ${dec.target:.2f}."))


def _run_reversal_bots(tk: str, res, price: float | None, phase=None) -> None:
    """Fast reversal-at-support day-trade family. Each variant fires its own bot
    (own cohort) so the trade book proves which risk profile wins — nothing is
    re-weighted here. Shares the ORB freeze: no entries in the first 15 min. The
    fired decisions are stashed for the radar card to surface."""
    fired: list = []
    if phase != "opening_range" and res is not None and price:
        reports = res.reports
        card = build_day_card(reports, price)
        if card is not None:
            higher_trend = None
            for tf in (Timeframe.M1, Timeframe.D5, Timeframe.M6):
                rep = reports.get(tf)
                if rep is not None:
                    higher_trend = rep.trend.direction
                    break
            prev_close = res.quote.prev_close if (res.quote and res.quote.prev_close) else None
            idf = _intraday_frame(reports)
            now = session.now_et()
            for cfg in reversal.REVERSAL_VARIANTS:
                try:
                    dec = reversal.reversal_signal(card, idf, float(price),
                                                   higher_trend, prev_close, now, cfg)
                except Exception:
                    continue
                if not dec.fired:
                    continue
                fired.append(dec)
                try:
                    if not virtualbook.has_open(
                            tk, dec.variant, user_id=st.session_state.auth_user_id):
                        virtualbook.open_position(
                            ticker=tk, trader=dec.variant, entry=dec.entry,
                            stop=dec.stop, target=dec.target, kind="immediate",
                            horizon_days=1, snapshot=_reversal_snapshot(dec),
                            user_id=st.session_state.auth_user_id)
                        st.toast(f"🔄 {dec.variant} opened reversal {tk} "
                                 f"@ ${dec.entry:.2f} (stop ${dec.stop:.2f}, "
                                 f"tgt ${dec.target:.2f})", icon="🔄")
                except Exception:
                    pass
    st.session_state.setdefault("radar_reversals", {})[tk] = fired


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


def _radar_decision_rep(res):
    """The frame the fast-pace radar plan is built on (shortest available)."""
    for tf in (Timeframe.D5, Timeframe.D1, Timeframe.M1):
        rep = res.reports.get(tf)
        if rep is not None:
            return rep
    return None


def _radar_plan(res, sentiment: float | None = None):
    rep = _radar_decision_rep(res)
    if rep is None:
        return None
    inv = round((build_verdict(res.reports, sentiment).score + 1) / 2 * 100)
    return build_swing_plan(rep, UseCase.BUY, SwingPace.FAST,
                            all_reports=res.reports,
                            context={"investor_pct": inv, "sentiment": sentiment})


def _plan_brief(plan) -> tuple[str, str, str]:
    """(color, state label, instruction) — the one-line read of a swing plan.

    Four-state ladder, weakest → strongest:
    ⚪ NO SETUP → 🟡 BUILDUP (setup forming, no trigger yet) →
    👀 WATCH CLOSE (buildup seen — confirm with a close past the trigger) → 🟢 GO.
    """
    if plan.go:
        return ("#1b9e3e", "🟢 GO",
                f"Enter ${plan.entry:.2f} · stop ${plan.stop:.2f} · "
                f"tgt ${plan.target1:.2f} · R:R {plan.rr:.1f}:1")
    if plan.kind == "breakout_wait":
        return ("#fb8c00", "👀 WATCH CLOSE",
                f"Buildup seen — needs a close above ${plan.trigger:.2f} "
                f"→ tgt ${plan.target1:.2f}")
    if plan.light == "forming":
        return ("#f9a825", "🟡 BUILDUP", f"{plan.setup} — forming, no trigger yet")
    return ("#546e7a", "⚪ NO SETUP", "no clean setup right now")


_TIER_BADGE = {"HOT": "🔥 live", "WATCH_CLOSE": "👁 watch", "BUILDUP": "… building",
               "PAUSED": "⏸ paused", "FAR": "💤 idle"}


def _radar_card(tk: str, plan, res=None, tier: str | None = None) -> None:
    """One glance = one decision: ticker + live price/change on top, the state
    pill + score next, then trend / signal-ratio / volatility, then the action."""
    color, state, instr = _plan_brief(plan)
    bells = "🔔" * sum(1 for lv in swingwatch.LEVELS if plan.score >= lv)

    px_html = ""
    info_bits: list[str] = []
    if res is not None:
        q = res.quote
        if q is not None:
            # Lead with the day move (regular_close vs prior close) — the same
            # headline number the detail header shows — not the extended wiggle.
            c, arrow = _chg_style(q.day_change_pct)
            moon = " 🌙" if q.session == "after-hours" else (" 🌅" if q.session == "pre-market" else "")
            px_html = (f"<span style='font-weight:700'>${q.price:,.2f}</span> "
                       f"<span style='color:{c};font-size:0.85em;font-weight:600'>"
                       f"{arrow}{q.day_change_pct:+.1f}%</span>"
                       f"<span style='font-size:0.8em'>{moon}</span>")
        dec = _radar_decision_rep(res)
        m1 = res.reports.get(Timeframe.M1)
        trend_bits = []
        if dec is not None:
            trend_bits.append(f"5D{_DIR_EMOJI[dec.trend.direction]}")
        if m1 is not None and m1 is not dec:
            trend_bits.append(f"1M{_DIR_EMOJI[m1.trend.direction]}")
        if trend_bits:
            info_bits.append("trend " + " ".join(trend_bits))
        if dec is not None:
            bull = sum(1 for s in dec.signals
                       if s.name != "trend" and s.direction == Direction.BULL)
            bear = sum(1 for s in dec.signals
                       if s.name != "trend" and s.direction == Direction.BEAR)
            info_bits.append(f"signals 🟢{bull}·🔴{bear}")
    info_bits.append(f"vol {plan.daily_atr_pct:.1f}%/d")
    if tier:
        info_bits.append(_TIER_BADGE.get(tier, tier))

    revs = st.session_state.get("radar_reversals", {}).get(tk) or []
    rev_html = ""
    if revs:
        d = revs[0]
        names = ", ".join(r.variant for r in revs)
        rev_html = (
            f"<div style='font-size:0.8em;margin-top:3px;color:#00897b;"
            f"font-weight:600'>🔄 Reversal @ support ${d.support:.2f} — "
            f"enter ${d.entry:.2f} · stop ${d.stop:.2f} · tgt ${d.target:.2f} · "
            f"R:R {d.rr:.1f} <span style='color:#8a93a0;font-weight:400'>"
            f"({names})</span></div>")

    st.markdown(
        f"<div style='background:{color}26;border:1px solid {color};border-radius:10px;"
        f"padding:8px 10px;margin:2px 0'>"
        f"<div style='display:flex;justify-content:space-between;align-items:baseline'>"
        f"<b style='font-size:1.15em'>{tk}</b><span>{px_html}</span></div>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin:3px 0'>"
        f"<span style='background:{color};color:#fff;border-radius:5px;padding:1px 8px;"
        f"font-weight:700;font-size:0.82em;white-space:nowrap'>{state}</span>"
        f"<span style='color:{color};font-weight:700'>{plan.score}% {bells}</span></div>"
        f"<div style='font-size:0.78em;color:#8a93a0'>{' · '.join(info_bits)}</div>"
        f"<div style='font-size:0.8em;margin-top:2px'>{instr}</div>{rev_html}</div>",
        unsafe_allow_html=True)
    if st.button(f"🔎 Open {tk}", key=f"radar_open_{tk}", use_container_width=True):
        st.session_state._pending_ticker = tk   # applied at top of main()
        st.rerun(scope="app")


_RADAR_REFRESH_S = 5


def _radar_panel(tracked: list[str]) -> None:
    @st.fragment(run_every=f"{_RADAR_REFRESH_S}s")
    def _radar():
        with _timed("radar", _RADAR_REFRESH_S):
            _radar_body(tracked)

    _radar()
    _journal_panel()
    st.divider()


def _radar_body(tracked: list[str]) -> None:
    ss = st.session_state
    ss.setdefault("radar_cache", {})     # tk -> {res, plan, orange, tier}
    ss.setdefault("radar_due", {})        # tk -> next recompute epoch
    now = time.time()
    ts_et = session.now_et()
    phase = session.market_phase(ts_et)
    orb_window = session.is_orb_window(ts_et)

    # Drop state for tickers no longer tracked (keep the dicts bounded).
    for k in list(ss.radar_cache):
        if k not in tracked:
            ss.radar_cache.pop(k, None)
            ss.radar_due.pop(k, None)

    head_l, head_r = st.columns([3, 1])
    head_l.markdown("#### 📡 Swing radar — adaptive (Gap-and-Go aware)")
    sort_pct = head_r.toggle("Sort by %", value=True, key="radar_sort",
                             help="Highest swing score first. Off = the order "
                                  "you added them.")

    items: list[tuple] = []
    for tk in tracked:
        has_open = virtualbook.has_any_open(
            tk, user_id=st.session_state.auth_user_id)
        due = ss.radar_due.get(tk, 0) <= now
        if due:                          # only recompute the heavy analysis when due
            try:
                res = _run_quiet(tk)
                plan = (_radar_plan(res, _quiet_sentiment(tk))
                        if res.reports else None)
            except Exception:
                res, plan = None, None
            orange = _opening_range(tk, res) if res is not None else None
            price = res.quote.price if (res is not None and res.quote) else None
            tier = _radar_tier(plan, price, has_open, phase, orange, orb_window)
            ss.radar_cache[tk] = {"res": res, "plan": plan,
                                  "orange": orange, "tier": tier}
            ss.radar_due[tk] = now + _RADAR_TIERS.get(tier, 60)
        cached = ss.radar_cache.get(tk) or {}
        items.append((tk, cached.get("res"), cached.get("plan"),
                      cached.get("tier", "FAR"), cached.get("orange"),
                      has_open, due))
    if sort_pct:
        items.sort(key=lambda it: it[2].score if it[2] is not None else -1,
                   reverse=True)

    cols = st.columns(min(4, max(1, len(tracked))))
    for i, (tk, res, plan, tier, orange, has_open, due) in enumerate(items):
        with cols[i % len(cols)]:
            if plan is None:
                st.caption(f"{tk}: no data")
                continue
            # Notices + trading only when we recomputed this tick, or when we
            # already hold a position (babysit it every tick, any tier).
            if due or has_open:
                fired = swingwatch.claim_notice(
                    tk, plan.score, user_id=st.session_state.auth_user_id)
                if fired:
                    stored = papertrade.record(dict(
                        ts=time.time(), date=time.strftime("%x %X"),
                        ticker=tk, level=fired[0], score=plan.score,
                        label=plan.score_label, kind=plan.kind, setup=plan.setup,
                        entry=plan.entry, stop=plan.stop, target=plan.target1,
                        rr=plan.rr, trigger=plan.trigger, horizon_days=3,
                        guidance=plan.guidance, status="open", result_pct=0.0),
                        user_id=st.session_state.auth_user_id)
                    note = " · 📒 recorded for paper trading" if stored else ""
                    st.toast(f"📡 {tk}: {fired[1]} — {plan.guidance[:80]}{note}", icon="🔔")
                # `res` is already in hand — going back through _quiet_price
                # would re-enter st.cache_data and pay a full unpickle of a
                # seven-timeframe AnalysisResult just to read one float.
                px = _price_of(res)
                reports = res.reports if res is not None else None
                _run_bots(tk, plan, reports=reports, orange=orange,
                          price=px, phase=phase)
                _run_reversal_bots(tk, res, px, phase)
                if px:
                    dec = _radar_decision_rep(res) if res is not None else None
                    tc = getattr(dec, "trend_change", None)
                    virtualbook.manage(
                        tk, px, reports, tc,
                        user_id=st.session_state.auth_user_id)   # before mark
                    for chg in virtualbook.mark(
                            tk, px, user_id=st.session_state.auth_user_id):
                        if chg["status"] == "closed":
                            st.toast(f"💼 {chg['trader']} closed {tk}: "
                                     f"{chg['close_reason']} ({chg['pnl_pct']:+.1f}%)",
                                     icon="💼")
                        else:
                            st.toast(f"💼 {chg['trader']}'s breakout order filled in {tk}",
                                     icon="🚀")
            _radar_card(tk, plan, res, tier=tier)
    st.caption("Adaptive scan — imminent setups refresh ~5–10s, far buildups ~60s "
               "(saves CPU). First 15 min after the open = ⏸ PAUSED, no entries. "
               "States: ⚪ NO SETUP → 🟡 BUILDUP → 👀 WATCH CLOSE → 🟢 GO · "
               "🔔 = score reached 60 / 70 / 80%.")


# --------------------------------------------------------------------------- #
# Top movers — suggested tickers from the most-active screener.
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False, ttl=300)
def _fetch_movers(count: int = 10):
    from stockanalyzer.data.movers import most_active
    return most_active(count)


@st.cache_data(show_spinner=False, ttl=300)
def _movers_trends(symbols: tuple[str, ...]) -> dict[str, str]:
    """1M structural trend per ticker (light fetch, threaded across tickers)."""
    from concurrent.futures import ThreadPoolExecutor

    def job(tk: str) -> tuple[str, str]:
        try:
            res = analyze_ticker(tk, timeframes=[Timeframe.M1],
                                 include_fundamentals=False)
            rep = res.reports.get(Timeframe.M1)
            return tk, (_badge(rep.trend.direction) if rep is not None else "—")
        except Exception:
            return tk, "—"

    with ThreadPoolExecutor(max_workers=5) as pool:
        return dict(pool.map(job, symbols))


@st.cache_data(show_spinner=False, ttl=120)
def _movers_plans(symbols: tuple[str, ...]) -> dict:
    """Fast-pace swing plan per ticker — the radar's read, run on demand."""
    from concurrent.futures import ThreadPoolExecutor

    def job(tk: str):
        try:
            res = analyze_ticker(tk, include_fundamentals=False)
            return tk, (_radar_plan(res) if res.reports else None)
        except Exception:
            return tk, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(job, symbols))


def _fmt_volume(v: int | None) -> str:
    if not v:
        return "—"
    if v >= 1e9:
        return f"{v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{v / 1e6:.0f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return str(v)


def _movers_page() -> None:
    ss = st.session_state
    st.markdown("### 🔥 Most active today — suggested tickers")
    st.caption("The 10 highest-volume US stocks right now (Yahoo screener). Heavy volume "
               "is where swings happen — add candidates to the **📡 swing radar**, or run "
               "the radar here for an immediate swing read on all of them. "
               "List refreshes every ~5 min.")

    movers = _fetch_movers()
    if not movers:
        st.error("Couldn't fetch the most-active list right now — the screener may be "
                 "briefly unavailable. Try again in a minute.")
        if st.button("↻ Retry"):
            _fetch_movers.clear()
            st.rerun()
        return

    b1, b2 = st.columns([1.6, 1])
    if b1.button("⚡ Run radar — quick swing read on all 10", type="primary",
                 use_container_width=True,
                 help="Runs the swing-radar scan (fast 1–3 day pace) on every "
                      "suggested ticker and shows the immediate suggestion."):
        ss.movers_scan = True
    if b2.button("↻ Refresh list", use_container_width=True):
        _fetch_movers.clear()
        _movers_trends.clear()
        _movers_plans.clear()
        ss.movers_scan = False
        st.rerun()

    with st.spinner("Reading 1-month trends…"):
        trends = _movers_trends(tuple(m.symbol for m in movers))

    plans: dict = {}
    if ss.get("movers_scan"):
        with st.spinner("Running the swing radar on all tickers…"):
            plans = _movers_plans(tuple(m.symbol for m in movers))

    tracked = set(swingwatch.load(user_id=st.session_state.auth_user_id))
    for m in movers:
        with st.container(border=True):
            cols = st.columns([2.0, 1.1, 1.1, 0.9, 1.3, 1.0, 1.0, 1.0])
            cols[0].markdown(
                f"**{m.symbol}**<br><span style='color:gray;font-size:0.8em'>"
                f"{m.name[:30]}</span>", unsafe_allow_html=True)
            cols[1].markdown(f"**${m.price:,.2f}**" if m.price is not None else "—")
            if m.change_pct is not None:
                up = m.change_pct >= 0
                color, arrow = ("#1b9e3e", "▲") if up else ("#e53935", "▼")
                cols[2].markdown(f"<span style='color:{color};font-weight:600'>"
                                 f"{arrow} {m.change_pct:+.2f}%</span>",
                                 unsafe_allow_html=True)
            else:
                cols[2].markdown("—")
            cols[3].markdown(f"{_fmt_volume(m.volume)}<br>"
                             f"<span style='color:gray;font-size:0.75em'>volume</span>",
                             unsafe_allow_html=True)
            cols[4].markdown(f"{trends.get(m.symbol, '—')}<br>"
                             f"<span style='color:gray;font-size:0.75em'>1M trend</span>",
                             unsafe_allow_html=True)
            if m.symbol in tracked:
                cols[5].button("✓ On radar", key=f"mv_radar_{m.symbol}",
                               disabled=True, use_container_width=True)
            elif cols[5].button("➕ Radar", key=f"mv_radar_{m.symbol}",
                                use_container_width=True,
                                help=f"Track {m.symbol} on the swing radar "
                                     "(60/70/80% alerts)"):
                swingwatch.add(m.symbol, user_id=st.session_state.auth_user_id)
                st.toast(f"📡 {m.symbol} added to the swing radar", icon="➕")
                st.rerun()
            if cols[6].button("🔎 Open", key=f"mv_open_{m.symbol}",
                              use_container_width=True,
                              help=f"Full analysis of {m.symbol}"):
                ss._pending_ticker = m.symbol   # applied at top of main()
                st.rerun()
            cols[7].link_button("📈 Yahoo",
                                f"https://finance.yahoo.com/quote/{m.symbol}/",
                                use_container_width=True,
                                help=f"Open {m.symbol} on Yahoo Finance (new tab)")

            if ss.get("movers_scan"):
                plan = plans.get(m.symbol)
                if plan is None:
                    st.caption("⚪ No swing read available (data fetch failed).")
                else:
                    color, head, instr = _plan_brief(plan)
                    st.markdown(
                        f"<div style='background:{color}26;border:1px solid {color};"
                        f"border-radius:8px;padding:6px 10px;margin-top:2px'>"
                        f"<b style='color:{color}'>{head}</b> · score {plan.score}% · "
                        f"{plan.score_label} — <span style='font-size:0.9em'>{instr}</span>"
                        f"<div style='font-size:0.8em;color:#bbb'>{plan.guidance}</div></div>",
                        unsafe_allow_html=True)

    st.caption("⚡ Swing reads use the radar's fast 1–3 day pace · suggestions are "
               "rule-based, **not financial advice.**")


def _pnl_style(df, pct_cols=(), usd_cols=(), price_cols=()):
    """Return a pandas Styler: green for gains, red for losses on P&L columns."""

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
    if not virtualbook.load(user_id=st.session_state.auth_user_id):
        with st.expander("💼 Virtual portfolio — empty (use the 📒 Virtual BUY button)",
                         expanded=False):
            st.info("No virtual positions yet. On any actionable plan press "
                    "**📒 Virtual BUY** to open a $1,000 paper position (stop + target "
                    "attached). Bots **bot-GO / bot-70 / bot-BRK** also trade automatically "
                    "as the radar scans, so the report card fills up on its own.")
        return

    @st.fragment(run_every="30s")
    def _folio():

        book = virtualbook.load(user_id=st.session_state.auth_user_id)
        live = [p for p in book if p["status"] in ("open", "pending")]
        # Mark holdings to fresh prices (auto-close stop/target hits).
        prices: dict[str, float] = {}
        for tk in {p["ticker"] for p in live}:
            px = _quiet_price(tk)
            if px:
                prices[tk] = px
                for chg in virtualbook.mark(tk, px, user_id=st.session_state.auth_user_id):
                    if chg["status"] == "closed":
                        st.toast(f"💼 {chg['trader']} closed {tk}: {chg['close_reason']} "
                                 f"({chg['pnl_pct']:+.1f}%)", icon="💼")
        book = virtualbook.load(user_id=st.session_state.auth_user_id)
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
            # 💰 Bottom line — gross $ won, $ lost, and net, in plain dollars.
            done = [p for p in book if p["status"] == "closed"
                    and p.get("close_reason") != "cancelled"]
            won = sum(p["pnl_usd"] for p in done if p["pnl_usd"] > 0)
            lost = sum(p["pnl_usd"] for p in done if p["pnl_usd"] <= 0)   # ≤0, negative
            net = won + lost
            line = (f"### 💰 Bottom line: net **${net:+,.0f}**\n"
                    f"Won **+${won:,.0f}** · Lost **−${abs(lost):,.0f}** "
                    f"over **{len(done)}** closed trades")
            if live:
                line += f" · open unrealized **${unreal:+,.0f}**"
            st.markdown(line)
            st.divider()
            if live:
                st.markdown("**Live positions** _($1,000 virtual stake each)_")
                rows = []
                for p in live:
                    cur = prices.get(p["ticker"])
                    upnl = ((cur / p["entry"] - 1) * 100) if (cur and p["status"] == "open") else None
                    udollar = (upnl / 100 * p.get("stake", 1000.0)) if upnl is not None else None
                    rows.append({
                        "ticker": p["ticker"], "trader": p["trader"],
                        "status": ("🔓 retest" if (p["status"] == "pending" and p.get("broke_out_ts"))
                                   else "⏳ armed" if p["status"] == "pending" else "📈 open"),
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
                    closed = virtualbook.close_position(
                        pid, px or (pos or {}).get("entry", 0), user_id=st.session_state.auth_user_id)
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

                # Algorithm correctness — the deduped view. The book copies one
                # idea across bot-GO/bot-70/bot-BRK/me, which inflates the sample;
                # this collapses each plan to a single idea so the numbers judge
                # the engine's CALLS, not how many bots echoed them.
                try:
                    algo = virtualbook.algorithm_correctness(user_id=st.session_state.auth_user_id)
                    at = algo["totals"]
                    if at["n_ideas"]:
                        st.markdown(
                            f"**🧭 Algorithm correctness** — {at['n_ideas']} distinct "
                            f"ideas (from {at['n_trades']} bot-trades) · "
                            f"idea win-rate **{at['win_rate']}%** · "
                            f"avg **{at['avg_pnl_pct']:+.2f}%** per idea")
                        idea_setup = [{"setup": k, "ideas": v["n_ideas"],
                                       "wins": v["wins"], "losses": v["losses"],
                                       "win_rate": v["win_rate"],
                                       "avg_pnl_pct": v["avg_pnl_pct"]}
                                      for k, v in algo["setups"].items()]
                        idea_band = [{"score band": k, "ideas": v["n_ideas"],
                                      "win_rate": v["win_rate"],
                                      "avg_pnl_pct": v["avg_pnl_pct"]}
                                     for k, v in algo["bands"].items()]
                        a1, a2 = st.columns(2)
                        with a1:
                            st.caption("By setup — one vote per idea")
                            st.dataframe(_pnl_style(pd.DataFrame(idea_setup),
                                                    pct_cols=["avg_pnl_pct"]),
                                         use_container_width=True)
                        with a2:
                            st.caption("By score band — does a higher score win more?")
                            st.dataframe(_pnl_style(pd.DataFrame(idea_band),
                                                    pct_cols=["avg_pnl_pct"]),
                                         use_container_width=True)
                        st.caption("Same idea taken by several bots counts once "
                                   "here — so a duplicated call can't masquerade as "
                                   "a winning streak. Compare with the per-trader "
                                   "cards above to see which bot *rule* earns its place.")
                except Exception:
                    pass

                # Managed vs fixed — is the Gap-and-Go bot SMARTER? Paired on the
                # same ORB entry (shared cohort_id), so this isolates the exit
                # policy: breakeven/EMA8-trail/test-don't-dump vs fixed stop/target.
                try:
                    ab = virtualbook.managed_vs_fixed(
                        user_id=st.session_state.auth_user_id)
                    if ab["n_pairs"]:
                        delta = ab["mean_delta_pct"]
                        st.markdown(
                            f"**🤖 Managed vs fixed (Gap-and-Go)** — {ab['n_pairs']} "
                            f"matched pair(s) · mean Δ **{delta:+.2f}%** "
                            f"(managed − fixed) · win% mgd **{ab['mgd_win_rate']}%** "
                            f"vs fixed **{ab['fixed_win_rate']}%** · avg "
                            f"{ab['avg_stop_moves']} stop-moves · mean MFE "
                            f"{ab['mean_mfe_pct']:+.2f}%")
                        verdict = ("smarter ✅" if delta > 0 else
                                   "not yet better ⚠️" if delta < 0 else "even")
                        st.caption(f"Management looks **{verdict}** on this sample. "
                                   "Small n — collect more before concluding "
                                   "(see algolab/LEARNINGS.md H5).")
                except Exception:
                    pass

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
    recs = papertrade.load(user_id=st.session_state.auth_user_id)
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
            recs = papertrade.evaluate_all(frames, user_id=st.session_state.auth_user_id)
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


def _chg_style(chg: float) -> tuple[str, str]:
    """(color, arrow) for a signed change — one place so every surface agrees."""
    up = chg >= 0
    return ("#1b9e3e" if up else "#e53935"), ("▲" if up else "▼")


_SESSION_BADGE = {
    "pre-market": "<span style='background:#5b3a86;color:#fff;padding:2px 8px;"
                  "border-radius:6px;font-size:0.7em'>🌅 PRE-MARKET</span>",
    "after-hours": "<span style='background:#2b4a86;color:#fff;padding:2px 8px;"
                   "border-radius:6px;font-size:0.7em'>🌙 AFTER-HOURS</span>",
}


def _quote_header_html(title: str, q, current_price: float | None = None,
                       status_html: str = "") -> str:
    """The one price-header renderer, shared by the fast and live headers.

    Everything displayed derives from the ``Quote`` (the root of trust). The only
    per-surface input is ``current_price`` — the fast header passes the quote's
    latest print, the live header passes the streaming tick — so the two never
    disagree about what a move means. During pre/after-hours it renders two lines
    (regular-session day move on top, extended-hours print below, Yahoo-style);
    otherwise a single line of the current price vs the prior close.
    """
    price = q.price if current_price is None else current_price
    reg, prev = q.regular_close, q.prev_close

    if q.is_extended and reg is not None and prev is not None:
        dcolor, darrow = _chg_style(q.day_change)
        ext_chg = (price - reg) if price is not None else 0.0
        ext_pct = (ext_chg / reg * 100) if reg else 0.0
        ecolor, earrow = _chg_style(ext_chg)
        badge = _SESSION_BADGE.get(q.session, "")
        return (
            "<div style='display:flex;align-items:baseline;gap:16px;flex-wrap:wrap'>"
            f"<span style='font-size:1.4em;font-weight:600'>{title}</span>"
            f"<span style='font-size:2.0em;font-weight:700'>${reg:,.2f}</span>"
            f"<span style='font-size:1.2em;color:{dcolor};font-weight:600'>"
            f"{darrow} {q.day_change:+,.2f} ({q.day_change_pct:+.2f}%)</span>"
            "<span style='color:#888;font-size:0.9em'>at close</span>"
            f"{status_html}</div>"
            "<div style='display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-top:2px'>"
            f"{badge}"
            f"<span style='font-size:1.1em;font-weight:600'>${price:,.2f}</span>"
            f"<span style='color:{ecolor};font-weight:600'>"
            f"{earrow} {ext_chg:+,.2f} ({ext_pct:+.2f}%) vs close</span></div>"
        )

    # Regular session (or no close reference): current price vs the prior close.
    chg = (price - prev) if (price is not None and prev is not None) else q.day_change
    pct = q.pct_from_prev(price)
    color, arrow = _chg_style(chg)
    return (
        "<div style='display:flex;align-items:baseline;gap:16px;flex-wrap:wrap'>"
        f"<span style='font-size:1.4em;font-weight:600'>{title}</span>"
        f"<span style='font-size:2.0em;font-weight:700'>${price:,.2f}</span>"
        f"<span style='font-size:1.2em;color:{color};font-weight:600'>"
        f"{arrow} {chg:+,.2f} ({pct:+.2f}%)</span>{status_html}</div>"
    )


def _price_header(result) -> None:
    q = result.quote
    name = result.company.fundamentals.name if (result.company and result.company.available) else ""
    title = f"{result.ticker}" + (f" — {name}" if name else "")
    if q is None:
        st.subheader(title)
        return
    st.markdown(_quote_header_html(title, q), unsafe_allow_html=True)
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


def _day_trader_card(result) -> None:
    """⚡ Focused day-trader snapshot for a quick action decision: today's range,
    buyer/seller battle zones, the intraday bull/bear split, and the nearest
    resistance target. Built from frames already fetched, so it renders instantly."""
    price = result.quote.price if result.quote else None
    card = build_day_card(result.reports, price)
    if card is None:
        return

    with st.container(border=True):
        head_l, head_r = st.columns([3, 1.5])
        head_l.markdown("### ⚡ Day-trader card")
        if card.day_change_pct is not None:
            up = card.day_change_pct >= 0
            c, arrow = ("#1b9e3e", "▲") if up else ("#e53935", "▼")
            head_r.markdown(
                f"<div style='text-align:right'>"
                f"<span style='font-size:1.4em;font-weight:700'>${card.current_price:,.2f}</span><br>"
                f"<span style='color:{c};font-weight:600'>{arrow} {card.day_change_pct:+.2f}% "
                f"<span style='color:gray;font-size:0.8em'>since open</span></span></div>",
                unsafe_allow_html=True)
        else:
            head_r.markdown(f"<div style='text-align:right;font-size:1.4em;font-weight:700'>"
                            f"${card.current_price:,.2f}</div>", unsafe_allow_html=True)

        # Buyers-vs-sellers split: who controlled the session's volume.
        if card.bull_pct is not None:
            bull, bear = card.bull_pct, card.bear_pct
            bull_lbl = f"{bull:.0f}% buyers" if bull >= 18 else ""
            bear_lbl = f"{bear:.0f}% sellers" if bear >= 18 else ""
            st.markdown(
                "<div style='display:flex;height:24px;border-radius:6px;overflow:hidden;"
                "font-size:0.74em;font-weight:700;color:#fff;margin:2px 0 8px'>"
                f"<div style='width:{bull}%;background:#1b9e3e;display:flex;align-items:center;"
                f"justify-content:center'>{bull_lbl}</div>"
                f"<div style='width:{bear}%;background:#e53935;display:flex;align-items:center;"
                f"justify-content:center'>{bear_lbl}</div></div>",
                unsafe_allow_html=True)

        # Compact metric grid — custom HTML (not st.metric, whose oversized value
        # font truncates dollar prices in a narrow column).
        def _cell(label: str, value: str, sub: str, value_color: str = "") -> str:
            vc = f"color:{value_color};" if value_color else ""
            return (f"<div style='flex:1 1 120px;min-width:108px'>"
                    f"<div style='font-size:0.76em;color:#8a93a0'>{label}</div>"
                    f"<div style='font-size:1.08em;font-weight:700;{vc}white-space:nowrap'>{value}</div>"
                    f"<div style='font-size:0.76em;color:#8a93a0'>{sub}</div></div>")

        if card.session_low is not None and card.session_high is not None:
            span = (f"{card.session_range_pct:.1f}% span"
                    if card.session_range_pct is not None else "today")
            range_cell = _cell("Today's range",
                               f"${card.session_low:,.2f} – ${card.session_high:,.2f}", span)
        else:
            range_cell = _cell("Today's range", "—", "")

        if card.next_target is not None:
            tgt_cell = _cell("🎯 Next target", f"${card.next_target:,.2f}",
                             f"{card.next_target_pct:+.1f}% to resistance", "#1b9e3e")
        else:
            tgt_cell = _cell("🎯 Next target", "—", "clear air above")

        if card.next_support is not None:
            sup_cell = _cell("🛡️ Support", f"${card.next_support:,.2f}",
                             f"{card.next_support_pct:+.1f}% to floor", "#e53935")
        else:
            sup_cell = _cell("🛡️ Support", "—", "no floor on range")

        bias_color = {"Bullish": "#1b9e3e", "Bearish": "#e53935"}.get(card.bias_label, "#8a93a0")
        bias_sub = (f"{card.bull_pct:.0f}% buy / {card.bear_pct:.0f}% sell"
                    if card.bull_pct is not None else "—")
        bias_cell = _cell("Day bias", card.bias_label, bias_sub, bias_color)

        st.markdown(
            "<div style='display:flex;gap:14px;flex-wrap:wrap;margin:2px 0 4px'>"
            + range_cell + tgt_cell + sup_cell + bias_cell + "</div>",
            unsafe_allow_html=True)

        # Where price sits in today's range — the day trader's "am I near the
        # high (breakout watch) or the low (bounce watch)?" glance.
        if card.range_position_pct is not None:
            pos = card.range_position_pct
            near = ("near the high — breakout watch" if pos >= 75
                    else "near the low — bounce watch" if pos <= 25
                    else "mid-range")
            st.markdown(
                f"<div style='margin:8px 0 2px;font-size:0.82em'>Position in today's range: "
                f"<b>{pos:.0f}%</b> <span style='color:#8a93a0'>· {near}</span></div>"
                "<div style='position:relative;height:10px;border-radius:5px;"
                "background:linear-gradient(90deg,#e5393544,#9e9e9e22,#1b9e3e44)'>"
                f"<div style='position:absolute;left:{pos}%;top:-4px;width:4px;height:18px;"
                "background:#ff9800;border-radius:2px;transform:translateX(-50%)'></div></div>",
                unsafe_allow_html=True)

        if card.battle_zones:
            chips = "".join(
                f"<span style='background:#8e44ad22;border:1px solid #8e44ad;"
                f"border-radius:5px;padding:2px 8px;margin-right:6px;font-size:0.92em'>"
                f"${z.price:,.2f} · {z.volume_pct:.0f}% vol</span>"
                for z in card.battle_zones)
            st.markdown(
                "<div style='margin-top:6px'><b>🥊 Battle zones</b> "
                "<span style='color:gray;font-size:0.85em'>"
                "(where buyers &amp; sellers fought hardest — heaviest traded prices):</span><br>"
                f"<div style='margin-top:4px'>{chips}</div></div>",
                unsafe_allow_html=True)

        sup_s = " · ".join(f"${p:,.2f}" for p in card.supports) or "—"
        res_s = " · ".join(f"${p:,.2f}" for p in card.resistances) or "—"
        bars_note = (f"{card.n_session_bars} session bars" if card.intraday
                     else "daily data (intraday unavailable)")
        st.caption(f"🟩 **Support below:** {sup_s}  　🟥 **Resistance above:** {res_s}  ·  "
                   f"levels from history + today · {bars_note} · for quick reads, **not advice**")


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
                       stop: float, target: float,
                       spread: float = _SPREAD_PER_SHARE) -> None:
    """Show P&L at current price, at stop, and at target — all relative to buy
    price and NET of the flat $/share spread, so 'break-even' is honest."""
    sp = spread / buy_price * 100 if buy_price else 0.0   # $/share fee as % of cost
    pnl_now  = (current - buy_price) / buy_price * 100 - sp
    pnl_stop = (stop    - buy_price) / buy_price * 100 - sp
    pnl_tgt  = (target  - buy_price) / buy_price * 100 - sp
    locked   = stop >= buy_price + spread   # stop covers cost + spread → real profit

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
                   f"~${buy_price + spread:.2f} (cost + spread) to make this trade risk-free.")
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

    # Non-GO plans get watch framing, never broker instructions — a forming
    # breakout below the R:R gate dressed up as an order ticket is how the
    # NOK $16.95 plan happened. OWN keeps its protective orders regardless.
    if not getattr(plan, "actionable", True) and usecase != UseCase.OWN:
        st.markdown("**👀 Watch plan — conditions not met yet, no orders to place**")
        if plan.kind == "breakout_wait":
            trig = plan.trigger or plan.entry
            word = "above" if long_side else "below"
            steps = [
                f"1️⃣ **Set an alert** just {word} **${trig:.2f}** — the breakout trigger.",
                f"2️⃣ **If a session *closes* {word} it**, re-run the analysis — the plan "
                f"only becomes actionable if R:R clears {_MIN_RR:g}:1 at that point.",
                "3️⃣ **No position before the close confirms** — arming orders here "
                "front-runs an unconfirmed level.",
            ]
        else:
            steps = [
                f"1️⃣ **Set alerts** at **${plan.entry:.2f}** (entry zone) and "
                f"**${plan.stop:.2f}** (invalidation).",
                "2️⃣ **Re-check when one fires** — the failing gate is named in the "
                "guidance line above.",
            ]
        for s in steps:
            st.write(s)
        st.caption("Levels are reference points, not broker instructions — the GO gate "
                   "isn't met. · **not financial advice.**")
        return

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

# Live mode runs as three sibling fragments, each redrawing only as fast as its
# contents actually change. The old single 1s fragment rebuilt everything —
# including two full plotly figures — every second, which is more work than one
# second of wall clock, so ticks queued and the chart appeared frozen.
#
#   FAST (1s)  price header. Pure HTML, no figures — this is what must feel live.
#   MID  (3s)  heartbeat figure, bot marking, flip detection, event feed.
#   SLOW (15s) plan, orders guide, the main candlestick chart, signal chips.
#
# A 5-minute candle does not need redrawing every second; the price above it does.
_LIVE_FAST_S = 1
_LIVE_MID_S = 3
_LIVE_SLOW_S = 15


class _timed:
    """Record how long a fragment body took, so the cadences above can be tuned
    from evidence instead of guesswork.

    Keeps the last 40 samples per section in session state; `_perf_panel` renders
    p50/p95. A fragment whose p95 exceeds its own run_every is the thing to fix —
    that is precisely the condition that makes redraws queue up and the UI stall.
    """

    def __init__(self, name: str, budget_s: float | None = None):
        self.name, self.budget_s = name, budget_s

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        ms = (time.perf_counter() - self._t0) * 1000
        ss = st.session_state
        ss.setdefault("_perf", {})
        rec = ss._perf.setdefault(self.name, {"ms": [], "budget_s": self.budget_s})
        rec["ms"] = (rec["ms"] + [ms])[-40:]
        return False


def _perf_panel() -> None:
    """Opt-in timing readout for live mode (sidebar toggle)."""
    perf = st.session_state.get("_perf") or {}
    if not perf:
        st.caption("No timings yet — let live mode run for a few seconds.")
        return
    # Plain markdown, not st.dataframe: this is a four-row table, and the grid
    # widget renders to a canvas that can't be read off the page (or copied out
    # of a bug report).
    lines = ["| fragment | n | p50 ms | p95 ms | budget ms | |",
             "|---|--:|--:|--:|--:|---|"]
    for name, rec in perf.items():
        s = sorted(rec["ms"])
        if not s:
            continue
        p50 = s[len(s) // 2]
        p95 = s[min(len(s) - 1, int(len(s) * 0.95))]
        budget_ms = (rec["budget_s"] or 0) * 1000
        over = budget_ms and p95 > budget_ms
        lines.append(f"| {name} | {len(s)} | {p50:.0f} | {p95:.0f} | "
                     f"{budget_ms:.0f} | {'⚠️ over' if over else '✅ ok'} |")
    st.markdown("\n".join(lines))
    st.caption("A fragment whose **p95 exceeds its budget** cannot finish before "
               "its next scheduled run — that backlog is what makes the UI feel "
               "stuck. Lower its cadence or move work out of it.")


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
        ss.live_shared = None
        # Chart view caches are per-ticker; drop them so a new symbol reframes
        # rather than inheriting the previous one's viewport.
        ss._chart_focus = {}
        ss._resample_memo = {}

    sent = result.sentiment.score if (result.sentiment and result.sentiment.available) else None
    ctx = dict(ticker=ticker, prefer=prefer, usecase=usecase, strategy=strategy, pace=pace,
               buy_price=buy_price, sent=sent, result=result, max_secs=max_secs)

    # Seed the shared state before any fragment runs, so the fast header never
    # paints against a half-built dashboard on the very first frame.
    if ss.get("live_shared") is None:
        _refresh_live_shared(ctx)

    @st.fragment(run_every=f"{_LIVE_FAST_S}s")
    def _fast():
        with _timed("fast · price header", _LIVE_FAST_S):
            _live_fast_frame(ctx)

    @st.fragment(run_every=f"{_LIVE_MID_S}s")
    def _mid():
        with _timed("mid · pulse/bots/feed", _LIVE_MID_S):
            _live_mid_frame(ctx)

    @st.fragment(run_every=f"{_LIVE_SLOW_S}s")
    def _slow():
        with _timed("slow · chart/plan", _LIVE_SLOW_S):
            _live_slow_frame(ctx)

    _fast()
    _mid()
    _slow()
    if ss.get("show_perf"):
        with st.expander("⏱ Live-mode frame timings", expanded=False):
            _perf_panel()


def _live_pulse(ctx: dict) -> dict:
    """Everything derivable from the tick stream alone — no engine, no network.

    Cheap enough to run at 1s: read the ring buffer, take the last price, note
    whether the session has hit its auto-stop.
    """
    ss = st.session_state
    stream = ss.get("rt_stream")
    now = time.time()
    elapsed = now - ss.get("rt_started_at", now)
    ended = ctx["max_secs"] is not None and elapsed >= ctx["max_secs"]
    if ended and stream is not None:
        stream.stop()

    snap = stream.snapshot() if stream is not None else None
    shared = ss.get("live_shared") or {}
    live_price = None
    if snap and snap.ticks:
        live_price = snap.ticks[-1].price
    if live_price is None and ctx["result"].quote:
        live_price = ctx["result"].quote.price
    if live_price is None and shared.get("d1") is not None:
        live_price = shared["d1"].meta.get("last_close")
    return {"snap": snap, "live_price": live_price, "elapsed": elapsed,
            "ended": ended, "now": now}


def _refresh_live_shared(ctx: dict, pulse: dict | None = None) -> dict:
    """Re-run the engine and rebuild both recommendation framings.

    This is the expensive half of live mode — `analyze_ticker` is a blocking
    network fetch — so it is confined to the SLOW fragment and gated to
    `_ENGINE_REFRESH_S`. The result lands in `ss.live_shared`, which the faster
    fragments read without ever touching the network themselves.
    """
    ss = st.session_state
    ticker = ctx["ticker"]
    now = time.time()
    pulse = pulse or {}
    live_price = pulse.get("live_price")

    eng = ss.get("live_engine")
    if eng is None:
        ss.live_engine = eng = {"reports": dict(ctx["result"].reports), "ts": now}
    elif now - eng["ts"] > _ENGINE_REFRESH_S and not pulse.get("ended"):
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

    if live_price is None and d1 is not None:
        live_price = d1.meta.get("last_close")

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

    primary = swing_rec if ctx["strategy"] == Strategy.SWING else inv_rec
    shared = {
        "reports": reports, "d1": d1, "eng_ts": eng["ts"],
        "swing_verdict": swing_verdict, "swing_rec": swing_rec, "inv_rec": inv_rec,
        "plan": swing_rec.swing, "key_level": _key_level(primary, ctx["usecase"]),
    }
    ss.live_shared = shared
    return shared


# --------------------------------------------------------------------------- #
# FAST (1s) — the price, and nothing but the price.
# --------------------------------------------------------------------------- #
def _live_fast_frame(ctx: dict) -> None:
    p = _live_pulse(ctx)
    engine_age = int(time.time() - (st.session_state.get("live_shared") or {}).get(
        "eng_ts", time.time()))
    _live_header(ctx, p["snap"], p["live_price"], p["elapsed"], engine_age, p["ended"])


# --------------------------------------------------------------------------- #
# MID (3s) — the live pulse: heartbeat, bots, flips, feed.
# --------------------------------------------------------------------------- #
def _live_mid_frame(ctx: dict) -> None:
    ss = st.session_state
    shared = ss.get("live_shared")
    if not shared:
        return
    p = _live_pulse(ctx)
    snap, live_price, now = p["snap"], p["live_price"], p["now"]
    ticker = ctx["ticker"]
    plan, d1 = shared["plan"], shared["d1"]

    if snap and len(snap.ticks) >= 2:
        _live_heartbeat(snap.ticks)

    # Flip detection → toast + event feed.
    cur = _build_live_state(shared["swing_rec"], shared["inv_rec"], d1, live_price,
                            plan, shared["key_level"])
    events = diff_states(ss.get("live_prev_state"), cur)
    ss.live_prev_state = cur
    if events:
        feed = ss.get("live_events", [])
        for e in events:
            feed.insert(0, (time.strftime("%X"), e))
        ss.live_events = feed[:20]
        top_ev = max(events, key=lambda e: _SEV_RANK.get(e.severity, 0))
        st.toast(top_ev.text, icon="⚡")

    # Virtual book: bots act on the analyzed ticker too. Mark every ~2s when we
    # hold a position (ASAP stop/target/trend reaction) else ~10s; manage runs
    # BEFORE mark so a freshly-tightened stop can close on the same tick.
    mark_gap = 2 if virtualbook.has_any_open(
        ticker, user_id=st.session_state.auth_user_id) else 10
    if plan is not None and live_price and now - ss.get("vb_last_mark", 0) > mark_gap:
        ss.vb_last_mark = now
        phase = session.market_phase()
        orange = _opening_range(ticker, ctx["result"])
        _run_bots(ticker, plan, reports=shared["reports"], verdict=shared["swing_verdict"],
                  rec=shared["swing_rec"], orange=orange, price=live_price, phase=phase)
        tc = d1.trend_change if d1 is not None else None
        virtualbook.manage(
            ticker, live_price, shared["reports"], tc,
            user_id=st.session_state.auth_user_id)
        for chg in virtualbook.mark(
                ticker, live_price, user_id=st.session_state.auth_user_id):
            if chg["status"] == "closed":
                st.toast(f"💼 {chg['trader']} closed {ticker}: {chg['close_reason']} "
                         f"({chg['pnl_pct']:+.1f}%)", icon="💼")
            else:
                st.toast(f"💼 {chg['trader']}'s breakout order filled in {ticker}", icon="🚀")

    if ctx["buy_price"] and plan is not None and live_price:
        _cost_basis_block(ctx["buy_price"], live_price, plan.stop, plan.target1)
    _live_event_feed()


# --------------------------------------------------------------------------- #
# SLOW (15s) — structure: the plan, the orders guide, the candlestick chart.
# --------------------------------------------------------------------------- #
def _live_slow_frame(ctx: dict) -> None:
    p = _live_pulse(ctx)
    shared = _refresh_live_shared(ctx, pulse=p)
    snap, live_price = p["snap"], p["live_price"]
    plan, d1 = shared["plan"], shared["d1"]
    stats = summarize(snap.ticks) if (snap and snap.ticks) else None

    _live_decision_strip(shared["swing_rec"], shared["inv_rec"], d1, ctx["pace"])
    if plan is not None:
        _swing_score_block(plan)
        _orders_guide(plan, ctx["usecase"],
                      live_momentum=(stats.momentum if stats else None),
                      reports=shared["reports"], verdict=shared["swing_verdict"],
                      rec=shared["swing_rec"])
    _live_chart(ctx["ticker"], d1, plan, shared["key_level"], live_price, snap,
                reports=shared["reports"])
    _live_signal_chips(d1)
    st.caption(f"Price updates ~{_LIVE_FAST_S}s · pulse/bots ~{_LIVE_MID_S}s · "
               f"chart & plan ~{_LIVE_SLOW_S}s · signals re-run ~{_ENGINE_REFRESH_S}s · "
               "the multi-timeframe verdict is structural · **not financial advice.**")


def _live_header(ctx, snap, live_price, elapsed, engine_age, ended) -> None:
    result = ctx["result"]
    q = result.quote
    name = result.company.fundamentals.name if (result.company and result.company.available) else ""
    title = ctx["ticker"] + (f" — {name}" if name else "")
    connected = snap.connected if snap else False
    dot = "🟢 LIVE" if connected and not ended else ("⏹ stopped" if ended else "🟡 connecting…")
    status_html = f"<span style='font-size:0.9em;color:#888'>{dot}</span>"
    # Same root of trust as the fast header — the live tick is just the current
    # price fed in, so the day / extended split is computed identically.
    if q is None:
        price_txt = f"${live_price:,.2f}" if live_price else "—"
        st.markdown(
            "<div style='display:flex;align-items:baseline;gap:16px;flex-wrap:wrap'>"
            f"<span style='font-size:1.4em;font-weight:600'>{title}</span>"
            f"<span style='font-size:2.0em;font-weight:700'>{price_txt}</span>{status_html}</div>",
            unsafe_allow_html=True)
    else:
        st.markdown(_quote_header_html(title, q, current_price=live_price,
                                       status_html=status_html), unsafe_allow_html=True)

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


def _compute_focus(df, frac: float = 0.4, keep_prices=None):
    """The initial viewport: the most recent `frac` of bars, with the price
    y-axis framed to the candles ACTUALLY VISIBLE in that window (plus any nearby
    keep_prices — live/entry/stop). Returns (x0, x1, y0, y1) or None.

    Pure: it reads the frame and returns numbers, so the caller can cache the
    result and re-apply the *same* viewport on every redraw."""
    n = len(df)
    if n < 8:
        return None
    i0 = int(n * (1 - frac))
    x0, x1 = df.index[i0], df.index[-1]

    win = df.iloc[i0:]
    if not len(win):
        return None
    lo, hi = float(win["low"].min()), float(win["high"].max())
    if hi <= lo:
        return None
    span = hi - lo
    # Pull in nearby reference prices, but never let a far one (e.g. a distant
    # target) re-expand the scale — clamp to ~0.5×span beyond.
    for p in (keep_prices or []):
        if p and lo - 0.5 * span <= p <= hi + 0.5 * span:
            lo, hi = min(lo, p), max(hi, p)
    pad = (hi - lo) * 0.08
    return x0, x1, lo - pad, hi + pad


def _apply_focus(fig, focus) -> None:
    if focus is None:
        return
    x0, x1, y0, y1 = focus
    # The price panel's x-axis is a *follower* (shared_xaxes matches it to the
    # bottom anchor axis), so setting the range on row=1 alone gets overridden by
    # the anchor's autorange. Set every x-axis so the anchor takes the range too.
    fig.update_xaxes(range=[x0, x1])
    fig.update_yaxes(range=[y0, y1], autorange=False, row=1, col=1)


def _stable_focus(view_key: str, df, keep_prices=None):
    """`_compute_focus`, but computed once per view and then held constant.

    Recomputing the window on every redraw was the chart's worst live habit: the
    range is derived from the last 40% of bars, so it crept on every new bar and
    the whole plot visibly drifted underneath the user. Pinning it per
    (ticker, timeframe, interval) means redraws are visually still, and plotly's
    uirevision can keep any pan/zoom the user applied on top."""
    ss = st.session_state
    ss.setdefault("_chart_focus", {})
    hit = ss._chart_focus.get(view_key)
    if hit is None:
        hit = _compute_focus(df, keep_prices=keep_prices)
        ss._chart_focus[view_key] = hit
    return hit


def _resampled_report(ticker, rep, tf, minutes):
    """Re-run the engine on rolled-up candles, memoized per (ticker, frame,
    interval, last bar).

    The roll-up itself is cheap, but `analyze_timeframe` recomputes every
    indicator — so without the memo a 15m view would re-derive RSI/MACD/levels on
    every redraw even when no new bar had printed. `ticker` belongs in the key:
    two symbols can easily share a bar count and a last timestamp, and without it
    one would be served the other's candles."""
    ss = st.session_state
    ss.setdefault("_resample_memo", {})
    df = rep.df
    key = f"{ticker}|{tf.value}"
    sig = (minutes, len(df), df.index[-1])
    hit = ss._resample_memo.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        out = analyze_timeframe(resample_ohlcv(df, minutes))
    except Exception:
        return rep              # a view option must never break the chart
    ss._resample_memo[key] = (sig, out)
    return out


def _chart_interval(ticker, rep, tf):
    """Candle-size picker for intraday frames. Returns (report, minutes).

    Coarser candles are exact integer roll-ups of the frame we already fetched
    (see data/resample.py), so switching interval costs no network call and no
    rate-limit budget."""
    ss = st.session_state
    choices = available_intervals(rep.df)
    if len(choices) < 2:
        return rep, (choices[0] if choices else None)

    native = choices[0]
    ss.setdefault("live_chart_interval", native)
    if ss.live_chart_interval not in choices:
        ss.live_chart_interval = native
    labels = [f"{m}m" for m in choices]
    cur = f"{ss.live_chart_interval}m"
    picker = getattr(st, "segmented_control", None) or getattr(st, "pills", None)
    if picker is not None:
        chosen = picker("Candle size", labels, default=cur,
                        key="live_chart_interval_pick", label_visibility="collapsed")
    else:
        chosen = st.radio("Candle size", labels, index=labels.index(cur),
                          horizontal=True, key="live_chart_interval_pick",
                          label_visibility="collapsed")
    minutes = choices[labels.index(chosen)] if chosen in labels else native
    ss.live_chart_interval = minutes
    if minutes == native:
        return rep, minutes
    return _resampled_report(ticker, rep, tf, minutes), minutes


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
    ivl_col, reset_col = st.columns([5, 1])
    with ivl_col:
        rep, minutes = _chart_interval(ticker, rep, tf)
    is_live = tf == Timeframe.D1
    ivl_txt = f"{minutes}-min" if minutes else "daily"
    suffix = f"live (1D · {ivl_txt})" if is_live else f"{tf.value} · {ivl_txt}"
    # The title doubles as plotly's uirevision key, so it must change when the
    # view changes (interval/timeframe switch = fresh framing) and stay constant
    # otherwise (redraw = keep the user's pan/zoom).
    fig = candlestick_figure(rep, title=f"{ticker} — {suffix}")

    # Plan overlays + the live price line belong on the live intraday frame only;
    # on the structural frames they'd be off-scale clutter. The plan levels (your
    # trade) get left-edge pills; market structure + the live price stay on the
    # right (drawn by the chart itself), so the two never fight for the same gutter.
    if is_live:
        # Risk/reward zones behind the candles: a green REWARD band (entry→target)
        # and a red RISK band (entry→stop) — the trade's geometry at a glance.
        if plan is not None and plan.entry:
            if plan.target1 and plan.target1 != plan.entry:
                fig.add_hrect(y0=plan.entry, y1=plan.target1,
                              fillcolor="rgba(38,166,154,0.12)", line_width=0,
                              layer="below", row=1, col=1)
            if plan.stop and plan.stop != plan.entry:
                fig.add_hrect(y0=plan.stop, y1=plan.entry,
                              fillcolor="rgba(239,83,80,0.12)", line_width=0,
                              layer="below", row=1, col=1)
        plan_lines = []
        if plan is not None:
            if plan.trigger:
                plan_lines.append((plan.trigger, "#ff9800", "dot", f"🚀 {plan.trigger:.2f}"))
            plan_lines += [
                (plan.entry, "#42a5f5", "dash", f"entry {plan.entry:.2f}"),
                (plan.stop, "#ef5350", "dash", f"stop {plan.stop:.2f}"),
                (plan.target1, "#26a69a", "dash", f"target {plan.target1:.2f}"),
            ]
        for y, c, dash, text in plan_lines:
            fig.add_hline(y=y, line=dict(color=c, width=1.3, dash=dash), row=1, col=1)
            fig.add_annotation(
                xref="x domain", x=0.0, xanchor="left", xshift=4,
                yref="y", y=y, yanchor="middle", showarrow=False,
                text=f" {text} ", font=dict(size=10.5, color="#0e1117"),
                bgcolor=c, borderpad=1, row=1, col=1)
        if live_price:
            fig.add_hline(y=live_price, line=dict(color="#ffeb3b", width=1.4), row=1, col=1)
            fig.add_annotation(
                xref="x domain", x=0.0, xanchor="left", xshift=4,
                yref="y", y=live_price, yanchor="middle", showarrow=False,
                text=f" ● LIVE {live_price:.2f} ", font=dict(size=11, color="#0e1117"),
                bgcolor="#ffeb3b", borderpad=1, row=1, col=1)

    keep = None
    if is_live:
        keep = [live_price]
        if plan is not None:
            keep += [plan.entry, plan.stop]
    view_key = f"{ticker}|{tf.value}|{minutes}"
    if reset_col.button("⤢", key="chart_reset_view", help="Re-frame the chart on "
                        "the latest candles", use_container_width=True):
        st.session_state.get("_chart_focus", {}).pop(view_key, None)
    _apply_focus(fig, _stable_focus(view_key, rep.df, keep_prices=keep))
    st.plotly_chart(fig, use_container_width=True, config=_PLOTLY_CFG)


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
    if _require_login():
        main()
