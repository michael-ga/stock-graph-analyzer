"""Token-bucket rate limiter for the free-tier data providers.

Per-provider caps (free tiers, 2026):
  twelvedata : 8 requests/min  AND  800/day
  finnhub    : 60 requests/min
  yfinance   : no official cap — we self-throttle to ~30/min to be polite

`acquire(provider)` consumes one token, sleeping briefly if the per-minute bucket
is empty. If the per-day cap is exceeded it raises RateLimitExceeded so the caller
can serve cached (possibly stale) data instead of hammering the API.

Daily counts persist to .ratelimit.json (reset by calendar date) so the 800/day
budget survives app restarts. Minute buckets are in-memory (process-local).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock

_STATE_PATH = Path(__file__).resolve().parent.parent.parent / ".ratelimit.json"


class RateLimitExceeded(RuntimeError):
    """Raised when a provider's daily cap is reached."""


@dataclass(frozen=True)
class Caps:
    per_min: int
    per_day: int | None = None


CAPS: dict[str, Caps] = {
    "twelvedata": Caps(per_min=8, per_day=800),
    "finnhub": Caps(per_min=60),
    "yfinance": Caps(per_min=30),
}

_DEFAULT = Caps(per_min=20)


class _Bucket:
    """A simple token bucket: `per_min` tokens, refilling continuously.

    Thread-safe: timeframe fetches now run concurrently, so refill+take must be
    atomic or parallel callers could double-spend the same token.
    """

    def __init__(self, per_min: int):
        self.capacity = float(per_min)
        self.tokens = float(per_min)
        self.refill_per_sec = per_min / 60.0
        self.last = time.monotonic()
        self._lock = Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now

    def take(self, max_wait: float = 12.0) -> bool:
        """Consume a token, waiting up to max_wait seconds for one to refill."""
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.25, self.refill_per_sec and 1.0 / self.refill_per_sec or 0.25))


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = Lock()
        self._day = date.today().isoformat()
        self._daily: dict[str, int] = {}
        self._load()

    # --- persistence (daily counts) ------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(_STATE_PATH.read_text())
            if data.get("day") == self._day:
                self._daily = dict(data.get("counts", {}))
        except Exception:
            self._daily = {}

    def _save(self) -> None:
        try:
            _STATE_PATH.write_text(json.dumps({"day": self._day, "counts": self._daily}))
        except Exception:
            pass

    def _roll_day(self) -> None:
        today = date.today().isoformat()
        if today != self._day:
            self._day = today
            self._daily = {}
            self._save()

    # --- public API ----------------------------------------------------------
    def acquire(self, provider: str) -> None:
        caps = CAPS.get(provider, _DEFAULT)
        with self._lock:
            self._roll_day()
            if caps.per_day is not None and self._daily.get(provider, 0) >= caps.per_day:
                raise RateLimitExceeded(
                    f"{provider} daily cap ({caps.per_day}) reached — using cached data."
                )
            bucket = self._buckets.get(provider)
            if bucket is None:
                bucket = self._buckets[provider] = _Bucket(caps.per_min)

        if not bucket.take():
            raise RateLimitExceeded(f"{provider} per-minute limit busy — using cached data.")

        with self._lock:
            self._daily[provider] = self._daily.get(provider, 0) + 1
            self._save()

    def remaining_today(self, provider: str) -> int | None:
        caps = CAPS.get(provider, _DEFAULT)
        if caps.per_day is None:
            return None
        with self._lock:
            self._roll_day()
            return max(0, caps.per_day - self._daily.get(provider, 0))


# Process-wide singleton.
LIMITER = RateLimiter()
