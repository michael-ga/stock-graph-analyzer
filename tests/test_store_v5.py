"""Schema v5: managed-position columns, trade_events, manage_trades, A/B pairing."""
from __future__ import annotations

from stockanalyzer import virtualbook as vb
from stockanalyzer.data import store

_GAP = {"setup": "gap_and_go_orb", "score": 70}


def test_v5_columns_and_events_table(tmp_path):
    p = tmp_path / "book.db"
    vb.open_position(ticker="FFF", trader="me", entry=10.0, stop=9.0, target=12.0,
                     snapshot={"setup": "x"}, now=1_000_000.0, path=p)
    conn = store._conn(p)
    tcols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    for c in ("init_stop", "mfe_pct", "mae_pct", "stop_moves", "managed",
              "entry_rvol", "hold_weekend"):
        assert c in tcols
    conn.execute("SELECT COUNT(*) FROM trade_events")          # table exists
    row = conn.execute("SELECT stop, init_stop FROM trades").fetchone()
    assert row["init_stop"] == row["stop"]                     # backfilled / set on open


def test_manage_trades_ratchets_and_logs(tmp_path):
    p = tmp_path / "book.db"
    pos = vb.open_position(ticker="AAA", trader="bot-gap-mgd", entry=100.0,
                           stop=98.0, target=104.0, managed=True, init_stop=98.0,
                           snapshot=_GAP, now=1_000_000.0, path=p)
    # +1R at 102 → breakeven move to spread-adjusted entry + $0.02 = 100.02.
    vb.manage("AAA", 102.0, None, None, now=1_000_100.0, path=p)
    row = {t["id"]: t for t in vb.load(p)}[pos["id"]]
    assert row["stop"] == round(100.0 + 0.02, 4)
    assert (row["stop_moves"] or 0) >= 1
    ev = store._conn(p).execute(
        "SELECT kind FROM trade_events WHERE trade_id=?", (pos["id"],)).fetchall()
    assert any(e["kind"] == "move_stop_breakeven" for e in ev)


def test_manage_trades_never_widens(tmp_path):
    p = tmp_path / "book.db"
    pos = vb.open_position(ticker="BBB", trader="bot-gap-mgd", entry=100.0,
                           stop=101.0, target=110.0, managed=True, init_stop=98.0,
                           snapshot=_GAP, now=1_000_000.0, path=p)
    vb.manage("BBB", 103.0, None, None, now=1_000_100.0, path=p)   # +2.5R, no candidate beats 101
    row = {t["id"]: t for t in vb.load(p)}[pos["id"]]
    assert row["stop"] == 101.0


def test_manage_does_not_touch_unmanaged(tmp_path):
    p = tmp_path / "book.db"
    pos = vb.open_position(ticker="GGG", trader="bot-GO", entry=100.0, stop=98.0,
                           target=104.0, managed=False, snapshot=_GAP,
                           now=1_000_000.0, path=p)
    vb.manage("GGG", 102.0, None, None, now=1_000_100.0, path=p)
    row = {t["id"]: t for t in vb.load(p)}[pos["id"]]
    assert row["stop"] == 98.0                                  # baseline untouched


def test_weekend_flat_applies_spread(tmp_path):
    p = tmp_path / "book.db"
    pos = vb.open_position(ticker="CCC", trader="bot-gap-mgd", entry=100.0,
                           stop=98.0, target=110.0, managed=True, init_stop=98.0,
                           hold_weekend=False, snapshot=_GAP, now=1_000_000.0, path=p)
    changed = store.manage_trades("CCC", 105.0, reports=None, trend_change=None,
                                  now=1_000_100.0, friday_flat=True, db_path=p)
    assert changed and changed[0]["close_reason"] == "weekend_flat"
    row = {t["id"]: t for t in vb.load(p)}[pos["id"]]
    expected = round(((105.0 - 0.02) / 100.0 - 1) * 100, 2)   # flat $0.02/share haircut
    assert row["pnl_pct"] == expected


def test_gap_pair_shares_cohort_and_ab(tmp_path):
    p = tmp_path / "book.db"
    f = vb.open_position(ticker="DDD", trader="bot-gap-fixed", entry=50.0, stop=49.0,
                         target=52.0, snapshot=_GAP, managed=False, init_stop=49.0,
                         now=1_000_000.0, path=p)
    m = vb.open_position(ticker="DDD", trader="bot-gap-mgd", entry=50.0, stop=49.0,
                         target=52.0, snapshot=_GAP, managed=True, init_stop=49.0,
                         now=1_000_000.0, path=p)
    book = {t["id"]: t for t in vb.load(p)}
    assert book[f["id"]]["cohort_id"] == book[m["id"]]["cohort_id"]   # same idea
    vb.close_position(f["id"], 49.0, now=1_000_100.0, path=p)         # fixed loss
    vb.close_position(m["id"], 52.0, now=1_000_100.0, path=p)         # managed win
    ab = vb.managed_vs_fixed(p)
    assert ab["n_pairs"] == 1
    assert ab["pairs"][0]["delta_pct"] == round((52.0 / 50 - 1) * 100
                                                - (49.0 / 50 - 1) * 100, 2)


def test_algorithm_correctness_excludes_managed(tmp_path):
    p = tmp_path / "book.db"
    m = vb.open_position(ticker="EEE", trader="bot-gap-mgd", entry=50.0, stop=49.0,
                         target=52.0, snapshot=_GAP, managed=True, init_stop=49.0,
                         now=1_000_000.0, path=p)
    vb.close_position(m["id"], 52.0, now=1_000_100.0, path=p)
    assert vb.algorithm_correctness(p)["totals"]["n_ideas"] == 0      # managed excluded


def test_mark_trades_baseline_unchanged(tmp_path):
    # Regression: a plain managed=False position still stops out at the stop with
    # NO spread haircut (mark_trades uses _close_row default spread=0.0).
    p = tmp_path / "book.db"
    vb.open_position(ticker="HHH", trader="me", entry=14.0, stop=13.3, target=14.8,
                     snapshot={"setup": "x"}, now=1_000_000.0, path=p)
    changed = vb.mark("HHH", 13.2, now=1_000_100.0, path=p)
    assert changed[0]["close_reason"] == "stop_hit"
    assert changed[0]["pnl_pct"] == round((13.3 / 14.0 - 1) * 100, 2)
