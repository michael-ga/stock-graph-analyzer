from __future__ import annotations

from datetime import datetime, timezone

from stockanalyzer.db.models import WorkerStatus
from stockanalyzer.db.session import session_scope


def _utc(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every stored value is UTC."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _row(row: WorkerStatus) -> dict:
    return {
        "name": row.name, "release": row.release, "build": row.build,
        "started_at": _utc(row.started_at), "heartbeat_at": _utc(row.heartbeat_at),
        "phase": row.phase,
    }


class WorkerStatusRepository:
    """One status row per background process. Only that process writes its row."""

    def get(self, name: str) -> dict | None:
        with session_scope() as s:
            row = s.get(WorkerStatus, name)
            return _row(row) if row else None

    def start(self, name: str, *, release: str, build: str, phase: str | None,
              now: datetime) -> None:
        """A new process run: (re)create the row."""
        with session_scope() as s:
            row = s.get(WorkerStatus, name)
            if row is None:
                row = WorkerStatus(name=name)
                s.add(row)
            row.release, row.build, row.phase = release, build, phase
            row.started_at = row.heartbeat_at = now

    def beat(self, name: str, *, phase: str | None, now: datetime) -> bool:
        """Refresh liveness. Returns False when the row does not exist yet."""
        with session_scope() as s:
            row = s.get(WorkerStatus, name)
            if row is None:
                return False
            row.heartbeat_at, row.phase = now, phase
            return True
