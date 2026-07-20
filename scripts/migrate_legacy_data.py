#!/usr/bin/env python3
"""Idempotently import supported legacy files into PostgreSQL (dry-run by default)."""
from __future__ import annotations
import argparse
import json
from datetime import date
from pathlib import Path
from sqlalchemy import select
from stockanalyzer.config import Settings
from stockanalyzer.data.store import load_paper_trades, load_trades
from stockanalyzer.db.models import User
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.repositories.rate_limits import RateLimitRepository
from stockanalyzer.db.repositories.swingwatch import SwingWatchRepository
from stockanalyzer.db.repositories.trades import TradeRepository
from stockanalyzer.db.repositories.watchlist import WatchlistRepository
from stockanalyzer.db.session import configure_engine, session_scope, transaction_scope


def _symbols(path: Path) -> list[str]:
    if not path.exists(): return []
    value = json.loads(path.read_text())
    if isinstance(value, dict): value = value.get("tickers", value.get("symbols", []))
    return sorted({str(item).strip().upper() for item in value if str(item).strip()})


def _rate_counts(path: Path) -> tuple[date | None, dict[str, int]]:
    if not path.exists():
        return None, {}
    value = json.loads(path.read_text())
    try:
        bucket = date.fromisoformat(str(value.get("day", "")))
    except ValueError:
        return None, {}
    counts = {
        str(provider).strip().lower(): max(0, int(count))
        for provider, count in (value.get("counts") or {}).items()
    }
    return bucket, counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--sqlite", type=Path, default=Path("trades.db"))
    parser.add_argument("--watchlist", type=Path, default=Path(".watchlist.json"))
    parser.add_argument("--swingwatch", type=Path, default=Path(".swingwatch.json"))
    parser.add_argument("--ratelimit", type=Path, default=Path(".ratelimit.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    configure_engine(Settings.from_env().database_url)
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == args.username.strip().lower()))
        if user is None: raise SystemExit("Target user does not exist")
        user_id = user.id
    trades = load_trades(args.sqlite) if args.sqlite.exists() else []
    papers = load_paper_trades(args.sqlite) if args.sqlite.exists() else []
    watch = _symbols(args.watchlist); swing = _symbols(args.swingwatch)
    rate_day, rate_counts = _rate_counts(args.ratelimit)
    print({"trades": len(trades), "paper_trades": len(papers),
           "watchlist": len(watch), "swingwatch": len(swing),
           "rate_limit_counters": len(rate_counts), "apply": args.apply})
    if not args.apply: return
    with transaction_scope():
        tr = TradeRepository(); pr = PaperTradeRepository()
        existing = {row["id"] for row in tr.list(user_id)}
        for row in trades:
            if row["id"] not in existing: tr.insert(user_id, row, row.get("snapshot"))
        # Paper repository's 24h uniqueness makes reruns idempotent for source propositions.
        for row in reversed(papers): pr.insert(user_id, row)
        wr = WatchlistRepository(); sr = SwingWatchRepository()
        for ticker in watch: wr.add(user_id, ticker)
        for ticker in swing: sr.add(user_id, ticker)
        if rate_day is not None:
            rate_repository = RateLimitRepository()
            for provider, count in rate_counts.items():
                rate_repository.import_count(provider, rate_day, count)
    print("Import committed atomically")


if __name__ == "__main__":
    main()
