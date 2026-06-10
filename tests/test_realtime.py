"""Tests for the real-time tick stream: pure trace analytics + graceful fallback."""
from __future__ import annotations

from stockanalyzer.data.realtime import (
    PriceTick,
    RealtimeStream,
    summarize,
    ticks_to_candles,
)


def _ticks(prices, start_ts=1_000.0, step=1.0, vol=100.0):
    return [PriceTick(ts=start_ts + i * step, price=p, volume=vol)
            for i, p in enumerate(prices)]


def test_summarize_empty_is_none():
    assert summarize([]) is None


def test_summarize_basic_change_and_extremes():
    s = summarize(_ticks([10.0, 11.0, 9.0, 12.0]))
    assert s is not None
    assert s.first == 10.0 and s.last == 12.0
    assert s.high == 12.0 and s.low == 9.0
    assert round(s.change, 2) == 2.0
    assert round(s.change_pct, 1) == 20.0
    assert s.n_ticks == 4
    assert s.cum_volume == 400.0
    assert round(s.elapsed_s, 1) == 3.0


def test_summarize_rising_momentum():
    s = summarize(_ticks([10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 11.2, 11.4]))
    assert s.momentum == "rising"
    assert s.last > s.ema          # price leads a trailing EMA in an uptrend


def test_summarize_falling_momentum():
    s = summarize(_ticks([20.0, 19.8, 19.6, 19.4, 19.2, 19.0, 18.8, 18.6]))
    assert s.momentum == "falling"
    assert s.last < s.ema


def test_summarize_flat_momentum_within_deadband():
    # Tiny oscillation around a level → flat (deadband suppresses jitter).
    s = summarize(_ticks([50.00, 50.01, 49.99, 50.00, 50.01, 49.99]))
    assert s.momentum == "flat"


def test_ticks_to_candles_empty():
    df = ticks_to_candles([])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 0


def test_ticks_to_candles_buckets_ohlcv():
    # Two 60s buckets: ts 0-59 and 60-119.
    ticks = [
        PriceTick(0, 10.0, 5), PriceTick(20, 12.0, 5), PriceTick(40, 9.0, 5),
        PriceTick(59, 11.0, 5),                          # bucket 0 close
        PriceTick(60, 11.5, 3), PriceTick(90, 13.0, 3),  # bucket 1
    ]
    df = ticks_to_candles(ticks, interval_s=60)
    assert len(df) == 2
    b0 = df.iloc[0]
    assert b0["open"] == 10.0 and b0["close"] == 11.0
    assert b0["high"] == 12.0 and b0["low"] == 9.0
    assert b0["volume"] == 20
    b1 = df.iloc[1]
    assert b1["open"] == 11.5 and b1["close"] == 13.0 and b1["high"] == 13.0


def test_stream_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    stream = RealtimeStream("AAPL", api_key="")
    assert stream.available is False
    assert stream.start() is False
    snap = stream.snapshot()
    assert snap.ticks == [] and snap.connected is False


def test_stream_buffer_is_ring(monkeypatch):
    # Buffer should never exceed its maxlen even after many synthetic appends.
    stream = RealtimeStream("AAPL", api_key="x")
    for i in range(1, 6001):
        stream._on_message(None, '{"type":"trade","data":[{"t":%d,"p":%d,"v":1}]}'
                           % (i * 1000, i))
    snap = stream.snapshot()
    assert len(snap.ticks) <= 5000
    assert snap.n_received == 6000          # count is not capped, buffer is
    assert snap.ticks[-1].price == 6000.0   # newest tick retained
