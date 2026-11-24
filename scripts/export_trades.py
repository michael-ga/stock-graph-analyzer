#!/usr/bin/env python3
"""Fast, read-only export of one user's trade data — no Streamlit, no radar.

Reads straight from the database the app uses and writes three tables:

  swing_journal   paper-trade journal (radar propositions)
  bot_positions   live virtual positions (open + pending)
  bot_history     closed virtual trades (bot + manual)

Every row keeps all stored fields; the columns the app shows come first.

Usage (on the server, output streams to stdout so the read-only container needs
no writable folder):

    docker compose exec -T app python -m scripts.export_trades --username admin > trades.zip
    docker compose exec -T app python -m scripts.export_trades --username admin --format json > trades.json

Locally, write a folder of .json + .csv files instead:

    python -m scripts.export_trades --username admin --out exports

``--from-raw FILE`` builds the same files from raw rows that
``scripts/fetch_trades.sh`` pulled straight out of PostgreSQL with psql (its
default "sql" mode), so the server does not need this script deployed.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from stockanalyzer import papertrade, virtualbook
from stockanalyzer.config import Settings
from stockanalyzer.db.models import User
from stockanalyzer.db.session import configure_engine, session_scope

# Columns the app shows, in the app's order. Any other stored field follows.
SWING_COLS = ["date", "ticker", "level", "score", "kind", "setup", "entry", "stop",
              "target", "rr", "status", "result_pct"]
POSITION_COLS = ["ticker", "trader", "status", "opened_ts", "stake", "entry", "stop",
                 "target", "trigger"]
HISTORY_COLS = ["closed_ts", "ticker", "trader", "close_reason", "stake", "entry",
                "exit_price", "pnl_pct", "pnl_usd", "score", "setup"]
TABLES = ("swing_journal", "bot_positions", "bot_history")


def _user_id(username: str) -> str:
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == username.strip().lower()))
        if user is None:
            raise SystemExit(f"No user named {username!r}")
        return user.id


def _iso(value):
    """Epoch seconds → ISO-8601 UTC; anything else unchanged."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1e9:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return value


def _flatten_bot(p: dict) -> dict:
    """Lift the snapshot's score/setup to the top (as the app's table does)."""
    row = dict(p)
    snap = row.get("snapshot") or {}
    if isinstance(snap, dict):
        row.setdefault("score", snap.get("score"))
        row.setdefault("setup", snap.get("setup"))
    for key in list(row):
        if key.endswith("_ts") or key in ("opened", "closed"):
            row[key] = _iso(row[key])
    return row


def collect(user_id: str) -> dict[str, list[dict]]:
    """All three tables for ``user_id``, newest first."""
    return shape(papertrade.load(user_id=user_id), virtualbook.load(user_id=user_id))


def from_raw(raw: dict) -> dict[str, list[dict]]:
    """Tables from the raw rows ``scripts/fetch_trades.sh`` (sql mode) pulls with
    psql: ``{"journal": [paper_trades rows], "book": [trades rows]}``. Mirrors the
    repositories' row → record mapping so both modes export the same shape."""
    journal = []
    for r in raw.get("journal") or []:
        journal.append({"id": r.get("id"), "source_id": r.get("source_id"),
                        "ts": r.get("ts"), "date": r.get("date"),
                        "ticker": r.get("ticker"), "level": r.get("level"),
                        **(r.get("payload") or {}), "status": r.get("status"),
                        "result_pct": r.get("result_pct")})
    book = []
    for r in raw.get("book") or []:
        row = {k: v for k, v in r.items() if k not in ("user_id", "trigger_price")}
        row["trigger"] = r.get("trigger_price")
        row["snapshot"] = r.get("snapshot") or {}
        book.append(row)
    return shape(journal, book)


def shape(journal_records: list[dict], book_records: list[dict]) -> dict[str, list[dict]]:
    journal = sorted(journal_records, key=lambda r: r.get("ts") or 0, reverse=True)
    for r in journal:
        if "ts" in r:
            r["ts"] = _iso(r["ts"])
    book = [_flatten_bot(p) for p in book_records]
    positions = [p for p in book if p.get("status") in ("open", "pending")]
    history = [p for p in book if p.get("status") == "closed"]
    history.sort(key=lambda p: str(p.get("closed_ts") or p.get("closed") or ""),
                 reverse=True)
    return {"swing_journal": journal, "bot_positions": positions, "bot_history": history}


def _ordered_columns(rows: list[dict], first: list[str]) -> list[str]:
    seen = {c: None for c in first if any(c in r for r in rows)}
    for r in rows:
        for c in r:
            seen.setdefault(c, None)
    return list(seen)


def to_csv(rows: list[dict], first: list[str]) -> str:
    cols = _ordered_columns(rows, first)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({c: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
                         for c, v in r.items()})
    return buf.getvalue()


def build_files(data: dict[str, list[dict]], username: str) -> dict[str, str]:
    """filename → text for every export file."""
    firsts = {"swing_journal": SWING_COLS, "bot_positions": POSITION_COLS,
              "bot_history": HISTORY_COLS}
    files: dict[str, str] = {}
    for name in TABLES:
        files[f"{name}.json"] = json.dumps(data[name], indent=1, default=str)
        files[f"{name}.csv"] = to_csv(data[name], firsts[name])
    files["all.json"] = json.dumps(
        {"username": username, "exported_at": _iso(time.time()),
         "counts": {n: len(data[n]) for n in TABLES}, **data},
        indent=1, default=str)
    return files


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Export trade history (read-only)")
    parser.add_argument("--username", required=True)
    parser.add_argument("--format", choices=("zip", "json"), default="zip",
                        help="what to write to stdout (ignored with --out)")
    parser.add_argument("--out", help="write .json/.csv files into this folder instead")
    parser.add_argument("--from-raw", metavar="FILE",
                        help="build the files from raw psql rows (fetch_trades.sh "
                             "sql mode) instead of querying the database")
    args = parser.parse_args(argv)

    if args.from_raw:
        text = Path(args.from_raw).read_text(encoding="utf-8").strip()
        if not text:
            raise SystemExit(f"No user named {args.username!r} on the server")
        data = from_raw(json.loads(text))
    else:
        configure_engine(Settings.from_env().database_url)
        data = collect(_user_id(args.username))
    files = build_files(data, args.username)
    counts = ", ".join(f"{n}={len(data[n])}" for n in TABLES)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (out / name).write_text(text, encoding="utf-8", newline="")
        print(f"Wrote {len(files)} files to {out} ({counts})", file=sys.stderr)
    elif args.format == "json":
        sys.stdout.write(files["all.json"])
        print(f"Exported {counts}", file=sys.stderr)
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in files.items():
                zf.writestr(name, text)
        sys.stdout.buffer.write(buf.getvalue())
        print(f"Exported {counts}", file=sys.stderr)


if __name__ == "__main__":
    main()
