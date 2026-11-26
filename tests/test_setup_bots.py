"""Live setup bots (stockanalyzer.setup_bots): the live context must match the
harness's, bar for bar, on completed bars only, and the radar worker must open each
fired variant as its own bot in the owner's book."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pandas as pd
import pytest

from stockanalyzer import setup_bots, virtualbook
from stockanalyzer.data.schema import Timeframe
from stockanalyzer.setups import SETUPS, double_bottom, harness, silver_bullet
from stockanalyzer.setups.base import SetupDecision
from tests.test_setups import DAY, _db_session, _hist, _sb_session

_REAL_MARKET_FRAME = setup_bots.market_frame      # before the conftest stub


def _live_inputs(hist):
    daily = hist["daily"]
    return hist["i5"], daily, float(daily["close"].iloc[-1])


def _done(t: str) -> pd.Timestamp:
    """The moment the bar labelled ``t`` (today) is complete."""
    return pd.Timestamp(f"{DAY} {t}", tz="America/New_York") + setup_bots.BAR


def _first_fire(hist, variant) -> tuple | None:
    """(bar clock, entry, stop, target) of the first live fire, stepping the day."""
    frame, daily, prev_close = _live_inputs(hist)
    for t in pd.date_range(f"{DAY} 09:30", f"{DAY} 15:55", freq="5min"):
        ctx = setup_bots.live_context(frame, daily, prev_close, _done(t.strftime("%H:%M")))
        if ctx is None:
            continue
        dec = next((d for d in setup_bots.decisions(ctx) if d.variant == variant), None)
        if dec is not None and dec.fired:
            return (ctx.asof.strftime("%H:%M"), dec.entry, dec.stop, dec.target)
    return None


# --------------------------------------------------------------------------- #
def test_live_variants_are_real_setup_variants():
    names = {cfg.name for m in SETUPS.values() for cfg in m.VARIANTS}
    assert set(setup_bots.LIVE_VARIANTS) <= names
    assert setup_bots.bot_names() == ["sb-am-bias", "sb-mkt", "xfade-dbl-bottom", "xfade-chop"]
    assert all(name in virtualbook.TRADER_RULES for name in setup_bots.bot_names())


def test_a_new_setup_variant_is_not_live_until_listed(monkeypatch):
    extra = NS(NAME="new", VARIANTS=[NS(name="new-idea")], signal=lambda c, cfg: None)
    monkeypatch.setitem(setup_bots.SETUPS, "new", extra)
    assert "new-idea" not in setup_bots.bot_names()


@pytest.mark.parametrize("session,mod", [(_sb_session, silver_bullet),
                                         (_db_session, double_bottom)])
def test_live_fires_on_the_same_bar_and_prices_as_the_harness(session, mod):
    hist = _hist(session())
    cfg = mod.VARIANTS[0]
    [t] = harness.run_day(hist, 1, [(mod, cfg)])
    assert _first_fire(hist, cfg.name) == (t["t"], t["entry"], t["stop"], t["target"])


def test_the_forming_bar_is_never_read():
    frame, daily, prev_close = _live_inputs(_hist(_sb_session()))
    now = pd.Timestamp(f"{DAY} 10:12", tz="America/New_York")   # the 10:10 bar is forming
    ctx = setup_bots.live_context(frame, daily, prev_close, now)
    assert ctx.asof.strftime("%H:%M") == "10:05"
    assert ctx.prev_session is not None and ctx.premarket is not None
    assert ctx.prev_session.index[0].strftime("%Y-%m-%d") < DAY
    assert (pd.DatetimeIndex(ctx.daily.index).normalize() < pd.Timestamp(DAY)).all()


def test_a_naive_index_is_read_as_eastern():
    frame, daily, prev_close = _live_inputs(_hist(_sb_session()))
    naive = frame.copy()
    naive.index = naive.index.tz_localize(None)
    now = _done("10:10")
    a = setup_bots.live_context(frame, daily, prev_close, now)
    b = setup_bots.live_context(naive, daily, prev_close, now.tz_localize(None))
    assert a.asof == b.asof and len(a.session) == len(b.session)


def test_a_thin_tape_stands_aside():
    frame, daily, prev_close = _live_inputs(_hist(_sb_session()))
    today = frame[frame.index.strftime("%Y-%m-%d") == DAY]
    holes = today.between_time("09:30", "15:55").index[1::2]   # every other bar missing
    assert setup_bots.live_context(frame.drop(holes), daily, prev_close,
                                   _done("11:00")) is None
    assert setup_bots.live_context(None, daily, prev_close, _done("11:00")) is None


def test_market_is_today_completed_regular_bars_only():
    frame, daily, prev_close = _live_inputs(_hist(_sb_session()))
    qqq = frame.copy()
    ctx = setup_bots.live_context(frame, daily, prev_close, _done("10:10"), market=qqq,
                                  market_sign=-1)
    assert ctx.market.index[0].strftime("%Y-%m-%d %H:%M") == f"{DAY} 09:30"
    assert ctx.market.index[-1] == ctx.asof and ctx.market_sign == -1


def test_stopped_on_fill_only_for_a_resting_order_whose_bar_hit_the_stop():
    frame, daily, prev_close = _live_inputs(_hist(_sb_session()))
    ctx = setup_bots.live_context(frame, daily, prev_close, _done("10:10"))
    low = float(ctx.session["low"].iloc[-1])
    dec = SetupDecision(True, "", "x", "x", entry=low + 1, stop=low, target=low + 3,
                        rr=2.0, filled_intrabar=True)
    assert setup_bots.stopped_on_fill(dec, ctx)
    assert not setup_bots.stopped_on_fill(SetupDecision(**{**dec.__dict__, "stop": low - 0.5}), ctx)
    assert not setup_bots.stopped_on_fill(SetupDecision(**{**dec.__dict__,
                                                           "filled_intrabar": False}), ctx)


def test_market_frame_is_read_once_per_refresh(monkeypatch):
    reads = []
    provider = NS(fetch_cached=lambda *a, **k: reads.append(a) or "qqq")
    monkeypatch.setitem(setup_bots._MARKET_MEMO, "df", None)
    clock = iter([100.0, 110.0, 200.0]).__next__
    assert _REAL_MARKET_FRAME(provider, clock) == "qqq"
    assert _REAL_MARKET_FRAME(provider, clock) == "qqq" and len(reads) == 1
    assert _REAL_MARKET_FRAME(provider, clock) == "qqq" and len(reads) == 2


# --------------------------------------------------------------------------- #
# Radar worker: each fired variant is its own bot in the owner's book.
# --------------------------------------------------------------------------- #
def _result(hist):
    frame, daily, prev_close = _live_inputs(hist)
    return NS(reports={Timeframe.D1: NS(df=frame), Timeframe.Y1: NS(df=daily)},
              quote=NS(prev_close=prev_close, price=float(frame["close"].iloc[-1])))


def test_worker_opens_the_fired_setup_once_per_session(owner, monkeypatch):
    import stockanalyzer.radar_worker as worker
    monkeypatch.setattr(worker.swingwatch, "assignment_is_active", lambda *a, **k: True)
    hist = _hist(_sb_session())
    [t] = harness.run_day(hist, 1, [(silver_bullet, silver_bullet.VARIANTS[0])])
    asof = _done(t["t"]).to_pydatetime()

    opened = worker.run_setup_bots(owner, "SNXX", _result(hist), asof)
    assert [r["trader"] for r in opened] == ["sb-am-bias"]
    [row] = [r for r in virtualbook.load(user_id=owner) if r["trader"] == "sb-am-bias"]
    assert (row["entry"], row["stop"], row["target"]) == pytest.approx(
        (t["entry"], t["stop"], t["target"]))
    assert row["snapshot"]["setup"] == "silver_bullet"

    assert worker.run_setup_bots(owner, "SNXX", _result(hist), asof) == []


def test_worker_closes_a_fill_bar_stop_at_once(owner, monkeypatch):
    import stockanalyzer.radar_worker as worker
    monkeypatch.setattr(worker.swingwatch, "assignment_is_active", lambda *a, **k: True)
    hist = _hist(_sb_session())
    asof = _done("10:10").to_pydatetime()
    ctx = setup_bots.live_context(*_live_inputs(hist), asof)
    low = float(ctx.session["low"].iloc[-1])
    dec = SetupDecision(True, "test", "double_bottom", "xfade-dbl-bottom", entry=low + 1,
                        stop=low, target=low + 2, rr=1.0, filled_intrabar=True)
    monkeypatch.setattr(worker.setup_bots, "decisions", lambda _ctx: [dec])

    [opened] = worker.run_setup_bots(owner, "SNXX", _result(hist), asof)
    [row] = [r for r in virtualbook.load(user_id=owner) if r["id"] == opened["id"]]
    assert row["status"] == "closed" and row["close_reason"] == "stop_hit"


def test_worker_skips_the_opening_range_and_missing_frames(owner):
    import stockanalyzer.radar_worker as worker
    hist = _hist(_sb_session())
    early = pd.Timestamp(f"{DAY} 09:35", tz="America/New_York").to_pydatetime()
    assert worker.run_setup_bots(owner, "SNXX", _result(hist), early) == []
    assert worker.run_setup_bots(owner, "SNXX", NS(reports={}, quote=None),
                                 _done("10:10").to_pydatetime()) == []
