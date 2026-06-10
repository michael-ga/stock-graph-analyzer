"""Real-time price streaming via the Finnhub trade WebSocket (free tier).

The free Finnhub plan exposes `wss://ws.finnhub.io` which pushes a `trade`
message every time a trade prints for a subscribed symbol — genuine
tick-by-tick data with **no polling and no per-minute REST rate limit**. This is
the right tool for "watch it live for a few minutes": a 5-minute window is a few
hundred ticks, nowhere near any cap.

Design:
- `RealtimeStream(ticker)` opens the socket on a **background daemon thread** and
  appends every trade to a thread-safe ring buffer (`collections.deque`).
- The Streamlit fragment polls `.snapshot()` ~once/second to redraw — the socket
  thread keeps filling the buffer between redraws, so no tick is missed.
- Everything degrades: no `FINNHUB_KEY` → `available` is False and the caller
  falls back to the existing 60-second REST polling mode.

US equities only trade tick data during regular hours on the free plan; outside
RTH the stream is quiet (the UI says so).
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

try:                                    # optional dependency
    import websocket                    # websocket-client
    _HAS_WS = True
except Exception:                       # pragma: no cover
    _HAS_WS = False

_WS_URL = "wss://ws.finnhub.io"
_MAX_TICKS = 5000                        # ~enough for a long session; ring-buffered


@dataclass(frozen=True)
class PriceTick:
    ts: float        # unix seconds (exchange time)
    price: float
    volume: float    # trade size (shares)


@dataclass(frozen=True)
class StreamSnapshot:
    ticks: list[PriceTick]
    connected: bool
    error: str | None
    n_received: int


class RealtimeStream:
    """Background Finnhub trade-WebSocket subscriber with a thread-safe buffer."""

    def __init__(self, ticker: str, api_key: str | None = None):
        self.ticker = ticker.upper()
        self.api_key = (api_key or os.environ.get("FINNHUB_KEY", "")).strip()
        self._ticks: deque[PriceTick] = deque(maxlen=_MAX_TICKS)
        self._lock = threading.Lock()
        self._ws: "websocket.WebSocketApp | None" = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._error: str | None = None
        self._n_received = 0
        self._stop = False

    @property
    def available(self) -> bool:
        return _HAS_WS and bool(self.api_key)

    # --- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        """Open the socket on a daemon thread. Returns False if unavailable."""
        if not self.available:
            self._error = ("FINNHUB_KEY not set" if _HAS_WS
                           else "websocket-client not installed")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop = False
        self._thread = threading.Thread(target=self._run, name=f"rt-{self.ticker}",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop = True
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    # --- websocket callbacks ------------------------------------------------
    def _run(self) -> None:
        url = f"{_WS_URL}?token={self.api_key}"
        try:
            self._ws = websocket.WebSocketApp(
                url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            # ping keeps the connection alive through idle (quiet-market) periods.
            self._ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as exc:          # pragma: no cover
            self._error = str(exc)
            self._connected = False

    def _on_open(self, ws) -> None:
        self._connected = True
        self._error = None
        ws.send(json.dumps({"type": "subscribe", "symbol": self.ticker}))

    def _on_message(self, ws, message: str) -> None:
        try:
            msg = json.loads(message)
        except (ValueError, TypeError):
            return
        if msg.get("type") != "trade":
            return
        new: list[PriceTick] = []
        for t in msg.get("data", []):
            try:
                new.append(PriceTick(
                    ts=float(t["t"]) / 1000.0,   # Finnhub ms → seconds
                    price=float(t["p"]),
                    volume=float(t.get("v", 0.0)),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        if new:
            with self._lock:
                self._ticks.extend(new)
                self._n_received += len(new)

    def _on_error(self, ws, error) -> None:
        self._error = str(error)
        self._connected = False

    def _on_close(self, ws, *_args) -> None:
        self._connected = False

    # --- reader -------------------------------------------------------------
    def snapshot(self) -> StreamSnapshot:
        with self._lock:
            ticks = list(self._ticks)
            n = self._n_received
        return StreamSnapshot(ticks=ticks, connected=self._connected,
                              error=self._error, n_received=n)


# --------------------------------------------------------------------------- #
# Pure analytics over a tick trace (testable, no I/O).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TraceStats:
    last: float
    first: float
    change: float
    change_pct: float
    high: float
    low: float
    ema: float                 # short EMA of recent prices (smooths the wiggle)
    momentum: str              # "rising" / "falling" / "flat" (price vs its EMA + slope)
    n_ticks: int
    cum_volume: float
    elapsed_s: float


def ticks_to_candles(ticks: list[PriceTick], interval_s: int = 60):
    """Aggregate a tick trace into OHLCV candles of `interval_s` seconds.

    Pure function — used to render a 'forming candle' / heartbeat panel from the
    live stream. Returns a pandas DataFrame indexed by candle-start (UTC) with
    open/high/low/close/volume columns (empty frame if no ticks).
    """
    import pandas as pd

    cols = ["open", "high", "low", "close", "volume"]
    if not ticks:
        return pd.DataFrame(columns=cols)
    buckets: dict[int, dict] = {}
    order: list[int] = []
    for t in ticks:
        b = int(t.ts // interval_s) * interval_s
        if b not in buckets:
            buckets[b] = {"open": t.price, "high": t.price, "low": t.price,
                          "close": t.price, "volume": t.volume}
            order.append(b)
        else:
            r = buckets[b]
            r["high"] = max(r["high"], t.price)
            r["low"] = min(r["low"], t.price)
            r["close"] = t.price
            r["volume"] += t.volume
    idx = pd.to_datetime(order, unit="s", utc=True)
    return pd.DataFrame([buckets[b] for b in order], index=idx)[cols]


def summarize(ticks: list[PriceTick], ema_span: int = 20) -> TraceStats | None:
    """Compute a live read from the tick trace. Returns None if there are no ticks."""
    if not ticks:
        return None
    prices = [t.price for t in ticks]
    last, first = prices[-1], prices[0]
    change = last - first
    change_pct = (change / first * 100.0) if first else 0.0

    # Incremental EMA over the trace (no pandas dependency in this hot path).
    alpha = 2.0 / (ema_span + 1.0)
    ema = prices[0]
    for p in prices[1:]:
        ema = alpha * p + (1 - alpha) * ema

    # Momentum: price relative to EMA, plus slope of the last few ticks.
    tail = prices[-min(len(prices), 5):]
    slope = tail[-1] - tail[0]
    eps = max(1e-9, last * 0.0005)       # 0.05% deadband to avoid jitter
    if last > ema + eps and slope > 0:
        momentum = "rising"
    elif last < ema - eps and slope < 0:
        momentum = "falling"
    else:
        momentum = "flat"

    return TraceStats(
        last=last, first=first, change=change, change_pct=change_pct,
        high=max(prices), low=min(prices), ema=ema, momentum=momentum,
        n_ticks=len(ticks), cum_volume=sum(t.volume for t in ticks),
        elapsed_s=(ticks[-1].ts - ticks[0].ts),
    )
