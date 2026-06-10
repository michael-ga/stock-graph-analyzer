"""Tests for watchlist persistence, rate limiter, and live assurance math."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockanalyzer import watchlist
from stockanalyzer.analysis.signals import Direction
from stockanalyzer.data.ratelimit import Caps, RateLimiter, RateLimitExceeded
from stockanalyzer.live import Tick, assess


# --- watchlist -------------------------------------------------------------
def test_watchlist_roundtrip(tmp_path):
    p = tmp_path / "wl.json"
    assert watchlist.load(p) == []
    watchlist.add("msft", p)
    watchlist.add("AAPL", p)
    assert watchlist.load(p) == ["MSFT", "AAPL"]
    watchlist.add("MSFT", p)                     # no duplicate
    assert watchlist.load(p) == ["MSFT", "AAPL"]
    assert watchlist.is_followed("aapl", p)
    watchlist.remove("MSFT", p)
    assert watchlist.load(p) == ["AAPL"]
    watchlist.toggle("AAPL", p)                  # toggle off
    assert watchlist.load(p) == []


# --- rate limiter ----------------------------------------------------------
def test_ratelimit_daily_cap(tmp_path, monkeypatch):
    import stockanalyzer.data.ratelimit as rl
    monkeypatch.setattr(rl, "_STATE_PATH", tmp_path / "rl.json")
    monkeypatch.setattr(rl, "CAPS", {"x": Caps(per_min=100, per_day=3)})
    limiter = RateLimiter()
    for _ in range(3):
        limiter.acquire("x")
    with pytest.raises(RateLimitExceeded):
        limiter.acquire("x")
    assert limiter.remaining_today("x") == 0


def test_ratelimit_per_minute_blocks(tmp_path, monkeypatch):
    import stockanalyzer.data.ratelimit as rl
    monkeypatch.setattr(rl, "_STATE_PATH", tmp_path / "rl.json")
    monkeypatch.setattr(rl, "CAPS", {"y": Caps(per_min=2)})
    limiter = RateLimiter()
    limiter.acquire("y")
    limiter.acquire("y")
    # Third within the same instant should exceed the minute bucket (short wait).
    with pytest.raises(RateLimitExceeded):
        limiter.acquire("y")


# --- live assurance --------------------------------------------------------
def _tick(direction: str, holds=True, vol=True) -> Tick:
    return Tick(direction=direction, bias_score=0.3, price=100.0,
                holds_level=holds, volume_rising=vol)


def test_assurance_high_when_consistent():
    ticks = [_tick("bullish") for _ in range(10)]
    a = assess(ticks, Direction.BULL, threshold=65)
    assert a.pct == 100
    assert a.go is True


def test_assurance_low_when_oscillating():
    ticks = [_tick("bullish" if i % 2 == 0 else "bearish") for i in range(10)]
    a = assess(ticks, Direction.BULL, threshold=65)
    assert a.pct < 65
    assert a.go is False


def test_assurance_gate_blocks_go(tmp_path):
    ticks = [_tick("bullish", holds=False, vol=False) for _ in range(10)]
    a = assess(ticks, Direction.BULL, threshold=65)
    assert a.pct == 100        # direction agrees…
    assert a.gate_ok is False  # …but price/volume don't confirm
    assert a.go is False
