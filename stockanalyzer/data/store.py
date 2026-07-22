"""SQLite persistence layer for trades and paper-trade propositions.

All trade data — positions, decision signals, indicator snapshots, verdicts,
swing checks — lives in a single ``trades.db`` file so it survives restarts and
deploys.  On first run the module auto-migrates any existing JSON files.

The API is intentionally functional (module-level functions with a default
``db_path``), matching the style of the modules it replaces.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

# Default DB lives at the project root; STOCKANALYZER_DB (read at import time)
# lets deployments and tests point the whole store elsewhere.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "trades.db"
DB_PATH = (Path(os.environ["STOCKANALYZER_DB"])
           if os.environ.get("STOCKANALYZER_DB") else _DEFAULT_DB_PATH)
SCHEMA_VERSION = 5

# A single sqlite3.Connection is NOT safe to share across threads — concurrent
# execute()/commit() on one connection corrupts cursor state ("bad parameter or
# other API misuse", partial rows). Streamlit runs each `run_every` fragment (the
# swing radar, the live panels) on its own ScriptRunner thread, so the store is
# hit from several threads at once. We therefore give each thread its OWN
# connection (thread-local); WAL mode lets many such connections read/write the
# same file concurrently, and busy_timeout makes a write wait for the lock rather
# than raise "database is locked". Schema init/migration is guarded to run exactly
# once per file, since concurrent migrations would race.
_local = threading.local()
_schema_lock = threading.Lock()
_schema_ready: set[str] = set()


# ── connection ────────────────────────────────────────────────────────────── #

def _conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    key = str(db_path)
    cache = getattr(_local, "connections", None)
    if cache is None:
        cache = _local.connections = {}
    conn = cache.get(key)
    if conn is None:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")   # wait up to 5s for a write lock
        # Create tables / run migrations once per file; other threads just reuse
        # the schema already on disk.
        with _schema_lock:
            if key not in _schema_ready:
                _ensure_schema(conn, db_path)
                _schema_ready.add(key)
        cache[key] = conn
    return conn


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
    broke_out_ts  REAL,
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
    snap_manual_levels INTEGER DEFAULT 0,
    cohort_id          TEXT          -- groups the same trade IDEA across bots/manual
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_trader ON trades(trader);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_opened ON trades(opened_ts);
-- idx_trades_cohort is created by the v4 migration (after the column is added),
-- so it is intentionally NOT here: this DDL runs before migrations on existing
-- DBs, where cohort_id may not exist yet.

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
    adx         REAL,
    plus_di     REAL,
    minus_di    REAL,
    bb_upper    REAL,
    bb_lower    REAL,
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

CREATE TABLE IF NOT EXISTS trade_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id  TEXT NOT NULL REFERENCES trades(id),
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,      -- move_stop_breakeven | trail_stop |
                                  -- trend_test_tighten | weekend_flat
    detail    TEXT,
    price     REAL,
    old_stop  REAL,
    new_stop  REAL,
    fraction  REAL
);
CREATE INDEX IF NOT EXISTS idx_tevt_trade ON trade_events(trade_id);
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
    if current < 3:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "broke_out_ts" not in cols:    # two-phase throwback fill for breakouts
            conn.execute("ALTER TABLE trades ADD COLUMN broke_out_ts REAL")
            conn.commit()
        conn.execute("INSERT OR REPLACE INTO schema_version VALUES (?, ?)",
                     (3, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    if current < 4:
        # v4: judge the ALGORITHM, not just the bots. `cohort_id` groups every
        # trade that acted on one plan (same ticker/setup/levels/day) so the same
        # idea taken by bot-GO/bot-70/bot-BRK/me counts once. Persist the famous
        # guard indicators (ADX/DMI, Bollinger) that the score already uses but
        # that were never stored — so a post-mortem can see them. Repair the
        # NULL/epoch close timestamps that broke duration analysis.
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if "cohort_id" not in tcols:
            conn.execute("ALTER TABLE trades ADD COLUMN cohort_id TEXT")
        icols = {r[1] for r in conn.execute(
            "PRAGMA table_info(trade_indicators)").fetchall()}
        for col in ("adx", "plus_di", "minus_di", "bb_upper", "bb_lower"):
            if col not in icols:
                conn.execute(f"ALTER TABLE trade_indicators ADD COLUMN {col} REAL")
        conn.commit()
        _backfill_cohorts(conn)
        _repair_closed_ts(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_cohort ON trades(cohort_id)")
        conn.execute("INSERT OR REPLACE INTO schema_version VALUES (?, ?)",
                     (4, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    if current < 5:
        # v5: managed Gap-and-Go A/B. `bot-gap-mgd` and `bot-gap-fixed` take the
        # same ORB entry (shared cohort_id) but differ in exit policy, so
        # `managed_vs_fixed` isolates whether the management is smarter.
        #   init_stop  — original 1R risk (so trailing never shrinks the R math)
        #   mfe/mae    — best/worst excursion (how much was left on the table)
        #   stop_moves — how many times the stop ratcheted
        #   managed    — 1 for the managed variant
        #   entry_rvol — relative volume at the breakout (post-mortem)
        #   hold_weekend — opt-out of the Friday flatten
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
        for col, ddl in (
            ("init_stop",   "ALTER TABLE trades ADD COLUMN init_stop REAL"),
            ("mfe_pct",     "ALTER TABLE trades ADD COLUMN mfe_pct REAL DEFAULT 0.0"),
            ("mae_pct",     "ALTER TABLE trades ADD COLUMN mae_pct REAL DEFAULT 0.0"),
            ("stop_moves",  "ALTER TABLE trades ADD COLUMN stop_moves INTEGER DEFAULT 0"),
            ("managed",     "ALTER TABLE trades ADD COLUMN managed INTEGER DEFAULT 0"),
            ("entry_rvol",  "ALTER TABLE trades ADD COLUMN entry_rvol REAL"),
            ("hold_weekend","ALTER TABLE trades ADD COLUMN hold_weekend INTEGER DEFAULT 0"),
        ):
            if col not in tcols:
                conn.execute(ddl)
        # existing rows: original stop == current stop (no management yet).
        conn.execute("UPDATE trades SET init_stop=stop WHERE init_stop IS NULL")
        conn.commit()
        conn.execute("INSERT OR REPLACE INTO schema_version VALUES (?, ?)",
                     (5, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()


# ── cohort identity + data repair ─────────────────────────────────────────── #

def _cohort_id(ticker: str | None, setup: str | None, kind: str | None,
               entry, stop, target, opened: str | None) -> str:
    """Stable id grouping the same trade IDEA across traders.

    Every bot (and a manual buy) that acts on one plan shares the ticker, setup,
    entry/stop/target and day — so they collapse to one cohort. This lets the
    book judge whether the *algorithm's decision* was right (one vote per idea),
    separately from which *bot rule* happened to take it. Levels are rounded to
    cents so trivially-different fills still group.
    """
    def _n(v) -> str:
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return "0.00"
    key = "|".join([(ticker or "").upper(), (setup or ""), (kind or ""),
                    _n(entry), _n(stop), _n(target), (opened or "")[:10]])
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _backfill_cohorts(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT id, ticker, snap_setup, kind, entry, stop, target, opened
           FROM trades WHERE cohort_id IS NULL"""
    ).fetchall()
    for r in rows:
        cid = _cohort_id(r["ticker"], r["snap_setup"], r["kind"],
                         r["entry"], r["stop"], r["target"], r["opened"])
        conn.execute("UPDATE trades SET cohort_id=? WHERE id=?", (cid, r["id"]))
    conn.commit()


def _repair_closed_ts(conn: sqlite3.Connection) -> None:
    """Backfill closed_ts where it is NULL or impossibly before opened_ts.

    Older rows (JSON migration, and a batch closed with test-sized clocks) carry
    NULL or near-epoch closed_ts, so any holding-period query silently breaks.
    Recover the real time from the human ``closed`` text; fall back to opened_ts.
    """
    rows = conn.execute(
        """SELECT id, opened_ts, closed, closed_ts FROM trades
           WHERE status='closed' AND (closed_ts IS NULL OR closed_ts < opened_ts)"""
    ).fetchall()
    for r in rows:
        ts = None
        if r["closed"]:
            try:
                ts = time.mktime(time.strptime(r["closed"], "%Y-%m-%d %H:%M"))
            except Exception:
                # Out-of-range years raise OverflowError (not ValueError) and
                # would otherwise propagate out of the migration on EVERY connect,
                # bricking the DB. Any unparseable text just falls back below.
                ts = None
        if ts is None or ts < r["opened_ts"]:
            ts = r["opened_ts"]
        conn.execute("UPDATE trades SET closed_ts=? WHERE id=?", (ts, r["id"]))
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


def has_any_open(ticker: str, db_path: Path = DB_PATH) -> bool:
    """True if ANY trader holds an open/pending position in this ticker.

    Lets the radar promote a ticker we already hold to the fastest tier without
    four per-bot queries."""
    row = _conn(db_path).execute(
        "SELECT 1 FROM trades WHERE ticker=? AND status IN ('open','pending') LIMIT 1",
        (ticker.upper(),)
    ).fetchone()
    return row is not None


def insert_trade(trade: dict, context: dict | None = None,
                 db_path: Path = DB_PATH) -> dict:
    """Insert a trade and its full decision context into the DB."""
    conn = _conn(db_path)
    ctx = context or {}
    snap = ctx if ctx else trade.get("snapshot") or {}
    rec_data = snap.get("recommendation") or {}
    cohort = _cohort_id(trade["ticker"], snap.get("setup"), trade["kind"],
                        trade["entry"], trade["stop"], trade["target"],
                        trade["opened"])

    conn.execute(
        """INSERT INTO trades
           (id, ticker, trader, status, kind, opened_ts, opened,
            activated_ts, entry, stop, target, trigger_price, stake,
            shares, horizon_days, snap_score, snap_label, snap_setup,
            snap_kind, snap_rr, snap_daily_atr_pct, snap_guidance,
            snap_go_score, snap_light_color, snap_preset, snap_bullish_pct,
            snap_manual_levels, cohort_id,
            managed, init_stop, entry_rvol, hold_weekend)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
         int(snap.get("manual_levels", False)), cohort,
         int(trade.get("managed", False)),
         round(float(trade.get("init_stop") or trade["stop"]), 4),
         trade.get("entry_rvol"), int(trade.get("hold_weekend", False))))

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
                    adx, plus_di, minus_di, bb_upper, bb_lower,
                    bias_score, trend_dir)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, tf_key, ind.get("close"), ind.get("sma20"),
                 ind.get("sma50"), ind.get("sma200"), ind.get("ema20"),
                 ind.get("rsi"), ind.get("macd"), ind.get("macd_signal"),
                 ind.get("macd_hist"), ind.get("stoch_k"), ind.get("stoch_d"),
                 ind.get("atr"), ind.get("adx"), ind.get("plus_di"),
                 ind.get("minus_di"), ind.get("bb_upper"), ind.get("bb_lower"),
                 tf_info.get("bias_score"), tf_info.get("trend_dir")))

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


# A break is only "confirmed" once price clears the trigger by this margin —
# a hair past the wall, not the bare touch. Then we wait for the throwback.
_CONFIRM_MARGIN = 0.002


def mark_trades(ticker: str, price: float, now: float | None = None,
                db_path: Path = DB_PATH) -> list[dict]:
    """Mark-to-market, activate pending orders, auto-close on stop/target/expiry.

    Breakout (pending) orders fill in **two phases** — the throwback model: first
    price must clear the trigger by ``_CONFIRM_MARGIN`` (the break is real), then
    it must pull *back* to the entry (the retest of the broken level) to fill.
    We never chase the spike: a break that runs away without retesting is missed
    (it expires unfilled), and a break that immediately collapses through the
    stop is cancelled, not filled-then-stopped. This matches the live data — the
    move to the wall is already spent, so the honest fill is the pullback."""
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
    dirty = False
    expiry_factor = 1.5
    stake_default = 1000.0

    def _cancel(p: dict, pid: str, reason: str = "cancelled") -> None:
        closed_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        conn.execute(
            """UPDATE trades SET status='closed', close_reason=?,
               closed_ts=?, closed=? WHERE id=?""",
            (reason, now, closed_str, pid))
        p.update(status="closed", close_reason=reason, closed_ts=now, closed=closed_str)
        changed.append(p)

    for row in rows:
        p = _row_to_trade(row)
        pid = p["id"]
        expiry_s = p["horizon_days"] * expiry_factor * 86400
        stale = now - p["opened_ts"] > expiry_s

        if p["status"] == "pending":
            trigger = p.get("trigger") or p["entry"]
            retest_entry = p["entry"]
            confirm_level = trigger * (1 + _CONFIRM_MARGIN)

            if not p.get("broke_out_ts"):
                # Phase 1 — wait for a real break above the trigger.
                if price >= confirm_level:
                    conn.execute("UPDATE trades SET broke_out_ts=? WHERE id=?",
                                 (now, pid))
                    p["broke_out_ts"] = now
                    dirty = True                 # internal sub-state, not a user toast
                elif stale:
                    _cancel(p, pid)              # break never came
                continue

            # Phase 2 — broke out; wait for the throwback to the entry (retest).
            if price <= p["stop"]:
                _cancel(p, pid)                  # broke out then failed — no fill
            elif price <= retest_entry:
                new_shares = round(p.get("stake", stake_default) / retest_entry, 4)
                conn.execute(
                    """UPDATE trades SET status='open', activated_ts=?,
                       shares=? WHERE id=?""",
                    (now, new_shares, pid))
                p.update(status="open", activated_ts=now, shares=new_shares)
                changed.append(p)
            elif stale:
                _cancel(p, pid)                  # ran away, never retested — missed
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

    if changed or dirty:
        conn.commit()
    return changed


def _close_row(conn: sqlite3.Connection, p: dict, exit_price: float,
               reason: str, now: float, spread: float = 0.0) -> None:
    # spread is a flat per-SHARE cost (managed Gap-and-Go closes) deducted from the
    # exit so realized P&L is friction-honest; the default 0.0 keeps the baseline
    # byte-identical.
    eff_exit = exit_price - spread
    pnl_pct = round((eff_exit / p["entry"] - 1) * 100, 2)
    pnl_usd = round((eff_exit - p["entry"]) * p["shares"], 2)
    closed_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    conn.execute(
        """UPDATE trades SET status='closed', exit_price=?, close_reason=?,
           closed_ts=?, closed=?, pnl_pct=?, pnl_usd=? WHERE id=?""",
        (round(exit_price, 4), reason, now, closed_str, pnl_pct, pnl_usd, p["id"]))
    p.update(status="closed", exit_price=round(exit_price, 4),
             close_reason=reason, closed_ts=now, closed=closed_str,
             pnl_pct=pnl_pct, pnl_usd=pnl_usd)


# ── active position management (managed bots only) ──────────────────────────── #

def _log_event(conn: sqlite3.Connection, trade_id: str, ts: float, kind: str,
               detail: str | None, price: float | None,
               old_stop: float | None, new_stop: float | None,
               fraction: float | None) -> None:
    conn.execute(
        """INSERT INTO trade_events
           (trade_id, ts, kind, detail, price, old_stop, new_stop, fraction)
           VALUES (?,?,?,?,?,?,?,?)""",
        (trade_id, ts, kind, detail, price, old_stop, new_stop, fraction))


def _update_excursions(conn: sqlite3.Connection, p: dict, price: float) -> None:
    """Track max favorable / adverse excursion (% from entry) on an open row."""
    entry = p.get("entry")
    if not entry:
        return
    fav = round((price / entry - 1) * 100, 2)
    cur_mfe = p.get("mfe_pct") or 0.0
    cur_mae = p.get("mae_pct") or 0.0
    new_mfe, new_mae = max(cur_mfe, fav), min(cur_mae, fav)
    if new_mfe != cur_mfe or new_mae != cur_mae:
        conn.execute("UPDATE trades SET mfe_pct=?, mae_pct=? WHERE id=?",
                     (new_mfe, new_mae, p["id"]))
        p["mfe_pct"], p["mae_pct"] = new_mfe, new_mae


def _friday_flat_now(now: float) -> bool:
    try:
        import pandas as pd

        from .. import session
        return session.is_friday_flat(pd.Timestamp(now, unit="s", tz="UTC"))
    except Exception:
        return False


def manage_trades(ticker: str, price: float, reports=None, trend_change=None,
                  now: float | None = None, friday_flat: bool | None = None,
                  db_path: Path = DB_PATH) -> list[dict]:
    """Apply best-practice exit management to MANAGED open positions only.

    Ratchets stops in-place (never widens), logs each action to ``trade_events``,
    tracks MFE/MAE, and flattens day-trades into the Friday close. Closing on a
    tightened stop still happens via ``mark_trades`` on the next tick — call this
    BEFORE ``mark_trades`` so a fresh tighten can trigger the close same-tick.
    """
    if not price or price <= 0:
        return []
    now = now or time.time()
    if friday_flat is None:
        friday_flat = _friday_flat_now(now)
    ticker = ticker.upper()
    conn = _conn(db_path)
    rows = conn.execute(
        "SELECT * FROM trades WHERE ticker=? AND status='open' AND managed=1",
        (ticker,)).fetchall()
    if not rows:
        return []

    from ..manage import assess_position, intraday_readout, _SPREAD_PER_SHARE
    ema8, iclose = intraday_readout(reports)
    changed: list[dict] = []
    for row in rows:
        p = _row_to_trade(row)
        _update_excursions(conn, p, price)
        actions = assess_position(
            p, price, reports, None, trend_change, now,
            ema8_5m=ema8, intraday_close=iclose, is_friday_flat=friday_flat,
            hold_weekend=bool(p.get("hold_weekend")))
        for a in actions:
            if a.kind == "weekend_flat":
                _close_row(conn, p, price, "weekend_flat", now,
                           spread=_SPREAD_PER_SHARE)
                _log_event(conn, p["id"], now, a.kind, a.reason, price,
                           p["stop"], None, a.fraction)
                changed.append(p)
                break
            if a.new_stop is not None and a.new_stop > p["stop"]:
                old = p["stop"]
                conn.execute(
                    "UPDATE trades SET stop=?, stop_moves=COALESCE(stop_moves,0)+1 "
                    "WHERE id=?", (round(a.new_stop, 4), p["id"]))
                _log_event(conn, p["id"], now, a.kind, a.reason, price,
                           old, round(a.new_stop, 4), None)
                p["stop"] = round(a.new_stop, 4)
                changed.append(dict(p))
    conn.commit()
    return changed


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


def algorithm_correctness(db_path: Path = DB_PATH) -> dict:
    """Judge the ALGORITHM's decisions, not the bots.

    Collapse closed trades by ``cohort_id`` so every trader that acted on one
    plan counts as a single idea (its outcome = mean P&L across the bots that
    took it). This strips the sample inflation from the same idea being copied
    across bot-GO/bot-70/bot-BRK/me, giving an honest, deduplicated read on
    whether the engine's *calls* were right. Returns idea-level totals, the same
    breakdowns by setup and score band as ``trade_stats``, and the idea list.
    """
    rows = _conn(db_path).execute(
        "SELECT * FROM trades WHERE status='closed' AND close_reason != 'cancelled' "
        "AND COALESCE(managed, 0) = 0"
    ).fetchall()
    closed = [_row_to_trade(r) for r in rows]
    by_cohort: dict = {}
    for p in closed:
        by_cohort.setdefault(p.get("cohort_id") or p["id"], []).append(p)

    ideas: list[dict] = []
    for cid, trades in by_cohort.items():
        pnls = [t["pnl_pct"] for t in trades]
        idea_pnl = round(sum(pnls) / len(pnls), 2)
        snap = trades[0].get("snapshot", {})
        ideas.append(dict(
            cohort_id=cid, ticker=trades[0]["ticker"],
            setup=snap.get("setup", "?"), score=snap.get("score"),
            band=_band(snap.get("score")), n_trades=len(trades),
            traders=sorted({t["trader"] for t in trades}),
            idea_pnl_pct=idea_pnl, win=idea_pnl > 0,
            best_pnl_pct=round(max(pnls), 2), worst_pnl_pct=round(min(pnls), 2),
        ))

    def _agg_ideas(items: list[dict]) -> dict:
        n = len(items)
        wins = [i for i in items if i["win"]]
        return dict(
            n_ideas=n, n_trades=sum(i["n_trades"] for i in items),
            wins=len(wins), losses=n - len(wins),
            win_rate=(round(len(wins) / n * 100) if n else None),
            avg_pnl_pct=(round(sum(i["idea_pnl_pct"] for i in items) / n, 2)
                         if n else None),
        )

    by_setup: dict = {}
    by_band: dict = {}
    for i in ideas:
        by_setup.setdefault(i["setup"], []).append(i)
        by_band.setdefault(i["band"], []).append(i)
    return dict(
        totals=_agg_ideas(ideas),
        setups={k: _agg_ideas(v) for k, v in sorted(by_setup.items())},
        bands={k: _agg_ideas(v) for k, v in sorted(by_band.items())},
        ideas=sorted(ideas, key=lambda i: i["idea_pnl_pct"]),
    )


def managed_vs_fixed(db_path: Path = DB_PATH) -> dict:
    """Paired A/B: does the managed Gap-and-Go exit beat the fixed-exit control?

    ``bot-gap-mgd`` and ``bot-gap-fixed`` take the SAME entry (shared
    ``cohort_id``), so pairing on the cohort isolates the exit policy. Returns the
    mean P&L delta (managed − fixed), each side's win rate, and management
    telemetry (avg stop moves, mean MFE captured) over the matched pairs.
    """
    rows = _conn(db_path).execute(
        "SELECT * FROM trades WHERE status='closed' AND close_reason != 'cancelled' "
        "AND trader IN ('bot-gap-mgd', 'bot-gap-fixed')"
    ).fetchall()
    by_cohort: dict = {}
    for r in (_row_to_trade(x) for x in rows):
        by_cohort.setdefault(r.get("cohort_id") or r["id"], {})[r["trader"]] = r

    pairs: list[dict] = []
    for cid, d in by_cohort.items():
        m, f = d.get("bot-gap-mgd"), d.get("bot-gap-fixed")
        if not (m and f):
            continue
        pairs.append(dict(
            cohort_id=cid, ticker=m["ticker"],
            mgd_pnl_pct=m["pnl_pct"], fixed_pnl_pct=f["pnl_pct"],
            delta_pct=round(m["pnl_pct"] - f["pnl_pct"], 2),
            stop_moves=m.get("stop_moves") or 0, mfe_pct=m.get("mfe_pct") or 0.0,
            mgd_reason=m.get("close_reason"), fixed_reason=f.get("close_reason")))

    n = len(pairs)
    if not n:
        return dict(n_pairs=0, mean_delta_pct=None, mgd_win_rate=None,
                    fixed_win_rate=None, avg_stop_moves=None, mean_mfe_pct=None,
                    pairs=[])
    return dict(
        n_pairs=n,
        mean_delta_pct=round(sum(p["delta_pct"] for p in pairs) / n, 2),
        mgd_win_rate=round(sum(1 for p in pairs if p["mgd_pnl_pct"] > 0) / n * 100),
        fixed_win_rate=round(sum(1 for p in pairs if p["fixed_pnl_pct"] > 0) / n * 100),
        avg_stop_moves=round(sum(p["stop_moves"] for p in pairs) / n, 2),
        mean_mfe_pct=round(sum(p["mfe_pct"] for p in pairs) / n, 2),
        pairs=sorted(pairs, key=lambda p: p["delta_pct"]),
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
