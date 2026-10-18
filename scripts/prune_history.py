#!/usr/bin/env python3
"""Delete only disposable expired cache data; durable history is opt-in."""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete
from stockanalyzer.config import Settings
from stockanalyzer.db.models import AnalysisRun, ApiCacheEntry, OhlcvBar
from stockanalyzer.db.session import configure_engine, session_scope


def prune(*, intraday_days: int = 30, history_days: int | None = None) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        api = session.execute(delete(ApiCacheEntry).where(ApiCacheEntry.expires_at < now)).rowcount or 0
        # Short rolling ranges are disposable; longer history is retained.
        bars = session.execute(delete(OhlcvBar).where(
            OhlcvBar.timeframe.in_(("1D", "5D")),
            OhlcvBar.fetched_at < now - timedelta(days=intraday_days),
        )).rowcount or 0
        history = 0
        if history_days is not None:
            history = session.execute(delete(AnalysisRun).where(
                AnalysisRun.completed_at < now - timedelta(days=history_days))).rowcount or 0
    return {"api_cache": api, "intraday_bars": bars, "analysis_history": history}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intraday-days", type=int, default=30)
    parser.add_argument("--history-days", type=int)
    args = parser.parse_args()
    configure_engine(Settings.from_env().database_url)
    print(prune(intraday_days=max(1, args.intraday_days), history_days=args.history_days))


if __name__ == "__main__":
    main()
