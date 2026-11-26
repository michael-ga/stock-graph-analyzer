"""Whether the background radar worker is alive, readable from the app.

The worker's heartbeat thread writes here; the app only reads. Writes never raise: a
status that cannot be saved must not stop the worker from trading."""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from stockanalyzer import session as market_session
from stockanalyzer.db.repositories.worker_status import WorkerStatusRepository
from stockanalyzer.version import release

logger = logging.getLogger(__name__)

WORKER = "radar-worker"
# The worker's own container health check allows 180 s without a heartbeat.
STALE_AFTER_S = 180.0

_REPOSITORY = WorkerStatusRepository()
_started = False
_in_flight = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _phase() -> str | None:
    try:
        return market_session.market_phase(market_session.now_et().to_pydatetime())
    except Exception:  # a clock problem must not hide a live worker
        return None


def beat() -> None:
    """Record that the worker is alive. The first call of a process run starts a
    fresh status row; later calls refresh liveness and the market phase."""
    global _started
    try:
        if not _started or not _REPOSITORY.beat(WORKER, phase=_phase(), now=_now()):
            _REPOSITORY.start(
                WORKER, release=release(),
                build=os.environ.get("APP_VERSION", "development")[:64],
                phase=_phase(), now=_now())
            _started = True
    except Exception:
        logger.debug("worker status beat not saved", exc_info=True)


def beat_in_background() -> bool:
    """``beat()`` on its own daemon thread, so a slow or hung database cannot hold up
    the caller. Skipped (False) while the previous beat is still running."""
    if not _in_flight.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            beat()
        finally:
            _in_flight.release()

    threading.Thread(target=run, name="worker-status-beat", daemon=True).start()
    return True


def current() -> dict | None:
    """The worker's status row, or None when no worker has ever reported."""
    try:
        return _REPOSITORY.get(WORKER)
    except Exception:
        logger.debug("worker status not readable", exc_info=True)
        return None


def health(status: dict | None, now: datetime | None = None) -> tuple[str, float | None]:
    """``("never"|"running"|"stale", seconds since the last heartbeat)``."""
    if status is None:
        return "never", None
    age = ((now or _now()) - status["heartbeat_at"]).total_seconds()
    return ("running" if age <= STALE_AFTER_S else "stale"), max(age, 0.0)
