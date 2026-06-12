"""SQLite persistence layer for trades and paper-trade propositions.

All trade data — positions, decision signals, indicator snapshots, verdicts,
swing checks — lives in a single ``trades.db`` file so it survives restarts and
deploys.  On first run the module auto-migrates any existing JSON files.

The API is intentionally functional (module-level functions with a default
``db_path``), matching the style of the modules it replaces.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

# Default DB lives at the project root; STOCKANALYZER_DB (read at import time)
# lets deployments and tests point the whole store elsewhere.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "trades.db"
DB_PATH = (Path(os.environ["STOCKANALYZER_DB"])
           if os.environ.get("STOCKANALYZER_DB") else _DEFAULT_DB_PATH)
SCHEMA_VERSION = 2

_connections: dict[str, sqlite3.Connection] = {}


# ── connection ────────────────────────────────────────────────────────────── #

def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    key = str(db_path)
    if key not in _connections:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_schema(conn, db_path)
        _connections[key] = conn
    return _connections[key]


# ── schema ────────────────────────────────────────────────────────────────── #

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id            TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    trader        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    kind          TEXT NOT NULL DEFAULT 'immediate',
    opened_ts     REAL NOT NULL,
    opened        TEXT NOT NULL,
    activated_ts  REAL,
    entry         REAL NOT NULL,
    stop          REAL NOT NULL,
    target        REAL NOT NULL,
    trigger_price REAL,
    stake         REAL NOT NULL DEFAULT 1000.0,
    shares        REAL NOT NULL DEFAULT 0.0,
    horizon_days  INTEGER NOT NULL DEFAULT 3,
    exit_price    REAL,
    close_reason  TEXT,
    closed_ts     REAL,
    closed        TEXT,
    pnl_pct       REAL DEFAULT 0.0,
    pnl_usd       REAL DEFAULT 0.0,
    snap_score         INTEGER,
    snap_label         TEXT,
    snap_setup         TEXT,
    snap_kind          TEXT,
    snap_rr            REAL,
    snap_daily_atr_pct REAL,
    snap_guidance      TEXT,
    snap_go_score      INTEGER,
    snap_light_color   TEXT,
    snap_preset        TEXT,
    snap_bullish_pct   INTEGER,
    snap_manual_levels INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_trader ON trades(trader);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_ts);

CREATE TABLE IF NOT EXISTS trade_signals (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id  TEXT NOT NULL REFERENCES trades(id),
    timeframe TEXT NOT NULL,
    name      TEXT NOT NULL,
    direction TEXT NOT NULL,
    strength  REAL NOT NULL,
    category  TEXT NOT NULL,
    evidence  TEXT
);
CREATE INDEX IF NOT EXISTS idx_tsig_trade ON trade_signals(trade_id);
CREATE INDEX IF NOT EXISTS idx_tsig_name  ON trade_signals(name);

CREATE TABLE IF NOT EXISTS trade_indicators (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    TEXT NOT NULL REFERENCES trades(id),
    timeframe   TEXT NOT NULL,
    close       REAL,
    sma20       REAL,
    sma50       REAL,
    sma200      REAL,
    ema20       REAL,
    rsi         REAL,
    macd        REAL,
    macd_signal REAL,
    macd_hist   REAL,
    stoch_k     REAL,
    stoch_d     REAL,
    atr         REAL,
    bias_score  REAL,
    trend_dir   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tind_trade ON trade_indicators(trade_id);

CREATE TABLE IF NOT EXISTS trade_verdict (
    trade_id   TEXT PRIMARY KEY REFERENCES trades(id),
    label      TEXT,
    direction  TEXT,
    score      REAL,
    confidence REAL,
    bias_1d    REAL,
    bias_5d    REAL,
    bias_1m    REAL,
    bias_6m    REAL,
    bias_ytd   REAL,
    bias_1y    REAL,
    bias_5y    REAL
);

CREATE TABLE IF NOT EXISTS trade_swing_checks (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL REFERENCES trades(id),
    name     TEXT NOT NULL,
    ok       INTEGER NOT NULL,
    na       INTEGER NOT NULL DEFAULT 0,
    detail   TEXT,
    weight   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tsc_trade ON trade_swing_checks(trade_id);

CREATE TABLE IF NOT EXISTS paper_trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    date          TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    level         INTEGER NOT NULL,
    score         INTEGER,
    label         TEXT,
    kind          TEXT,
    setup         TEXT,
    entry         REAL NOT NULL,
    stop          REAL NOT NULL,
    target        REAL NOT NULL,
    rr            REAL,
    trigger_price REAL,
    horizon_days  INTEGER DEFAULT 3,
    guidance      TEXT,
    status        TEXT DEFAULT 'open',
    result_pct    REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_pt_ticker ON paper_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_pt_status ON paper_trades(status);
"""


def _ensure_schema(conn: sqlite3.Connection, db_path: Path) -> None:
    conn.executescript(_DDL)
    row = conn.execute(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    ).fetchone()
    current = row["version"] if row else 0
    if current < 1:
        conn.execute("INSERT INTO schema_version VALUES (?, ?)",
                     (1, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        _migrate_json(conn, db_path)
        current = 1
    if current < 2:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "closed_ts" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN closed_ts REAL")
            conn.commit()
        conn.execute("INSERT OR REPLACE INTO schema_version VALUES (?, ?)",
                     (2, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()


# ── JSON migration ────────────────────────────────────────────────────────── #

def _migrate_json(conn: sqlite3.Connection, db_path: Path) -> None:
    root = db_path.parent
    _migrate_virtualbook(conn, root / ".virtualbook.json")
    _migrate_papertrade(conn, root / ".papertrade.json")


def _migrate_virtualbook(conn: sqlite3.Connection, path: Path) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list) or not data:
            return
    except Exception:
        return
    existing = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if existing:
        return
    for p in data:
        snap = p.get("snapshot") or {}
        conn.execute(
            """INSERT OR IGNORE INTO trades
               (id, ticker, trader, status, kind, opened_ts, opened,
                activated_ts, entry, stop, target, trigger_price, stake,
                shares, horizon_days, exit_price, close_reason, closed,
                pnl_pct, pnl_usd, snap_score, snap_label, snap_setup,
                snap_kind, snap_rr, snap_daily_atr_pct, snap_guidance,
                snap_manual_levels)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p.get("id"), p.get("ticker"), p.get("trader"), p.get("status"),
             p.get("kind"), p.get("opened_ts"), p.get("opened"),
             p.get("activated_ts"), p.get("entry"), p.get("stop"),
             p.get("target"), p.get("trigger"),
             p.get("stake", 1000.0), p.get("shares", 0.0),
             p.get("horizon_days", 3), p.get("exit_price"),
             p.get("close_reason"), p.get("closed"),
             p.get("pnl_pct", 0.0), p.get("pnl_usd", 0.0),
             snap.get("score"), snap.get("label"), snap.get("setup"),
             snap.get("kind"), snap.get("rr"), snap.get("daily_atr_pct"),
             snap.get("guidance"), int(snap.get("manual_levels", False))))
    conn.commit()


def _migrate_papertrade(conn: sqlite3.Connection, path: Path) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list) or not data:
            return
    except Exception:
        return
    existing = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    if existing:
        return
    for r in data:
        conn.execute(
            """INSERT INTO paper_trades
               (ts, date, ticker, level, score, label, kind, setup, entry,
                stop, target, rr, trigger_price, horizon_days, guidance,
                status, result_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("ts"), r.get("date", ""), r.get("ticker"), r.get("level"),
             r.get("score"), r.get("label"), r.get("kind"), r.get("setup"),
             r.get("entry"), r.get("stop"), r.get("target"), r.get("rr"),
             r.get("trigger"), int(r.get("horizon_days", 3)),
             r.get("guidance"), r.get("status", "open"),
             r.get("result_pct", 0.0)))
    conn.commit()


# ── trade CRUD ────────────────────────────────────────────────────────────── #

def _row_to_trade(row: sqlite3.Row) -> dict:
    """Convert a DB row back to the dict shape the rest of the app expects."""
    d = dict(row)
    d["trigger"] = d.pop("trigger_price", None)
    d["snapshot"] = {
        "score": d.pop("snap_score", None),
        "label": d.pop("snap_label", None),
        "setup": d.pop("snap_setup", None),
        "kind": d.pop("snap_kind", None),
        "rr": d.pop("snap_rr", None),
        "daily_atr_pct": d.pop("snap_daily_atr_pct", None),
        "guidance": d.pop("snap_guidance", None),
        "go_score": d.pop("snap_go_score", None),
        "light_color": d.pop("snap_light_color", None),
        "preset": d.pop("snap_preset", None),
        "bullish_pct": d.pop("snap_bullish_pct", None),
        "manual_levels": bool(d.pop("snap_manual_levels", 0)),
    }
    return d


def load_trades(db_path: Path = DB_PATH) -> list[dict]:
    rows = _conn(db_path).execute(
        "SELECT * FROM trades ORDER BY opened_ts DESC"
    ).fetchall()
    return [_row_to_trade(r) for r in rows]


def has_open_trade(ticker: str, trader: str, db_path: Path = DB_PATH) -> bool:
    row = _conn(db_path).execute(
        "SELECT 1 FROM trades WHERE ticker=? AND trader=? AND status IN ('open','pending') LIMIT 1",
        (ticker.upper(), trader)
    ).fetchone()
    return row is not None


def insert_trade(trade: dict, context: dict | None = None,
                 db_path: Path = DB_PATH) -> dict:
    """Insert a trade and its full decision context into the DB."""
    conn = _conn(db_path)
    ctx = context or {}
    snap = ctx if ctx else trade.get("snapshot") or {}
    rec_data = snap.get("recommendation") or {}

    conn.execute(
        """INSERT INTO trades
           (id, ticker, trader, status, kind, opened_ts, opened,
            activated_ts, entry, stop, target, trigger_price, stake,
            shares, horizon_days, snap_score, snap_label, snap_setup,
            snap_kind, snap_rr, snap_daily_atr_pct, snap_guidance,
            snap_go_score, snap_light_color, snap_preset, snap_bullish_pct,
            snap_manual_levels)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (trade["id"], trade["ticker"], trade["trader"], trade["status"],
         trade["kind"], trade["opened_ts"], trade["opened"],
         trade.get("activated_ts"), trade["entry"], trade["stop"],
         trade["target"], trade.get("trigger"),
         trade.get("stake", 1000.0), trade.get("shares", 0.0),
         trade.get("horizon_days", 3),
         snap.get("score"), snap.get("label"), snap.get("setup"),
         snap.get("kind"), snap.get("rr"), snap.get("daily_atr_pct"),
         snap.get("guidance"),
         rec_data.get("go_score"), rec_data.get("light_color"),
         rec_data.get("preset"), rec_data.get("bullish_pct"),
         int(snap.get("manual_levels", False))))

    tid = trade["id"]

    # Signals per timeframe
    tf_data = snap.get("timeframes") or {}
    for tf_key, tf_info in tf_data.items():
        for sig in tf_info.get("signals") or []:
            conn.execute(
                """INSERT INTO trade_signals
                   (trade_id, timeframe, name, direction, strength, category, evidence)
                   VALUES (?,?,?,?,?,?,?)""",
                (tid, tf_key, sig["name"], sig["direction"],
                 sig["strength"], sig["category"], sig.get("evidence")))
        ind = tf_info.get("indicators") or {}
        if ind:
            conn.execute(
                """INSERT INTO trade_indicators
                   (trade_id, timeframe, close, sma20, sma50, sma200, ema20,
                    rsi, macd, macd_signal, macd_hist, stoch_k, stoch_d, atr,
                    bias_score, trend_dir)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, tf_key, ind.get("close"), ind.get("sma20"),
                 ind.get("sma50"), ind.get("sma200"), ind.get("ema20"),
                 ind.get("rsi"), ind.get("macd"), ind.get("macd_signal"),
                 ind.get("macd_hist"), ind.get("stoch_k"), ind.get("stoch_d"),
                 ind.get("atr"), tf_info.get("bias_score"),
                 tf_info.get("trend_dir")))

    # Verdict
    vdict = snap.get("verdict") or {}
    if vdict:
        ptf = vdict.get("per_timeframe") or {}
        conn.execute(
            """INSERT OR REPLACE INTO trade_verdict
               (trade_id, label, direction, score, confidence,
                bias_1d, bias_5d, bias_1m, bias_6m, bias_ytd, bias_1y, bias_5y)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, vdict.get("label"), vdict.get("direction"),
             vdict.get("score"), vdict.get("confidence"),
             ptf.get("1D"), ptf.get("5D"), ptf.get("1M"),
             ptf.get("6M"), ptf.get("YTD"), ptf.get("1Y"), ptf.get("5Y")))

    # Swing checks
    for chk in snap.get("checks") or []:
        conn.execute(
            """INSERT INTO trade_swing_checks
               (trade_id, name, ok, na, detail, weight)
               VALUES (?,?,?,?,?,?)""",
            (tid, chk["name"], int(chk["ok"]), int(chk.get("na", False)),
             chk.get("detail"), chk.get("weight", 0)))

    conn.commit()
    return trade


def update_trade(pid: str, updates: dict, db_path: Path = DB_PATH) -> None:
    """Update specific columns on a trade row."""
    if not updates:
        return
    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [pid]
    _conn(db_path).execute(f"UPDATE trades SET {cols} WHERE id=?", vals)
    _conn(db_path).commit()


def close_trade(pid: str, exit_price: float | None, reason: str,
                now: float | None = None, db_path: Path = DB_PATH) -> dict | None:
    now = now or time.time()
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT * FROM trades WHERE id=? AND status IN ('open','pending')", (pid,)
    ).fetchone()
    if row is None:
        return None
    trade = _row_to_trade(row)
    if trade["status"] == "pending":
        closed_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        conn.execute(
            """UPDATE trades SET status='closed', close_reason='cancelled',
               closed_ts=?, closed=? WHERE id=?""",
            (now, closed_str, pid))
        conn.commit()
        trade.update(status="closed", close_reason="cancelled",
                     closed_ts=now, closed=closed_str)
        return trade
    if exit_price is None:
        return None
    pnl_pct = round((exit_price / trade["entry"] - 1) * 100, 2)
    pnl_usd = round((exit_price - trade["entry"]) * trade["shares"], 2)
    closed_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    conn.execute(
        """UPDATE trades SET status='closed', exit_price=?, close_reason=?,
           closed_ts=?, closed=?, pnl_pct=?, pnl_usd=? WHERE id=?""",
        (round(exit_price, 4), reason, now, closed_str, pnl_pct, pnl_usd, pid))
    conn.commit()
    trade.update(status="closed", exit_price=round(exit_price, 4),
                 close_reason=reason, closed_ts=now, closed=closed_str,
                 pnl_pct=pnl_pct, pnl_usd=pnl_usd)
    return trade


def mark_trades(ticker: str, price: float, now: float | None = None,
                db_path: Path = DB_PATH) -> list[dict]:
    """Mark-to-market, activate pending orders, auto-close on stop/target/expiry."""
    if not price or price <= 0:
        return []
    now = now or time.time()
    ticker = ticker.upper()
    conn = _conn(db_path)
    rows = conn.execute(
        "SELECT * FROM trades WHERE ticker=? AND status IN ('open','pending')",
        (ticker,)
    ).fetchall()
    changed: list[dict] = []
    expiry_factor = 1.5
    stake_default = 1000.0

    for row in rows:
        p = _row_to_trade(row)
        pid = p["id"]
        expiry_s = p["horizon_days"] * expiry_factor * 86400

        if p["status"] == "pending":
            fill_level = max(p["entry"], p.get("trigger") or 0)
            if price >= fill_level:
                new_shares = round(p.get("stake", stake_default) / fill_level, 4)
                conn.execute(
                    """UPDATE trades SET status='open', activated_ts=?,
                       entry=?, shares=? WHERE id=?""",
                    (now, fill_level, new_shares, pid))
                p.update(status="open", activated_ts=now, entry=fill_level,
                         shares=new_shares)
                changed.append(p)
            elif now - p["opened_ts"] > expiry_s:
                closed_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
                conn.execute(
                    """UPDATE trades SET status='closed', close_reason='cancelled',
                       closed_ts=?, closed=? WHERE id=?""",
                    (now, closed_str, pid))
                p.update(status="closed", close_reason="cancelled",
                         closed_ts=now, closed=closed_str)
                changed.append(p)
            continue

        ref_ts = p.get("activated_ts") or p["opened_ts"]
        if price <= p["stop"]:
            _close_row(conn, p, p["stop"], "stop_hit", now)
            changed.append(p)
        elif price >= p["target"]:
            _close_row(conn, p, p["target"], "target_hit", now)
            changed.append(p)
        elif now - ref_ts > expiry_s:
            _close_row(conn, p, price, "expired", now)
            changed.append(p)

    if changed:
        conn.commit()
    return changed


def _close_row(conn: sqlite3.Connection, p: dict, exit_price: float,
               reason: str, now: float) -> None:
    pnl_pct = round((exit_price / p["entry"] - 1) * 100, 2)
    pnl_usd = round((exit_price - p["entry"]) * p["shares"], 2)
    closed_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    conn.execute(
        """UPDATE trades SET status='closed', exit_price=?, close_reason=?,
           closed_ts=?, closed=?, pnl_pct=?, pnl_usd=? WHERE id=?""",
        (round(exit_price, 4), reason, now, closed_str, pnl_pct, pnl_usd, p["id"]))
    p.update(status="closed", exit_price=round(exit_price, 4),
             close_reason=reason, closed_ts=now, closed=closed_str,
             pnl_pct=pnl_pct, pnl_usd=pnl_usd)


# ── trade analytics ───────────────────────────────────────────────────────── #

def _agg(rows: list[dict]) -> dict:
    wins = [p for p in rows if p["pnl_pct"] > 0]
    losses = [p for p in rows if p["pnl_pct"] <= 0]
    n = len(rows)
    return dict(
        n=n, wins=len(wins), losses=len(losses),
        win_rate=(round(len(wins) / n * 100) if n else None),
        avg_pnl_pct=(round(sum(p["pnl_pct"] for p in rows) / n, 2) if n else None),
        total_pnl_usd=round(sum(p["pnl_usd"] for p in rows), 2),
    )


def _band(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "?"
    return "80+" if s >= 80 else "70–79" if s >= 70 else "60–69" if s >= 60 else "<60"


def trade_stats(db_path: Path = DB_PATH) -> dict:
    rows = _conn(db_path).execute(
        """SELECT * FROM trades
           WHERE status='closed' AND close_reason != 'cancelled'"""
    ).fetchall()
    closed = [_row_to_trade(r) for r in rows]
    by_trader: dict = {}
    by_setup: dict = {}
    by_band: dict = {}
    for p in closed:
        by_trader.setdefault(p["trader"], []).append(p)
        by_setup.setdefault(p.get("snapshot", {}).get("setup", "?"), []).append(p)
        by_band.setdefault(_band(p.get("snapshot", {}).get("score")), []).append(p)
    return dict(
        totals=_agg(closed),
        traders={k: _agg(v) for k, v in sorted(by_trader.items())},
        setups={k: _agg(v) for k, v in sorted(by_setup.items())},
        bands={k: _agg(v) for k, v in sorted(by_band.items())},
    )


def query_closed_trades(*, trader: str | None = None, setup: str | None = None,
                        score_min: int | None = None, score_max: int | None = None,
                        signal_name: str | None = None,
                        db_path: Path = DB_PATH) -> list[dict]:
    """Flexible query over closed trades with optional filters."""
    conn = _conn(db_path)
    clauses = ["t.status='closed'", "t.close_reason != 'cancelled'"]
    params: list = []
    if trader:
        clauses.append("t.trader=?")
        params.append(trader)
    if setup:
        clauses.append("t.snap_setup=?")
        params.append(setup)
    if score_min is not None:
        clauses.append("t.snap_score >= ?")
        params.append(score_min)
    if score_max is not None:
        clauses.append("t.snap_score <= ?")
        params.append(score_max)
    join = ""
    if signal_name:
        join = " JOIN trade_signals s ON s.trade_id = t.id"
        clauses.append("s.name=?")
        params.append(signal_name)
    sql = f"SELECT DISTINCT t.* FROM trades t{join} WHERE {' AND '.join(clauses)} ORDER BY t.opened_ts DESC"
    return [_row_to_trade(r) for r in conn.execute(sql, params).fetchall()]


def losing_trade_patterns(db_path: Path = DB_PATH) -> dict:
    """Aggregate common patterns in losing trades for algorithm review."""
    conn = _conn(db_path)
    losers = conn.execute(
        "SELECT id FROM trades WHERE status='closed' AND pnl_pct < 0 AND close_reason != 'cancelled'"
    ).fetchall()
    if not losers:
        return {"n": 0, "failed_checks": {}, "common_signals": {}, "avg_indicators": {}}
    ids = [r["id"] for r in losers]
    placeholders = ",".join("?" * len(ids))

    # Most common failed checks
    checks = conn.execute(
        f"""SELECT name, COUNT(*) as cnt FROM trade_swing_checks
            WHERE trade_id IN ({placeholders}) AND ok=0 AND weight>0
            GROUP BY name ORDER BY cnt DESC""", ids
    ).fetchall()

    # Most common signals
    sigs = conn.execute(
        f"""SELECT name, direction, COUNT(*) as cnt FROM trade_signals
            WHERE trade_id IN ({placeholders})
            GROUP BY name, direction ORDER BY cnt DESC LIMIT 20""", ids
    ).fetchall()

    # Average indicator values
    inds = conn.execute(
        f"""SELECT AVG(rsi) as avg_rsi, AVG(macd_hist) as avg_macd_hist,
                   AVG(bias_score) as avg_bias
            FROM trade_indicators WHERE trade_id IN ({placeholders})""", ids
    ).fetchone()

    return {
        "n": len(ids),
        "failed_checks": {r["name"]: r["cnt"] for r in checks},
        "common_signals": {f"{r['name']}:{r['direction']}": r["cnt"] for r in sigs},
        "avg_indicators": dict(inds) if inds else {},
    }


def bot_comparison(db_path: Path = DB_PATH) -> dict:
    """Per-bot performance breakdown."""
    return trade_stats(db_path)["traders"]


# ── paper-trade CRUD ──────────────────────────────────────────────────────── #

def _row_to_paper(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["trigger"] = d.pop("trigger_price", None)
    return d


def load_paper_trades(db_path: Path = DB_PATH) -> list[dict]:
    rows = _conn(db_path).execute(
        "SELECT * FROM paper_trades ORDER BY ts DESC"
    ).fetchall()
    return [_row_to_paper(r) for r in rows]


def recent_paper_duplicate(ticker: str, level: int, ts: float,
                           hours: float = 24.0,
                           db_path: Path = DB_PATH) -> bool:
    cutoff = ts - hours * 3600
    row = _conn(db_path).execute(
        """SELECT 1 FROM paper_trades
           WHERE ticker=? AND level=? AND ts >= ? LIMIT 1""",
        (ticker, level, cutoff)
    ).fetchone()
    return row is not None


def insert_paper_trade(rec: dict, db_path: Path = DB_PATH) -> bool:
    """Append a proposition unless it's a fresh duplicate. Returns True if stored."""
    if recent_paper_duplicate(rec.get("ticker", ""), rec.get("level", 0),
                              rec.get("ts", time.time()), db_path=db_path):
        return False
    _conn(db_path).execute(
        """INSERT INTO paper_trades
           (ts, date, ticker, level, score, label, kind, setup, entry,
            stop, target, rr, trigger_price, horizon_days, guidance,
            status, result_pct)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rec.get("ts"), rec.get("date", ""), rec.get("ticker"),
         rec.get("level"), rec.get("score"), rec.get("label"),
         rec.get("kind"), rec.get("setup"), rec.get("entry"),
         rec.get("stop"), rec.get("target"), rec.get("rr"),
         rec.get("trigger"), int(rec.get("horizon_days", 3)),
         rec.get("guidance"), rec.get("status", "open"),
         rec.get("result_pct", 0.0)))
    _conn(db_path).commit()
    return True


def update_paper_status(rowid: int, status: str, result_pct: float,
                        db_path: Path = DB_PATH) -> None:
    _conn(db_path).execute(
        "UPDATE paper_trades SET status=?, result_pct=? WHERE id=?",
        (status, result_pct, rowid))
    _conn(db_path).commit()
