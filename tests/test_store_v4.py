"""Schema v4: idea-level cohorts, full indicator persistence, closed_ts repair."""
from __future__ import annotations

import sqlite3

from stockanalyzer import virtualbook as vb
from stockanalyzer.data import store


def _snap(score=70, setup="Support test (uptrend)", **ind):
    base = {"close": 100.0, "rsi": 55.0}
    base.update(ind)
    return {"score": score, "setup": setup,
            "timeframes": {"6M": {"signals": [], "bias_score": 0.2,
                                  "trend_dir": "bull", "indicators": base}}}


def test_same_idea_across_bots_is_one_cohort(tmp_path):
    p = tmp_path / "book.db"
    snap = {"score": 80, "setup": "Pullback to 20-EMA"}
    # Two bots take the IDENTICAL plan (same ticker/setup/levels/day) → one idea.
    a = vb.open_position(ticker="NOK", trader="bot-GO", entry=14.0, stop=13.3,
                         target=14.8, snapshot=snap, now=1_000_000.0, path=p)
    b = vb.open_position(ticker="NOK", trader="me", entry=14.0, stop=13.3,
                         target=14.8, snapshot=snap, now=1_000_000.0, path=p)
    # A genuinely different idea (different ticker/levels).
    c = vb.open_position(ticker="MU", trader="bot-BRK", entry=100.0, stop=95.0,
                         target=110.0, snapshot={"score": 60,
                         "setup": "Support test (uptrend)"}, now=1_000_000.0, path=p)
    book = {t["id"]: t for t in vb.load(p)}
    assert book[a["id"]]["cohort_id"] == book[b["id"]]["cohort_id"]   # same idea
    assert book[a["id"]]["cohort_id"] != book[c["id"]]["cohort_id"]   # different idea

    vb.close_position(a["id"], 13.4, now=1_000_100.0, path=p)         # loss
    vb.close_position(b["id"], 13.4, now=1_000_100.0, path=p)         # loss (dup)
    vb.close_position(c["id"], 110.0, now=1_000_100.0, path=p)        # win

    ac = vb.algorithm_correctness(p)
    assert ac["totals"]["n_trades"] == 3       # three bot-trades…
    assert ac["totals"]["n_ideas"] == 2        # …but only two distinct ideas
    assert ac["totals"]["wins"] == 1 and ac["totals"]["losses"] == 1
    assert ac["totals"]["win_rate"] == 50      # NOT 33 (the inflated per-trade rate)


def test_full_indicator_set_is_persisted(tmp_path):
    p = tmp_path / "book.db"
    vb.open_position(ticker="MU", trader="me", entry=100.0, stop=95.0, target=110.0,
                     snapshot=_snap(adx=30.0, plus_di=25.0, minus_di=10.0,
                                    bb_upper=110.0, bb_lower=90.0),
                     now=1_000_000.0, path=p)
    conn = store._conn(p)
    r = conn.execute("""SELECT adx, plus_di, minus_di, bb_upper, bb_lower
                        FROM trade_indicators""").fetchone()
    assert r["adx"] == 30.0 and r["plus_di"] == 25.0 and r["minus_di"] == 10.0
    assert r["bb_upper"] == 110.0 and r["bb_lower"] == 90.0


def test_closed_ts_never_before_opened(tmp_path):
    p = tmp_path / "book.db"
    pos = vb.open_position(ticker="NOK", trader="me", entry=14.0, stop=13.3,
                          target=14.8, snapshot={"score": 70, "setup": "x"},
                          now=1_000_000.0, path=p)
    vb.close_position(pos["id"], 14.4, now=1_000_100.0, path=p)
    row = {t["id"]: t for t in vb.load(p)}[pos["id"]]
    assert row["closed_ts"] is not None and row["closed_ts"] >= row["opened_ts"]


def test_repair_closed_ts_backfills_bad_rows(tmp_path):
    # Simulate the legacy corruption: a closed row with NULL closed_ts but a
    # valid `closed` text, and one with a near-epoch closed_ts < opened_ts.
    p = tmp_path / "book.db"
    pos = vb.open_position(ticker="NOK", trader="me", entry=14.0, stop=13.3,
                          target=14.8, snapshot={"score": 70, "setup": "x"},
                          now=1_700_000_000.0, path=p)
    conn = store._conn(p)
    conn.execute("""UPDATE trades SET status='closed', exit_price=14.4,
                    close_reason='stop_hit', closed='2023-11-14 22:13',
                    closed_ts=NULL WHERE id=?""", (pos["id"],))
    conn.commit()
    store._repair_closed_ts(conn)
    row = conn.execute("SELECT opened_ts, closed_ts FROM trades WHERE id=?",
                       (pos["id"],)).fetchone()
    assert row["closed_ts"] is not None and row["closed_ts"] >= row["opened_ts"]


def test_repair_closed_ts_survives_out_of_range_year(tmp_path):
    # An out-of-range year makes time.mktime raise OverflowError (not ValueError).
    # Since the repair runs inside the migration on EVERY connect, an unhandled
    # raise would brick the DB permanently. It must fall back, never raise.
    p = tmp_path / "book.db"
    pos = vb.open_position(ticker="NOK", trader="me", entry=14.0, stop=13.3,
                          target=14.8, snapshot={"score": 70, "setup": "x"},
                          now=1_700_000_000.0, path=p)
    conn = store._conn(p)
    conn.execute("""UPDATE trades SET status='closed', close_reason='stop_hit',
                    closed='1900-01-01 00:00', closed_ts=NULL WHERE id=?""",
                 (pos["id"],))
    conn.commit()
    store._repair_closed_ts(conn)        # must not raise
    row = conn.execute("SELECT opened_ts, closed_ts FROM trades WHERE id=?",
                       (pos["id"],)).fetchone()
    assert row["closed_ts"] == row["opened_ts"]   # fell back, invariant holds
