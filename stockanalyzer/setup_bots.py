"""Live bots for the ``stockanalyzer/setups`` modules (Silver Bullet, Double Bottom).

Each setup variant is its own bot (trader = variant name, e.g. ``sb-am-bias``), the
same way each ``REVERSAL_VARIANTS`` row is its own ``rev-*`` bot. They trade the
virtual book to collect data on new sessions (H8, H9, H15, H16 in
``algolab/LEARNINGS.md``); none has passed its keep rule yet.

The context is built the way the setups harness and the replay build it
(``setups.harness.run_day``, ``replay._setup_reads``), from the radar's live 5-minute
frame (``Timeframe.D1``: two days with pre/post-market bars):

- only **completed** bars count: a bar labelled ``t`` is done at ``t + 5 min``. The
  harness and the replay decide on a finished bar, so the forming bar is dropped;
- ``session`` = today's regular bars so far, ``prev_session`` = the previous
  regular session in the frame, ``premarket`` = today's 04:00–09:25 bars;
- ``daily`` = daily bars strictly before today; ``prev_close`` from the quote;
- ``market`` = QQQ's completed regular bars today (``sb-mkt`` only), ``market_sign``
  = −1 for an inverse ETF (``harness.fund_sign``, the harness's own rule).

A setup fires only when its trigger is the latest completed bar, so a radar that
polls at least once per 5-minute bar sees every trigger once. Execution mirrors
``replay.apply_setups``: open at the decision's entry, stop and target, one-day
horizon; a resting order that filled inside a bar whose low also reached the stop is
closed at the stop at once (stop-first, as the harness judges the fill bar).
"""
from __future__ import annotations

import functools
import logging
from datetime import timedelta

import pandas as pd

from .analysis import asof as _asof
from .setups import SETUPS
from .setups.base import SetupContext, SetupDecision

log = logging.getLogger(__name__)

BAR = timedelta(minutes=5)
MARKET = "QQQ"
# The harness and the replay skip a session with fewer than 60 of its 78 bars (a
# half day or an illiquid tape). Live, only the bars so far are known, so the same
# coverage is asked of the completed bars: ≥ 60/78 of the slots since 09:30.
MIN_COVERAGE = 60 / 78


# The setup variants that trade live, by name. A setup or variant added to
# ``SETUPS`` later stays backtest/replay-only until it is listed here (its own
# [ALGO] PR), so adding a research module never starts a bot by itself.
LIVE_VARIANTS = ("sb-am-bias", "sb-mkt", "xfade-dbl-bottom", "xfade-chop")


def variants() -> list[tuple]:
    """The (module, config) pairs of the live setup bots, in ``SETUPS`` order."""
    return [(m, cfg) for m in SETUPS.values() for cfg in m.VARIANTS
            if cfg.name in LIVE_VARIANTS]


def bot_names() -> list[str]:
    return [cfg.name for _, cfg in variants()]


def _et(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """The frame on a tz-aware Eastern index (a naive index is taken as Eastern)."""
    if df is None or not len(df) or not isinstance(df.index, pd.DatetimeIndex):
        return None
    idx = df.index
    idx = idx.tz_convert(_asof.ET) if idx.tz is not None else idx.tz_localize(_asof.ET)
    out = df.copy()
    out.index = idx
    return out[["open", "high", "low", "close", "volume"]].astype(float)


def _now_et(now) -> pd.Timestamp:
    ts = pd.Timestamp(now)
    return ts.tz_convert(_asof.ET) if ts.tzinfo is not None else ts.tz_localize(_asof.ET)


def _completed(df: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    return df[df.index + BAR <= now]


def _regular(df: pd.DataFrame) -> pd.DataFrame:
    return df.between_time(_asof.REG_START, _asof.REG_END, inclusive="left")


def live_context(frame: pd.DataFrame | None, daily: pd.DataFrame | None,
                 prev_close: float | None, now, market: pd.DataFrame | None = None,
                 market_sign: int = 1) -> SetupContext | None:
    """The as-of ``SetupContext`` for ``now`` from live frames, or None when there is
    no completed regular bar today or the tape is too thin (``MIN_COVERAGE``)."""
    now = _now_et(now)
    bars = _et(frame)
    if bars is None:
        return None
    bars = _completed(bars, now)
    today = now.tz_localize(None).normalize()
    dates = _asof.naive_dates(bars.index)
    session = _regular(bars[dates == today])
    if not len(session):
        return None
    slots = int((session.index[-1] - session.index[-1].normalize()
                 - pd.Timedelta(hours=9, minutes=30)) / BAR) + 1
    if len(session) < MIN_COVERAGE * slots:
        return None
    prior = sorted({d for d in dates if d < today})
    prev = _regular(bars[dates == prior[-1]]) if prior else None
    pre = bars[dates == today].between_time("04:00", _asof.REG_START, inclusive="left")
    dly = None
    if daily is not None and len(daily):
        dly = daily[_asof.naive_dates(pd.DatetimeIndex(daily.index)) < today]
    mkt = _et(market)
    if mkt is not None:
        mkt = _regular(_completed(mkt, now))
        mkt = mkt[_asof.naive_dates(mkt.index) == today]
        mkt = mkt if len(mkt) else None
    return SetupContext(session, prev if prev is not None and len(prev) else None,
                        pre if len(pre) else None, prev_close, dly,
                        market=mkt, market_sign=market_sign)


def decisions(ctx: SetupContext | None) -> list[SetupDecision]:
    """Every live setup bot's read on ``ctx``; a variant that raises stands aside."""
    if ctx is None:
        return []
    out = []
    for mod, cfg in variants():
        try:
            out.append(mod.signal(ctx, cfg))
        except Exception:                      # one bad read must not stop the others
            log.exception("setup %s failed on %s", cfg.name, ctx.asof)
    return out


def stopped_on_fill(dec: SetupDecision, ctx: SetupContext) -> bool:
    """A resting order that filled inside the trigger bar, whose low also reached the
    stop: the harness and the replay count it as stopped on that bar."""
    return bool(dec.filled_intrabar and float(ctx.session["low"].iloc[-1]) <= dec.stop)


def snapshot(dec: SetupDecision) -> dict:
    """The book snapshot of a setup trade (replay.apply_setups stores setup + reason)."""
    return {"setup": dec.setup, "reason": dec.reason, "kind": "immediate",
            "score": None, "label": f"{dec.setup} ({dec.variant})", "rr": dec.rr,
            "guidance": dec.reason}


@functools.lru_cache(maxsize=256)
def market_sign(ticker: str) -> int:
    """−1 for an inverse ETF (from its fund name, the harness's rule), else 1."""
    from .setups.harness import fund_sign
    return fund_sign(ticker)


_MARKET_MEMO: dict = {"at": 0.0, "df": None}
MARKET_REFRESH_S = 30.0     # one QQQ read per radar cycle, not one per ticker


def market_frame(provider=None, clock=None) -> pd.DataFrame | None:
    """QQQ's live 5-minute frame, re-read at most every ``MARKET_REFRESH_S`` seconds
    (the provider's own cache sits behind it), or None when it can't be had."""
    import time
    from .data.providers import get_provider
    from .data.schema import Timeframe
    now = (clock or time.monotonic)()
    if _MARKET_MEMO["df"] is not None and now - _MARKET_MEMO["at"] < MARKET_REFRESH_S:
        return _MARKET_MEMO["df"]
    try:
        df = (provider or get_provider()).fetch_cached(MARKET, Timeframe.D1, live_mode=True)
    except Exception:
        log.warning("no %s frame; setups that need the market stand aside", MARKET)
        return _MARKET_MEMO["df"]
    _MARKET_MEMO.update(at=now, df=df)
    return df
