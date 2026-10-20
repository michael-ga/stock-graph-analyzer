"""Intraday replay — step a past trading day forward bar by bar, with no hindsight.

This is the headless half of the replay simulator (the Streamlit page is
``replay_page.py``). It answers one question honestly: *what did the engine
actually know at 10:15, and what would it have done?* Every frame handed to every
bot is reconstructed as-of that bar via ``analysis.asof`` — the same functions
``daytrade_backtest.py`` uses, so a replay and a backtest cannot disagree.

The expensive work happens once: ``precompute_day`` walks 09:45 → 15:55 and
freezes each bar's decision into an immutable ``ReplayBar``. Stepping, scrubbing
and playback are then pure lookups, which is what makes the chart animate
smoothly instead of stalling on ~120 ms of indicator math per frame.

Trades go to a **separate** SQLite book (see ``replay_db_path``) so practice runs
can never contaminate the real trade record the algorithm is judged on.

No Streamlit import lives here on purpose — everything below is unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re

import pandas as pd

from stockanalyzer import virtualbook
from stockanalyzer.analysis import asof, orb, reversal
from stockanalyzer.analysis.daycard import DayCard, build_day_card
from stockanalyzer.data.schema import Timeframe, validate_ohlcv
from stockanalyzer.explain import snapshot
from stockanalyzer.explain.swing import build_swing_plan, is_extended
from stockanalyzer.explain.usecase import UseCase
from stockanalyzer.strategy import SwingPace
from stockanalyzer.verdict.aggregate import build_verdict

# Replay window: the opening range is complete at 09:45, and 15:55 is the last
# bar that can still be traded before the close.
REPLAY_START, REPLAY_END = "09:45", "15:55"

# Setups that are momentum plays — a mean-reversion/value veto on one of these is a
# "contradiction" (the thing the setup-isolation bypass would target).
_MOMENTUM_SETUPS = {"gap_and_go_orb", "Momentum / gap", "Breakout (volume)"}
# Score checks that are mean-reversion / value in spirit.
_MEANREV_CHECKS = ("Bollinger", "overextended", "No chase", "Buying low",
                   "Trend strength")

# Bot identities. Deliberately the *same* trader names the live radar uses
# (app.py) — isolation comes from the separate DB file, so identical names make a
# replay scorecard directly comparable, row for row, with the live one.
GAP_BOTS = (("bot-gap-fixed", False), ("bot-gap-mgd", True))

# The swing family, mirroring app.py's `_BOTS` exactly — contrasting strategies so
# the book shows which rules actually earn their place. `bot-BRK` is the only one
# that arms a *pending* order: `open_position` sets status="pending" whenever the
# kind is breakout_wait and a trigger exists, and `mark` then decides whether the
# break confirms and pulls back far enough to fill.
SWING_BOTS = (
    ("bot-GO", lambda b: b.go),
    # bot-70 respects the engine's own guardrails: a high score on a with-trend
    # momentum name, NOT an extended blowoff it should never chase.
    ("bot-70", lambda b: b.score >= 70 and b.kind == "immediate" and not b.go
               and b.actionable and not b.extended),
    ("bot-BRK", lambda b: b.kind == "breakout_wait" and b.score >= 55),
)
SWING_BOT = SWING_BOTS[0][0]        # kept for callers that only mean bot-GO

# The auto-place toggle keys the UI owns.
BOT_KEYS = ("orb", "rev", "swing")


# --------------------------------------------------------------------------- #
# Per-bar records.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BotVerdicts:
    """Every bot's read at one bar — including *why* it stood aside.

    The blocking reason matters more than the fire/no-fire flag: it's what turns
    the replay into a teaching tool instead of a slot machine.
    """
    orb: orb.GapGoDecision
    reversals: tuple[reversal.ReversalDecision, ...]   # one per REVERSAL_VARIANTS
    swing_go: bool
    swing_block: str                    # "" when the swing plan says GO
    contradiction: str | None           # momentum setup vetoed by a mean-reversion gate

    @property
    def fired_reversals(self) -> tuple[reversal.ReversalDecision, ...]:
        return tuple(d for d in self.reversals if d.fired)

    @property
    def any_fired(self) -> bool:
        return self.orb.fired or bool(self.fired_reversals) or self.swing_go


@dataclass(frozen=True)
class ReplayBar:
    """One 5-minute bar plus the complete decision as of its close.

    ``d1`` is the as-of trailing-2-day intraday report. It is kept (rather than
    recomputed) because it is built during precompute anyway, and it is what the
    chart draws — so rendering a frame costs a dict lookup, not 120 ms of
    indicator math.
    """
    i: int
    t: pd.Timestamp
    epoch: float                        # the `now=` every book call uses
    open: float
    high: float
    low: float
    close: float
    volume: float
    d1: object                          # TimeframeReport — the chart source
    card: DayCard | None
    # Collapsed swing-plan scalars (the SwingPlan itself holds report refs).
    setup: str
    kind: str
    go: bool
    score: int
    rr: float
    entry: float
    stop: float
    target: float
    trigger: float | None
    actionable: bool
    extended: bool                      # the plan's own "too stretched" veto
    light: str
    horizon: str
    earned: int
    denom: int
    lost: tuple[tuple[str, int], ...]   # (failed check, weight lost), worst first
    snapshot: dict                      # ready to hand to open_position
    bots: BotVerdicts

    @property
    def clock(self) -> str:
        return self.t.strftime("%H:%M")


@dataclass(frozen=True)
class ReplaySession:
    """A fully precomputed ticker-day, ready to step through."""
    ticker: str
    day: pd.Timestamp
    prev_close: float | None
    orange: orb.OpeningRange | None
    bars: tuple[ReplayBar, ...]
    session_full: pd.DataFrame          # REVEAL ONLY — never read while playing
    fired_bars: tuple[int, ...] = field(default=())   # bars where any bot fired

    @property
    def last(self) -> int:
        return len(self.bars) - 1

    def bar_at(self, i: int) -> ReplayBar:
        return self.bars[max(0, min(i, self.last))]


# --------------------------------------------------------------------------- #
# History loading (bypasses the provider cache — it only holds 2 days of 5m).
# --------------------------------------------------------------------------- #
def yf_intraday(ticker: str, interval: str, period: str = "1mo") -> pd.DataFrame:
    """Raw intraday OHLCV from yfinance (with pre/post), ET index."""
    import yfinance as yf

    df = yf.download(ticker, period=period, interval=interval, auto_adjust=False,
                     progress=False, threads=False, prepost=True)
    if df is None or len(df) == 0:
        raise RuntimeError(f"no {interval} data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    idx = pd.DatetimeIndex(df.index)
    df.index = (idx.tz_convert(asof.ET) if idx.tz is not None
                else idx.tz_localize("UTC").tz_convert(asof.ET))
    return validate_ohlcv(df[["open", "high", "low", "close", "volume"]])


def load_history(ticker: str, provider=None) -> dict:
    """~1 month of 5m + 30m intraday, plus the daily frames, for one ticker.

    The provider cache only carries 2 days of 5-min bars, so the intraday pulls go
    straight to yfinance. Callers should memoize this — it is the only network
    cost in the whole replay.
    """
    if provider is None:
        from stockanalyzer.data.providers import get_provider
        provider = get_provider("yfinance")
    i5 = yf_intraday(ticker, "5m", "1mo")
    i30 = yf_intraday(ticker, "30m", "1mo")
    daily = {tf: provider.fetch_cached(ticker, tf) for tf in
             (Timeframe.M1, Timeframe.M6, Timeframe.Y1)}
    for tf, df in daily.items():                     # normalize to tz-naive dates
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            df.index = df.index.tz_localize(None)
    days = asof.trading_days(i5)
    m1 = daily[Timeframe.M1]
    prev_close = {}
    for day in days:
        prior = m1[asof.naive_dates(m1.index) < day]
        prev_close[day] = float(prior["close"].iloc[-1]) if len(prior) else None
    return {"i5": i5, "i30": i30, "daily": daily, "days": days,
            "prev_close": prev_close}


def replayable_days(history: dict) -> list[pd.Timestamp]:
    """Days worth offering: everything except the earliest, which has no prior
    session inside the 1-month intraday window (degraded context and prev_close)."""
    return list(history["days"][1:])


# --------------------------------------------------------------------------- #
# Plan introspection — why a plan did or didn't fire.
# --------------------------------------------------------------------------- #
def swing_block(plan) -> str:
    """The binding reason a non-GO swing plan didn't fire, from its own fields."""
    if plan.go:
        return "GO"
    g = plan.guidance.lower()
    if plan.kind == "breakout_wait":
        return "armed breakout (not immediate) — waits for a close past the trigger"
    if plan.kind == "no_trade":
        if "2σ" in plan.guidance or "spent" in g:
            return "no_trade: leg spent / 2σ-extended (Bollinger blowoff)"
        if "reach" in g or "away" in g:
            return "no_trade: workable wall beyond the horizon's reach"
        return f"no_trade: {plan.guidance[:60]}"
    if "volume" in g:
        return "vol_ok: breakout needs volume confirmation (rvol ≤ 1.5×)"
    if "indicator says don't" in g or "bollinger" in g:
        return "veto: overextended / adverse indicator (Bollinger / ADX / MACD)"
    if "r:r" in g or "risk" in g:
        return f"rr < {plan.rr:.1f} below the 1.5 floor"
    if "minimum" in g:
        return "aim below the pace minimum move"
    if "earnings" in g:
        return "earnings inside the horizon"
    if "crash" in g:
        return "crash-rebound regime"
    if plan.setup == "No setup":
        return "no valid setup detected"
    return "blocked (see guidance)"


def contradiction(plan) -> str | None:
    """Flag a momentum setup vetoed by a mean-reversion / value gate."""
    if plan.setup not in _MOMENTUM_SETUPS:
        return None
    # Geometry-level veto (leg_spent) shows as no_trade + a 2σ guidance.
    if plan.kind == "no_trade" and ("2σ" in plan.guidance or "spent" in plan.guidance.lower()):
        return f"{plan.setup} vetoed by the 2σ Bollinger 'leg spent' rule"
    failed = [c.name for c in plan.checks
              if not c.ok and not c.na and any(k in c.name for k in _MEANREV_CHECKS)]
    if failed:
        return f"{plan.setup} loses points to mean-reversion gates: {', '.join(failed[:3])}"
    return None


def score_breakdown(plan) -> tuple[int, int, list[tuple[str, int]]]:
    """(earned, denom, [(failed_check, weight_lost), ...]) behind the headline score."""
    denom = sum(int(c.weight / 2) if c.na else c.weight for c in plan.checks if c.weight)
    earned = sum(c.weight for c in plan.checks if c.weight and c.ok and not c.na)
    lost = sorted(((c.name, c.weight) for c in plan.checks
                   if c.weight and not c.ok and not c.na), key=lambda x: -x[1])
    return earned, denom, lost


def armed_swing_bots(bar: "ReplayBar") -> tuple[str, ...]:
    """Which swing bots would take this bar, after the shared actionable/extended
    chokepoint. Empty when the plan is watch-only."""
    if not bar.actionable or bar.extended:
        return ()
    return tuple(name for name, fires in SWING_BOTS if fires(bar))


def judge_intraday(rest, entry, stop, target, rr_floor) -> tuple[str, float]:
    """Stop-first conservative judging over the remaining bars; EOD close otherwise."""
    for _, row in rest.iterrows():
        if float(row["low"]) <= stop:
            return "stop", -1.0
        if float(row["high"]) >= target:
            return "target", rr_floor
    if len(rest):
        r = (float(rest["close"].iloc[-1]) - entry) / (entry - stop)
        return "eod", round(r, 2)
    return "open", 0.0


# --------------------------------------------------------------------------- #
# Precompute — the whole day's decisions, once.
# --------------------------------------------------------------------------- #
def _eval_bar(i, t, row, reports, orange, prev_close, plan=None) -> ReplayBar | None:
    """Freeze one bar's complete decision. Everything it reads is as-of ``t``.

    ``plan`` lets live mode pass the exact plan its bots acted on (user pace +
    sentiment) instead of replay's FAST, sentiment-free one."""
    price = float(row["close"])
    if plan is None:
        dec = asof.decision_rep(reports)
        if dec is None:
            return None
        inv = round((build_verdict(reports).score + 1) / 2 * 100)
        plan = build_swing_plan(dec, UseCase.BUY, SwingPace.FAST, price_override=price,
                                all_reports=reports, context={"investor_pct": inv})
    if plan is None:
        return None

    d1 = reports.get(Timeframe.D1)
    idf = d1.df if d1 is not None else None
    card = build_day_card(reports, price)
    gap = orb.gap_and_go_signal(orange, idf, price, t)
    trend = asof.higher_trend(reports)
    revs = tuple(reversal.reversal_signal(card, idf, price, trend, prev_close, t, cfg)
                 for cfg in reversal.REVERSAL_VARIANTS)
    earned, denom, lost = score_breakdown(plan)

    return ReplayBar(
        i=i, t=t, epoch=t.timestamp(),
        open=float(row["open"]), high=float(row["high"]),
        low=float(row["low"]), close=price, volume=float(row["volume"]),
        d1=d1, card=card,
        setup=plan.setup, kind=plan.kind, go=bool(plan.go), score=int(plan.score),
        rr=float(plan.rr), entry=float(plan.entry), stop=float(plan.stop),
        target=float(plan.target1), trigger=plan.trigger,
        actionable=bool(getattr(plan, "actionable", True)),
        extended=is_extended(plan),
        light=plan.light, horizon=plan.horizon,
        earned=earned, denom=denom, lost=tuple(lost),
        snapshot=snapshot.plan_snapshot(plan, reports),
        bots=BotVerdicts(orb=gap, reversals=revs, swing_go=bool(plan.go),
                         swing_block=("" if plan.go else swing_block(plan)),
                         contradiction=contradiction(plan)),
    )


def live_bar(reports, price, now, orange: dict | None, prev_close, plan) -> ReplayBar | None:
    """One live 'bar' for the replay bot panel: the same gates the live bots run,
    evaluated on the current reports at ``price``."""
    d1 = reports.get(Timeframe.D1) if reports else None
    if d1 is None or d1.df is None or not len(d1.df) or not price:
        return None
    last = d1.df.iloc[-1]
    row = {"open": last["open"], "high": max(float(last["high"]), price),
           "low": min(float(last["low"]), price), "close": price,
           "volume": last["volume"]}
    rng = orb.OpeningRange(**orange) if orange else None
    return _eval_bar(0, pd.Timestamp(now), row, reports, rng, prev_close, plan=plan)


def precompute_day(i5, i30, daily, day, prev_close, ticker: str = "",
                   progress=None) -> ReplaySession:
    """Walk 09:45 → 15:55 and freeze every bar's decision.

    ``progress`` is an optional ``callable(done, total)`` so a UI can show a bar;
    this is ~120 ms per bar (five ``analyze_timeframe`` calls), so a session takes
    roughly ten seconds.
    """
    day = pd.Timestamp(day).normalize()
    sess_full = asof.day_bars(i5, day).between_time(
        asof.REG_START, asof.REG_END, inclusive="left")
    if sess_full.empty:
        return ReplaySession(ticker, day, prev_close, None, (), sess_full)

    window = sess_full.between_time(REPLAY_START, REPLAY_END)
    orange = None
    if len(window):
        # The 09:30–09:45 range is complete by the first replay bar, so this reads
        # only the past — no need for the backtest's forced-timestamp workaround.
        first = window.index[0]
        orange = orb.opening_range(asof.asof_intraday(i5, first, days=2),
                                   prev_close, first)

    bars: list[ReplayBar] = []
    total = len(window)
    for n, (t, row) in enumerate(window.iterrows()):
        reports = asof.asof_reports(i5, i30, daily, day, t)
        bar = _eval_bar(len(bars), t, row, reports, orange, prev_close)
        if bar is not None:
            bars.append(bar)
        if progress is not None:
            progress(n + 1, total)

    fired = tuple(b.i for b in bars if b.bots.any_fired)
    return ReplaySession(ticker=ticker, day=day, prev_close=prev_close,
                         orange=orange, bars=tuple(bars), session_full=sess_full,
                         fired_bars=fired)


# --------------------------------------------------------------------------- #
# The replay book (an isolated sandbox — never the real trades.db).
# --------------------------------------------------------------------------- #
def practice_root(root: Path, user_id: str) -> Path:
    """Keep each authenticated user's practice files in their own namespace."""
    if not user_id:
        raise ValueError("Replay requires an authenticated user")
    base = Path(os.environ.get("STOCKANALYZER_REPLAY_DIR") or root)
    owner = hashlib.sha256(user_id.encode()).hexdigest()
    return base / "users" / owner


def _book_key(ticker: str, day) -> str:
    symbol = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=_-]{0,31}", symbol):
        raise ValueError("Invalid replay ticker")
    return f"{symbol}-{pd.Timestamp(day):%Y%m%d}"


def replay_db_path(root: Path, ticker: str, day, attempt: int = 1) -> Path:
    """Where one practice attempt is recorded.

    The subdirectory is load-bearing, not cosmetic. ``store._ensure_schema`` seeds
    a brand-new DB from ``<db_path>.parent/.virtualbook.json`` — so a replay book
    at the project root would silently inherit the real trade history and wreck
    the scorecard. Keeping it one level down means the migration finds no JSON.
    """
    key = _book_key(ticker, day)
    folder = Path(root) / ".replay"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{key}-{attempt}.db"


def next_attempt(root: Path, ticker: str, day) -> int:
    """The first attempt number with no book on disk yet.

    Attempt counters live in session state, which resets when the server does —
    so numbering from 1 each time would silently hand a "fresh" run the trades of
    a previous one. Ask the filesystem instead; old attempts stay put as a
    practice log.
    """
    key = _book_key(ticker, day)
    folder = Path(root) / ".replay"
    used = set()
    for p in folder.glob(f"{key}-*.db"):
        try:
            used.add(int(p.stem.rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    n = 1
    while n in used:
        n += 1
    return n


def create_attempt(root: Path, ticker: str, day) -> tuple[int, Path]:
    """Reserve a fresh book even when two browser sessions start together."""
    attempt = next_attempt(root, ticker, day)
    while True:
        path = replay_db_path(root, ticker, day, attempt)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            attempt += 1
        else:
            os.close(descriptor)
            return attempt, path


def _open(trader: str, ticker: str, bar: ReplayBar, db: Path, *, entry: float,
          stop: float, target: float, snap: dict, kind: str = "immediate",
          trigger=None, horizon_days: int = 1, managed: bool = False,
          entry_rvol=None, stake: float | None = None) -> dict | None:
    """Open one position in the replay book, at the *bar's* clock. Returns the row
    (or None when this trader already holds the ticker)."""
    if virtualbook.has_open(ticker, trader, path=db):
        return None
    kw = dict(ticker=ticker, trader=trader, entry=entry, stop=stop, target=target,
              kind=kind, trigger=trigger, horizon_days=horizon_days,
              managed=managed, entry_rvol=entry_rvol, init_stop=stop,
              snapshot=snap, now=bar.epoch, path=db)
    if stake is not None:
        kw["stake"] = stake
    return virtualbook.open_position(**kw)


def apply_bar(sess: ReplaySession, i: int, bots: dict, db: Path) -> list[dict]:
    """Advance the book by one bar: manage → mark → auto-place.

    Order is not negotiable. ``manage`` runs before ``mark`` so a stop tightened on
    this bar can close on it (the live radar enforces the same). Marking walks
    low → high → close, which makes an ambiguous bar resolve stop-first — the
    conservative reading, and it reuses the store's own fill logic rather than
    inventing a second judge.

    Auto-placement happens *after* marking, so a position opened here is never
    judged against the bar it was opened on.
    """
    bar = sess.bars[i]
    tk, ts = sess.ticker, bar.epoch
    reports = {Timeframe.D1: bar.d1} if bar.d1 is not None else None
    events: list[dict] = []

    virtualbook.manage(tk, bar.close, reports,
                       bar.d1.trend_change if bar.d1 is not None else None,
                       now=ts, path=db)
    for px in (bar.low, bar.high, bar.close):
        for changed in virtualbook.mark(tk, px, now=ts, path=db):
            events.append({"bar": i, "t": bar.clock, "kind": "mark", "row": changed})

    if bots.get("orb") and bar.bots.orb.fired:
        g = bar.bots.orb
        snap = snapshot.gap_snapshot(sess.orange.as_dict(), g.rvol,
                                     g.entry, g.stop, g.target)
        for trader, managed in GAP_BOTS:
            row = _open(trader, tk, bar, db, entry=g.entry, stop=g.stop,
                        target=g.target, snap=snap, managed=managed,
                        entry_rvol=g.rvol)
            if row:
                events.append({"bar": i, "t": bar.clock, "kind": "open", "row": row})

    if bots.get("rev"):
        for dec in bar.bots.fired_reversals:
            row = _open(dec.variant, tk, bar, db, entry=dec.entry, stop=dec.stop,
                        target=dec.target, snap=snapshot.reversal_snapshot(dec))
            if row:
                events.append({"bar": i, "t": bar.clock, "kind": "open", "row": row})

    # The swing family shares one chokepoint with the live radar: a non-actionable
    # or extended plan is watch-only and never becomes a position, whatever the
    # individual bot rules say.
    if bots.get("swing") and bar.actionable and not bar.extended:
        for trader, fires in SWING_BOTS:
            if not fires(bar):
                continue
            row = _open(trader, tk, bar, db, entry=bar.entry, stop=bar.stop,
                        target=bar.target, snap=bar.snapshot, kind=bar.kind,
                        trigger=bar.trigger, horizon_days=3)
            if row:
                events.append({"bar": i, "t": bar.clock, "kind": "open", "row": row})

    return events


def force_flat(sess: ReplaySession, db: Path, reason: str = "eod") -> list[dict]:
    """Close everything still open at the last replay bar — a day trade doesn't
    hold overnight, and an open row has no R to score."""
    if not sess.bars:
        return []
    last = sess.bars[-1]
    closed = []
    for row in virtualbook.load(path=db):
        if row.get("status") in ("open", "pending"):
            out = virtualbook.close_position(row["id"], last.close, reason=reason,
                                             now=last.epoch, path=db)
            if out:
                closed.append(out)
    return closed


# --------------------------------------------------------------------------- #
# Scoring the session.
# --------------------------------------------------------------------------- #
def trade_r(row: dict) -> float | None:
    """Realized R, measured against the ORIGINAL stop.

    Using ``init_stop`` rather than ``stop`` is what keeps a trailed exit honest:
    a stop moved to breakeven would otherwise report an infinite R.
    """
    entry, init = row.get("entry"), row.get("init_stop") or row.get("stop")
    exit_px = row.get("exit_price")
    if not entry or not init or exit_px is None or entry <= init:
        return None
    return round((float(exit_px) - float(entry)) / (float(entry) - float(init)), 2)


def excursions(sess: ReplaySession, row: dict, upto: int) -> tuple[float, float]:
    """(MFE%, MAE%) for one position, from the session bars it has been held.

    The store only maintains ``mfe_pct``/``mae_pct`` for managed rows, so a manual
    trade would otherwise read 0/0. Computed here instead — read-only, no schema
    change.
    """
    entry = float(row.get("entry") or 0)
    start = row.get("activated_ts") or row.get("opened_ts")
    if not entry or start is None:
        return 0.0, 0.0
    held = [b for b in sess.bars[:upto + 1] if b.epoch >= float(start)]
    if not held:
        return 0.0, 0.0
    return (round(max(b.high for b in held) / entry - 1, 4) * 100,
            round(min(b.low for b in held) / entry - 1, 4) * 100)


def scorecard(sess: ReplaySession, db: Path) -> dict:
    """End-of-session summary: per-trader P&L plus the deduped idea-level view.

    Both views are reported on purpose. Per-trader answers "which bot made money";
    the idea-level view answers "was the *algorithm* right" — four reversal
    variants taking one plan is one idea, not four (see CLAUDE.md).
    """
    book = virtualbook.load(path=db)
    rows = [r for r in book if (r.get("ticker") or "").upper() == sess.ticker.upper()]
    return {
        "book": rows,
        # stats() with no argument reads the REAL trades.db — always pass the list.
        "stats": virtualbook.stats(rows),
        "ideas": virtualbook.algorithm_correctness(path=db),
    }


def missed_signals(sess: ReplaySession, db: Path) -> list[dict]:
    """Bot signals that fired and you didn't take — judged over the revealed day.

    This is the honest-evaluation half of the exercise: not "did I make money"
    but "what did standing aside cost me".
    """
    taken = {float(r["entry"]) for r in virtualbook.load(path=db)
             if r.get("trader") == "me" and r.get("entry")}
    out: list[dict] = []
    for i in sess.fired_bars:
        bar = sess.bars[i]
        cands = []
        if bar.bots.orb.fired:
            g = bar.bots.orb
            cands.append(("🚀 Gap-and-Go", g.entry, g.stop, g.target, g.rr))
        for dec in bar.bots.fired_reversals:
            cands.append((f"🔄 {dec.variant}", dec.entry, dec.stop, dec.target, dec.rr))
        for trader, fires in SWING_BOTS:
            if bar.actionable and not bar.extended and fires(bar):
                cands.append((f"📈 {trader}", bar.entry, bar.stop, bar.target, bar.rr))
        for name, entry, stop, target, rr in cands:
            if entry is None or any(abs(entry - e) < 0.01 for e in taken):
                continue
            rest = sess.session_full[sess.session_full.index > bar.t]
            status, r = judge_intraday(rest, entry, stop, target, rr or 2.0)
            out.append({"bar": i, "t": bar.clock, "name": name, "entry": entry,
                        "stop": stop, "target": target, "status": status, "r": r})
    return out


def blocked_digest(sess: ReplaySession) -> tuple[str, int, int]:
    """The day's modal blocking gate: (reason, bars, total). What kept you out."""
    from collections import Counter

    blocks = [b.bots.swing_block for b in sess.bars if b.bots.swing_block]
    if not blocks:
        return "", 0, len(sess.bars)
    reason, n = Counter(blocks).most_common(1)[0]
    return reason, n, len(sess.bars)
