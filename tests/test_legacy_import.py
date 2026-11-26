"""Legacy SQLite ``trades.db`` → SQLAlchemy repositories import path.

The app no longer writes SQLite; ``stockanalyzer.migrations.legacy`` reads an old
file read-only so ``scripts/migrate_legacy_data.py`` can import it. The fixture
below recreates the relevant part of the last legacy schema (v5).
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from stockanalyzer.db.models import (
    TradeIndicator, TradeSignal, TradeSwingCheck, TradeVerdict,
)
from stockanalyzer.db.repositories.paper_trades import PaperTradeRepository
from stockanalyzer.db.repositories.trades import TradeRepository
from stockanalyzer.db.session import session_scope
from stockanalyzer.migrations.legacy import (
    read_legacy_paper_trades, read_legacy_trades, repair_closed_ts,
)

_LEGACY_DDL = """
CREATE TABLE trades (
    id TEXT PRIMARY KEY, ticker TEXT NOT NULL, trader TEXT NOT NULL,
    status TEXT NOT NULL, kind TEXT NOT NULL, opened_ts REAL NOT NULL,
    opened TEXT NOT NULL, activated_ts REAL, broke_out_ts REAL,
    entry REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL,
    trigger_price REAL, stake REAL, shares REAL, horizon_days INTEGER,
    exit_price REAL, close_reason TEXT, closed_ts REAL, closed TEXT,
    pnl_pct REAL, pnl_usd REAL, snap_score INTEGER, snap_label TEXT,
    snap_setup TEXT, snap_kind TEXT, snap_rr REAL, snap_daily_atr_pct REAL,
    snap_guidance TEXT, snap_go_score INTEGER, snap_light_color TEXT,
    snap_preset TEXT, snap_bullish_pct INTEGER, snap_manual_levels INTEGER,
    cohort_id TEXT, init_stop REAL, mfe_pct REAL, mae_pct REAL,
    stop_moves INTEGER, managed INTEGER, entry_rvol REAL, hold_weekend INTEGER
);
CREATE TABLE trade_signals (id INTEGER PRIMARY KEY, trade_id TEXT, timeframe TEXT,
    name TEXT, direction TEXT, strength REAL, category TEXT, evidence TEXT);
CREATE TABLE trade_indicators (id INTEGER PRIMARY KEY, trade_id TEXT, timeframe TEXT,
    close REAL, rsi REAL, macd_hist REAL, adx REAL, bias_score REAL, trend_dir TEXT);
CREATE TABLE trade_verdict (trade_id TEXT PRIMARY KEY, label TEXT, direction TEXT,
    score REAL, confidence REAL, bias_1d REAL, bias_5d REAL, bias_1m REAL,
    bias_6m REAL, bias_ytd REAL, bias_1y REAL, bias_5y REAL);
CREATE TABLE trade_swing_checks (id INTEGER PRIMARY KEY, trade_id TEXT, name TEXT,
    ok INTEGER, na INTEGER, detail TEXT, weight INTEGER);
CREATE TABLE paper_trades (id INTEGER PRIMARY KEY, ts REAL, date TEXT, ticker TEXT,
    level INTEGER, score INTEGER, label TEXT, kind TEXT, setup TEXT, entry REAL,
    stop REAL, target REAL, rr REAL, trigger_price REAL, horizon_days INTEGER,
    guidance TEXT, status TEXT, result_pct REAL);
"""


def _legacy_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_LEGACY_DDL)
    conn.execute(
        """INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
           ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("legacy0001", "NOK", "bot-GO", "closed", "immediate", 1_700_000_000.0,
         "2023-11-14 22:13", 1_700_000_000.0, None, 14.0, 13.3, 14.8, None,
         1000.0, 71.4286, 3, 13.3, "stop_hit", None, "2023-11-15 10:00",
         -5.0, -50.0, 82, "Strong", "Pullback to 20-EMA", "immediate", 2.1, 3.4,
         "buy the dip", 77, "green", "Buy", 64, 1, "cohort00001", 13.3, 1.0,
         -5.0, 0, 0, None, 0))
    conn.execute(
        """INSERT INTO trade_signals (trade_id, timeframe, name, direction, strength,
           category, evidence) VALUES ('legacy0001','6M','MACD cross','bear',0.5,
           'momentum','hist < 0')""")
    conn.execute(
        """INSERT INTO trade_indicators (trade_id, timeframe, close, rsi, macd_hist,
           adx, bias_score, trend_dir) VALUES ('legacy0001','6M',14.0,41.0,-0.12,
           22.0,0.3,'bull')""")
    conn.execute(
        """INSERT INTO trade_verdict (trade_id, label, direction, score, confidence,
           bias_1d, bias_6m) VALUES ('legacy0001','Buy','bull',0.4,0.7,0.1,0.5)""")
    conn.execute(
        """INSERT INTO trade_swing_checks (trade_id, name, ok, na, detail, weight)
           VALUES ('legacy0001','Engine bias agrees',0,0,'bias −0.1',2)""")
    conn.execute(
        """INSERT INTO paper_trades (ts, date, ticker, level, score, label, kind,
           setup, entry, stop, target, rr, trigger_price, horizon_days, guidance,
           status, result_pct) VALUES (1700000000,'11/14/23','NOK',70,72,'Good',
           'breakout_wait','Breakout',14.2,13.5,15.4,2.0,14.2,3,'wait','open',0.0)""")
    conn.commit()
    conn.close()
    return path


def test_repair_closed_ts_backfills_from_closed_text():
    trade = {"status": "closed", "opened_ts": 1_700_000_000.0, "closed_ts": None,
             "closed": "2023-11-15 10:00"}
    repaired = repair_closed_ts(dict(trade))["closed_ts"]
    assert repaired is not None and repaired >= trade["opened_ts"]


def test_repair_closed_ts_survives_out_of_range_year():
    # time.mktime raises OverflowError (not ValueError) on far-out years; the
    # repair must fall back to opened_ts, never raise.
    trade = {"status": "closed", "opened_ts": 1_700_000_000.0, "closed_ts": 5.0,
             "closed": "1900-01-01 00:00"}
    assert repair_closed_ts(trade)["closed_ts"] == trade["opened_ts"]


def test_reader_rebuilds_snapshot_and_context_read_only(tmp_path):
    db = _legacy_db(tmp_path / "trades.db")
    before = db.read_bytes()
    [trade] = read_legacy_trades(db)
    assert db.read_bytes() == before                          # opened read-only
    assert trade["trigger"] is None and trade["closed_ts"] >= trade["opened_ts"]
    assert trade["managed"] is False
    snap = trade["snapshot"]
    assert snap["score"] == 82 and snap["setup"] == "Pullback to 20-EMA"
    assert snap["manual_levels"] is True
    assert snap["recommendation"]["light_color"] == "green"
    frame = snap["timeframes"]["6M"]
    assert frame["signals"][0]["name"] == "MACD cross"
    assert frame["indicators"]["rsi"] == 41.0 and frame["bias_score"] == 0.3
    assert snap["verdict"]["per_timeframe"] == {"1D": 0.1, "6M": 0.5}
    assert snap["checks"] == [{"name": "Engine bias agrees", "ok": False, "na": False,
                               "detail": "bias −0.1", "weight": 2}]
    [paper] = read_legacy_paper_trades(db)
    assert paper["trigger"] == 14.2 and "trigger_price" not in paper


def test_imported_trade_keeps_lifecycle_and_decision_context(owner, tmp_path):
    [trade] = read_legacy_trades(_legacy_db(tmp_path / "trades.db"))
    repo = TradeRepository()
    repo.insert(owner, trade, trade["snapshot"])
    [loaded] = repo.list(owner)
    assert loaded["status"] == "closed" and loaded["close_reason"] == "stop_hit"
    assert loaded["pnl_usd"] == -50.0 and loaded["cohort_id"] == "cohort00001"
    with session_scope() as session:
        assert session.scalars(select(TradeSignal.name)).all() == ["MACD cross"]
        assert session.scalars(select(TradeIndicator.payload)).one()["rsi"] == 41.0
        assert session.scalars(select(TradeVerdict.payload)).one()["label"] == "Buy"
        assert session.scalars(select(TradeSwingCheck.name)).all() == [
            "Engine bias agrees"]
    patterns = repo.losing_trade_patterns(owner)
    assert patterns["failed_checks"] == {"Engine bias agrees": 1}


def test_migration_script_dry_run_then_apply_is_idempotent(tmp_path):
    from stockanalyzer.auth import AuthService
    from stockanalyzer.db.models import Base
    from stockanalyzer.db.session import configure_engine

    url = f"sqlite:///{tmp_path / 'target.db'}"
    engine = configure_engine(url)
    Base.metadata.create_all(engine)
    owner = AuthService().create_user("importer", "correct horse battery staple").id
    legacy = _legacy_db(tmp_path / "trades.db")
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "development"}
    args = [sys.executable, "-m", "scripts.migrate_legacy_data",
            "--username", "importer", "--sqlite", str(legacy),
            "--watchlist", str(tmp_path / "w.json"),
            "--swingwatch", str(tmp_path / "s.json"),
            "--ratelimit", str(tmp_path / "r.json")]
    root = Path(__file__).resolve().parent.parent
    dry = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    assert "'trades': 1" in dry.stdout and TradeRepository().list(owner) == []
    for _ in range(2):                                         # rerun is a no-op
        applied = subprocess.run(args + ["--apply"], cwd=root, env=env,
                                 capture_output=True, text=True)
        assert applied.returncode == 0, applied.stderr
    assert len(TradeRepository().list(owner)) == 1
    assert len(PaperTradeRepository().list(owner)) == 1
