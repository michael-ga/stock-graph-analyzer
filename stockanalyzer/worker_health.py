"""Worker heartbeat probe. Keep imports in this module standard-library only."""
from __future__ import annotations

import math
import os
import time
from pathlib import Path


def write_heartbeat(path: Path, *, now: float | None = None) -> None:
    timestamp = time.time() if now is None else float(now)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(f"{timestamp}\n", encoding="utf-8")
    temporary.replace(path)


def heartbeat_is_healthy(
    path: Path, *, now: float | None = None, max_age: float = 120.0,
) -> bool:
    try:
        timestamp = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    current = time.time() if now is None else float(now)
    return math.isfinite(timestamp) and 0 <= current - timestamp <= max_age


def main() -> int:
    try:
        max_age = float(os.getenv("RADAR_WORKER_HEALTH_MAX_AGE_SECONDS", "180"))
        if not math.isfinite(max_age) or max_age <= 0:
            return 1
    except ValueError:
        return 1
    path = Path(os.getenv("RADAR_WORKER_HEARTBEAT", "/tmp/radar-worker-heartbeat"))
    return 0 if heartbeat_is_healthy(path, max_age=max_age) else 1


if __name__ == "__main__":
    raise SystemExit(main())
