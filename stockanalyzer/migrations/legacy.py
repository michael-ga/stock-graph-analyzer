"""Read-only readers for pre-PostgreSQL state (JSON symbol files, SQLite ``trades.db``).

The application no longer writes SQLite; these helpers exist only so
``scripts/migrate_legacy_data.py`` can import an old ``trades.db`` into the
SQLAlchemy repositories. The database is opened read-only and never migrated in
place: rows are converted to the dict shape ``TradeRepository.insert`` /
``PaperTradeRepository.insert`` expect, with the per-trade decision context
(signals, indicators, verdict, swing checks) folded back into the snapshot so the
import re-creates those child rows too.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# Legacy flattened snapshot columns → snapshot keys.
_SNAPSHOT_COLUMNS = {
    "snap_score": "score", "snap_label": "label", "snap_setup": "setup",
    "snap_kind": "kind", "snap_rr": "rr", "snap_daily_atr_pct": "daily_atr_pct",
    "snap_guidance": "guidance",
}
_RECOMMENDATION_COLUMNS = {
    "snap_go_score": "go_score", "snap_light_color": "light_color",
    "snap_preset": "preset", "snap_bullish_pct": "bullish_pct",
}
_VERDICT_BIAS_COLUMNS = {
    "bias_1d": "1D", "bias_5d": "5D", "bias_1m": "1M", "bias_6m": "6M",
    "bias_ytd": "YTD", "bias_1y": "1Y", "bias_5y": "5Y",
}


def read_symbol_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    value = json.loads(path.read_text())
    if isinstance(value, dict):
        value = value.get("tickers", value.get("symbols", []))
    if not isinstance(value, list):
        raise ValueError("Legacy symbol file must contain a list")
    return sorted({str(item).strip().upper() for item in value if str(item).strip()})


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Rows of ``sql``, or [] when the table predates the legacy schema version."""
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise


def repair_closed_ts(trade: dict) -> dict:
    """Backfill ``closed_ts`` when it is NULL or impossibly before ``opened_ts``.

    Old rows (JSON migration, a batch closed with test-sized clocks) carry NULL or
    near-epoch ``closed_ts``. Recover it from the human ``closed`` text, falling
    back to ``opened_ts``; never raises (out-of-range years raise OverflowError)."""
    if trade.get("status") != "closed":
        return trade
    opened_ts = trade.get("opened_ts") or 0.0
    closed_ts = trade.get("closed_ts")
    if closed_ts is not None and closed_ts >= opened_ts:
        return trade
    ts = None
    if trade.get("closed"):
        try:
            ts = time.mktime(time.strptime(trade["closed"], "%Y-%m-%d %H:%M"))
        except Exception:
            ts = None
    trade["closed_ts"] = ts if ts is not None and ts >= opened_ts else opened_ts
    return trade


def _snapshot(row: dict) -> dict:
    snapshot = {key: row.pop(column, None) for column, key in _SNAPSHOT_COLUMNS.items()}
    snapshot["manual_levels"] = bool(row.pop("snap_manual_levels", 0) or 0)
    recommendation = {key: row.pop(column, None)
                      for column, key in _RECOMMENDATION_COLUMNS.items()}
    if any(value is not None for value in recommendation.values()):
        snapshot["recommendation"] = recommendation
    return snapshot


def _attach_context(conn: sqlite3.Connection, trades: dict[str, dict]) -> None:
    for row in _rows(conn, "SELECT * FROM trade_signals ORDER BY id"):
        trade = trades.get(row["trade_id"])
        if trade is None:
            continue
        frames = trade["snapshot"].setdefault("timeframes", {})
        frames.setdefault(row["timeframe"], {}).setdefault("signals", []).append({
            "name": row["name"], "direction": row["direction"],
            "strength": row["strength"], "category": row["category"],
            "evidence": row["evidence"],
        })
    for row in _rows(conn, "SELECT * FROM trade_indicators ORDER BY id"):
        trade = trades.get(row["trade_id"])
        if trade is None:
            continue
        values = dict(row)
        for column in ("id", "trade_id", "timeframe"):
            values.pop(column, None)
        frame = trade["snapshot"].setdefault("timeframes", {}).setdefault(
            row["timeframe"], {})
        frame["bias_score"] = values.pop("bias_score", None)
        frame["trend_dir"] = values.pop("trend_dir", None)
        frame["indicators"] = values
    for row in _rows(conn, "SELECT * FROM trade_verdict"):
        trade = trades.get(row["trade_id"])
        if trade is None:
            continue
        values = dict(row)
        trade["snapshot"]["verdict"] = {
            "label": values.get("label"), "direction": values.get("direction"),
            "score": values.get("score"), "confidence": values.get("confidence"),
            "per_timeframe": {frame: values[column]
                              for column, frame in _VERDICT_BIAS_COLUMNS.items()
                              if values.get(column) is not None},
        }
    for row in _rows(conn, "SELECT * FROM trade_swing_checks ORDER BY id"):
        trade = trades.get(row["trade_id"])
        if trade is None:
            continue
        trade["snapshot"].setdefault("checks", []).append({
            "name": row["name"], "ok": bool(row["ok"]), "na": bool(row["na"]),
            "detail": row["detail"], "weight": int(row["weight"] or 0),
        })


def read_legacy_trades(path: Path) -> list[dict]:
    """Every virtual-book trade in a legacy SQLite ``trades.db`` (newest first)."""
    conn = _connect_read_only(path)
    try:
        trades: dict[str, dict] = {}
        for row in _rows(conn, "SELECT * FROM trades ORDER BY opened_ts DESC"):
            trade = dict(row)
            trade["trigger"] = trade.pop("trigger_price", None)
            trade["snapshot"] = _snapshot(trade)
            for flag in ("managed", "hold_weekend"):
                if flag in trade:
                    trade[flag] = bool(trade[flag])
            trades[trade["id"]] = repair_closed_ts(trade)
        _attach_context(conn, trades)
        return list(trades.values())
    finally:
        conn.close()


def read_legacy_paper_trades(path: Path) -> list[dict]:
    """Every paper-trade proposition in a legacy SQLite ``trades.db`` (newest first)."""
    conn = _connect_read_only(path)
    try:
        records = []
        for row in _rows(conn, "SELECT * FROM paper_trades ORDER BY ts DESC"):
            record = dict(row)
            record["trigger"] = record.pop("trigger_price", None)
            records.append(record)
        return records
    finally:
        conn.close()
